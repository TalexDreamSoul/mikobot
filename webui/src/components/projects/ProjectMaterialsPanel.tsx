import { useCallback, useEffect, useState } from "react";
import { File, FolderOpen, Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { fetchCollaborationProjectMaterials } from "@/lib/api";
import type {
  CollaborationProjectMaterial,
  CollaborationProjectMaterialsPayload,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function ProjectMaterialsPanel({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const { getToken } = useClient();
  const [files, setFiles] = useState<CollaborationProjectMaterial[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [preview, setPreview] = useState<CollaborationProjectMaterialsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFiles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchCollaborationProjectMaterials(getToken(), projectId);
      setFiles(payload.files ?? []);
      setSelectedPath((current) => current && (payload.files ?? []).some((file) => file.path === current)
        ? current
        : null);
      setPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [getToken, projectId]);

  useEffect(() => {
    void loadFiles();
  }, [loadFiles]);

  const openFile = async (file: CollaborationProjectMaterial) => {
    if (!file.previewable) return;
    setSelectedPath(file.path);
    setPreviewLoading(true);
    setError(null);
    try {
      setPreview(await fetchCollaborationProjectMaterials(getToken(), projectId, file.path));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setPreview(null);
    } finally {
      setPreviewLoading(false);
    }
  };

  return (
    <section aria-labelledby="project-materials-title" className="overflow-hidden rounded-panel bg-settings-surface">
      <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
        <FolderOpen className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 id="project-materials-title" className="text-sm font-semibold">
            {t("projects.materials.title")}
          </h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {t("projects.materials.description")}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => void loadFiles()}
          disabled={loading}
          aria-label={t("projects.materials.refresh")}
          className="h-9 w-9 shrink-0"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />}
        </Button>
      </header>

      {error ? <p role="alert" className="border-t border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">{error}</p> : null}
      {loading ? (
        <div aria-busy="true" className="flex min-h-40 items-center justify-center border-t border-border/45 text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          {t("projects.materials.loading")}
        </div>
      ) : files.length ? (
        <div className="grid border-t border-border/45 lg:grid-cols-[minmax(13rem,18rem)_minmax(0,1fr)]">
          <div className="max-h-[32rem] overflow-y-auto border-b border-border/45 lg:border-b-0 lg:border-r">
            <ul>
              {files.map((file) => (
                <li key={file.path}>
                  <button
                    type="button"
                    onClick={() => void openFile(file)}
                    disabled={!file.previewable}
                    aria-current={file.path === selectedPath ? "page" : undefined}
                    className="flex min-h-11 w-full items-center gap-2 px-4 py-2.5 text-left text-xs transition-colors hover:bg-muted/35 disabled:cursor-default disabled:opacity-55 aria-[current=page]:bg-muted/55"
                  >
                    <File className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
                    <span className="shrink-0 tabular-nums text-[10px] text-muted-foreground">{formatBytes(file.size)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="min-h-56 min-w-0 bg-background/45">
            {previewLoading ? (
              <div className="flex h-full min-h-56 items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
                {t("projects.materials.previewLoading")}
              </div>
            ) : preview ? (
              <div className="min-w-0">
                <div className="flex min-h-11 items-center justify-between gap-3 border-b border-border/45 px-4 py-2.5">
                  <code className="min-w-0 truncate text-xs font-medium">{preview.path}</code>
                  {preview.truncated ? <span className="shrink-0 text-[10px] text-muted-foreground">{t("projects.materials.truncated")}</span> : null}
                </div>
                <pre className="max-h-[29rem] overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-5 text-foreground/85">{preview.content}</pre>
              </div>
            ) : (
              <div className="flex h-full min-h-56 items-center justify-center px-6 text-center text-sm text-muted-foreground">
                {t("projects.materials.selectFile")}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="flex min-h-40 items-center justify-center border-t border-border/45 px-6 text-center text-sm text-muted-foreground">
          {t("projects.materials.empty")}
        </div>
      )}
    </section>
  );
}
