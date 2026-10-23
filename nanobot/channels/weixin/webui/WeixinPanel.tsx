import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import { channelTranslator } from "@/channel-plugins/i18n";
import type { ChannelPluginPanelProps } from "@/channel-plugins/types";
import {
  channelValuesForSave,
  defaultChannelFieldValues,
} from "@/components/settings/channels/CredentialForm";
import { NanobotFeatureInstallDialog } from "@/components/settings/shared/SettingsControls";
import { Button } from "@/components/ui/button";
import { ToggleButton } from "@/components/ui/toggle-button";
import {
  configureChannel,
  disableNanobotFeature,
  enableNanobotFeature,
} from "@/lib/api";
import type { NanobotChannelInstanceInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

import { WeixinConnectFlow } from "./WeixinConnectFlow";
import { WeixinInstanceControls } from "./WeixinInstanceControls";
import { WeixinPanelView } from "./WeixinPanelView";
import {
  WEIXIN_AUTH_EXPIRED_MESSAGE,
  localizeBooleanFields,
  weixinRuntimeError,
  weixinSetupFields,
} from "./presentation";

type WeixinSaveContext = {
  enabled: boolean;
  installed: boolean;
  installSupported: boolean;
  extensionId: string;
  expectedRevision: string;
  onFeaturesUpdate: ChannelPluginPanelProps["onFeaturesUpdate"];
};

export function WeixinPanel({
  token,
  feature,
  actionKey,
  showBrandLogos,
  onAction,
  onFeaturesUpdate,
  connectRequestId = 0,
}: ChannelPluginPanelProps) {
  const { client } = useClient();
  const { t, i18n } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const channelTx = channelTranslator(t, "weixin");
  const defaultInstance = feature.instances?.find((instance) => instance.id === "default");
  const defaultPairingOnly = defaultInstance?.pairing_only === true;
  const [selectedInstanceId, setSelectedInstanceId] = useState<string | null>(null);
  const [busyInstanceId, setBusyInstanceId] = useState<string | null>(null);
  const [installConfirmInstance, setInstallConfirmInstance] = useState<NanobotChannelInstanceInfo | null>(null);
  const [installConfirmSave, setInstallConfirmSave] = useState(false);
  const [instanceError, setInstanceError] = useState<string | null>(null);
  const authExpired = feature.runtime_error === WEIXIN_AUTH_EXPIRED_MESSAGE;
  const [authRecoveryActive, setAuthRecoveryActive] = useState(authExpired);
  const runtimeError = weixinRuntimeError(feature.runtime_error, channelTx)
    ?? (authRecoveryActive ? channelTx("custom.expired", WEIXIN_AUTH_EXPIRED_MESSAGE) : undefined);
  const [touchedFields, setTouchedFields] = useState<Set<string>>(() => new Set());
  const [saving, setSaving] = useState(false);
  const [saveRevision, setSaveRevision] = useState(0);
  const [attemptedRevision, setAttemptedRevision] = useState(0);
  const [saveState, setSaveState] = useState<"idle" | "saved">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const configValuesKey = JSON.stringify(feature.config_values ?? {});
  const setupFieldsKey = JSON.stringify(feature.setup?.fields ?? []);
  const configuredFields = useMemo(
    () => new Set(feature.configured_fields ?? []),
    [feature.configured_fields],
  );
  const onLabel = tx("settings.values.on", "On");
  const offLabel = tx("settings.values.off", "Off");
  const setupFields = weixinSetupFields(
    feature,
    i18n.resolvedLanguage ?? i18n.language,
  );
  const primaryFields = localizeBooleanFields(setupFields.primary, onLabel, offLabel);
  const advancedFields = localizeBooleanFields(setupFields.advanced, onLabel, offLabel);
  const editableFields = [...primaryFields, ...advancedFields];
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() =>
    defaultChannelFieldValues(editableFields, feature.config_values),
  );
  const fieldValuesRef = useRef(fieldValues);
  const touchedFieldsRef = useRef(touchedFields);
  const editableFieldsRef = useRef(editableFields);
  const pendingSaveRef = useRef<{
    values: Record<string, string>;
    savedFields: Set<string>;
    context: WeixinSaveContext;
  } | null>(null);
  const saveContextRef = useRef<WeixinSaveContext>({
    enabled: feature.enabled,
    installed: feature.installed,
    installSupported: feature.install_supported,
    extensionId: feature.action_target_id ?? "",
    expectedRevision: feature.action_target_revision ?? "",
    onFeaturesUpdate,
  });
  editableFieldsRef.current = editableFields;
  saveContextRef.current = {
    enabled: feature.enabled,
    installed: feature.installed,
    installSupported: feature.install_supported,
    extensionId: feature.action_target_id ?? "",
    expectedRevision: feature.action_target_revision ?? "",
    onFeaturesUpdate,
  };

  useEffect(() => {
    if (authExpired) {
      setAuthRecoveryActive(true);
    } else if (feature.runtime_status === "running") {
      setAuthRecoveryActive(false);
    }
  }, [authExpired, feature.runtime_status]);

  useEffect(() => {
    const nextValues = defaultChannelFieldValues(editableFields, feature.config_values);
    for (const key of touchedFieldsRef.current) {
      nextValues[key] = fieldValuesRef.current[key] ?? "";
    }
    fieldValuesRef.current = nextValues;
    setFieldValues(nextValues);
  }, [configValuesKey, setupFieldsKey]);

  useEffect(() => {
    if (saveState !== "saved") return;
    const timeout = window.setTimeout(() => setSaveState("idle"), 1500);
    return () => window.clearTimeout(timeout);
  }, [saveState]);

  const saveSettings = useCallback(async (
    values: Record<string, string>,
    savedFields: Set<string>,
    riskAcknowledged = false,
    context: WeixinSaveContext = saveContextRef.current,
  ) => {
    if (context.enabled && !riskAcknowledged && !context.installed && context.installSupported) {
      pendingSaveRef.current = { values, savedFields, context: { ...context } };
      setSaveError(null);
      setInstallConfirmSave(true);
      return;
    }
    setSaving(true);
    setSaveError(null);
    setSaveState("idle");
    try {
      const payload = await configureChannel(
        client,
        "weixin",
        channelValuesForSave(editableFieldsRef.current, values),
        {
          enable: context.enabled,
          extensionId: context.extensionId,
          expectedRevision: context.expectedRevision,
          ...(riskAcknowledged ? { riskAcknowledged: true } : {}),
        },
      );
      const remainingFields = new Set(touchedFieldsRef.current);
      for (const key of savedFields) {
        if (fieldValuesRef.current[key] === values[key]) remainingFields.delete(key);
      }
      touchedFieldsRef.current = remainingFields;
      setTouchedFields(remainingFields);
      setSaveState(remainingFields.size ? "idle" : "saved");
      if (payload.nanobot_features) context.onFeaturesUpdate(payload.nanobot_features);
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }, [client]);

  useEffect(() => {
    if (
      !editableFields.length
      || !touchedFields.size
      || saving
      || saveRevision <= attemptedRevision
    ) return;
    const timeout = window.setTimeout(() => {
      setAttemptedRevision(saveRevision);
      void saveSettings(
        { ...fieldValuesRef.current },
        new Set(touchedFieldsRef.current),
      );
    }, 500);
    return () => window.clearTimeout(timeout);
  }, [
    attemptedRevision,
    editableFields.length,
    saveRevision,
    saveSettings,
    saving,
    touchedFields.size,
  ]);

  const setFieldValue = (key: string, value: string) => {
    if (fieldValuesRef.current[key] === value) return;
    const nextValues = { ...fieldValuesRef.current, [key]: value };
    const nextTouchedFields = new Set(touchedFieldsRef.current).add(key);
    fieldValuesRef.current = nextValues;
    touchedFieldsRef.current = nextTouchedFields;
    setFieldValues(nextValues);
    setTouchedFields(nextTouchedFields);
    setSaveError(null);
    setSaveState("idle");
    setSaveRevision((current) => current + 1);
  };

  const additionalInstances = (feature.instances ?? []).filter((instance) => instance.id !== "default");
  const toggleInstance = async (instance: NanobotChannelInstanceInfo, checked: boolean, riskAcknowledged = false) => {
    if (busyInstanceId !== null) return;
    if (checked && !riskAcknowledged && !feature.installed && feature.install_supported) {
      setInstallConfirmInstance(instance);
      return;
    }
    setBusyInstanceId(instance.id);
    setInstanceError(null);
    try {
      const options = {
        extensionId: instance.extension_id ?? "",
        expectedRevision: instance.extension_revision ?? "",
        instanceId: instance.id,
        ...(riskAcknowledged ? { riskAcknowledged: true } : {}),
      };
      onFeaturesUpdate(checked
        ? await enableNanobotFeature(client, feature.name, options)
        : await disableNanobotFeature(client, feature.name, options));
    } catch (reason) {
      setInstanceError((reason as Error).message);
    } finally {
      setBusyInstanceId(null);
    }
  };
  return (
    <WeixinPanelView
      feature={feature}
      actionKey={actionKey}
      showBrandLogos={showBrandLogos}
      primaryFields={primaryFields}
      advancedFields={advancedFields}
      fieldValues={fieldValues}
      configuredFields={configuredFields}
      secretVisibilityKey={`${configValuesKey}:${setupFieldsKey}`}
      runtimeError={runtimeError}
      saving={saving}
      saveState={saveState}
      saveError={saveError}
      connectFlow={<>
        <NanobotFeatureInstallDialog feature={installConfirmInstance ? feature : null} installing={busyInstanceId !== null}
          onOpenChange={(open) => { if (!open) setInstallConfirmInstance(null); }}
          onConfirm={() => { const instance = installConfirmInstance; setInstallConfirmInstance(null); if (instance) void toggleInstance(instance, true, true); }} />
        <NanobotFeatureInstallDialog feature={installConfirmSave ? feature : null} installing={saving}
          onOpenChange={(open) => { if (!open) { pendingSaveRef.current = null; setInstallConfirmSave(false); } }}
          onConfirm={() => { const pending = pendingSaveRef.current; pendingSaveRef.current = null; setInstallConfirmSave(false); if (pending) void saveSettings(pending.values, pending.savedFields, true, pending.context); }} />
        {defaultPairingOnly ? (
          <div className="rounded-control border border-amber-500/30 bg-amber-500/5 px-3 py-3 text-[12px] text-amber-900 dark:text-amber-100">
            <p className="font-semibold">
              {tx("settings.channels.pairingRequiredTitle", "Connected — bot assignment required")}
            </p>
            <p className="mt-1 leading-5">
              {tx(
                "settings.channels.pairingRequiredDescription",
                "Assign instance {{instance}} to a bot with a one-time Pair Code before it can receive messages.",
              ).replace("{{instance}}", defaultInstance?.id || "default")}
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-3 h-8 rounded-full px-3 text-[12px] font-semibold"
              onClick={() => { window.location.hash = "#/projects?section=bots"; }}
            >
              {tx("settings.channels.manageBots", "Open bot management")}
            </Button>
          </div>
        ) : null}

        {defaultInstance ? (
        <WeixinConnectFlow
          token={token}
          feature={feature}
          authRecoveryActive={authRecoveryActive}
          idleLabel={channelTx("setup.primaryAction", "Connect WeChat")}
          connectRequestId={connectRequestId}
          instanceId="default"
          mode="replace"
          onFeaturesUpdate={onFeaturesUpdate}
        />
        ) : null}
        {defaultInstance ? <WeixinInstanceControls token={token} feature={feature} instance={defaultInstance} onFeaturesUpdate={onFeaturesUpdate} /> : null}

        {additionalInstances.length ? (
          <div className="space-y-2">
            {additionalInstances.map((instance) => {
              const expanded = selectedInstanceId === instance.id;
              const status = instance.pairing_only
                ? channelTx("custom.pairingOnly", "Awaiting Pair Code")
                : instance.runtime_status ?? (instance.enabled ? "running" : "stopped");
              const running = instance.runtime_status === "running" || instance.runtime_status === "starting";
              return (
                <article key={instance.id} className="rounded-control border border-border/50 bg-background px-3 py-2 text-[12px]">
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-center justify-between gap-2 text-left"
                      aria-expanded={expanded}
                      onClick={() => setSelectedInstanceId(expanded ? null : instance.id)}
                    >
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-foreground">
                          {instance.display_name?.trim() || instance.name || instance.id}
                        </span>
                        <span className="mt-0.5 block truncate text-muted-foreground">
                          {instance.id} · {status}
                        </span>
                      </span>
                      <ChevronDown className={cn("h-4 w-4 shrink-0 transition-transform", expanded && "rotate-180")} aria-hidden />
                    </button>
                    <ToggleButton
                      checked={running}
                      disabled={busyInstanceId === instance.id || !instance.configured || instance.pairing_only}
                      ariaLabel={t("settings.channels.toggleInstance", {
                        name: instance.display_name?.trim() || instance.name || instance.id,
                        defaultValue: "{{name}} instance",
                      })}
                      label={running ? onLabel : offLabel}
                      onChange={(checked) => void toggleInstance(instance, checked)}
                    />
                  </div>
                  {expanded ? (
                    <div className="mt-3 border-t border-border/45 pt-3">
                      <WeixinInstanceControls token={token} feature={feature} instance={instance} onFeaturesUpdate={onFeaturesUpdate} />
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : null}

        {instanceError ? (
          <div role="alert" className="rounded-control border border-destructive/20 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
            {instanceError}
          </div>
        ) : null}

        <WeixinConnectFlow
          token={token}
          feature={feature}
          idleLabel={channelTx("custom.addBot", "Add another WeChat bot")}
          instanceId="default"
          mode="create"
          onFeaturesUpdate={onFeaturesUpdate}
        />
      </>}
      onInstall={() => onAction("enable", feature.name)}
      onFieldChange={setFieldValue}
    />
  );
}
