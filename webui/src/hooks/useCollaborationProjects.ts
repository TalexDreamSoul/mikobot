import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  addCollaborationOrganizationMember,
  addCollaborationProjectMember,
  createCollaborationContextSource,
  createCollaborationOrganization,
  createCollaborationProject,
  createCollaborationTask,
  createCollaborationTaskList,
  deleteCollaborationContextSource,
  deleteCollaborationOrganization,
  deleteCollaborationTask,
  fetchCollaboration,
  fetchCollaborationOrganization,
  fetchCollaborationProject,
  removeCollaborationOrganizationMember,
  removeCollaborationProjectMember,
  updateCollaborationContextSource,
  updateCollaborationExtensions,
  updateCollaborationOrganization,
  updateCollaborationTask,
} from "@/lib/api";
import type {
  CollaborationEditableContextSourceKind,
  CollaborationExtensionSettings,
  CollaborationOrganizationPayload,
  CollaborationOrganizationRole,
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
  const [projectId, setProjectId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CollaborationProjectPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const detailRequestRef = useRef(0);
  const organizationRequestRef = useRef(0);

  const loadSummary = useCallback(async (
    preferredProjectId?: string | null,
    preferredOrganizationId?: string | null,
  ) => {
    const next = await fetchCollaboration(getToken());
    const activeProject = next.projects.find((project) => project.id === next.active_project_id);
    const personalId = personalOrganizationId(next);
    setSummary(next);
    setOrganizationId((current) => {
      const preferred = preferredOrganizationId ?? current ?? activeProject?.organization_id ?? personalId;
      if (preferred && next.organizations.some((organization) => organization.id === preferred)) return preferred;
      return activeProject?.organization_id ?? personalId ?? next.organizations[0]?.id ?? null;
    });
    setProjectId((current) => {
      const preferred = preferredProjectId ?? current ?? next.active_project_id;
      if (preferred && next.projects.some((project) => project.id === preferred)) return preferred;
      return next.projects[0]?.id ?? null;
    });
    return next;
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
    setOrganizationId(nextOrganizationId);
  }, []);
  const selectProject = useCallback((nextProjectId: string) => {
    setDetail(null);
    setProjectId(nextProjectId);
  }, []);

  return {
    summary,
    organizationId,
    organizationDetail,
    organizationLoading,
    organizationError,
    personalOrganizationId: personalId,
    projectId,
    detail,
    loading,
    detailLoading,
    busyKey,
    error,
    setError,
    selectOrganization,
    selectProject,
    reload: loadSummary,
    refreshOrganization,
    refreshDetail,
    createOrganization,
    renameOrganization,
    removeOrganization,
    addOrganizationMember,
    removeOrganizationMember,
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
