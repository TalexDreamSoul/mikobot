import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Bot,
  Cable,
  FolderKanban,
  KeyRound,
  Loader2,
  Plus,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import {
  fetchCollaborationPairing,
  fetchNanobotFeatures,
  fetchSkillDetail,
  fetchSkills,
} from "@/lib/api";
import type {
  CollaborationPairingChallenge,
  NanobotFeatureInfo,
  SkillDetail,
  SkillSummary,
} from "@/lib/types";
import { nanobotFeaturesRestricted } from "@/components/settings/contracts";
import { useClient } from "@/providers/ClientProvider";

interface ChannelOption {
  channelType: string;
  instanceId: string;
  label: string;
  status: string;
}

export function BotManagementPanel({
  projects,
}: {
  projects: CollaborationProjectsController;
}) {
  const { t } = useTranslation();
  const { getToken } = useClient();
  const [name, setName] = useState("");
  const [featuresLoading, setFeaturesLoading] = useState(true);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const [featuresError, setFeaturesError] = useState<string | null>(null);
  const [featuresRestricted, setFeaturesRestricted] = useState(false);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [discoveryRevision, setDiscoveryRevision] = useState(0);
  const [features, setFeatures] = useState<NanobotFeatureInfo[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null);
  const [skillDetailLoading, setSkillDetailLoading] = useState(false);
  const [skillDetailError, setSkillDetailError] = useState<string | null>(null);
  const [skillSelection, setSkillSelection] = useState<Set<string> | null>(null);
  const [skillSaveError, setSkillSaveError] = useState<string | null>(null);
  const [selectedChannel, setSelectedChannel] = useState("");
  const [projectChannel, setProjectChannel] = useState("");
  const [pairing, setPairing] = useState<CollaborationPairingChallenge | null>(null);
  const [pairingAction, setPairingAction] = useState<"claim" | "project" | null>(null);
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [pairingClock, setPairingClock] = useState(() => Date.now());

  const pairingExpired = Boolean(
    pairing && !pairing.consumed && pairing.expires_at_ms <= pairingClock,
  );
  const pairingSecondsRemaining = pairing
    ? Math.max(0, Math.ceil((pairing.expires_at_ms - pairingClock) / 1_000))
    : 0;

  useEffect(() => {
    setPairingClock(Date.now());
    if (!pairing || pairing.consumed) return;
    const timer = window.setInterval(() => setPairingClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [pairing?.consumed, pairing?.id]);

  useEffect(() => {
    let cancelled = false;
    setFeaturesLoading(true);
    setSkillsLoading(true);
    setFeaturesError(null);
    setSkillsError(null);
    void fetchNanobotFeatures(getToken())
      .then((payload) => {
        if (!cancelled) {
          setFeatures(payload.features.filter((feature) => feature.type === "channel"));
          setFeaturesRestricted(nanobotFeaturesRestricted(payload));
        }
      })
      .catch((reason) => {
        if (!cancelled) setFeaturesError((reason as Error).message);
      })
      .finally(() => {
        if (!cancelled) setFeaturesLoading(false);
      });
    void fetchSkills(getToken())
      .then((payload) => {
        if (!cancelled) setSkills(payload.skills);
      })
      .catch((reason) => {
        if (!cancelled) setSkillsError((reason as Error).message);
      })
      .finally(() => {
        if (!cancelled) setSkillsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [discoveryRevision, getToken]);

  useEffect(() => {
    if (
      !pairing
      || pairing.verified
      || pairing.consumed
      || pairing.expires_at_ms <= Date.now()
    ) return;
    let cancelled = false;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const payload = await fetchCollaborationPairing(getToken(), pairing.id);
        if (cancelled) return;
        setPairingError(null);
        if (payload.pairing.verified && !payload.pairing.consumed) {
          const completed = await projects.finishPairing(payload.pairing.id);
          if (!cancelled) setPairing(completed.pairing);
          return;
        }
        setPairing(payload.pairing);
      } catch (reason) {
        if (!cancelled) setPairingError((reason as Error).message);
      }
      if (!cancelled && pairing.expires_at_ms > Date.now()) {
        timer = window.setTimeout(() => void poll(), 1_200);
      }
    };
    timer = window.setTimeout(() => void poll(), 800);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [getToken, pairing, projects.finishPairing]);

  const channelOptions = useMemo<ChannelOption[]>(() => features.flatMap((feature) => {
    const instances = feature.instances?.length
      ? feature.instances
      : [{
          id: "default",
          name: feature.display_name,
          enabled: feature.enabled,
          configured: Boolean(feature.configured),
          config_values: {},
          configured_fields: [],
          runtime_status: feature.runtime_status,
        }];
    return instances.map((instance) => ({
      channelType: feature.name,
      instanceId: instance.id,
      label: `${feature.display_name} · ${instance.display_name?.trim() || instance.name || instance.id}`,
      status: instance.runtime_status ?? (instance.enabled ? "running" : "stopped"),
    }));
  }), [features]);

  const activeBot = projects.summary?.bots.find((bot) => bot.id === projects.botId) ?? null;
  const activeProject = projects.summary?.projects.find((project) => project.id === projects.projectId) ?? null;
  const claimedChannels = projects.botDetail?.channels ?? [];
  const selected = channelOptions.find(
    (option) => `${option.channelType}:${option.instanceId}` === selectedChannel,
  ) ?? null;
  const selectedClaimedChannel = claimedChannels.find(
    (channel) => `${channel.channel_type}:${channel.instance_id}` === projectChannel,
  ) ?? null;
  const globalCapabilities = projects.botDetail?.capability_profiles.find(
    (profile) => profile.project_id === null,
  );
  const configuredSkillIds = globalCapabilities?.settings.skills;

  useEffect(() => {
    if (!projects.botDetail) {
      setSkillSelection(null);
      return;
    }
    setSkillSelection(new Set(
      configuredSkillIds
      ?? skills.filter((skill) => skill.enabled !== false).map((skill) => skill.name),
    ));
    setSkillSaveError(null);
  }, [configuredSkillIds, projects.botDetail?.bot?.id, skills]);
  useEffect(() => {
    if (selectedClaimedChannel || !claimedChannels.length) return;
    const first = claimedChannels[0];
    setProjectChannel(`${first.channel_type}:${first.instance_id}`);
  }, [claimedChannels, selectedClaimedChannel]);

  const toggleSkill = (name: string) => {
    setSkillSelection((current) => {
      if (current === null) return current;
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
    setSkillSaveError(null);
  };

  const saveSkills = async () => {
    if (skillSelection === null) return;
    setSkillSaveError(null);
    try {
      await projects.saveBotCapabilities({
        ...(globalCapabilities?.settings ?? {}),
        skills: [...skillSelection].sort(),
      });
    } catch (reason) {
      setSkillSaveError((reason as Error).message);
    }
  };

  const openSkill = async (name: string) => {
    setSkillDetailLoading(true);
    setSkillDetailError(null);
    try {
      setSkillDetail(await fetchSkillDetail(getToken(), name));
    } catch (reason) {
      setSkillDetailError((reason as Error).message);
    } finally {
      setSkillDetailLoading(false);
    }
  };

  const createBot = async (event: FormEvent) => {
    event.preventDefault();
    const value = name.trim();
    if (!value) return;
    await projects.createBot(value);
    setName("");
  };

  const beginClaim = async () => {
    setPairingError(null);
    setPairingClock(Date.now());
    if (!selected) return;
    const payload = await projects.beginPairing({
      purpose: "claim_channel",
      channelType: selected.channelType,
      instanceId: selected.instanceId,
    });
    setPairing(payload.pairing);
    setPairingAction("claim");
  };

  const beginProjectAssignment = async () => {
    if (!selectedClaimedChannel || !activeProject) return;
    setPairingError(null);
    setPairingClock(Date.now());
    const payload = await projects.beginPairing({
      purpose: "assign_bot_project",
      channelType: selectedClaimedChannel.channel_type,
      instanceId: selectedClaimedChannel.instance_id,
      projectId: activeProject.id,
    });
    setPairing(payload.pairing);
    setPairingAction("project");
  };

  const regeneratePairing = async () => {
    if (pairingAction === "project") {
      await beginProjectAssignment();
    } else {
      await beginClaim();
    }
  };

  const retryPairingStatus = () => {
    if (!pairing) return;
    setPairingError(null);
    setPairing({ ...pairing });
  };

  return (
    <section aria-labelledby="project-bots-title" className="space-y-5">
      <div>
        <h2 id="project-bots-title" className="text-lg font-semibold tracking-tight">
          {t("projects.bots.title")}
        </h2>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
          {t("projects.bots.description")}
        </p>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <section className="rounded-panel bg-settings-surface p-4 sm:p-5">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4" aria-hidden />
            <h3 className="font-semibold">{t("projects.bots.available")}</h3>
          </div>
          <div className="mt-3 grid gap-2">
            {(projects.summary?.bots ?? [])
              .filter((bot) => !projects.organizationId || bot.organization_id === projects.organizationId)
              .map((bot) => (
                <button
                  type="button"
                  key={bot.id}
                  onClick={() => projects.selectBot(bot.id)}
                  className={`rounded-control border px-3 py-2 text-left text-sm transition-colors ${
                    bot.id === projects.botId
                      ? "border-primary/40 bg-primary/10"
                      : "border-border/55 bg-background hover:bg-muted"
                  }`}
                >
                  <span className="block font-medium">{bot.name === "Personal bot" ? t("projects.bots.defaultName") : bot.name}</span>
                  <span className="text-xs text-muted-foreground">{t(`projects.bots.state.${bot.state}`)}</span>
                </button>
              ))}
          </div>
          <form onSubmit={(event) => void createBot(event)} className="mt-4 flex gap-2">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("projects.bots.newPlaceholder")}
              maxLength={256}
            />
            <Button type="submit" disabled={!name.trim() || projects.busyKey === "bot:create"}>
              <Plus className="h-4 w-4" aria-hidden />
              <span className="sr-only">{t("projects.bots.create")}</span>
            </Button>
          </form>
        </section>

        <section className="rounded-panel bg-settings-surface p-4 sm:p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold">{activeBot
                ? (activeBot.name === "Personal bot" ? t("projects.bots.defaultName") : activeBot.name)
                : t("projects.bots.select")}</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {projects.botDetailLoading
                  ? t("common.loading")
                  : t("projects.bots.assignmentSummary", {
                      projects: projects.botDetail?.projects.length ?? 0,
                      channels: claimedChannels.length,
                    })}
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!activeBot || projects.busyKey === "bot:state"}
              onClick={() => void projects.updateBotState(
                activeBot?.state === "active" ? "disabled" : "active",
              )}
            >
              {projects.busyKey === "bot:state" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <ShieldCheck className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              )}
              {activeBot?.state === "active"
                ? t("projects.bots.disableBot")
                : t("projects.bots.enableBot")}
            </Button>
          </div>

          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium">
                <Cable className="h-4 w-4" aria-hidden />
                {t("projects.bots.channels")}
              </div>
              <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                {claimedChannels.map((channel) => (
                  <li key={`${channel.channel_type}:${channel.instance_id}`}>
                    {channel.channel_type} · {channel.instance_id}
                  </li>
                ))}
                {!claimedChannels.length ? <li>{t("projects.bots.noClaimedChannels")}</li> : null}
              </ul>
            </div>
            <div>
              <div className="flex items-center gap-2 text-sm font-medium">
                <FolderKanban className="h-4 w-4" aria-hidden />
                {t("projects.bots.projects")}
              </div>
              <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                {(projects.botDetail?.projects ?? []).map(({ project }) => (
                  <li key={project.id}>{project.name}</li>
                ))}
                {!projects.botDetail?.projects.length ? <li>{t("projects.bots.noProjects")}</li> : null}
              </ul>
            </div>
          </div>

          <div className="mt-5 space-y-2 border-t border-border/45 pt-4">
            <label className="block text-xs font-medium" htmlFor="bot-channel-instance">
              {t("projects.bots.channelInstance")}
            </label>
            {featuresLoading ? (
              <p className="text-xs text-muted-foreground">{t("projects.bots.loadingChannels")}</p>
            ) : null}
            {featuresError ? (
              <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-destructive">
                <span>{t("projects.bots.loadChannelsFailed", { error: featuresError })}</span>
                <Button type="button" size="sm" variant="outline" onClick={() => setDiscoveryRevision((value) => value + 1)}>
                  {t("common.retry")}
                </Button>
              </div>
            ) : null}
            {featuresRestricted ? (
              <p className="text-xs text-muted-foreground">
                {t("projects.bots.channelsRestricted")}
              </p>
            ) : null}
            <select
              id="bot-channel-instance"
              value={selectedChannel}
              onChange={(event) => setSelectedChannel(event.target.value)}
              disabled={featuresLoading || Boolean(featuresError) || featuresRestricted}
              className="h-11 w-full rounded-control border border-input bg-background px-3 text-sm disabled:opacity-60"
            >
              <option value="">{t("projects.bots.chooseChannel")}</option>
              {channelOptions.map((option) => (
                <option
                  key={`${option.channelType}:${option.instanceId}`}
                  value={`${option.channelType}:${option.instanceId}`}
                >
                  {option.label} · {option.status}
                </option>
              ))}
            </select>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={() => void beginClaim()} disabled={!activeBot || !selected}>
                <KeyRound className="mr-2 h-4 w-4" aria-hidden />
                {t("projects.bots.claimChannel")}
              </Button>
            </div>
            <label className="mt-4 block text-xs font-medium" htmlFor="bot-project-channel">
              {t("projects.bots.projectChannel")}
            </label>
            <select
              id="bot-project-channel"
              value={projectChannel}
              onChange={(event) => setProjectChannel(event.target.value)}
              disabled={!claimedChannels.length}
              className="h-11 w-full rounded-control border border-input bg-background px-3 text-sm disabled:opacity-60"
            >
              <option value="">{t("projects.bots.chooseClaimedChannel")}</option>
              {claimedChannels.map((channel) => (
                <option
                  key={`${channel.channel_type}:${channel.instance_id}`}
                  value={`${channel.channel_type}:${channel.instance_id}`}
                >
                  {channel.channel_type} · {channel.instance_id}
                </option>
              ))}
            </select>
            <Button
              type="button"
              onClick={() => void beginProjectAssignment()}
              disabled={!activeBot || !activeProject || !selectedClaimedChannel}
            >
              {t("projects.bots.assignToProject")}
            </Button>
          </div>
        </section>
      </div>

      {pairing ? (
        <section className="rounded-panel border border-primary/25 bg-primary/5 p-4 sm:p-5" aria-live="polite">
          <div className="flex items-start gap-3">
            {pairing.consumed ? (
              <ShieldCheck className="mt-0.5 h-5 w-5 text-primary" aria-hidden />
            ) : pairingExpired ? (
              <KeyRound className="mt-0.5 h-5 w-5 text-muted-foreground" aria-hidden />
            ) : (
              <Loader2 className="mt-0.5 h-5 w-5 animate-spin text-primary" aria-hidden />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <h3 className="font-semibold">
                  {pairing.consumed
                    ? t("projects.pairing.completed")
                    : pairingExpired
                      ? t("projects.pairing.expired")
                      : t("projects.pairing.title")}
                </h3>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 shrink-0"
                  aria-label={t("projects.pairing.dismiss")}
                  onClick={() => { setPairing(null); setPairingError(null); }}
                >
                  <X className="h-4 w-4" aria-hidden />
                </Button>
              </div>
              {!pairing.consumed && !pairingExpired ? (
                <>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {t("projects.pairing.instructions", {
                      channel: `${pairing.channel_type} · ${pairing.instance_id}`,
                    })}
                  </p>
                  <code className="mt-3 inline-flex rounded-control bg-background px-4 py-2 text-lg font-semibold tracking-[0.2em]">
                    {pairing.code}
                  </code>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {pairingAction === "project"
                      ? t("projects.pairing.projectPurpose")
                      : t("projects.pairing.claimPurpose")}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("projects.pairing.validFor", { seconds: pairingSecondsRemaining })}
                  </p>
                  {pairingError ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-destructive">
                      <span>{t("projects.pairing.statusFailed", { error: pairingError })}</span>
                      <Button type="button" size="sm" variant="outline" onClick={retryPairingStatus}>
                        <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                        {t("projects.pairing.retry")}
                      </Button>
                    </div>
                  ) : null}
                </>
              ) : pairingExpired ? (
                <div className="mt-2">
                  <p className="text-sm text-muted-foreground">{t("projects.pairing.expiredDescription")}</p>
                  <Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => void regeneratePairing()}>
                    <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                    {t("projects.pairing.regenerate")}
                  </Button>
                </div>
              ) : null}
            </div>
          </div>
        </section>
      ) : null}

      <section className="rounded-panel bg-settings-surface p-4 sm:p-5">
        <h3 className="font-semibold">{t("projects.bots.skills")}</h3>
        <p className="mt-1 text-xs text-muted-foreground">{t("projects.bots.skillsDescription")}</p>
        {skillsLoading ? (
          <p className="mt-3 text-sm text-muted-foreground">{t("projects.bots.loadingSkills")}</p>
        ) : null}
        {skillsError ? (
          <div role="alert" className="mt-3 flex flex-wrap items-center gap-2 text-xs text-destructive">
            <span>{t("projects.bots.loadSkillsFailed", { error: skillsError })}</span>
            <Button type="button" size="sm" variant="outline" onClick={() => setDiscoveryRevision((value) => value + 1)}>
              {t("common.retry")}
            </Button>
          </div>
        ) : null}
        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {skills.slice(0, 48).map((skill) => (
            <div key={`${skill.source}:${skill.name}`} className="overflow-hidden rounded-control border border-border/50 bg-background">
              <button
                type="button"
                onClick={() => void openSkill(skill.name)}
                className="w-full p-3 text-left transition-colors hover:bg-muted/40"
              >
                <p className="truncate text-sm font-medium">{skill.name}</p>
                <p className="mt-1 truncate text-xs text-muted-foreground">
                  {skill.logical_path ?? `${skill.source}/${skill.name}/SKILL.md`}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {skill.enabled === false ? t("projects.bots.skillDisabled") : t("projects.bots.skillEnabled")}
                </p>
              </button>
              <label className="flex cursor-pointer items-center gap-2 border-t border-border/45 px-3 py-2 text-xs">
                <input
                  type="checkbox"
                  checked={skillSelection?.has(skill.name) ?? false}
                  disabled={skillSelection === null || skill.enabled === false}
                  onChange={() => toggleSkill(skill.name)}
                />
                {skillSelection?.has(skill.name)
                  ? t("projects.bots.skillAssigned")
                  : t("projects.bots.skillUnassigned")}
              </label>
            </div>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button
            type="button"
            onClick={() => void saveSkills()}
            disabled={skillSelection === null || projects.busyKey === "bot:capabilities"}
          >
            {projects.busyKey === "bot:capabilities" ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
            ) : null}
            {t("projects.bots.saveSkills")}
          </Button>
          {skillSaveError ? (
            <span role="alert" className="text-xs text-destructive">
              {t("projects.bots.saveSkillsFailed", { error: skillSaveError })}
            </span>
          ) : null}
        </div>
        {skillDetailError ? (
          <div role="alert" className="mt-4 text-xs text-destructive">
            {t("projects.bots.skillDetailFailed", { error: skillDetailError })}
          </div>
        ) : null}
        {skillDetailLoading ? (
          <p className="mt-4 text-sm text-muted-foreground">{t("common.loading")}</p>
        ) : skillDetail ? (
          <div className="mt-4 rounded-control border border-border/50 bg-background p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h4 className="font-semibold">{skillDetail.name}</h4>
                <p className="mt-1 text-xs text-muted-foreground">{skillDetail.logical_path}</p>
              </div>
              <Button type="button" variant="ghost" size="sm" onClick={() => setSkillDetail(null)}>
                {t("common.close")}
              </Button>
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{skillDetail.description}</p>
            <h5 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("projects.bots.skillFiles")}
            </h5>
            <ul className="mt-2 grid gap-1 text-xs sm:grid-cols-2">
              {(skillDetail.files ?? []).map((file) => (
                <li key={file.path} className="flex items-center justify-between gap-3 rounded-compact bg-muted/45 px-2 py-1.5">
                  <code className="min-w-0 truncate">{file.path}</code>
                  <span className="shrink-0 text-muted-foreground">{file.size.toLocaleString()} B</span>
                </li>
              ))}
              {!skillDetail.files?.length ? <li>{t("projects.bots.noSkillFiles")}</li> : null}
            </ul>
          </div>
        ) : null}
      </section>
    </section>
  );
}
