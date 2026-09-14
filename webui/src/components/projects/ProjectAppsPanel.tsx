import { useTranslation } from "react-i18next";
import { Blocks, Loader2, ShieldAlert, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import type {
  CollaborationProjectApp,
  CollaborationProjectPayload,
} from "@/lib/types";

/**
 * The apps a project may use.
 *
 * An app grants the skills and MCP servers it contributes. Approving one records
 * the exact revision the host runs; when that revision changes, the host takes
 * the capabilities back until someone approves the app again.
 */
export function ProjectAppsPanel({
  detail,
  busyKey,
  canManage,
  onJoin,
  onLeave,
}: {
  detail: CollaborationProjectPayload;
  busyKey: string | null;
  canManage: boolean;
  onJoin: CollaborationProjectsController["joinProjectApp"];
  onLeave: CollaborationProjectsController["leaveProjectApp"];
}) {
  const { t } = useTranslation();
  const apps = detail.apps;

  return (
    <section
      aria-labelledby="project-apps-title"
      className="overflow-hidden rounded-panel bg-settings-surface"
    >
      <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
        <Blocks className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
        <div className="min-w-0">
          <h2 id="project-apps-title" className="text-sm font-semibold">
            {t("projects.apps.title")}
          </h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {t("projects.apps.description")}
          </p>
        </div>
      </header>
      {apps.length ? (
        <ul className="border-t border-border/45">
          {apps.map((app) => (
            <li
              key={app.name}
              className="border-t border-border/45 px-4 py-3.5 first:border-t-0 sm:px-5"
            >
              <ProjectAppRow
                app={app}
                busyKey={busyKey}
                canManage={canManage}
                onJoin={onJoin}
                onLeave={onLeave}
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className="border-t border-border/45 px-4 py-5 text-sm text-muted-foreground sm:px-5">
          {t("projects.apps.empty")}
        </p>
      )}
      {!canManage ? (
        <p className="border-t border-border/45 px-4 py-3 text-xs leading-5 text-muted-foreground sm:px-5">
          {t("projects.apps.readOnly")}
        </p>
      ) : null}
    </section>
  );
}

function ProjectAppRow({
  app,
  busyKey,
  canManage,
  onJoin,
  onLeave,
}: {
  app: CollaborationProjectApp;
  busyKey: string | null;
  canManage: boolean;
  onJoin: CollaborationProjectsController["joinProjectApp"];
  onLeave: CollaborationProjectsController["leaveProjectApp"];
}) {
  const { t } = useTranslation();
  const busy = busyKey === `project:app:join:${app.name}`
    || busyKey === `project:app:leave:${app.name}`;
  const capabilities = [...app.skills, ...app.mcp_servers];

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium">{app.display_name}</span>
          <AppStateBadge app={app} />
        </p>
        {app.description ? (
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{app.description}</p>
        ) : null}
        <p className="mt-1 text-xs text-muted-foreground">
          {t("projects.apps.grants", { count: capabilities.length })}
          {capabilities.length ? ` · ${capabilities.join(", ")}` : ""}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {app.approved
            ? t("projects.apps.approvedRevision", { revision: app.approved_revision ?? "—" })
            : t("projects.apps.installedRevision", { revision: app.revision ?? "—" })}
        </p>
      </div>
      {canManage ? (
        <div className="flex shrink-0 items-center gap-2">
          {app.approved ? (
            <>
              {app.drifted ? (
                <Button
                  type="button"
                  size="sm"
                  disabled={busy || !app.enabled}
                  onClick={() => void onJoin(app.name, app.revision).catch(() => undefined)}
                >
                  {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
                  {t("projects.apps.reapprove")}
                </Button>
              ) : null}
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void onLeave(app.name).catch(() => undefined)}
              >
                {t("projects.apps.remove")}
              </Button>
            </>
          ) : (
            <Button
              type="button"
              size="sm"
              disabled={busy || !app.enabled}
              onClick={() => void onJoin(app.name, app.revision).catch(() => undefined)}
            >
              {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
              {t("projects.apps.add")}
            </Button>
          )}
        </div>
      ) : null}
    </div>
  );
}

function AppStateBadge({ app }: { app: CollaborationProjectApp }) {
  const { t } = useTranslation();
  if (!app.approved) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
        {t("projects.apps.stateAvailable")}
      </span>
    );
  }
  if (!app.drifted) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
        <ShieldCheck className="h-3 w-3" aria-hidden />
        {t("projects.apps.stateApproved")}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2 py-0.5 text-[11px] font-medium text-destructive">
      <ShieldAlert className="h-3 w-3" aria-hidden />
      {app.enabled
        ? t("projects.apps.stateDrifted")
        : t("projects.apps.stateUnavailable")}
    </span>
  );
}
