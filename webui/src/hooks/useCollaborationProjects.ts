import { useCallback, useEffect, useRef, useState } from "react";

import {
  addCollaborationProjectMember,
  consumeCollaborationPairingChallenge,
  createCollaborationPairingChallenge,
  createCollaborationProject,
  deleteCollaborationAssignment,
  deleteCollaborationProject,
  fetchCollaboration,
  fetchCollaborationProject,
  removeCollaborationProjectMember,
  updateCollaborationAssignment,
  updateCollaborationDefaults,
  updateCollaborationProject,
} from "@/lib/api";
import type {
  CollaborationCapabilities,
  CollaborationPayload,
  CollaborationProjectPayload,
  CollaborationProjectRole,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

function errorMessage(reason: unknown): string {
  return reason instanceof Error && reason.message.trim()
    ? reason.message
    : "The project could not be updated. Try again.";
}

export function useCollaborationProjects() {
  const { client, getToken } = useClient();
  const [summary, setSummary] = useState<CollaborationPayload | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CollaborationProjectPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const detailRequestRef = useRef(0);

  const loadSummary = useCallback(async (preferredProjectId?: string | null) => {
    const next = await fetchCollaboration(getToken());
    setSummary(next);
    setProjectId((current) => {
      const preferred = preferredProjectId ?? current ?? next.active_project_id;
      if (preferred && next.projects.some((project) => project.id === preferred)) return preferred;
      return next.projects[0]?.id ?? null;
    });
    return next;
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

  const requireProject = useCallback(() => {
    if (!projectId) throw new Error("Select a project first.");
    return projectId;
  }, [projectId]);

  const createProject = useCallback(async (name: string) => {
    setBusyKey("project:create");
    setError(null);
    try {
      const { project } = await createCollaborationProject(client, name);
      await loadSummary(project.id);
      setProjectId(project.id);
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [client, loadSummary]);

  const renameProject = useCallback(async (name: string) => {
    const id = requireProject();
    await run("project:update", () => updateCollaborationProject(client, id, { name }), id);
    await loadSummary(id);
  }, [client, loadSummary, requireProject, run]);

  const removeProject = useCallback(async () => {
    const id = requireProject();
    setBusyKey("project:delete");
    setError(null);
    try {
      await deleteCollaborationProject(client, id);
      setDetail(null);
      setProjectId(null);
      await loadSummary(null);
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [client, loadSummary, requireProject]);

  const saveCapabilities = useCallback((capabilities: CollaborationCapabilities) => {
    const id = requireProject();
    return run("capabilities:save", () => (
      updateCollaborationProject(client, id, { capabilities })
    ), id);
  }, [client, requireProject, run]);

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

  const beginPairing = useCallback(async (values: {
    channelType: string;
    instanceId: string;
    assigneeUserId?: string | null;
    projectId?: string;
  }) => {
    const id = values.projectId ?? requireProject();
    setBusyKey("pairing:create");
    setError(null);
    try {
      return await createCollaborationPairingChallenge(client, { ...values, projectId: id });
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [client, requireProject]);

  const finishPairing = useCallback(async (challengeId: string) => {
    setBusyKey("pairing:consume");
    setError(null);
    try {
      const result = await consumeCollaborationPairingChallenge(client, challengeId);
      await loadSummary(projectId);
      if (projectId) await loadDetail(projectId);
      return result;
    } catch (reason) {
      setError(errorMessage(reason));
      throw reason;
    } finally {
      setBusyKey(null);
    }
  }, [client, loadDetail, loadSummary, projectId]);

  const setAssignmentEnabled = useCallback(async (
    channelType: string,
    instanceId: string,
    enabled: boolean,
  ) => {
    await run(`assignment:update:${channelType}:${instanceId}`, () => (
      updateCollaborationAssignment(client, { channelType, instanceId, enabled })
    ));
    await loadSummary(projectId);
  }, [client, loadSummary, projectId, run]);

  const moveAssignment = useCallback(async (
    channelType: string,
    instanceId: string,
    targetProjectId: string,
  ) => {
    await run(`assignment:update:${channelType}:${instanceId}`, () => (
      updateCollaborationAssignment(client, {
        channelType, instanceId, projectId: targetProjectId,
      })
    ));
    await loadSummary(projectId);
  }, [client, loadSummary, projectId, run]);

  const removeAssignment = useCallback(async (channelType: string, instanceId: string) => {
    await run(`assignment:delete:${channelType}:${instanceId}`, () => (
      deleteCollaborationAssignment(client, { channelType, instanceId })
    ));
    await loadSummary(projectId);
  }, [client, loadSummary, projectId, run]);

  const selectProject = useCallback((nextProjectId: string) => {
    setDetail(null);
    setProjectId(nextProjectId);
    void updateCollaborationDefaults(client, { projectId: nextProjectId })
      .catch((reason) => setError(errorMessage(reason)));
  }, [client]);

  return {
    summary,
    isAdmin: summary?.is_admin ?? false,
    projectId,
    detail,
    loading,
    detailLoading,
    busyKey,
    error,
    setError,
    selectProject,
    reload: loadSummary,
    refreshDetail,
    createProject,
    renameProject,
    removeProject,
    saveCapabilities,
    addProjectMember,
    removeProjectMember,
    beginPairing,
    finishPairing,
    setAssignmentEnabled,
    moveAssignment,
    removeAssignment,
  };
}

export type CollaborationProjectsController = ReturnType<typeof useCollaborationProjects>;
