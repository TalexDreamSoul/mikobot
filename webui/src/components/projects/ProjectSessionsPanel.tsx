import { useTranslation } from "react-i18next";
import { MessagesSquare } from "lucide-react";

import { deriveTitle, relativeTime } from "@/lib/format";
import type { ChatSummary } from "@/lib/types";

/**
 * The conversations this project owns.
 *
 * Every conversation runs inside a project: one started with a project keeps
 * that project, and one without a project of its own belongs to the caller's
 * default project. This panel is where that attribution becomes visible, so a
 * project is not only its board and its members but also its conversations.
 */
export function ProjectSessionsPanel({
  projectId,
  sessions,
  builtin = false,
  onOpenSession,
}: {
  projectId: string;
  sessions: ChatSummary[];
  /** The built-in project holds the host's own conversations. */
  builtin?: boolean;
  onOpenSession?: (key: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...values });
  const owned = sessions.filter(
    (session) => session.collaborationProjectId === projectId,
  );

  return (
    <section
      aria-labelledby="project-sessions-title"
      className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
    >
      <h3
        id="project-sessions-title"
        className="text-sm font-semibold text-foreground"
      >
        {tx("projects.panels.sessions", "Conversations")}
      </h3>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        {tx(
          "projects.sessions.description",
          "Conversations that run in this project. Ones without a project of their own belong to the default project.",
        )}
      </p>
      {builtin ? (
        <p className="mt-2 rounded-control border border-border/55 bg-settings-surface px-3 py-2 text-xs leading-5 text-muted-foreground">
          {tx(
            "projects.sessions.builtinNote",
            "The host's own conversations belong here: they run in the default project, so this project is their home.",
          )}
        </p>
      ) : null}
      {owned.length ? (
        <ul className="mt-3 space-y-1">
          {owned.map((session) => {
            const title = session.title?.trim() || deriveTitle(
              session.preview,
              tx("projects.sessions.untitled", "Untitled conversation"),
            );
            const updated = relativeTime(session.updatedAt, i18n.language);
            return (
              <li key={session.key}>
                <button
                  type="button"
                  onClick={onOpenSession ? () => onOpenSession(session.key) : undefined}
                  disabled={!onOpenSession}
                  className="flex w-full items-center gap-3 rounded-control px-2 py-2 text-left transition-colors hover:bg-muted/55 disabled:cursor-default disabled:hover:bg-transparent"
                >
                  <MessagesSquare
                    className="h-4 w-4 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                  <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                    {title}
                  </span>
                  {updated ? (
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {updated}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          {tx("projects.sessions.empty", "No conversations in this project yet.")}
        </p>
      )}
    </section>
  );
}
