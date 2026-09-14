import { useTranslation } from "react-i18next";
import { CalendarClock, Loader2, ShieldCheck } from "lucide-react";

import { formatAutomationSchedule } from "@/components/settings/system/AutomationsSettings";
import { fmtDateTime } from "@/lib/format";
import type { CollaborationProjectPayload, SessionAutomationJob } from "@/lib/types";

const BUILTIN_AUTOMATION_LABELS: Record<string, string> = {
  heartbeat: "projects.automations.heartbeat",
  dream: "projects.automations.dream",
};

const BUILTIN_AUTOMATION_DESCRIPTIONS: Record<string, string> = {
  heartbeat: "projects.automations.heartbeatDescription",
  dream: "projects.automations.dreamDescription",
};

/**
 * The automations every project owns.
 *
 * Heartbeat and Dream belong to the project, run on configuration's schedule,
 * and cannot be deleted or edited from here — only inspected.
 */
export function ProjectAutomationsPanel({
  detail,
}: {
  detail: CollaborationProjectPayload;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...values });

  return (
    <section
      aria-labelledby="project-automations-title"
      className="overflow-hidden rounded-panel bg-settings-surface"
    >
      <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
        <CalendarClock className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
        <div className="min-w-0">
          <h2 id="project-automations-title" className="text-sm font-semibold">
            {t("projects.automations.title")}
          </h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {t("projects.automations.description")}
          </p>
        </div>
      </header>
      {detail.automations.length ? (
        <ul className="border-t border-border/45">
          {detail.automations.map((job) => (
            <li
              key={job.id}
              className="border-t border-border/45 px-4 py-3.5 first:border-t-0 sm:px-5"
            >
              <ProjectAutomationRow job={job} locale={locale} tx={tx} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="border-t border-border/45 px-4 py-5 text-sm text-muted-foreground sm:px-5">
          {t("projects.automations.empty")}
        </p>
      )}
    </section>
  );
}

function ProjectAutomationRow({
  job,
  locale,
  tx,
}: {
  job: SessionAutomationJob;
  locale: string;
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string;
}) {
  const { t } = useTranslation();
  const labelKey = BUILTIN_AUTOMATION_LABELS[job.name];
  const descriptionKey = BUILTIN_AUTOMATION_DESCRIPTIONS[job.name];
  const lastRun = job.state.last_run_at_ms
    ? fmtDateTime(job.state.last_run_at_ms, locale)
    : t("projects.automations.never");

  return (
    <div className="flex min-h-11 items-start gap-3">
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium">
            {labelKey ? t(labelKey) : job.name}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
            <ShieldCheck className="h-3 w-3" aria-hidden />
            {t("settings.automations.protected")}
          </span>
          {job.state.pending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
          ) : null}
        </p>
        {descriptionKey ? (
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{t(descriptionKey)}</p>
        ) : null}
        <p className="mt-1 text-xs text-muted-foreground">
          {formatAutomationSchedule(job, locale, tx)}
          {job.state.next_run_at_ms
            ? ` · ${t("projects.automations.nextRun", {
              time: fmtDateTime(job.state.next_run_at_ms, locale),
            })}`
            : ""}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t("projects.automations.lastRun", { time: lastRun })}
        </p>
      </div>
    </div>
  );
}
