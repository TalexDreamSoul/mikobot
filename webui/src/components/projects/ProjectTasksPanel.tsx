import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft,
  ChevronRight,
  ListChecks,
  Loader2,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import type {
  CollaborationProjectPayload,
  CollaborationProjectTask,
  CollaborationTaskStatus,
} from "@/lib/types";

const COLUMNS: CollaborationTaskStatus[] = ["todo", "doing", "done"];

/** The column a card moves to, or ``null`` at the ends of the board. */
function destinationOf(
  status: CollaborationTaskStatus,
  direction: -1 | 1,
): CollaborationTaskStatus | null {
  return COLUMNS[COLUMNS.indexOf(status) + direction] ?? null;
}

/**
 * The project's shared board.
 *
 * Tasks belong to the project, so every member reads and writes the same three
 * columns; only discarding someone else's card needs the author or a manager.
 */
export function ProjectTasksPanel({
  detail,
  currentUserId,
  busyKey,
  onCreate,
  onUpdate,
  onDelete,
}: {
  detail: CollaborationProjectPayload;
  currentUserId: string;
  busyKey: string | null;
  onCreate: CollaborationProjectsController["createTask"];
  onUpdate: CollaborationProjectsController["updateTask"];
  onDelete: CollaborationProjectsController["deleteTask"];
}) {
  const { t } = useTranslation();
  const [composing, setComposing] = useState(false);
  const [editing, setEditing] = useState<CollaborationProjectTask | null>(null);
  const [discarding, setDiscarding] = useState<CollaborationProjectTask | null>(null);

  const authorName = (userId: string) => {
    if (userId === currentUserId) return t("projects.tasks.you");
    return detail.members.find((member) => member.user_id === userId)?.user_id
      ?? t("projects.tasks.unknownAuthor");
  };

  const move = (task: CollaborationProjectTask, direction: -1 | 1) => {
    const destination = destinationOf(task.status, direction);
    if (!destination) return;
    void onUpdate(task.id, { status: destination }).catch(() => {
      // The shared project error region reports the failure.
    });
  };

  return (
    <section
      aria-labelledby="project-tasks-title"
      className="overflow-hidden rounded-panel bg-settings-surface"
    >
      <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
        <ListChecks className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 id="project-tasks-title" className="text-sm font-semibold">
            {t("projects.tasks.title")}
          </h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {t("projects.tasks.description")}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => setComposing(true)}
          disabled={busyKey === "task:create"}
          className="h-9 shrink-0"
        >
          {busyKey === "task:create"
            ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
            : <Plus className="mr-2 h-4 w-4" aria-hidden />}
          {t("projects.tasks.add")}
        </Button>
      </header>

      <div className="grid gap-3 border-t border-border/45 p-4 sm:p-5 md:grid-cols-3">
        {COLUMNS.map((column) => {
          const cards = detail.tasks.filter((task) => task.status === column);
          return (
            <div key={column} className="flex min-w-0 flex-col rounded-control bg-background">
              <h3 className="flex items-center justify-between gap-2 px-3 py-2 text-xs font-semibold text-muted-foreground">
                <span>{t(`projects.tasks.columns.${column}`)}</span>
                <span aria-hidden className="tabular-nums">{cards.length}</span>
              </h3>
              {cards.length ? (
                <ul className="flex flex-col gap-2 px-3 pb-3">
                  {cards.map((task) => (
                    <li
                      key={task.id}
                      className="rounded-compact border border-border/55 bg-settings-surface p-3"
                    >
                      <p className="break-words text-sm font-medium">{task.title}</p>
                      {task.detail ? (
                        <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">
                          {task.detail}
                        </p>
                      ) : null}
                      <p className="mt-2 text-[11px] text-muted-foreground">
                        {t("projects.tasks.author", { name: authorName(task.created_by_user_id) })}
                      </p>
                      <div className="mt-2 flex items-center gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => move(task, -1)}
                          disabled={column === "todo" || Boolean(busyKey)}
                          aria-label={t("projects.tasks.moveBack", { title: task.title })}
                          className="h-8 w-8 text-muted-foreground"
                        >
                          <ChevronLeft className="h-4 w-4" aria-hidden />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => move(task, 1)}
                          disabled={column === "done" || Boolean(busyKey)}
                          aria-label={t("projects.tasks.moveForward", { title: task.title })}
                          className="h-8 w-8 text-muted-foreground"
                        >
                          <ChevronRight className="h-4 w-4" aria-hidden />
                        </Button>
                        <span className="flex-1" />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => setEditing(task)}
                          disabled={Boolean(busyKey)}
                          aria-label={t("projects.tasks.editAria", { title: task.title })}
                          className="h-8 w-8 text-muted-foreground"
                        >
                          <Pencil className="h-4 w-4" aria-hidden />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => setDiscarding(task)}
                          disabled={Boolean(busyKey)}
                          aria-label={t("projects.tasks.deleteAria", { title: task.title })}
                          className="h-8 w-8 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" aria-hidden />
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-3 pb-4 pt-1 text-xs text-muted-foreground">
                  {t("projects.tasks.emptyColumn")}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <TaskForm
        key={editing?.id ?? "new"}
        open={composing || editing !== null}
        task={editing}
        busy={busyKey === "task:create" || busyKey === `task:update:${editing?.id ?? ""}`}
        onCancel={() => {
          setComposing(false);
          setEditing(null);
        }}
        onSubmit={async (title, detailText) => {
          if (editing) {
            await onUpdate(editing.id, { title, detail: detailText });
          } else {
            await onCreate(title, detailText);
          }
          setComposing(false);
          setEditing(null);
        }}
      />

      <AlertDialog
        open={discarding !== null}
        onOpenChange={(next) => !next && setDiscarding(null)}
      >
        <AlertDialogContent className="w-[min(calc(100vw-2rem),24rem)]">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("projects.tasks.deleteConfirmTitle", { title: discarding?.title })}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("projects.tasks.deleteConfirmDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="h-11">{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="h-11 bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                const task = discarding;
                setDiscarding(null);
                if (task) {
                  void onDelete(task.id).catch(() => {
                    // The shared project error region reports the failure.
                  });
                }
              }}
            >
              {t("projects.tasks.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

function TaskForm({
  open,
  task,
  busy,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  task: CollaborationProjectTask | null;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (title: string, detail: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  // The dialog content unmounts when it closes, and the parent re-keys it per
  // card, so the fields always start from the card being edited.
  const [title, setTitle] = useState(task?.title ?? "");
  const [detail, setDetail] = useState(task?.detail ?? "");
  const editingId = task?.id ?? null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = title.trim();
    if (!value || busy) return;
    try {
      await onSubmit(value, detail);
    } catch {
      // The shared project error region reports the failure; keep the dialog open.
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="w-[min(calc(100vw-2rem),28rem)]">
        <form onSubmit={(event) => void submit(event)}>
          <DialogHeader>
            <DialogTitle>
              {editingId ? t("projects.tasks.edit") : t("projects.tasks.add")}
            </DialogTitle>
            <DialogDescription>{t("projects.tasks.formDescription")}</DialogDescription>
          </DialogHeader>
          <div className="mt-4 space-y-3">
            <div>
              <label htmlFor="task-title" className="text-xs font-medium">
                {t("projects.tasks.titleLabel")}
              </label>
              <Input
                id="task-title"
                value={title}
                maxLength={512}
                autoFocus
                disabled={busy}
                onChange={(event) => setTitle(event.target.value)}
                placeholder={t("projects.tasks.titlePlaceholder")}
                className="mt-1 bg-background"
              />
            </div>
            <div>
              <label htmlFor="task-detail" className="text-xs font-medium">
                {t("projects.tasks.detailLabel")}
              </label>
              <Textarea
                id="task-detail"
                value={detail}
                rows={4}
                disabled={busy}
                onChange={(event) => setDetail(event.target.value)}
                placeholder={t("projects.tasks.detailPlaceholder")}
                className="mt-1 bg-background"
              />
            </div>
          </div>
          <DialogFooter className="mt-5">
            <Button type="button" variant="outline" className="h-11" onClick={onCancel} disabled={busy}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" className="h-11" disabled={!title.trim() || busy}>
              {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
              {editingId ? t("projects.tasks.save") : t("projects.tasks.add")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
