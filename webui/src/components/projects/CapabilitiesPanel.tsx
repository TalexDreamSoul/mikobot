import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Brain, Check, Loader2, Server } from "lucide-react";

import { Button } from "@/components/ui/button";
import type {
  CollaborationCapabilities,
  CollaborationProjectPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

function sortedUnique(values: readonly string[]): string[] {
  return [...new Set(values)].sort();
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

function CapabilityGroup({
  title,
  description,
  icon,
  unrestricted,
  onToggleUnrestricted,
  disabled,
  emptyMessage,
  children,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  unrestricted: boolean;
  onToggleUnrestricted: (unrestricted: boolean) => void;
  disabled: boolean;
  emptyMessage: string;
  children: React.ReactNode[];
}) {
  const { t } = useTranslation();
  return (
    <section className="overflow-hidden rounded-panel bg-settings-surface">
      <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
        <span className="mt-0.5 text-muted-foreground" aria-hidden>{icon}</span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">{description}</p>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={unrestricted}
            disabled={disabled}
            onChange={(event) => onToggleUnrestricted(event.target.checked)}
          />
          {t("projects.capabilities.unrestricted")}
        </label>
      </header>
      {unrestricted ? null : (
        <div>
          {children.length ? children : (
            <p className="border-t border-border/45 px-4 py-4 text-sm text-muted-foreground">{emptyMessage}</p>
          )}
        </div>
      )}
    </section>
  );
}

export function CapabilitiesPanel({
  detail,
  busy,
  canManage,
  onSave,
}: {
  detail: CollaborationProjectPayload;
  busy: boolean;
  canManage: boolean;
  onSave: (capabilities: CollaborationCapabilities) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const savedSkills = useMemo(
    () => (detail.project.allowed_skills === null ? null : sortedUnique(detail.project.allowed_skills)),
    [detail.project.allowed_skills],
  );
  const savedMcp = useMemo(
    () => (detail.project.allowed_mcp_servers === null ? null : sortedUnique(detail.project.allowed_mcp_servers)),
    [detail.project.allowed_mcp_servers],
  );
  const [skillIds, setSkillIds] = useState<string[] | null>(savedSkills);
  const [mcpIds, setMcpIds] = useState<string[] | null>(savedMcp);

  useEffect(() => { setSkillIds(savedSkills); }, [detail.project.id, savedSkills]);
  useEffect(() => { setMcpIds(savedMcp); }, [detail.project.id, savedMcp]);

  const same = (left: string[] | null, right: string[] | null) => (
    left === null || right === null ? left === right : left.join("\\0") === right.join("\\0")
  );
  const dirty = !same(skillIds, savedSkills) || !same(mcpIds, savedMcp);
  const disabled = busy || !canManage;

  const toggle = (
    current: string[] | null,
    id: string,
    selected: boolean,
    update: (next: string[] | null) => void,
  ) => {
    const base = current ?? [];
    update(selected ? sortedUnique([...base, id]) : base.filter((value) => value !== id));
  };

  const save = async () => {
    if (!dirty || disabled) return;
    try {
      await onSave({ allowed_skills: skillIds, allowed_mcp_servers: mcpIds });
    } catch {
      // The shared project error region reports conflicts and network failures.
    }
  };

  return (
    <section aria-labelledby="project-capabilities-title" className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="project-capabilities-title" className="text-lg font-semibold tracking-tight">
            {t("projects.capabilities.title")}
          </h2>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
            {t("projects.capabilities.description")}
          </p>
        </div>
        {canManage ? (
          <Button type="button" disabled={!dirty || busy} onClick={() => void save()} className="w-full sm:w-auto">
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
            {busy ? t("projects.capabilities.saving") : t("projects.capabilities.save")}
          </Button>
        ) : null}
      </div>

      <div className="space-y-4">
        <CapabilityGroup
          title={t("projects.capabilities.skills")}
          description={t("projects.capabilities.skillsDescription")}
          icon={<Brain className="h-4 w-4" />}
          unrestricted={skillIds === null}
          onToggleUnrestricted={(unrestricted) => setSkillIds(unrestricted ? null : [])}
          disabled={disabled}
          emptyMessage={t("projects.capabilities.noSkills")}
        >
          {detail.available.skills.map((skill) => (
            <SelectionRow
              key={skill.id}
              id={`skill-${skill.id}`}
              name={skill.name}
              description={skill.description}
              selected={skillIds?.includes(skill.id) ?? false}
              disabled={disabled}
              onChange={(selected) => toggle(skillIds, skill.id, selected, setSkillIds)}
            />
          ))}
        </CapabilityGroup>

        <CapabilityGroup
          title={t("projects.capabilities.mcpServers")}
          description={t("projects.capabilities.mcpDescription")}
          icon={<Server className="h-4 w-4" />}
          unrestricted={mcpIds === null}
          onToggleUnrestricted={(unrestricted) => setMcpIds(unrestricted ? null : [])}
          disabled={disabled}
          emptyMessage={t("projects.capabilities.noMcp")}
        >
          {detail.available.mcp_servers.map((server) => (
            <SelectionRow
              key={server.id}
              id={`mcp-${server.id}`}
              name={server.name}
              selected={mcpIds?.includes(server.id) ?? false}
              disabled={disabled}
              onChange={(selected) => toggle(mcpIds, server.id, selected, setMcpIds)}
            />
          ))}
        </CapabilityGroup>
      </div>
    </section>
  );
}
