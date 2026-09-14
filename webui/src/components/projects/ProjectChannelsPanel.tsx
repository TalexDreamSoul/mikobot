import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Cable,
  KeyRound,
  Loader2,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
  Trash2,
  X,
} from "lucide-react";

import { ChannelStatusBadge } from "@/components/settings/channels/ChannelIdentity";
import { ToggleButton } from "@/components/settings/ToggleButton";
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
import { Select } from "@/components/ui/select";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import {
  ApiError,
  fetchCollaborationClaimableChannels,
  fetchCollaborationPairing,
} from "@/lib/api";
import type {
  CollaborationChannelAssignment,
  CollaborationClaimableChannel,
  CollaborationPairingChallenge,
  CollaborationPairingPayload,
  CollaborationProjectPayload,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

function instanceLabel(assignment: CollaborationChannelAssignment): string {
  const channel = assignment.channel_display_name?.trim() || assignment.channel_type;
  const name = assignment.display_name?.trim();
  // Name and instance id together: the channel settings page shows the name, the
  // project surfaces act on the id, and an operator has to match the two.
  const identity = name && name !== assignment.instance_id
    ? `${name} · ${assignment.instance_id}`
    : assignment.instance_id;
  return `${channel} · ${identity}`;
}

/**
 * The channel instances of one project.
 *
 * Assignment is owned by the project it serves, so this panel reads the project's
 * own assignments and offers the Pair Code flow for that project only. Moving an
 * instance to another project stays available to whoever manages both.
 */
export function ProjectChannelsPanel({
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
  const [channelActivation, setChannelActivation] = useState<
    CollaborationPairingPayload["channel_activation"] | null
  >(null);
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [pairingClock, setPairingClock] = useState(() => Date.now());
  const [assignmentToRemove, setAssignmentToRemove] =
    useState<CollaborationChannelAssignment | null>(null);

  const projectId = detail.project.id;
  const summary = projects.summary;
  const allProjects = useMemo(() => summary?.projects ?? [], [summary]);
  const manageableProjectIds = useMemo(
    () => new Set(summary?.manageable_project_ids ?? []),
    [summary],
  );
  const manageableProjects = useMemo(
    () => allProjects.filter((project) => manageableProjectIds.has(project.id)),
    [allProjects, manageableProjectIds],
  );
  const currentUserId = summary?.user.id ?? "";
  const canManage = manageableProjectIds.has(projectId);
  const projectName = (id: string) =>
    allProjects.find((project) => project.id === id)?.name ?? id;

  const pairingExpired = Boolean(
    pairing && !pairing.consumed && pairing.expires_at_ms <= pairingClock,
  );
  const pairingSecondsRemaining = pairing
    ? Math.max(0, Math.ceil((pairing.expires_at_ms - pairingClock) / 1_000))
    : 0;

  const assigneeOptions = useMemo(() => {
    const members = [...detail.members].sort((left, right) => {
      if (left.role !== right.role) return left.role === "owner" ? -1 : 1;
      return left.user_id.localeCompare(right.user_id);
    });
    return [
      ...members
        .filter((member) => member.user_id === currentUserId)
        .map((member) => ({
          userId: member.user_id,
          role: member.role,
          current: true,
        })),
      ...members
        .filter((member) => member.user_id !== currentUserId)
        .map((member) => ({
          userId: member.user_id,
          role: member.role,
          current: false,
        })),
    ];
  }, [currentUserId, detail.members]);

  useEffect(() => {
    if (!projects.isAdmin) return;
    setAssigneeUserId((current) => (
      current && assigneeOptions.some((member) => member.userId === current)
        ? current
        : currentUserId || assigneeOptions[0]?.userId || ""
    ));
  }, [assigneeOptions, currentUserId, projects.isAdmin]);

  useEffect(() => {
    setPairingClock(Date.now());
    if (!pairing || pairing.consumed) return;
    const timer = window.setInterval(() => setPairingClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [pairing?.consumed, pairing?.id]);

  useEffect(() => {
    if (!canManage) {
      // A member who cannot assign instances is not asked to list them.
      setClaimable([]);
      setChannelsLoading(false);
      setChannelsError(null);
      setChannelsRestricted(false);
      return;
    }
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
        // empty list. The panel must not present them as the same state.
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
  }, [canManage, detail.assignments.length, discoveryRevision, getToken]);

  useEffect(() => {
    if (
      !pairing
      || pairing.verified
      || pairing.consumed
      || pairing.expires_at_ms <= Date.now()
    ) return;
    let cancelled = false;
    let timer: number | null = null;
    // Only the response that issued the challenge carries its code. Every later
    // read reports status alone, so the code is carried forward here instead of
    // being overwritten with nothing while the person is still reading it.
    const withCode = (next: CollaborationPairingChallenge): CollaborationPairingChallenge => ({
      ...next,
      code: next.code ?? pairing.code,
    });
    const poll = async () => {
      try {
        const payload = await fetchCollaborationPairing(getToken(), pairing.id);
        if (cancelled) return;
        setPairingError(null);
        if (payload.pairing.verified && !payload.pairing.consumed) {
          const completed = await projects.finishPairing(payload.pairing.id);
          if (!cancelled) {
            setChannelActivation(completed.channel_activation ?? null);
            setPairing(withCode(completed.pairing));
            setDiscoveryRevision((value) => value + 1);
          }
          return;
        }
        setPairing(withCode(payload.pairing));
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

  const channelOptions = useMemo(() => claimable.map((channel) => {
    // The same instance appears in the channel's settings page as a name and an
    // instance id; show both here so an operator can match one to the other.
    const name = channel.display_name.trim();
    const identity = name && name !== channel.instance_id ? `${name} · ${channel.instance_id}` : channel.instance_id;
    return {
      channelType: channel.channel_type,
      instanceId: channel.instance_id,
      label: `${channel.channel_display_name} · ${identity}`,
      status: channel.status,
    };
  }), [claimable]);

  const selected = channelOptions.find(
    (option) => `${option.channelType}:${option.instanceId}` === selectedChannel,
  ) ?? null;

  const beginAssignment = async () => {
    if (!selected) return;
    setPairingError(null);
    setChannelActivation(null);
    setPairingClock(Date.now());
    const assignee = assigneeUserId.trim();
    try {
      const payload = await projects.beginPairing({
        channelType: selected.channelType,
        instanceId: selected.instanceId,
        projectId,
        assigneeUserId: projects.isAdmin ? assignee || currentUserId : null,
      });
      setPairing(payload.pairing);
      setChannelActivation(null);
    } catch {
      // The shared error region reports why the Pair Code was refused.
    }
  };

  const removeAssignment = async () => {
    if (!assignmentToRemove) return;
    try {
      await projects.removeAssignment(
        assignmentToRemove.channel_type,
        assignmentToRemove.instance_id,
      );
      setAssignmentToRemove(null);
    } catch {
      // The shared error region reports the failure.
    }
  };

  const retryPairingStatus = () => {
    if (!pairing) return;
    setPairingError(null);
    setPairing({ ...pairing });
  };

  return (
    <>
      <section aria-labelledby="assigned-channels-title" className="overflow-hidden rounded-panel bg-settings-surface">
        <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
          <Cable className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
          <div className="min-w-0">
            <h2 id="assigned-channels-title" className="text-sm font-semibold">
              {t("channels.assigned")}
            </h2>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {t("channels.assignedDescription")}
            </p>
          </div>
        </header>
        {detail.assignments.length ? (
          <ul className="border-t border-border/45">
            {detail.assignments.map((assignment) => {
              const key = `${assignment.channel_type}:${assignment.instance_id}`;
              const busy = projects.busyKey === `assignment:update:${key}`
                || projects.busyKey === `assignment:delete:${key}`;
              return (
                <li key={key} className="border-t border-border/45 px-4 py-3.5 first:border-t-0 sm:px-5">
                  <div className="flex min-h-11 items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-sm font-medium">
                          {instanceLabel(assignment)}
                        </span>
                        {assignment.status ? (
                          <ChannelStatusBadge status={assignment.status}>
                            {t(`channels.status.${assignment.status}`)}
                          </ChannelStatusBadge>
                        ) : null}
                      </p>
                      <p className="mt-0.5 break-all text-xs text-muted-foreground">
                        {t("channels.assignee", { user: assignment.assignee_user_id })}
                        {assignment.assignee_user_id === currentUserId
                          ? ` (${t("projects.members.you")})`
                          : ""}
                      </p>
                    </div>
                    {busy ? (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />
                    ) : null}
                    {canManage ? (
                      <>
                        <ToggleButton
                          checked={assignment.enabled}
                          disabled={Boolean(projects.busyKey)}
                          ariaLabel={assignment.enabled
                            ? t("channels.disableAria", { instance: assignment.instance_id })
                            : t("channels.enableAria", { instance: assignment.instance_id })}
                          label={assignment.enabled
                            ? t("channels.enabled")
                            : t("channels.disabled")}
                          onChange={(enabled) => void projects.setAssignmentEnabled(
                            assignment.channel_type, assignment.instance_id, enabled,
                          )}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          disabled={Boolean(projects.busyKey)}
                          onClick={() => setAssignmentToRemove(assignment)}
                          aria-label={t("channels.removeAria", { instance: assignment.instance_id })}
                          className="h-11 w-11 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" aria-hidden />
                        </Button>
                      </>
                    ) : null}
                  </div>
                  {canManage && manageableProjects.length > 1 ? (
                    <div className="mt-2.5 flex flex-wrap items-center gap-2">
                      <label
                        htmlFor={`channel-project-${key}`}
                        className="text-xs font-medium text-muted-foreground"
                      >
                        {t("channels.project")}
                      </label>
                      <Select
                        id={`channel-project-${key}`}
                        value={assignment.project_id}
                        disabled={Boolean(projects.busyKey)}
                        onChange={(event) => void projects.moveAssignment(
                          assignment.channel_type,
                          assignment.instance_id,
                          event.target.value,
                        )}
                        containerClassName="w-auto min-w-[12rem] max-w-full"
                        className="h-9 text-[13px]"
                      >
                        {manageableProjects.map((project) => (
                          <option key={project.id} value={project.id}>{project.name}</option>
                        ))}
                      </Select>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="border-t border-border/45 px-4 py-5 text-sm text-muted-foreground sm:px-5">
            {t("channels.empty")}
          </p>
        )}
      </section>

      {canManage ? (
        <section aria-labelledby="assign-channel-title" className="rounded-panel bg-settings-surface p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <KeyRound className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
            <div>
              <h2 id="assign-channel-title" className="text-sm font-semibold">{t("channels.assign")}</h2>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {projects.isAdmin
                  ? t("channels.assignDescriptionAdmin")
                  : t("channels.assignDescription")}
              </p>
            </div>
          </div>
          <div className="mt-4 space-y-3">
            {channelsLoading ? (
              <p className="text-xs text-muted-foreground">{t("channels.loadingChannels")}</p>
            ) : null}
            {channelsError ? (
              <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-destructive">
                <span>{t("channels.loadChannelsFailed", { error: channelsError })}</span>
                <Button type="button" size="sm" variant="outline" onClick={() => setDiscoveryRevision((value) => value + 1)}>
                  {t("common.retry")}
                </Button>
              </div>
            ) : null}
            {channelsRestricted ? (
              <p className="text-xs text-muted-foreground">{t("channels.channelsRestricted")}</p>
            ) : null}
            {!channelsLoading && !channelsError && !channelsRestricted && !channelOptions.length ? (
              <p className="text-xs text-muted-foreground">{t("channels.noClaimableChannels")}</p>
            ) : null}
            <div>
              <label className="block text-xs font-medium" htmlFor="channel-instance">
                {t("channels.channelInstance")}
              </label>
              <Select
                id="channel-instance"
                value={selectedChannel}
                onChange={(event) => setSelectedChannel(event.target.value)}
                disabled={channelsLoading || Boolean(channelsError) || channelsRestricted}
                containerClassName="mt-1.5"
                className="h-11"
              >
                <option value="">{t("channels.chooseChannel")}</option>
                {channelOptions.map((option) => (
                  <option
                    key={`${option.channelType}:${option.instanceId}`}
                    value={`${option.channelType}:${option.instanceId}`}
                  >
                    {option.label} · {option.status}
                  </option>
                ))}
              </Select>
            </div>
            {projects.isAdmin ? (
              <div>
                <label className="block text-xs font-medium" htmlFor="channel-assignee">
                  {t("channels.assigneeUserId")}
                </label>
                <Select
                  id="channel-assignee"
                  value={assigneeUserId}
                  onChange={(event) => setAssigneeUserId(event.target.value)}
                  disabled={!assigneeOptions.length}
                  containerClassName="mt-1.5"
                  className="h-11"
                >
                  {assigneeOptions.map((member) => (
                    <option key={member.userId} value={member.userId}>
                      {member.current
                        ? t("channels.assigneeCurrentUser", { user: member.userId })
                        : t("channels.assigneeProjectMember", {
                          user: member.userId,
                          role: member.role
                            ? t(`projects.members.roles.${member.role}`)
                            : "",
                        })}
                    </option>
                  ))}
                </Select>
                <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                  {t("channels.assigneeHelp")}
                </p>
              </div>
            ) : null}
            <Button
              type="button"
              onClick={() => void beginAssignment()}
              disabled={!selected || projects.busyKey === "pairing:create"}
            >
              <KeyRound className="mr-2 h-4 w-4" aria-hidden />
              {t("channels.generatePairCode")}
            </Button>
          </div>
        </section>
      ) : null}

      {pairing ? (
        <section className="rounded-panel border border-primary/25 bg-primary/5 p-4 sm:p-5" aria-live="polite">
          <div className="flex items-start gap-3">
            {pairing.consumed && channelActivation?.ok === false ? (
              <TriangleAlert className="mt-0.5 h-5 w-5 text-destructive" aria-hidden />
            ) : pairing.consumed ? (
              <ShieldCheck className="mt-0.5 h-5 w-5 text-primary" aria-hidden />
            ) : pairingExpired ? (
              <KeyRound className="mt-0.5 h-5 w-5 text-muted-foreground" aria-hidden />
            ) : (
              <Loader2 className="mt-0.5 h-5 w-5 animate-spin text-primary" aria-hidden />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <h2 className="font-semibold">
                  {pairing.consumed
                    ? channelActivation?.ok === false
                      ? t("channels.pairing.activationFailed")
                      : t("channels.pairing.completed")
                    : pairingExpired
                      ? t("channels.pairing.expired")
                      : t("channels.pairing.title")}
                </h2>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 shrink-0"
                  aria-label={t("channels.pairing.dismiss")}
                  onClick={() => {
                    setPairing(null);
                    setPairingError(null);
                    setChannelActivation(null);
                  }}
                >
                  <X className="h-4 w-4" aria-hidden />
                </Button>
              </div>
              {!pairing.consumed && !pairingExpired ? (
                <>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {t("channels.pairing.instructions", {
                      channel: `${pairing.channel_type} · ${pairing.instance_id}`,
                    })}
                  </p>
                  <code className="mt-3 inline-flex rounded-control bg-background px-4 py-2 text-lg font-semibold tracking-[0.2em]">
                    {pairing.code}
                  </code>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {t("channels.pairing.purpose", { project: projectName(pairing.project_id) })}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("channels.pairing.validFor", { seconds: pairingSecondsRemaining })}
                  </p>
                  {pairingError ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-destructive">
                      <span>{t("channels.pairing.statusFailed", { error: pairingError })}</span>
                      <Button type="button" size="sm" variant="outline" onClick={retryPairingStatus}>
                        <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                        {t("channels.pairing.retry")}
                      </Button>
                    </div>
                  ) : null}
                </>
              ) : pairingExpired ? (
                <div className="mt-2">
                  <p className="text-sm text-muted-foreground">{t("channels.pairing.expiredDescription")}</p>
                  <Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => void beginAssignment()}>
                    <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                    {t("channels.pairing.regenerate")}
                  </Button>
                </div>
              ) : channelActivation?.ok === false ? (
                <div role="alert" className="mt-2 rounded-control border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                  <p className="text-xs leading-5">
                    {channelActivation.message
                      || t("channels.pairing.activationFailedDescription")}
                  </p>
                </div>
              ) : (
                <p role="status" className="mt-2 text-sm text-muted-foreground">
                  {channelActivation?.message
                    || t("channels.pairing.assignmentSaved")}
                </p>
              )}
            </div>
          </div>
        </section>
      ) : null}

      <AlertDialog
        open={Boolean(assignmentToRemove)}
        onOpenChange={(next) => !next && setAssignmentToRemove(null)}
      >
        <AlertDialogContent className="w-[min(calc(100vw-2rem),24rem)]">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("channels.removeConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription className="break-words">
              {t("channels.removeConfirmDescription", {
                instance: assignmentToRemove ? instanceLabel(assignmentToRemove) : "",
                project: assignmentToRemove ? projectName(assignmentToRemove.project_id) : "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="h-11">{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void removeAssignment()}
              className="h-11 bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {t("channels.removeAssignment")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
