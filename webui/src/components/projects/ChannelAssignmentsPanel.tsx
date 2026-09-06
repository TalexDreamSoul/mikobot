import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Cable, KeyRound, Loader2, Power, RotateCcw, ShieldCheck, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import {
  ApiError,
  fetchCollaborationClaimableChannels,
  fetchCollaborationPairing,
} from "@/lib/api";
import type {
  CollaborationClaimableChannel,
  CollaborationPairingChallenge,
  CollaborationProjectPayload,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

interface ChannelOption {
  channelType: string;
  instanceId: string;
  label: string;
  status: string;
}

export function ChannelAssignmentsPanel({
  detail,
  projects,
}: {
  detail: CollaborationProjectPayload;
  projects: CollaborationProjectsController;
}) {
  const { t } = useTranslation();
  const { getToken } = useClient();
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelsError, setChannelsError] = useState<string | null>(null);
  const [channelsRestricted, setChannelsRestricted] = useState(false);
  const [discoveryRevision, setDiscoveryRevision] = useState(0);
  const [claimable, setClaimable] = useState<CollaborationClaimableChannel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState("");
  const [assigneeUserId, setAssigneeUserId] = useState("");
  const [pairing, setPairing] = useState<CollaborationPairingChallenge | null>(null);
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [pairingClock, setPairingClock] = useState(() => Date.now());

  const canManage = detail.can_manage;
  const currentUserId = projects.summary?.user.id ?? "";
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
    setChannelsLoading(true);
    setChannelsError(null);
    setChannelsRestricted(false);
    void fetchCollaborationClaimableChannels(getToken())
      .then((payload) => {
        if (!cancelled) setClaimable(payload.channels);
      })
      .catch((reason) => {
        if (cancelled) return;
        // A refusal means "you may not ask", which is a different answer from an
        // empty list. The screen must not present them as the same state.
        if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) {
          setChannelsRestricted(true);
          return;
        }
        setChannelsError((reason as Error).message);
      })
      .finally(() => {
        if (!cancelled) setChannelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [discoveryRevision, getToken, detail.assignments.length]);

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
          if (!cancelled) {
            setPairing(completed.pairing);
            setDiscoveryRevision((value) => value + 1);
          }
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

  const channelOptions = useMemo<ChannelOption[]>(() => claimable.map((channel) => ({
    channelType: channel.channel_type,
    instanceId: channel.instance_id,
    label: `${channel.channel_display_name} · ${channel.display_name.trim() || channel.instance_id}`,
    status: channel.status,
  })), [claimable]);

  const selected = channelOptions.find(
    (option) => `${option.channelType}:${option.instanceId}` === selectedChannel,
  ) ?? null;

  const beginAssignment = async () => {
    if (!selected) return;
    setPairingError(null);
    setPairingClock(Date.now());
    const assignee = assigneeUserId.trim();
    const payload = await projects.beginPairing({
      channelType: selected.channelType,
      instanceId: selected.instanceId,
      assigneeUserId: projects.isAdmin && assignee ? assignee : null,
    });
    setPairing(payload.pairing);
  };

  const retryPairingStatus = () => {
    if (!pairing) return;
    setPairingError(null);
    setPairing({ ...pairing });
  };

  return (
    <section aria-labelledby="project-channels-title" className="space-y-5">
      <div>
        <h2 id="project-channels-title" className="text-lg font-semibold tracking-tight">
          {t("projects.channels.title")}
        </h2>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
          {t("projects.channels.description")}
        </p>
      </div>

      <section className="overflow-hidden rounded-panel bg-settings-surface">
        <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
          <Cable className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">{t("projects.channels.assigned")}</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {t("projects.channels.assignedDescription")}
            </p>
          </div>
        </header>
        {detail.assignments.length ? (
          <ul className="border-t border-border/45">
            {detail.assignments.map((assignment) => {
              const key = `${assignment.channel_type}:${assignment.instance_id}`;
              const busy = projects.busyKey?.endsWith(`:${assignment.channel_type}:${assignment.instance_id}`);
              return (
                <li key={key} className="flex min-h-14 items-center gap-3 border-t border-border/45 px-4 py-3 first:border-t-0 sm:px-5">
                  <div className="min-w-0 flex-1">
                    <p className="break-all text-sm font-medium">
                      {assignment.channel_type} · {assignment.instance_id}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {t("projects.channels.assignee", { user: assignment.assignee_user_id })}
                      {assignment.assignee_user_id === currentUserId
                        ? ` (${t("projects.members.you")})`
                        : ""}
                      {" · "}
                      {assignment.enabled
                        ? t("projects.channels.enabled")
                        : t("projects.channels.disabled")}
                    </p>
                  </div>
                  {canManage ? (
                    <>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        disabled={Boolean(projects.busyKey)}
                        onClick={() => void projects.setAssignmentEnabled(
                          assignment.channel_type, assignment.instance_id, !assignment.enabled,
                        )}
                        aria-label={assignment.enabled
                          ? t("projects.channels.disableAria", { instance: assignment.instance_id })
                          : t("projects.channels.enableAria", { instance: assignment.instance_id })}
                        className="h-11 w-11 shrink-0 text-muted-foreground"
                      >
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Power className="h-4 w-4" aria-hidden />}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        disabled={Boolean(projects.busyKey)}
                        onClick={() => void projects.removeAssignment(
                          assignment.channel_type, assignment.instance_id,
                        )}
                        aria-label={t("projects.channels.removeAria", { instance: assignment.instance_id })}
                        className="h-11 w-11 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
                      </Button>
                    </>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="border-t border-border/45 px-4 py-5 text-sm text-muted-foreground sm:px-5">
            {t("projects.channels.empty")}
          </p>
        )}
      </section>

      <section className="rounded-panel bg-settings-surface p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <KeyRound className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
          <div>
            <h3 className="text-sm font-semibold">{t("projects.channels.assign")}</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {projects.isAdmin
                ? t("projects.channels.assignDescriptionAdmin")
                : t("projects.channels.assignDescription")}
            </p>
          </div>
        </div>
        <div className="mt-4 space-y-3">
          <label className="block text-xs font-medium" htmlFor="project-channel-instance">
            {t("projects.channels.channelInstance")}
          </label>
          {channelsLoading ? (
            <p className="text-xs text-muted-foreground">{t("projects.channels.loadingChannels")}</p>
          ) : null}
          {channelsError ? (
            <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-destructive">
              <span>{t("projects.channels.loadChannelsFailed", { error: channelsError })}</span>
              <Button type="button" size="sm" variant="outline" onClick={() => setDiscoveryRevision((value) => value + 1)}>
                {t("common.retry")}
              </Button>
            </div>
          ) : null}
          {channelsRestricted ? (
            <p className="text-xs text-muted-foreground">{t("projects.channels.channelsRestricted")}</p>
          ) : null}
          {!channelsLoading && !channelsError && !channelsRestricted && !channelOptions.length ? (
            <p className="text-xs text-muted-foreground">{t("projects.channels.noClaimableChannels")}</p>
          ) : null}
          <select
            id="project-channel-instance"
            value={selectedChannel}
            onChange={(event) => setSelectedChannel(event.target.value)}
            disabled={channelsLoading || Boolean(channelsError) || channelsRestricted}
            className="h-11 w-full rounded-control border border-input bg-background px-3 text-sm disabled:opacity-60"
          >
            <option value="">{t("projects.channels.chooseChannel")}</option>
            {channelOptions.map((option) => (
              <option
                key={`${option.channelType}:${option.instanceId}`}
                value={`${option.channelType}:${option.instanceId}`}
              >
                {option.label} · {option.status}
              </option>
            ))}
          </select>
          {projects.isAdmin ? (
            <div>
              <label className="block text-xs font-medium" htmlFor="project-channel-assignee">
                {t("projects.channels.assigneeUserId")}
              </label>
              <Input
                id="project-channel-assignee"
                value={assigneeUserId}
                onChange={(event) => setAssigneeUserId(event.target.value)}
                placeholder={t("projects.channels.assigneePlaceholder")}
                autoComplete="off"
                maxLength={128}
                className="mt-1.5 h-11 bg-background"
              />
            </div>
          ) : null}
          <Button
            type="button"
            onClick={() => void beginAssignment()}
            disabled={!selected || projects.busyKey === "pairing:create"}
          >
            <KeyRound className="mr-2 h-4 w-4" aria-hidden />
            {t("projects.channels.generatePairCode")}
          </Button>
        </div>
      </section>

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
                    {t("projects.pairing.purpose", { project: detail.project.name })}
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
                  <Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => void beginAssignment()}>
                    <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                    {t("projects.pairing.regenerate")}
                  </Button>
                </div>
              ) : null}
            </div>
          </div>
        </section>
      ) : null}
    </section>
  );
}
