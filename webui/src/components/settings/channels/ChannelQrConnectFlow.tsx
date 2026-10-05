import { channelValidationMessage } from "./validationMessages";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import QRCode from "qrcode";
import { Check, Loader2, RotateCcw } from "lucide-react";
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

export type ChannelQrConnectLabels = {
  qrAlt?: string;
  scanTitle: string;
  scanDescription: string;
  waiting: string;
  connected: string;
  stopped: string;
  connecting: string;
  scanAgain: string;
  connect: string;
};

export type ChannelConnectStartOptions = Omit<ChannelConnectTarget, "riskAcknowledged"> & {
  domain?: string;
  mode?: "replace" | "create";
  force?: boolean;
};
export type ChannelQrConnectPendingContext = {
  connect: ChannelConnectPayload;
  busy: boolean;
  poll: (
    params?: Readonly<Record<string, string>>,
  ) => Promise<ChannelConnectPayload | null>;
};

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
}) {
  const { client } = useClient();
  const pageVisible = usePageVisibility();
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [connect, setConnect] = useState<ChannelConnectPayload | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
  const canStart = !pending && !busy;
  useEffect(() => {
    onActiveChange?.(pending || busy);
  }, [pending, busy, onActiveChange]);
  const pollingPaused = Boolean(connect && pausePolling?.(connect));
  const displayMessage = connect
    ? resolveMessage?.(connect) ?? (connect.message ? channelValidationMessage(connect.message, t) : undefined)
    : undefined;

  useEffect(() => {
    if (!showQrCode || !connect?.qr_url) {
      setQrDataUrl("");
      return;
    }
    let cancelled = false;
    void QRCode.toDataURL(connect.qr_url, {
      width: 184,
      margin: 1,
      color: { dark: "#111827", light: "#ffffff" },
    })
      .then((url) => {
        if (!cancelled) setQrDataUrl(url);
      })
      .catch(() => {
        if (!cancelled) setQrDataUrl("");
      });
    return () => {
      cancelled = true;
    };
  }, [connect?.qr_url, showQrCode]);

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
        if (payload.status !== "pending") {
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
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
      setError((err as Error).message);
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
      setError("Extension action target is unavailable.");
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
    } catch (err) {
      setError((err as Error).message);
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
      setError("Extension action target is unavailable.");
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
      setError((err as Error).message);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const renderActionRow = renderActions ?? ((connectButton: ReactNode) => (
    <div className="flex flex-wrap justify-end gap-2">{connectButton}</div>
  ));

  return (
    <div className="mt-3 space-y-3">
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
      {autoStart && busy && !connect ? (
        <div role="status" className="grid min-h-[228px] place-items-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
          <span className="sr-only">{labels.connecting}</span>
        </div>
      ) : null}
      {pending && minimalPending && showQrCode ? (
        <div className="flex min-h-[228px] flex-col items-center justify-center gap-4 py-4">
          <div className="grid h-[196px] w-[196px] place-items-center rounded-control bg-background shadow-[inset_0_0_0_1px_oklch(0_0_0/0.1)] dark:shadow-[inset_0_0_0_1px_oklch(1_0_0/0.1)]">
            {qrDataUrl ? (
              <img
                src={qrDataUrl}
                alt={labels.qrAlt ?? labels.scanTitle}
                className="h-[184px] w-[184px]"
              />
            ) : (
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
            )}
          </div>
          {renderPending?.({ connect, busy, poll: submitPoll })}
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void cancel()}>{tx("settings.actions.cancel", "Cancel")}</Button>
        </div>
      ) : pending ? (
        <div className={`grid gap-4 rounded-control border border-border/70 p-4 ${showQrCode ? "sm:grid-cols-[auto_minmax(0,1fr)]" : ""}`}>
          {showQrCode ? <div className="grid h-[196px] w-[196px] place-items-center rounded-control border border-border/60 bg-background">
            {qrDataUrl ? (
              <img
                src={qrDataUrl}
                alt={labels.qrAlt ?? labels.scanTitle}
                className="h-[184px] w-[184px]"
              />
            ) : (
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
            )}
          </div> : null}
          <div className="flex min-w-0 flex-col justify-center">
            <div className="text-[13px] font-semibold text-foreground">
              {labels.scanTitle}
            </div>
            <p className="mt-1 text-[12.5px] leading-5 text-muted-foreground">
              {!showQrCode || qrDataUrl ? labels.scanDescription : null}
            </p>
            {renderPending?.({ connect, busy, poll: submitPoll }) ?? (
              <div className="mt-3 flex items-center gap-2 text-[12px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                {!showQrCode || qrDataUrl ? labels.waiting : labels.connecting}
              </div>
            )}
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 rounded-full px-3 text-[12px] font-semibold"
                onClick={() => void cancel()}
                disabled={busy}
              >
                {tx("settings.actions.cancel", "Cancel")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {succeeded && !suppressSucceeded && pairingRequired ? (
        <div className="rounded-control border border-amber-500/30 bg-amber-500/5 px-3 py-3 text-[12px] text-amber-900 dark:text-amber-100">
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

      {succeeded && !suppressSucceeded && !pairingRequired ? (
        <div className="flex items-center gap-2 rounded-control border border-emerald-500/20 px-3 py-2 text-[12px] font-medium text-emerald-700 dark:text-emerald-200">
          <Check className="h-3.5 w-3.5" aria-hidden />
          {displayMessage ?? labels.connected}
        </div>
      ) : null}

      {connect && ["expired", "failed", "cancelled"].includes(connect.status)
        && !(connected && connect.status === "cancelled") ? (
        <div className="rounded-control border border-border/60 px-3 py-2 text-[12px] leading-5 text-muted-foreground">
          {displayMessage || labels.stopped}
        </div>
      ) : null}

      {error ? (
        <div className="rounded-control border border-destructive/20 px-3 py-2 text-[12px] leading-5 text-destructive">
          {error}
        </div>
      ) : null}

      {!pending && !(autoStart && busy && !connect) ? renderActionRow(
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 rounded-full border-border/65 bg-background/80 px-3 text-[12px] font-semibold settings-hover"
          onClick={() => requestStart(forceOnRepeat && succeeded)}
          disabled={!canStart}
        >
          {busy ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : succeeded ? (
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          ) : null}
          {pending
            ? labels.connecting
            : succeeded
              ? labels.scanAgain
              : idleLabel ?? labels.connect}
        </Button>
      ) : null}
    </div>
  );
}
