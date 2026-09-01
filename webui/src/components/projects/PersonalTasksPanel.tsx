import { CheckCircle2, Plus, Trash2, UserRound, Vault } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { usePersonalAssistant } from "@/hooks/usePersonalAssistant";
import type { PersonalTask } from "@/lib/types";

function taskBucket(task: PersonalTask): "overdue" | "today" | "upcoming" | "inbox" {
  if (task.review_state === "proposed" || task.due_at_ms === null) return "inbox";
  const due = new Date(task.due_at_ms);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  if (due < start) return "overdue";
  if (due < end) return "today";
  return "upcoming";
}

const SECTIONS: Array<{ id: ReturnType<typeof taskBucket>; label: string }> = [
  { id: "overdue", label: "Overdue" },
  { id: "today", label: "Today" },
  { id: "inbox", label: "Inbox" },
  { id: "upcoming", label: "Upcoming" },
];

function TaskRow({
  task,
  busy,
  onComplete,
  onDelete,
}: {
  task: PersonalTask;
  busy: boolean;
  onComplete: () => Promise<unknown>;
  onDelete: () => Promise<unknown>;
}) {
  const due = task.due_at_ms === null ? null : new Date(task.due_at_ms).toLocaleString();
  return (
    <li className="flex items-center gap-3 border-t border-border/45 py-3 first:border-t-0">
      <button
        type="button"
        disabled={busy || task.status === "done"}
        onClick={() => void onComplete()}
        aria-label={`Complete ${task.title}`}
        className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-input text-muted-foreground transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
      >
        <CheckCircle2 className="h-4 w-4" aria-hidden />
      </button>
      <div className="min-w-0 flex-1">
        <p className={task.status === "done" ? "truncate text-sm text-muted-foreground line-through" : "truncate text-sm font-medium"}>
          {task.title}
        </p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {task.review_state === "proposed" ? "Needs confirmation" : due ?? "No scheduled time"}
        </p>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        disabled={busy}
        onClick={() => void onDelete()}
        aria-label={`Delete ${task.title}`}
        className="h-9 w-9 text-muted-foreground"
      >
        <Trash2 className="h-4 w-4" aria-hidden />
      </Button>
    </li>
  );
}

export function PersonalTasksPanel() {
  const assistant = usePersonalAssistant();
  const [title, setTitle] = useState("");
  const [vaultName, setVaultName] = useState("");
  const [personaName, setPersonaName] = useState("");
  const defaultVaultId = assistant.data?.default_vault_id ?? assistant.data?.vaults[0]?.id ?? "";
  const groups = useMemo(() => {
    const result = new Map<ReturnType<typeof taskBucket>, PersonalTask[]>();
    SECTIONS.forEach(({ id }) => result.set(id, []));
    assistant.data?.tasks.forEach((task) => result.get(taskBucket(task))?.push(task));
    return result;
  }, [assistant.data?.tasks]);

  if (assistant.loading && !assistant.data) {
    return <div className="rounded-panel bg-settings-surface p-5 text-sm text-muted-foreground">Loading personal assistant…</div>;
  }

  return (
    <div className="space-y-5">
      {assistant.error ? (
        <div role="alert" className="rounded-control border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {assistant.error}
        </div>
      ) : null}

      <section className="rounded-panel border border-border/50 bg-settings-surface p-4 sm:p-5">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-primary" aria-hidden />
          <div>
            <h3 className="font-semibold">My day</h3>
            <p className="text-xs text-muted-foreground">Private tasks stay inside their selected vault.</p>
          </div>
        </div>
        <form
          className="mt-4 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const next = title.trim();
            if (!next || !defaultVaultId) return;
            void assistant.createTask({ vault_id: defaultVaultId, title: next }).then(() => setTitle(""));
          }}
        >
          <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Add a task" />
          <Button type="submit" disabled={!title.trim() || !defaultVaultId || assistant.busy === "task:create"}>
            <Plus className="mr-2 h-4 w-4" aria-hidden />Add
          </Button>
        </form>
      </section>

      {SECTIONS.map(({ id, label }) => {
        const tasks = groups.get(id) ?? [];
        if (!tasks.length) return null;
        return (
          <section key={id} className="rounded-panel border border-border/50 bg-background px-4 py-3 sm:px-5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</h3>
            <ul className="mt-2">
              {tasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  busy={assistant.busy === `task:${task.id}`}
                  onComplete={() => assistant.updateTask(task, { status: "done", review_state: "confirmed" })}
                  onDelete={() => assistant.removeTask(task)}
                />
              ))}
            </ul>
          </section>
        );
      })}

      <section className="grid gap-4 rounded-panel border border-border/50 bg-settings-surface p-4 sm:grid-cols-2 sm:p-5">
        <div>
          <div className="flex items-center gap-2"><Vault className="h-4 w-4" aria-hidden /><h3 className="font-semibold">Vaults</h3></div>
          <ul className="mt-3 space-y-1 text-sm text-muted-foreground">
            {assistant.data?.vaults.map((vault) => <li key={vault.id}>{vault.name} · {vault.kind}</li>)}
          </ul>
          <form className="mt-3 flex gap-2" onSubmit={(event) => {
            event.preventDefault();
            const next = vaultName.trim();
            if (!next) return;
            void assistant.createVault(next, "private").then(() => setVaultName(""));
          }}>
            <Input value={vaultName} onChange={(event) => setVaultName(event.target.value)} placeholder="New vault" />
            <Button type="submit" variant="outline" disabled={!vaultName.trim() || assistant.busy === "vault:create"}>Add</Button>
          </form>
        </div>
        <div>
          <div className="flex items-center gap-2"><UserRound className="h-4 w-4" aria-hidden /><h3 className="font-semibold">Assistant identities</h3></div>
          <ul className="mt-3 space-y-1 text-sm text-muted-foreground">
            {assistant.data?.personas.map((persona) => <li key={persona.id}>{persona.name}</li>)}
          </ul>
          <label className="mt-3 block text-xs font-medium text-muted-foreground" htmlFor="default-persona">Default identity</label>
          <select
            id="default-persona"
            value={assistant.data?.default_persona_id ?? ""}
            disabled={!assistant.data?.personas.length || assistant.busy === "persona:default"}
            onChange={(event) => {
              if (event.target.value) void assistant.setDefaultPersona(event.target.value);
            }}
            className="mt-1 h-10 w-full rounded-control border border-input bg-background px-3 text-sm"
          >
            {assistant.data?.personas.map((persona) => <option key={persona.id} value={persona.id}>{persona.name}</option>)}
          </select>
          <form className="mt-3 flex gap-2" onSubmit={(event) => {
            event.preventDefault();
            const next = personaName.trim();
            if (!next || !defaultVaultId) return;
            void assistant.createPersona({ name: next, vault_id: defaultVaultId }).then(() => setPersonaName(""));
          }}>
            <Input value={personaName} onChange={(event) => setPersonaName(event.target.value)} placeholder="New identity" />
            <Button type="submit" variant="outline" disabled={!personaName.trim() || !defaultVaultId || assistant.busy === "persona:create"}>Add</Button>
          </form>
        </div>
      </section>
    </div>
  );
}
