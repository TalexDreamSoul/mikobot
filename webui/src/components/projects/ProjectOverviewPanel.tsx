import { useEffect, useState, type FormEvent } from "react";
import { FileText, Info, Loader2, Save } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { CollaborationProjectPayload } from "@/lib/types";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";

export function ProjectOverviewPanel({
  detail,
  canManage,
  busyKey,
  onSave,
}: {
  detail: CollaborationProjectPayload;
  canManage: boolean;
  busyKey: string | null;
  onSave: CollaborationProjectsController["updateProjectDescription"];
}) {
  const { t } = useTranslation();
  const savedDescription = detail.project.description ?? "";
  const [description, setDescription] = useState(savedDescription);

  useEffect(() => {
    setDescription(savedDescription);
  }, [detail.project.id, savedDescription]);

  const dirty = description !== savedDescription;
  const saving = busyKey === "project:description";
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!dirty || saving) return;
    try {
      await onSave(description.trim());
    } catch {
      // The shared project error region reports the authoritative failure.
    }
  };

  return (
    <section aria-labelledby="project-overview-title" className="space-y-5">
      <div>
        <h2 id="project-overview-title" className="text-lg font-semibold tracking-tight">
          {t("projects.overview.title")}
        </h2>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
          {t("projects.overview.description")}
        </p>
      </div>

      <form onSubmit={(event) => void submit(event)} className="rounded-panel bg-settings-surface p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold">{t("projects.overview.introductionTitle")}</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {t("projects.overview.introductionDescription")}
            </p>
          </div>
        </div>
        <Textarea
          aria-label={t("projects.overview.introductionLabel")}
          value={description}
          maxLength={8000}
          rows={7}
          disabled={!canManage || saving}
          onChange={(event) => setDescription(event.target.value)}
          placeholder={t("projects.overview.placeholder")}
          className="mt-4 min-h-36 resize-y"
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {description.length}/8000
          </span>
          {canManage ? (
            <Button type="submit" disabled={!dirty || saving} className="h-9">
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : <Save className="mr-2 h-4 w-4" aria-hidden />}
              {t("projects.overview.save")}
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">{t("projects.overview.readOnly")}</span>
          )}
        </div>
      </form>

      <div className="rounded-panel border border-border/55 bg-settings-surface p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <div>
            <h3 className="text-sm font-semibold">{t("projects.overview.runtimeTitle")}</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {t("projects.overview.runtimeDescription")}
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
