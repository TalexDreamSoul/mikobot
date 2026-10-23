import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { NanobotFeatureInstallDialog } from "@/components/settings/shared/SettingsControls";
import { Button } from "@/components/ui/button";
import { usePageVisibility } from "@/hooks/usePageVisibility";
import {
  cancelChannelConnect,
  pollChannelConnect,
  startChannelConnect,
  type ChannelConnectTarget,
} from "@/lib/api";
import type {
  ChannelConnectPayload,
  NanobotFeatureInfo,
  NanobotFeaturesPayload,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

import {
  ChannelQrConnectFlowView,
  type ChannelQrConnectFlowViewProps,
  type ChannelQrConnectLabels,
  type ChannelQrConnectPendingContext,
} from "./ChannelQrConnectFlowView";

export type ChannelConnectStartOptions = Omit<ChannelConnectTarget, "riskAcknowledged"> & {
  domain?: string;
  mode?: "replace" | "create";
  force?: boolean;
};
export type { ChannelQrConnectLabels, ChannelQrConnectPendingContext } from "./ChannelQrConnectFlowView";

export function ChannelQrConnectFlow({
  feature,
  channelName,
  startOptions,
  idleLabel,
  connectRequestId,
  forceOnRepeat = false,
  connected = false,
  autoStart = false,
  minimalPending = false,
  showQrCode = true,
  labels,
  onFeaturesUpdate,
  pausePolling,
  renderPending,
  resolveMessage,
  suppressSucceeded = false,
  onActiveChange,
  renderActions,
  renderView,
}: {
  feature: NanobotFeatureInfo;
  token: string;
  channelName: string;
  startOptions: ChannelConnectStartOptions;
  idleLabel?: string;
  connectRequestId?: number;
  forceOnRepeat?: boolean;
  connected?: boolean;
  autoStart?: boolean;
  minimalPending?: boolean;
  /** Browser-based authorization can reuse the flow without generating or showing a QR code. */
  showQrCode?: boolean;
  labels: ChannelQrConnectLabels;
  onFeaturesUpdate: (payload: NanobotFeaturesPayload) => void;
  pausePolling?: (payload: ChannelConnectPayload) => boolean;
  renderPending?: (context: ChannelQrConnectPendingContext) => ReactNode;
  resolveMessage?: (payload: ChannelConnectPayload) => string | undefined;
  suppressSucceeded?: boolean;
  onActiveChange?: (active: boolean) => void;
  renderActions?: (connectButton: ReactNode) => ReactNode;
  renderView?: (props: ChannelQrConnectFlowViewProps) => ReactNode;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { client } = useClient();
  const pageVisible = usePageVisibility();
  const [connect, setConnect] = useState<ChannelConnectPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{
    action: NonNullable<ChannelQrConnectFlowViewProps["errorAction"]>;
    message: string;
  } | null>(null);
  const [installConfirm, setInstallConfirm] = useState<NanobotFeatureInfo | null>(null);
  const handledRequestId = useRef(0);
  const autoStarted = useRef(false);
  const pollInFlight = useRef(false);
  const operationGeneration = useRef(0);
  const startInFlight = useRef(false);
  useEffect(() => () => { operationGeneration.current += 1; }, []);
  const sessionTargetRef = useRef<ChannelConnectTarget | null>(null);
  const pendingStartRef = useRef<{
    force: boolean;
    options: ChannelConnectStartOptions;
  } | null>(null);
  const pending = connect?.status === "pending";
  const succeeded = !pending && (connected || connect?.status === "succeeded");
  const pairingRequired = succeeded && connect?.pairing_required === true;
  useEffect(() => {
    onActiveChange?.(pending || busy);
  }, [pending, busy, onActiveChange]);
  const pollingPaused = Boolean(connect && pausePolling?.(connect));
  useEffect(() => {
    if (
      !connect?.session_id
      || connect.status !== "pending"
      || pollingPaused
      || !pageVisible
    ) return;
    const sessionId = connect.session_id;
    let cancelled = false;
    const actionTarget = sessionTargetRef.current;
    if (!actionTarget) return;
    const poll = async () => {
      if (pollInFlight.current) return;
      pollInFlight.current = true;
      const generation = operationGeneration.current;
      try {
        const payload = await pollChannelConnect(
          client,
          channelName,
          sessionId,
          actionTarget,
        );
        if (cancelled || generation !== operationGeneration.current) return;
        setConnect((current) => ({
          ...(current ?? payload),
          ...payload,
          qr_url: payload.qr_url ?? current?.qr_url,
        }));
        if (payload.nanobot_features) {
          onFeaturesUpdate(payload.nanobot_features);
        }
        setError((current) => payload.status !== "pending" || current?.action === "poll" ? null : current);
      } catch (err) {
        if (!cancelled) setError((current) => current?.action === "cancel"
          ? current : { action: "poll", message: (err as Error).message });
      } finally {
        pollInFlight.current = false;
      }
    };
    const initial = window.setTimeout(() => void poll(), 900);
    const interval = window.setInterval(
      () => void poll(),
      Math.max(2500, connect.interval_ms ?? 5000),
    );
    return () => {
      cancelled = true;
      window.clearTimeout(initial);
      window.clearInterval(interval);
    };
  }, [
    channelName,
    client,
    connect?.interval_ms,
    connect?.session_id,
    connect?.status,
    onFeaturesUpdate,
    pageVisible,
    pollingPaused,
  ]);

  const runStart = useCallback(async (
    options: ChannelConnectStartOptions,
    force = false,
    riskAcknowledged = false,
  ) => {
    if (startInFlight.current) return;
    startInFlight.current = true;
    const generation = ++operationGeneration.current;
    setBusy(true);
    setError(null);
    try {
      const actionTarget: ChannelConnectTarget = {
        extensionId: options.extensionId,
        expectedRevision: options.expectedRevision,
        instanceId: options.instanceId,
        ...(riskAcknowledged ? { riskAcknowledged: true } : {}),
      };
      const payload = await startChannelConnect(client, channelName, {
        ...options,
        ...actionTarget,
        force: force || options.force,
      });
      if (generation !== operationGeneration.current) return;
      sessionTargetRef.current = actionTarget;
      setConnect(payload);
      if (payload.nanobot_features) {
        onFeaturesUpdate(payload.nanobot_features);
      }
    } catch (err) {
      setError({ action: "start", message: (err as Error).message });
    } finally {
      startInFlight.current = false;
      setBusy(false);
    }
  }, [channelName, client, onFeaturesUpdate]);

  const requestStart = useCallback((force = false) => {
    if (!feature.installed && feature.install_supported) {
      pendingStartRef.current = { force, options: { ...startOptions } };
      setInstallConfirm(feature);
      return;
    }
    void runStart(startOptions, force);
  }, [feature, runStart, startOptions]);

  useEffect(() => {
    const requested = Boolean(connectRequestId && connectRequestId !== handledRequestId.current);
    if (!requested && !(autoStart && !autoStarted.current)) return;
    handledRequestId.current = connectRequestId ?? 0;
    autoStarted.current = true;
    requestStart();
  }, [autoStart, connectRequestId, requestStart]);

  const cancel = async () => {
    if (!connect?.session_id) {
      setConnect(null);
      return;
    }
    const actionTarget = sessionTargetRef.current;
    if (!actionTarget) {
      setError({ action: "cancel", message: "Extension action target is unavailable." });
      return;
    }
    operationGeneration.current += 1;
    setBusy(true);
    try {
      const payload = await cancelChannelConnect(
        client,
        channelName,
        connect.session_id,
        actionTarget,
      );
      setConnect(payload);
      setError(null);
    } catch (err) {
      setError({ action: "cancel", message: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const submitPoll = async (
    params: Readonly<Record<string, string>> = {},
  ): Promise<ChannelConnectPayload | null> => {
    if (!connect?.session_id) return null;
    const actionTarget = sessionTargetRef.current;
    if (!actionTarget) {
      setError({ action: "poll", message: "Extension action target is unavailable." });
      return null;
    }
    const generation = operationGeneration.current;
    setBusy(true);
    setError(null);
    try {
      const payload = await pollChannelConnect(
        client,
        channelName,
        connect.session_id,
        actionTarget,
        params,
      );
      if (generation !== operationGeneration.current) return null;
      setConnect((current) => ({
        ...(current ?? payload),
        ...payload,
        qr_url: payload.qr_url ?? current?.qr_url,
      }));
      if (payload.nanobot_features) {
        onFeaturesUpdate(payload.nanobot_features);
      }
      if (payload.status !== "pending") {
        setError(null);
      }
      return payload;
    } catch (err) {
      setError({ action: "poll", message: (err as Error).message });
      return null;
    } finally {
      setBusy(false);
    }
  };

  const viewProps: ChannelQrConnectFlowViewProps = {
    connect,
    busy,
    error: error?.message ?? null,
    errorAction: error?.action,
    onStart: () => requestStart(forceOnRepeat && succeeded),
    onCancel: () => void cancel(),
    onPoll: submitPoll,
    idleLabel,
    connected,
    autoStart,
    minimalPending,
    showQrCode,
    labels,
    renderPending,
    resolveMessage,
    suppressSucceeded: suppressSucceeded || pairingRequired,
    renderActions,
  };
  return (
    <>
      <NanobotFeatureInstallDialog
        feature={installConfirm}
        installing={busy}
        onOpenChange={(open) => {
          if (!open) {
            pendingStartRef.current = null;
            setInstallConfirm(null);
          }
        }}
        onConfirm={() => {
          const pendingStart = pendingStartRef.current;
          pendingStartRef.current = null;
          setInstallConfirm(null);
          if (pendingStart) void runStart(pendingStart.options, pendingStart.force, true);
        }}
      />
      {pairingRequired && !suppressSucceeded ? (
        <div className="mt-3 rounded-control border border-amber-500/30 bg-amber-500/5 px-3 py-3 text-[12px] text-amber-900 dark:text-amber-100">
          <p className="font-semibold">
            {tx("settings.channels.pairingRequiredTitle", "Connected — project assignment required")}
          </p>
          <p className="mt-1 leading-5">
            {tx(
              "settings.channels.pairingRequiredDescription",
              "Assign instance {{instance}} to a project with a one-time Pair Code before it can receive messages.",
            ).replace("{{instance}}", connect?.instance_id ?? "default")}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-3 h-8 rounded-full px-3 text-[12px] font-semibold"
            onClick={() => { window.location.hash = "#/projects?section=channels"; }}
          >
            {tx("settings.channels.manageBots", "Open Channels")}
          </Button>
        </div>
      ) : null}
      {renderView ? renderView(viewProps) : <ChannelQrConnectFlowView {...viewProps} />}
      {pending && minimalPending && showQrCode ? (
        <div className="flex justify-center">
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void cancel()}>
            {tx("settings.actions.cancel", "Cancel")}
          </Button>
        </div>
      ) : null}
    </>
  );
}
