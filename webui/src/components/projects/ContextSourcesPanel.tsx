import { useState, type FormEvent } from "react";
import { FileText, Loader2, Plus, Trash2, Type } from "lucide-react";

import { ToggleButton } from "@/components/settings/ToggleButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type {
  CollaborationContextSource,
  CollaborationEditableContextSourceKind,
  CollaborationProjectPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const DOCUMENT_PATH_LIMIT = 512;
const CUSTOM_CONTENT_LIMIT = 8_000;

function documentPathError(value: string): string | null {
  const path = value.trim();
  if (!path) return "Enter a document path.";
  if (path.length > DOCUMENT_PATH_LIMIT) return "The path is too long.";
  if (path.includes("\0")) return "The path contains an invalid character.";
  if (path.startsWith("~") || path.startsWith("/") || path.startsWith("\\") || /^[A-Za-z]:[\\/]/.test(path)) {
    return "Use a path relative to this project’s workspace.";
  }
  if (path.split(/[\\/]+/).includes("..")) {
    return "The path must stay inside this project’s workspace.";
  }
  return null;
}

function SourceRow({
  source,
  busy,
  onToggle,
  onDelete,
}: {
  source: CollaborationContextSource;
  busy: boolean;
  onToggle: (enabled: boolean) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const documentPath = typeof source.config.path === "string" ? source.config.path : "";
  const customContent = typeof source.config.content === "string" ? source.config.content : "";
  const detail = source.kind === "document"
    ? documentPath || "Document path unavailable"
    : source.kind === "custom"
      ? `${customContent.length.toLocaleString()} characters of custom text`
      : source.kind;

  const remove = async () => {
    try {
      await onDelete();
    } catch {
      setConfirmDelete(false);
    }
  };

  return (
    <li className="flex min-h-16 flex-col gap-3 border-t border-border/45 px-4 py-3 first:border-t-0 sm:flex-row sm:items-center sm:px-5">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-compact bg-muted text-muted-foreground" aria-hidden>
        {source.kind === "document" ? <FileText className="h-4 w-4" /> : <Type className="h-4 w-4" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-5">{source.name}</p>
        <p className="mt-0.5 truncate text-xs leading-5 text-muted-foreground">{detail}</p>
      </div>
      <div className="flex min-h-10 items-center justify-between gap-2 sm:justify-end">
        <ToggleButton
          checked={source.enabled}
          disabled={busy}
          onChange={(enabled) => void onToggle(enabled)}
          ariaLabel={`${source.enabled ? "Disable" : "Enable"} ${source.name}`}
          label={source.enabled ? "Enabled" : "Disabled"}
        />
        {confirmDelete ? (
          <div className="flex items-center gap-1">
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button type="button" variant="destructive" size="sm" disabled={busy} onClick={() => void remove()}>
              {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
              Delete
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={busy}
            onClick={() => setConfirmDelete(true)}
            aria-label={`Delete ${source.name}`}
            title="Delete source"
            className="h-10 w-10 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" aria-hidden />
          </Button>
        )}
      </div>
    </li>
  );
}

export function ContextSourcesPanel({
  detail,
  busyKey,
  onCreate,
  onToggle,
  onDelete,
}: {
  detail: CollaborationProjectPayload;
  busyKey: string | null;
  onCreate: (draft: {
    name: string;
    kind: CollaborationEditableContextSourceKind;
    value: string;
  }) => Promise<unknown>;
  onToggle: (sourceId: string, enabled: boolean) => Promise<unknown>;
  onDelete: (sourceId: string) => Promise<unknown>;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CollaborationEditableContextSourceKind>("document");
  const [value, setValue] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const creating = busyKey === "context:create";
  const changeKind = (next: CollaborationEditableContextSourceKind) => {
    setKind(next);
    setValue("");
    setValidationError(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const cleanName = name.trim();
    const cleanValue = value.trim();
    const nextError = !cleanName
      ? "Enter a source name."
      : kind === "document"
        ? documentPathError(cleanValue)
        : !cleanValue
          ? "Enter the custom text to share with this project."
          : null;
    setValidationError(nextError);
    if (nextError || creating) return;
    try {
      await onCreate({ name: cleanName, kind, value: cleanValue });
      setName("");
      setValue("");
    } catch {
      // The shared project error region reports mutation failures.
    }
  };

  return (
    <section aria-labelledby="project-context-title" className="space-y-5">
      <div>
        <h2 id="project-context-title" className="text-lg font-semibold tracking-tight">Context sources</h2>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
          Add bounded project material that nanobot may read as data when it is useful. Document paths cannot leave the project workspace.
        </p>
      </div>

      <form onSubmit={(event) => void submit(event)} className="rounded-panel bg-settings-surface p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="context-source-name" className="mb-1.5 block text-xs font-medium">Source name</label>
            <Input
              id="context-source-name"
              value={name}
              maxLength={512}
              onChange={(event) => setName(event.target.value)}
              placeholder={kind === "document" ? "Project brief" : "Team conventions"}
              disabled={creating}
              aria-invalid={Boolean(validationError && !name.trim())}
            />
          </div>
          <fieldset>
            <legend className="mb-1.5 block text-xs font-medium">Source type</legend>
            <div className="grid grid-cols-2 rounded-control bg-muted p-1">
              {(["document", "custom"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  role="radio"
                  aria-checked={kind === option}
                  onClick={() => changeKind(option)}
                  disabled={creating}
                  className={cn(
                    "h-11 rounded-compact px-3 text-xs font-medium capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    kind === option ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {option === "custom" ? "Custom text" : "Document"}
                </button>
              ))}
            </div>
          </fieldset>
        </div>

        <div className="mt-4">
          {kind === "document" ? (
            <>
              <label htmlFor="context-document-path" className="mb-1.5 block text-xs font-medium">Relative path</label>
              <Input
                id="context-document-path"
                value={value}
                maxLength={DOCUMENT_PATH_LIMIT}
                onChange={(event) => {
                  setValue(event.target.value);
                  setValidationError(null);
                }}
                placeholder="docs/project-brief.txt"
                disabled={creating}
                aria-invalid={Boolean(validationError)}
                aria-describedby="context-document-help"
              />
              <p id="context-document-help" className="mt-1.5 text-xs leading-5 text-muted-foreground">
                Plain-text content is read up to {CUSTOM_CONTENT_LIMIT.toLocaleString()} characters and must remain inside this project’s workspace.
              </p>
            </>
          ) : (
            <>
              <label htmlFor="context-custom-text" className="mb-1.5 block text-xs font-medium">Custom text</label>
              <Textarea
                id="context-custom-text"
                value={value}
                maxLength={CUSTOM_CONTENT_LIMIT}
                onChange={(event) => {
                  setValue(event.target.value);
                  setValidationError(null);
                }}
                placeholder="Add stable reference information, not instructions or secrets."
                disabled={creating}
                aria-invalid={Boolean(validationError)}
                className="min-h-32 resize-y bg-background"
              />
              <p className="mt-1.5 text-right text-xs tabular-nums text-muted-foreground">
                {value.length.toLocaleString()} / {CUSTOM_CONTENT_LIMIT.toLocaleString()}
              </p>
            </>
          )}
        </div>

        {validationError ? <p role="alert" className="mt-3 text-sm text-destructive">{validationError}</p> : null}
        <div className="mt-4 flex justify-end">
          <Button type="submit" disabled={creating || !name.trim() || !value.trim()} className="w-full sm:w-auto">
            {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : <Plus className="mr-2 h-4 w-4" aria-hidden />}
            Add source
          </Button>
        </div>
      </form>

      {detail.context_sources.length === 0 ? (
        <div className="rounded-panel bg-settings-surface px-5 py-8 text-center">
          <p className="text-sm font-medium">No context sources yet</p>
          <p className="mx-auto mt-1 max-w-md text-sm leading-6 text-muted-foreground">
            Add a project document or a short piece of stable custom reference text above.
          </p>
        </div>
      ) : (
        <ul className="overflow-hidden rounded-panel bg-settings-surface">
          {detail.context_sources.map((source) => (
            <SourceRow
              key={source.id}
              source={source}
              busy={busyKey === `context:update:${source.id}` || busyKey === `context:delete:${source.id}`}
              onToggle={(enabled) => onToggle(source.id, enabled)}
              onDelete={() => onDelete(source.id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
