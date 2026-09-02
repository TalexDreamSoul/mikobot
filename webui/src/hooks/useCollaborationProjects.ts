import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  addCollaborationOrganizationMember,
  addCollaborationProjectMember,
  consumeCollaborationPairingChallenge,
  createCollaborationBot,
  createCollaborationContextSource,
  createCollaborationOrganization,
  createCollaborationPairingChallenge,
  createCollaborationProject,
  createCollaborationTask,
  createCollaborationTaskList,
  deleteCollaborationContextSource,
  deleteCollaborationOrganization,
  deleteCollaborationTask,
  fetchCollaboration,
  fetchCollaborationBot,
  fetchCollaborationOrganization,
  fetchCollaborationProject,
  removeCollaborationOrganizationMember,
  removeCollaborationProjectMember,
  updateCollaborationBot,
  updateCollaborationBotCapabilities,
  updateCollaborationContextSource,
  updateCollaborationDefaults,
  updateCollaborationExtensions,
  updateCollaborationOrganization,
  updateCollaborationTask,
} from "@/lib/api";
import type {
  CollaborationBotPayload,
  CollaborationEditableContextSourceKind,
  CollaborationExtensionSettings,
  CollaborationOrganizationPayload,
  CollaborationOrganizationRole,
  CollaborationPairingPurpose,
  CollaborationPayload,
  CollaborationProjectPayload,
  CollaborationProjectRole,
  CollaborationTaskStatus,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

interface ContextSourceDraft {
  name: string;
  kind: CollaborationEditableContextSourceKind;
  value: string;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error && reason.message.trim()
    ? reason.message
    : "The project could not be updated. Try again.";
}

function personalOrganizationId(payload: CollaborationPayload): string | null {
  return payload.organizations.find((organization) => (
    organization.is_personal && organization.created_by_user_id === payload.user.id
  ))?.id ?? null;
}

export function useCollaborationProjects() {
  const { client, getToken } = useClient();
  const [summary, setSummary] = useState<CollaborationPayload | null>(null);
  const [organizationId, setOrganizationId] = useState<string | null>(null);
  const [organizationDetail, setOrganizationDetail] = useState<CollaborationOrganizationPayload | null>(null);
  const [organizationLoading, setOrganizationLoading] = useState(false);
  const [organizationError, setOrganizationError] = useState<string | null>(null);
  const [botId, setBotId] = useState<string | null>(null);
  const [botDetail, setBotDetail] = useState<CollaborationBotPayload | null>(null);
  const [botDetailLoading, setBotDetailLoading] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CollaborationProjectPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const detailRequestRef = useRef(0);
  const organizationRequestRef = useRef(0);
  const botRequestRef = useRef(0);

  const loadSummary = useCallback(async (
    preferredProjectId?: string | null,
    preferredOrganizationId?: string | null,
  ) => {
    const next = await fetchCollaboration(getToken());
    const nextBots = next.bots ?? [];
    const normalized = {
      ...next,
      bots: nextBots,
      active_organization_id: next.active_organization_id ?? null,
      active_bot_id: next.active_bot_id ?? null,
    };
    const activeProject = next.projects.find((project) => project.id === next.active_project_id);
    const personalId = personalOrganizationId(next);
    const activeOrganizationId = (
      preferredOrganizationId
      ?? next.active_organization_id
      ?? activeProject?.organization_id
      ?? personalId
      ?? next.organizations[0]?.id
      ?? null
    );
    setSummary(normalized);
    setOrganizationId((current) => {
      const preferred = preferredOrganizationId ?? current ?? activeOrganizationId;
      if (preferred && next.organizations.some((organization) => organization.id === preferred)) return preferred;
      return activeOrganizationId;
    });
    setBotId((current) => {
      const preferred = current ?? normalized.active_bot_id;
      if (preferred && nextBots.some((bot) => bot.id === preferred)) return preferred;
      return nextBots.find((bot) => bot.organization_id === activeOrganizationId)?.id
        ?? nextBots[0]?.id
        ?? null;
    });
    setProjectId((current) => {
      const preferred = preferredProjectId ?? current ?? next.active_project_id;
      if (preferred && next.projects.some((project) => project.id === preferred)) return preferred;
      return next.projects[0]?.id ?? null;
    });
    return normalized;
  }, [getToken]);

  const loadOrganization = useCallback(async (nextOrganizationId: string) => {
    const request = organizationRequestRef.current + 1;
    organizationRequestRef.current = request;
    setOrganizationLoading(true);
    try {
      const next = await fetchCollaborationOrganization(getToken(), nextOrganizationId);
      if (organizationRequestRef.current === request) {
        setOrganizationDetail(next);
        setOrganizationError(null);
      }
      return next;
    } finally {
      if (organizationRequestRef.current === request) setOrganizationLoading(false);
    }
  }, [getToken]);

  const loadDetail = useCallback(async (nextProjectId: string) => {
    const request = detailRequestRef.current + 1;
    detailRequestRef.current = request;
    setDetailLoading(true);
    try {
      const next = await fetchCollaborationProject(getToken(), nextProjectId);
      if (detailRequestRef.current === request) {
        setDetail(next);
        setError(null);
      }
      return next;
    } finally {
      if (detailRequestRef.current === request) setDetailLoading(false);
    }
  }, [getToken]);

  const loadBotDetail = useCallback(async (nextBotId: string) => {
    const request = botRequestRef.current + 1;
    botRequestRef.current = request;
    setBotDetailLoading(true);
    try {
      const next = await fetchCollaborationBot(getToken(), nextBotId);
      if (botRequestRef.current === request) setBotDetail(next);
      return next;
    } finally {
      if (botRequestRef.current === request) setBotDetailLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadSummary()
      .catch((reason) => {
        if (!cancelled) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadSummary]);

  useEffect(() => {
    if (!summary || !organizationId) {
      setProjectId(null);
      return;
    }
    setBotId((current) => {
      if (current && summary.bots.some((bot) => (
        bot.id === current && bot.organization_id === organizationId
      ))) return current;
      return summary.bots.find((bot) => bot.organization_id === organizationId)?.id ?? null;
    });
    setProjectId((current) => {
      if (current && summary.projects.some((project) => (
        project.id === current && project.organization_id === organizationId
      ))) return current;
      return summary.projects.find((project) => project.organization_id === organizationId)?.id ?? null;
    });
  }, [organizationId, summary]);

  const refreshOrganization = useCallback(async () => {
    if (!organizationId) {
      setOrganizationDetail(null);
      setOrganizationError(null);
      return;
    }
    setOrganizationError(null);
    try {
      await loadOrganization(organizationId);
    } catch (reason) {
      setOrganizationError(errorMessage(reason));
    }
  }, [loadOrganization, organizationId]);

  useEffect(() => {
    setOrganizationDetail(null);
    void refreshOrganization();
  }, [refreshOrganization]);

  const refreshDetail = useCallback(async () => {
    if (!projectId) {
      setDetail(null);
      return;
    }
    setError(null);
    try {
      await loadDetail(projectId);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, [loadDetail, projectId]);

  useEffect(() => {
    setDetail(null);
    void refreshDetail();
  }, [refreshDetail]);

  useEffect(() => {
    if (!botId) {
      setBotDetail(null);
      return;
    }
    setBotDetail(null);
    void loadBotDetail(botId).catch((reason) => setError(errorMessage(reason)));
  }, [botId, loadBotDetail]);

  const run = useCallback(async (
    key: string,
    action: () => Promise<unknown>,
    refreshProjectId: string | null = projectId,
  ) => {
    setBusyKey(key);
    setError(null);
    try {
      await action();
      if (refreshProjectId) await loadDetail(refreshProjectId);
    } catch (reason) {
      const conflict = typeof reason === "object"
        && reason !== null
        && "status" in reason
        && reason.status === 409;
      if (conflict && refreshProjectId) {
        try {
          await loadDetail(refreshProjectId);
        } catch {
          // Preserve the mutation conflict as the actionable error.
        }
      }
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [loadDetail, projectId]);

  const runOrganization = useCallback(async (key: string, action: () => Promise<unknown>) => {
    if (!organizationId) throw new Error("Select an organization first.");
    setBusyKey(key);
    setError(null);
    try {
      await action();
      const next = await loadSummary(undefined, organizationId);
      if (next.organizations.some((organization) => organization.id === organizationId)) {
        await loadOrganization(organizationId);
      }
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [loadOrganization, loadSummary, organizationId]);

  const createOrganization = useCallback(async (name: string) => {
    setBusyKey("organization:create");
    setError(null);
    try {
      const { organization } = await createCollaborationOrganization(client, name);
      setProjectId(null);
      setOrganizationDetail(null);
      setOrganizationLoading(true);
      setOrganizationId(organization.id);
      await loadSummary(null, organization.id);
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [client, loadSummary]);

  const renameOrganization = useCallback((name: string) => {
    if (!organizationId) throw new Error("Select an organization first.");
    return runOrganization("organization:update", () => (
      updateCollaborationOrganization(client, organizationId, name)
    ));
  }, [client, organizationId, runOrganization]);

  const removeOrganization = useCallback(async () => {
    if (!organizationId) throw new Error("Select an organization first.");
    const removedId = organizationId;
    setBusyKey("organization:delete");
    setError(null);
    try {
      await deleteCollaborationOrganization(client, removedId);
      setOrganizationDetail(null);
      await loadSummary();
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [client, loadSummary, organizationId]);

  const addOrganizationMember = useCallback((memberUserId: string, role: CollaborationOrganizationRole) => {
    if (!organizationId) throw new Error("Select an organization first.");
    return runOrganization(`organization:member:add:${memberUserId}`, () => (
      addCollaborationOrganizationMember(client, organizationId, memberUserId, role)
    ));
  }, [client, organizationId, runOrganization]);

  const removeOrganizationMember = useCallback((memberUserId: string) => {
    if (!organizationId) throw new Error("Select an organization first.");
    return runOrganization(`organization:member:remove:${memberUserId}`, () => (
      removeCollaborationOrganizationMember(client, organizationId, memberUserId)
    ));
  }, [client, organizationId, runOrganization]);

  const createBot = useCallback(async (name: string) => {
    if (!organizationId) throw new Error("Select an organization first.");
    setBusyKey("bot:create");
    setError(null);
    try {
      const { bot } = await createCollaborationBot(client, {
        organizationId,
        name,
      });
      setBotId(bot.id);
      await loadSummary(projectId, organizationId);
      await loadBotDetail(bot.id);
      return bot;
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [client, loadBotDetail, loadSummary, organizationId, projectId]);

  const updateBotState = useCallback(async (state: "active" | "disabled") => {
    if (!botId) throw new Error("Select a bot first.");
    await run("bot:state", () => updateCollaborationBot(client, botId, { state }));
    await loadSummary(projectId, organizationId);
    await loadBotDetail(botId);
  }, [botId, client, loadBotDetail, loadSummary, organizationId, projectId, run]);

  const beginPairing = useCallback(async (values: {
    purpose: CollaborationPairingPurpose;
    channelType: string;
    instanceId: string;
    projectId?: string | null;
  }) => {
    if (!organizationId || !botId) throw new Error("Select an organization and bot first.");
    setBusyKey("pairing:create");
    setError(null);
    try {
      return await createCollaborationPairingChallenge(client, {
        ...values,
        organizationId,
        botId,
      });
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [botId, client, organizationId]);

  const finishPairing = useCallback(async (challengeId: string) => {
    setBusyKey("pairing:consume");
    setError(null);
    try {
      const result = await consumeCollaborationPairingChallenge(client, challengeId);
      if (botId) await loadBotDetail(botId);
      if (projectId) await loadDetail(projectId);
      return result;
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [botId, client, loadBotDetail, loadDetail, projectId]);

  const saveBotCapabilities = useCallback(async (
    settings: CollaborationExtensionSettings,
    targetProjectId?: string | null,
  ) => {
    if (!botId) throw new Error("Select a bot first.");
    const prior = botDetail?.capability_profiles.find(
      (profile) => profile.project_id === (targetProjectId ?? null),
    );
    return run("bot:capabilities", () => updateCollaborationBotCapabilities(
      client,
      botId,
      settings,
      { projectId: targetProjectId, revision: prior?.revision ?? 0 },
    )).then(async (result) => {
      await loadBotDetail(botId);
      return result;
    });
  }, [botDetail, botId, client, loadBotDetail, run]);

  const createProject = useCallback(async (name: string) => {
    if (!organizationId) throw new Error("Select an organization first.");
    setBusyKey("project:create");
    setError(null);
    try {
      const { project } = await createCollaborationProject(client, name, organizationId);
      await loadSummary(project.id, project.organization_id ?? organizationId);
      setProjectId(project.id);
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [client, loadSummary, organizationId]);

  const requireProject = useCallback(() => {
    if (!projectId) throw new Error("Select a project first.");
    return projectId;
  }, [projectId]);

  const addProjectMember = useCallback((memberUserId: string, role: CollaborationProjectRole) => {
    const id = requireProject();
    return run(`project:member:add:${memberUserId}`, () => (
      addCollaborationProjectMember(client, id, memberUserId, role)
    ), id);
  }, [client, requireProject, run]);

  const removeProjectMember = useCallback((memberUserId: string) => {
    const id = requireProject();
    return run(`project:member:remove:${memberUserId}`, () => (
      removeCollaborationProjectMember(client, id, memberUserId)
    ), id);
  }, [client, requireProject, run]);

  const createTaskList = useCallback((name: string) => {
    const id = requireProject();
    return run("list:create", () => createCollaborationTaskList(client, id, name), id);
  }, [client, requireProject, run]);

  const createTask = useCallback((taskListId: string, title: string) => {
    const id = requireProject();
    return run(`task:create:${taskListId}`, () => (
      createCollaborationTask(client, id, taskListId, title)
    ), id);
  }, [client, requireProject, run]);

  const updateTaskStatus = useCallback((taskId: string, status: CollaborationTaskStatus) => {
    const id = requireProject();
    return run(`task:update:${taskId}`, () => (
      updateCollaborationTask(client, id, taskId, { status })
    ), id);
  }, [client, requireProject, run]);

  const deleteTask = useCallback((taskId: string) => {
    const id = requireProject();
    return run(`task:delete:${taskId}`, () => deleteCollaborationTask(client, id, taskId), id);
  }, [client, requireProject, run]);

  const saveExtensions = useCallback((settings: CollaborationExtensionSettings) => {
    const id = requireProject();
    if (!detail) throw new Error("Project extensions are not loaded.");
    return run("extensions:save", () => updateCollaborationExtensions(
      client,
      id,
      detail.extension_profile.revision,
      settings,
    ), id);
  }, [client, detail, requireProject, run]);

  const createContextSource = useCallback((draft: ContextSourceDraft) => {
    const id = requireProject();
    const config: Record<string, string> = draft.kind === "document"
      ? { path: draft.value }
      : { content: draft.value };
    return run("context:create", () => createCollaborationContextSource(client, id, {
      name: draft.name,
      kind: draft.kind,
      config,
      enabled: true,
    }), id);
  }, [client, requireProject, run]);

  const toggleContextSource = useCallback((sourceId: string, enabled: boolean) => {
    const id = requireProject();
    return run(`context:update:${sourceId}`, () => (
      updateCollaborationContextSource(client, id, sourceId, { enabled })
    ), id);
  }, [client, requireProject, run]);

  const deleteContextSource = useCallback((sourceId: string) => {
    const id = requireProject();
    return run(`context:delete:${sourceId}`, () => (
      deleteCollaborationContextSource(client, id, sourceId)
    ), id);
  }, [client, requireProject, run]);

  const personalId = useMemo(() => summary ? personalOrganizationId(summary) : null, [summary]);
  const selectOrganization = useCallback((nextOrganizationId: string) => {
    setOrganizationDetail(null);
    setOrganizationLoading(true);
    setOrganizationError(null);
    setDetail(null);
    setBotDetail(null);
    setOrganizationId(nextOrganizationId);
    const nextBot = summary?.bots.find((bot) => bot.organization_id === nextOrganizationId);
    const nextProject = summary?.projects.find(
      (project) => project.organization_id === nextOrganizationId,
    );
    setBotId(nextBot?.id ?? null);
    setProjectId(nextProject?.id ?? null);
    if (nextBot) {
      void updateCollaborationDefaults(client, {
        organizationId: nextOrganizationId,
        botId: nextBot.id,
        projectId: nextProject?.id,
      }).then(() => loadSummary(nextProject?.id, nextOrganizationId))
        .catch((reason) => setError(errorMessage(reason)));
    }
  }, [client, loadSummary, summary]);
  const selectBot = useCallback((nextBotId: string) => {
    setBotId(nextBotId);
    setBotDetail(null);
    const bot = summary?.bots.find((item) => item.id === nextBotId);
    if (!bot) return;
    const currentProject = summary?.projects.find((item) => item.id === projectId);
    const nextProjectId = currentProject?.organization_id === bot.organization_id
      ? projectId
      : summary?.projects.find((item) => item.organization_id === bot.organization_id)?.id ?? null;
    setOrganizationId(bot.organization_id);
    setProjectId(nextProjectId);
    void updateCollaborationDefaults(client, {
      organizationId: bot.organization_id,
      botId: bot.id,
      projectId: nextProjectId,
    }).then(() => loadSummary(nextProjectId, bot.organization_id))
      .catch((reason) => setError(errorMessage(reason)));
  }, [client, loadSummary, projectId, summary]);
  const selectProject = useCallback((nextProjectId: string) => {
    setDetail(null);
    setProjectId(nextProjectId);
    if (organizationId && botId) {
      void updateCollaborationDefaults(client, {
        organizationId,
        botId,
        projectId: nextProjectId,
      }).catch((reason) => setError(errorMessage(reason)));
    }
  }, [botId, client, organizationId]);

  return {
    summary,
    organizationId,
    organizationDetail,
    organizationLoading,
    organizationError,
    personalOrganizationId: personalId,
    botId,
    botDetail,
    botDetailLoading,
    projectId,
    detail,
    loading,
    detailLoading,
    busyKey,
    error,
    setError,
    selectOrganization,
    selectBot,
    selectProject,
    reload: loadSummary,
    refreshOrganization,
    refreshDetail,
    createOrganization,
    renameOrganization,
    removeOrganization,
    addOrganizationMember,
    removeOrganizationMember,
    createBot,
    beginPairing,
    updateBotState,
    finishPairing,
    saveBotCapabilities,
    createProject,
    addProjectMember,
    removeProjectMember,
    createTaskList,
    createTask,
    updateTaskStatus,
    deleteTask,
    saveExtensions,
    createContextSource,
    toggleContextSource,
    deleteContextSource,
  };
}

export type CollaborationProjectsController = ReturnType<typeof useCollaborationProjects>;
