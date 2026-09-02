import { Children, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Brain, Check, Loader2, Server } from "lucide-react";

import { Button } from "@/components/ui/button";
import type {
  CollaborationAvailableMcpServer,
  CollaborationAvailableSkill,
  CollaborationExtensionSettings,
  CollaborationProjectPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

function sortedUnique(values: unknown): string[] {
  return Array.isArray(values)
    ? [...new Set(values.filter((value): value is string => typeof value === "string"))].sort()
    : [];
}

function effectiveSelection(values: unknown, availableIds: Set<string>): string[] {
  const selectedIds = Array.isArray(values) ? sortedUnique(values) : [...availableIds].sort();
  return selectedIds.filter((id) => availableIds.has(id));
}

function SelectionRow({
  id,
  name,
  description,
  selected,
  disabled,
  onChange,
}: {
  id: string;
  name: string;
  description?: string;
  selected: boolean;
  disabled: boolean;
  onChange: (selected: boolean) => void;
}) {
  return (
    <label className={cn(
      "flex min-h-12 cursor-pointer items-start gap-3 border-t border-border/45 px-4 py-3 first:border-t-0",
      "transition-colors hover:bg-muted/35",
      disabled && "cursor-default opacity-60",
    )}>
      <input
        type="checkbox"
        name={id}
        checked={selected}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="peer sr-only"
      />
      <span className={cn(
        "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-mark border border-input bg-background",
        "peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2",
        selected && "border-primary bg-primary text-primary-foreground",
      )} aria-hidden>
        {selected ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : null}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium leading-5">{name}</span>
        {description ? (
          <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">{description}</span>
        ) : null}
      </span>
    </label>
  );
}

function ExtensionGroup({
  title,
  description,
  icon,
  emptyMessage,
  children,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  emptyMessage: string;
  children: ReactNode;
}) {
  const hasChildren = Children.count(children) > 0;
  return (
    <section className="overflow-hidden rounded-panel bg-settings-surface">
      <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
        <span className="mt-0.5 text-muted-foreground" aria-hidden>{icon}</span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">{description}</p>
        </div>
      </header>
      <div>
        {hasChildren ? children : <p className="border-t border-border/45 px-4 py-4 text-sm text-muted-foreground">{emptyMessage}</p>}
      </div>
    </section>
  );
}

export function ExtensionsPanel({
  detail,
  busy,
  onSave,
}: {
  detail: CollaborationProjectPayload;
  busy: boolean;
  onSave: (settings: CollaborationExtensionSettings) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const profile = detail.extension_profile;
  const availableSkillIds = useMemo(
    () => new Set(detail.available.skills.map((skill) => skill.id)),
    [detail.available.skills],
  );
  const availableMcpIds = useMemo(
    () => new Set(detail.available.mcp_servers.map((server) => server.id)),
    [detail.available.mcp_servers],
  );
  const savedSkills = useMemo(
    () => effectiveSelection(profile.settings.skills, availableSkillIds),
    [availableSkillIds, profile.settings.skills],
  );
  const savedMcp = useMemo(
    () => effectiveSelection(profile.settings.mcpServers, availableMcpIds),
    [availableMcpIds, profile.settings.mcpServers],
  );
  const [skillIds, setSkillIds] = useState<string[]>(() => savedSkills);
  const [mcpIds, setMcpIds] = useState<string[]>(() => savedMcp);

  useEffect(() => {
    setSkillIds(savedSkills);
  }, [detail.project.id, profile.revision, savedSkills]);
  useEffect(() => {
    setMcpIds(savedMcp);
  }, [detail.project.id, profile.revision, savedMcp]);
  const skillsDirty = skillIds.join("\0") !== savedSkills.join("\0");
  const mcpDirty = mcpIds.join("\0") !== savedMcp.join("\0");
  const dirty = skillsDirty || mcpDirty;

  const toggle = (
    current: string[],
    id: string,
    selected: boolean,
    update: (next: string[]) => void,
  ) => {
    update(selected ? sortedUnique([...current, id]) : current.filter((value) => value !== id));
  };

  const save = async () => {
    if (!dirty || busy) return;
    try {
      const settings: CollaborationExtensionSettings = { ...profile.settings };
      if (skillsDirty) settings.skills = skillIds;
      if (mcpDirty) settings.mcpServers = mcpIds;
      await onSave(settings);
    } catch {
      // The shared project error region reports conflicts and network failures.
    }
  };

  return (
    <section aria-labelledby="project-extensions-title" className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="project-extensions-title" className="text-lg font-semibold tracking-tight">{t("projects.extensions.title")}</h2>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
            {t("projects.extensions.description")}
          </p>
        </div>
        <Button type="button" disabled={!dirty || busy} onClick={() => void save()} className="w-full sm:w-auto">
          {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
          {busy ? t("projects.extensions.saving") : t("projects.extensions.save")}
        </Button>
      </div>

      <div className="space-y-4">
        <ExtensionGroup
          title={t("projects.extensions.skills")}
          description={t("projects.extensions.skillsDescription")}
          icon={<Brain className="h-4 w-4" />}
          emptyMessage={t("projects.extensions.noSkills")}
        >
          {detail.available.skills.map((skill: CollaborationAvailableSkill) => (
            <SelectionRow
              key={skill.id}
              id={`skill-${skill.id}`}
              name={skill.name}
              description={skill.description}
              selected={skillIds.includes(skill.id)}
              disabled={busy}
              onChange={(selected) => toggle(skillIds, skill.id, selected, setSkillIds)}
            />
          ))}
        </ExtensionGroup>

        <ExtensionGroup
          title={t("projects.extensions.mcpServers")}
          description={t("projects.extensions.mcpDescription")}
          icon={<Server className="h-4 w-4" />}
          emptyMessage={t("projects.extensions.noMcp")}
        >
          {detail.available.mcp_servers.map((server: CollaborationAvailableMcpServer) => (
            <SelectionRow
              key={server.id}
              id={`mcp-${server.id}`}
              name={server.name}
              selected={mcpIds.includes(server.id)}
              disabled={busy}
              onChange={(selected) => toggle(mcpIds, server.id, selected, setMcpIds)}
            />
          ))}
        </ExtensionGroup>
      </div>
    </section>
  );
}
