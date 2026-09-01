import { useCallback, useEffect, useState } from "react";

import {
  createPersonalPersona,
  createPersonalTask,
  createPersonalVault,
  deletePersonalTask,
  fetchPersonalAssistant,
  setDefaultPersonalPersona,
  updatePersonalTask,
} from "@/lib/api";
import type {
  PersonalAssistantPayload,
  PersonalTask,
  PersonalTaskReviewState,
  PersonalVault,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

function message(reason: unknown): string {
  return reason instanceof Error && reason.message.trim()
    ? reason.message
    : "The personal assistant could not be updated. Try again.";
}

export function usePersonalAssistant() {
  const { client, getToken } = useClient();
  const [data, setData] = useState<PersonalAssistantPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await fetchPersonalAssistant(getToken());
    setData(next);
    setError(null);
    return next;
  }, [getToken]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void refresh()
      .catch((reason) => {
        if (!cancelled) setError(message(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const run = useCallback(async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (reason) {
      setError(message(reason));
      throw reason;
    } finally {
      setBusy(null);
    }
  }, [refresh]);

  const createTask = useCallback(async (values: {
    vault_id: string;
    title: string;
    note?: string;
    due_at_ms?: number;
    priority?: number;
    review_state?: PersonalTaskReviewState;
  }) => run("task:create", () => createPersonalTask(client, values)), [client, run]);

  const updateTask = useCallback(async (
    task: PersonalTask,
    values: { status?: PersonalTask["status"]; review_state?: PersonalTaskReviewState; priority?: number },
  ) => run(`task:${task.id}`, () => updatePersonalTask(client, task.id, values)), [client, run]);

  const setDefaultPersona = useCallback(async (personaId: string) => (
    run("persona:default", () => setDefaultPersonalPersona(client, personaId))
  ), [client, run]);

  const removeTask = useCallback(async (task: PersonalTask) => (
    run(`task:${task.id}`, () => deletePersonalTask(client, task.id))
  ), [client, run]);

  const createVault = useCallback(async (name: string, kind: PersonalVault["kind"]) => (
    run("vault:create", () => createPersonalVault(client, name, kind))
  ), [client, run]);

  const createPersona = useCallback(async (values: { name: string; vault_id: string; instructions?: string }) => (
    run("persona:create", () => createPersonalPersona(client, values))
  ), [client, run]);

  return {
    data,
    loading,
    busy,
    error,
    setError,
    refresh,
    createTask,
    setDefaultPersona,
    updateTask,
    removeTask,
    createVault,
    createPersona,
  };
}
