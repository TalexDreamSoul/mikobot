import { useCallback, useEffect, useMemo, useState } from "react";

import {
  EMPTY_EXTENSION_FILTERS,
  filterExtensionPackages,
  requiresRiskAcknowledgement,
  type ExtensionFilters,
} from "@/components/settings/extensions/extensionModel";
import { ApiError, fetchExtensionInventory, runExtensionAction } from "@/lib/api";
import type {
  ExtensionInventoryPayload,
  ExtensionPackage,
  NanobotExtensionAction,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

/** A pending action held open until the operator acknowledges the exact revision shown. */
export interface ExtensionRiskPrompt {
  pkg: ExtensionPackage;
  action: NanobotExtensionAction;
  /** The revision rendered in the dialog, and the one the confirmation submits. */
  revision: string;
}

export interface ExtensionConflict {
  targetId: string;
  message: string;
}

const EMPTY_INVENTORY: ExtensionInventoryPayload = {
  available: true,
  packages: [],
  diagnostics: [],
};

export function useExtensionsSettings(active: boolean) {
  const { client, getToken } = useClient();
  const [inventory, setInventory] = useState<ExtensionInventoryPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filters, setFilters] = useState<ExtensionFilters>(EMPTY_EXTENSION_FILTERS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [conflict, setConflict] = useState<ExtensionConflict | null>(null);
  const [riskPrompt, setRiskPrompt] = useState<ExtensionRiskPrompt | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setInventory(await fetchExtensionInventory(getToken()));
    } catch (err) {
      setInventory(null);
      setLoadError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    if (!active) return;
    void refresh();
  }, [active, refresh]);

  const packages = inventory?.packages ?? EMPTY_INVENTORY.packages;
  const visiblePackages = useMemo(
    () => filterExtensionPackages(packages, filters),
    [packages, filters],
  );
  const selected = useMemo(
    () => packages.find((pkg) => pkg.id === selectedId) ?? null,
    [packages, selectedId],
  );

  const clearFeedback = useCallback(() => {
    setActionError(null);
    setActionMessage(null);
  }, []);

  const select = useCallback(
    (id: string | null) => {
      setSelectedId(id);
      setConflict(null);
      clearFeedback();
    },
    [clearFeedback],
  );

  const submit = useCallback(
    async (
      action: NanobotExtensionAction,
      targetId: string,
      expectedRevision: string | null,
      riskAcknowledged: boolean,
    ) => {
      setPendingAction(`${action}:${targetId}`);
      clearFeedback();
      try {
        const result = await runExtensionAction(client, {
          targetId,
          action,
          expectedRevision,
          ...(riskAcknowledged ? { riskAcknowledged: true } : {}),
        });
        setActionMessage(result.message || null);
        if (!result.ok) setActionError(result.message || null);
        // The server owns lifecycle. Reload rather than patching the row locally so the
        // next action is bound to a revision the gateway actually reported.
        await refresh();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // A stale revision is not retried with a refreshed one: the operator reopens
          // the detail and decides again against what the host reports now.
          setConflict({ targetId, message: err.message });
        } else {
          setActionError((err as Error).message);
        }
      } finally {
        setPendingAction(null);
      }
    },
    [clearFeedback, client, refresh],
  );

  /**
   * Start an action.
   *
   * An external executable enable or install never reaches the transport here: it opens
   * the acknowledgement prompt instead, and cancelling it sends nothing.
   */
  const startAction = useCallback(
    (pkg: ExtensionPackage, action: NanobotExtensionAction, targetId?: string) => {
      const target = targetId ?? pkg.id;
      const isPackageTarget = target === pkg.id;
      if (isPackageTarget && requiresRiskAcknowledgement(pkg, action)) {
        setConflict(null);
        clearFeedback();
        setRiskPrompt({ pkg, action, revision: pkg.revision ?? "" });
        return;
      }
      const revision = isPackageTarget
        ? pkg.revision
        : pkg.components.find((component) => component.id === target)?.revision ?? null;
      void submit(action, target, revision, false);
    },
    [clearFeedback, submit],
  );

  const cancelRiskPrompt = useCallback(() => setRiskPrompt(null), []);

  const confirmRiskPrompt = useCallback(() => {
    const prompt = riskPrompt;
    if (!prompt) return;
    setRiskPrompt(null);
    void submit(prompt.action, prompt.pkg.id, prompt.revision, true);
  }, [riskPrompt, submit]);

  const reopenAfterConflict = useCallback(() => {
    setConflict(null);
    clearFeedback();
    void refresh();
  }, [clearFeedback, refresh]);

  return {
    actionError,
    actionMessage,
    available: inventory === null ? true : inventory.available,
    cancelRiskPrompt,
    clearFilters: () => setFilters(EMPTY_EXTENSION_FILTERS),
    confirmRiskPrompt,
    conflict,
    diagnostics: inventory?.diagnostics ?? EMPTY_INVENTORY.diagnostics,
    filters,
    loadError,
    loading,
    packages,
    pendingAction,
    refresh,
    reopenAfterConflict,
    riskPrompt,
    select,
    selected,
    setFilters,
    startAction,
    visiblePackages,
  };
}

export type ExtensionsSettingsController = ReturnType<typeof useExtensionsSettings>;
