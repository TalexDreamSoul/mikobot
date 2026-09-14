import { useCallback, useEffect, useState } from "react";
import { Brain, Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { fetchCollaborationProjectMemory } from "@/lib/api";
import type { CollaborationProjectMaterialsPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

export function ProjectMemoryPanel({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const { getToken } = useClient();
  const [memory, setMemory] = useState<CollaborationProjectMaterialsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadMemory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMemory(await fetchCollaborationProjectMemory(getToken(), projectId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setMemory(null);
    } finally {
      setLoading(false);
    }
  }, [getToken, projectId]);

  useEffect(() => {
    void loadMemory();
  }, [loadMemory]);

  return (
    <section aria-labelledby="project-memory-title" className="overflow-hidden rounded-panel bg-settings-surface">
      <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
        <Brain className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 id="project-memory-title" className="text-sm font-semibold">
            {t("projects.memory.title")}
          </h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {t("projects.memory.description")}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => void loadMemory()}
          disabled={loading}
          aria-label={t("projects.memory.refresh")}
          className="h-9 w-9 shrink-0"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />}
        </Button>
      </header>
      {error ? <p role="alert" className="border-t border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">{error}</p> : null}
      {loading ? (
        <div aria-busy="true" className="flex min-h-48 items-center justify-center border-t border-border/45 text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          {t("projects.memory.loading")}
        </div>
      ) : memory?.previewable ? (
        <div className="border-t border-border/45">
          <div className="flex min-h-11 items-center justify-between gap-3 px-4 py-2.5">
            <code className="text-xs font-medium">memory/MEMORY.md</code>
            {memory.truncated ? <span className="text-[10px] text-muted-foreground">{t("projects.memory.truncated")}</span> : null}
          </div>
          <pre className="max-h-[34rem] overflow-auto whitespace-pre-wrap break-words border-t border-border/45 bg-background/45 p-4 font-mono text-xs leading-5 text-foreground/85">{memory.content}</pre>
        </div>
      ) : (
        <div className="flex min-h-48 items-center justify-center border-t border-border/45 px-6 text-center text-sm text-muted-foreground">
          {t("projects.memory.empty")}
        </div>
      )}
    </section>
  );
}
