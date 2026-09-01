import { useMemo, useState, type FormEvent } from "react";
import { CheckCircle2, Circle, CircleDot, Loader2, Plus, Trash2, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type {
  CollaborationProjectPayload,
  CollaborationTask,
  CollaborationTaskStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const TASK_STATUSES: Array<{
  value: CollaborationTaskStatus;
  label: string;
}> = [
  { value: "todo", label: "To do" },
  { value: "in_progress", label: "In progress" },
  { value: "done", label: "Done" },
  { value: "cancelled", label: "Cancelled" },
];

function StatusIcon({ status }: { status: CollaborationTaskStatus }) {
  if (status === "done") return <CheckCircle2 className="h-4 w-4 text-foreground/70" aria-hidden />;
  if (status === "in_progress") return <CircleDot className="h-4 w-4 text-foreground/70" aria-hidden />;
  if (status === "cancelled") return <XCircle className="h-4 w-4 text-muted-foreground" aria-hidden />;
  return <Circle className="h-4 w-4 text-muted-foreground" aria-hidden />;
}

function TaskRow({
  task,
  busy,
  onStatusChange,
  onDelete,
}: {
  task: CollaborationTask;
  busy: boolean;
  onStatusChange: (status: CollaborationTaskStatus) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
}) {
  return (
    <li className="flex min-h-12 items-center gap-3 border-t border-border/45 px-3 py-2.5 first:border-t-0 sm:px-4">
      <StatusIcon status={task.status} />
      <div className="min-w-0 flex-1">
        <p className={cn(
          "break-words text-sm font-medium leading-5",
          (task.status === "done" || task.status === "cancelled") && "text-muted-foreground line-through",
        )}>
          {task.title}
        </p>
        {task.description ? (
          <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-muted-foreground">
            {task.description}
          </p>
        ) : null}
      </div>
      <label className="sr-only" htmlFor={`task-status-${task.id}`}>Status for {task.title}</label>
      <select
        id={`task-status-${task.id}`}
        value={task.status}
        disabled={busy}
        onChange={(event) => void onStatusChange(event.target.value as CollaborationTaskStatus)}
        className="h-11 max-w-32 rounded-control border border-input bg-background px-2 text-xs font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
      >
        {TASK_STATUSES.map((status) => (
          <option key={status.value} value={status.value}>{status.label}</option>
        ))}
      </select>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        disabled={busy}
        aria-label={`Delete ${task.title}`}
        title="Delete task"
        onClick={() => void onDelete()}
        className="h-11 w-11 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Trash2 className="h-4 w-4" aria-hidden />}
      </Button>
    </li>
  );
}

function NewTaskForm({
  listId,
  busy,
  onCreate,
}: {
  listId: string;
  busy: boolean;
  onCreate: (title: string) => Promise<unknown>;
}) {
  const [title, setTitle] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = title.trim();
    if (!value || busy) return;
    try {
      await onCreate(value);
      setTitle("");
    } catch {
      // The shared project error region reports mutation failures.
    }
  };

  return (
    <form onSubmit={(event) => void submit(event)} className="flex gap-2 border-t border-border/45 p-3">
      <label className="sr-only" htmlFor={`new-task-${listId}`}>Task title</label>
      <Input
        id={`new-task-${listId}`}
        value={title}
        maxLength={512}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Add a task"
        disabled={busy}
        className="h-11 min-w-0 flex-1 bg-background"
      />
      <Button type="submit" variant="secondary" disabled={!title.trim() || busy} className="h-11 px-3">
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Plus className="h-4 w-4" aria-hidden />}
        <span className="ml-2 hidden sm:inline">Add task</span>
      </Button>
    </form>
  );
}

export function TaskBoard({
  detail,
  busyKey,
  onCreateList,
  onCreateTask,
  onUpdateTaskStatus,
  onDeleteTask,
}: {
  detail: CollaborationProjectPayload;
  busyKey: string | null;
  onCreateList: (name: string) => Promise<unknown>;
  onCreateTask: (listId: string, title: string) => Promise<unknown>;
  onUpdateTaskStatus: (taskId: string, status: CollaborationTaskStatus) => Promise<unknown>;
  onDeleteTask: (taskId: string) => Promise<unknown>;
}) {
  const [listName, setListName] = useState("");
  const tasksByList = useMemo(() => {
    const grouped = new Map<string, CollaborationTask[]>();
    detail.task_lists.forEach((list) => grouped.set(list.id, []));
    detail.tasks.forEach((task) => grouped.get(task.task_list_id)?.push(task));
    return grouped;
  }, [detail.task_lists, detail.tasks]);

  const submitList = async (event: FormEvent) => {
    event.preventDefault();
    const name = listName.trim();
    if (!name || busyKey) return;
    try {
      await onCreateList(name);
      setListName("");
    } catch {
      // The shared project error region reports mutation failures.
    }
  };

  return (
    <section aria-labelledby="project-tasks-title" className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="project-tasks-title" className="text-lg font-semibold tracking-tight">Tasks</h2>
          <p className="mt-1 text-sm leading-5 text-muted-foreground">
            Keep the project’s next actions close to the conversations that use them.
          </p>
        </div>
        <form onSubmit={(event) => void submitList(event)} className="flex w-full gap-2 sm:max-w-sm">
          <label className="sr-only" htmlFor="new-task-list">List name</label>
          <Input
            id="new-task-list"
            value={listName}
            maxLength={512}
            onChange={(event) => setListName(event.target.value)}
            placeholder="New list name"
            disabled={Boolean(busyKey)}
            className="min-w-0 flex-1"
          />
          <Button type="submit" variant="outline" disabled={!listName.trim() || Boolean(busyKey)} className="shrink-0">
            {busyKey === "list:create" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : <Plus className="mr-2 h-4 w-4" aria-hidden />}
            Add list
          </Button>
        </form>
      </div>

      {detail.task_lists.length === 0 ? (
        <div className="rounded-panel bg-settings-surface px-5 py-8 text-center">
          <p className="text-sm font-medium">Start with a task list</p>
          <p className="mx-auto mt-1 max-w-md text-sm leading-6 text-muted-foreground">
            Create a list such as “Next” or “Backlog”, then add the work you want nanobot to keep in context.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {detail.task_lists.map((list) => {
            const tasks = tasksByList.get(list.id) ?? [];
            return (
              <article key={list.id} className="overflow-hidden rounded-panel bg-settings-surface">
                <header className="flex min-h-12 items-center justify-between gap-3 px-4 py-3">
                  <h3 className="text-sm font-semibold">{list.name}</h3>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {tasks.length} {tasks.length === 1 ? "task" : "tasks"}
                  </span>
                </header>
                {tasks.length ? (
                  <ul>
                    {tasks.map((task) => (
                      <TaskRow
                        key={task.id}
                        task={task}
                        busy={busyKey === `task:update:${task.id}` || busyKey === `task:delete:${task.id}`}
                        onStatusChange={(status) => onUpdateTaskStatus(task.id, status)}
                        onDelete={() => onDeleteTask(task.id)}
                      />
                    ))}
                  </ul>
                ) : (
                  <p className="border-t border-border/45 px-4 py-4 text-sm text-muted-foreground">
                    Add the first task to this list.
                  </p>
                )}
                <NewTaskForm
                  listId={list.id}
                  busy={busyKey === `task:create:${list.id}`}
                  onCreate={(title) => onCreateTask(list.id, title)}
                />
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
