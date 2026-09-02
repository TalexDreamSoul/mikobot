import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, ExternalLink, Loader2, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { channelFieldMessageKey, channelTranslator } from "@/channel-plugins/i18n";
import { channelLocaleMessages } from "@/channel-plugins/locale-registry";
import type { ChannelPluginPanelProps } from "@/channel-plugins/types";
import { ToggleButton } from "@/components/settings/ToggleButton";
import {
  chatAppGuideUrl,
  docsUrlWithBase,
  type ChannelConfigField,
} from "@/components/settings/channels/catalog";
import {
  CredentialForm,
  channelValuesForSave,
  defaultChannelFieldValues,
} from "@/components/settings/channels/CredentialForm";
import { Button } from "@/components/ui/button";
import { useLogoFallback } from "@/hooks/useLogoFallback";
import { normalizeLocale } from "@/i18n/config";
import {
  configureChannel,
  disableNanobotFeature,
  enableNanobotFeature,
} from "@/lib/api";
import { logoFallbackUrls } from "@/lib/provider-brand";
import type {
  ChannelRuntimeStatus,
  ChannelSetupContractField,
  NanobotFeatureInfo,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

import {
  WEIXIN_AUTH_EXPIRED_MESSAGE,
  WeixinConnectFlow,
} from "./WeixinConnectFlow";
import {
  WEIXIN_ADVANCED_FIELD_KEYS,
  WEIXIN_PRIMARY_FIELD_KEYS,
} from "./presentation";

export function WeixinPanel({
  token,
  feature,
  actionKey,
  chatAppsDocsUrl,
  showBrandLogos,
  onAction,
  onFeaturesUpdate,
}: ChannelPluginPanelProps) {
  const { client } = useClient();
  const { t, i18n } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const channelTx = channelTranslator(t, "weixin");
  const defaultInstance = feature.instances?.find((instance) => instance.id === "default");
  const defaultPairingOnly = defaultInstance?.pairing_only === true;
  const runtimeError = weixinRuntimeError(feature.runtime_error, channelTx);
  const displayName = channelTx("displayName", "WeChat");
  const enabledBusy = actionKey === `enable:${feature.name}`;
  const disabledBusy = actionKey === `disable:${feature.name}`;
  const channelBusy = enabledBusy || disabledBusy;
  const channelChecked = !defaultPairingOnly
    && (feature.runtime_status === "running" || feature.runtime_status === "starting");
  const missingSupport = feature.enabled && !feature.installed;
  const alwaysEnabled = feature.capabilities?.includes("always_enabled") ?? false;
  const toggleChecked = !defaultPairingOnly && (alwaysEnabled || channelChecked);
  const channelToggleDisabled =
    defaultPairingOnly
    || alwaysEnabled
    || channelBusy
    || (!feature.install_supported && !feature.installed && !feature.enabled);
  const [connectRequestId, setConnectRequestId] = useState(0);
  const [selectedInstanceId, setSelectedInstanceId] = useState<string | null>(null);
  const [busyInstanceId, setBusyInstanceId] = useState<string | null>(null);
  const [instanceError, setInstanceError] = useState<string | null>(null);
  const [visibleSecrets, setVisibleSecrets] = useState<Record<string, boolean>>({});
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
  const docsUrl = docsUrlWithBase(chatAppGuideUrl("wechat"), chatAppsDocsUrl)
    ?? chatAppGuideUrl("wechat");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() =>
    defaultChannelFieldValues(editableFields, feature.config_values),
  );
  const fieldValuesRef = useRef(fieldValues);
  const touchedFieldsRef = useRef(touchedFields);
  const editableFieldsRef = useRef(editableFields);
  const saveContextRef = useRef({
    token,
    enabled: feature.enabled,
    onFeaturesUpdate,
  });
  editableFieldsRef.current = editableFields;
  saveContextRef.current = {
    token,
    enabled: feature.enabled,
    onFeaturesUpdate,
  };

  useEffect(() => {
    const nextValues = defaultChannelFieldValues(editableFields, feature.config_values);
    for (const key of touchedFieldsRef.current) {
      nextValues[key] = fieldValuesRef.current[key] ?? "";
    }
    fieldValuesRef.current = nextValues;
    setFieldValues(nextValues);
    setVisibleSecrets({});
  }, [configValuesKey, setupFieldsKey]);

  useEffect(() => {
    if (saveState !== "saved") return;
    const timeout = window.setTimeout(() => setSaveState("idle"), 1500);
    return () => window.clearTimeout(timeout);
  }, [saveState]);

  const saveSettings = useCallback(async (
    values: Record<string, string>,
    savedFields: Set<string>,
  ) => {
    const context = saveContextRef.current;
    setSaving(true);
    setSaveError(null);
    setSaveState("idle");
    try {
      const payload = await configureChannel(
        client,
        "weixin",
        channelValuesForSave(editableFieldsRef.current, values),
        { enable: context.enabled },
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

  const toggleAriaLabel = t("settings.channels.toggleChannel", {
    name: displayName,
    defaultValue: "{{name}} channel",
  });

  const additionalInstances = (feature.instances ?? []).filter(
    (instance) => instance.id !== "default",
  );

  const toggleInstance = async (instanceId: string, checked: boolean) => {
    setBusyInstanceId(instanceId);
    setInstanceError(null);
    try {
      const payload = checked
        ? await enableNanobotFeature(client, feature.name, { instanceId })
        : await disableNanobotFeature(client, feature.name, { instanceId });
      onFeaturesUpdate(payload);
    } catch (reason) {
      setInstanceError((reason as Error).message);
    } finally {
      setBusyInstanceId(null);
    }
  };

  return (
    <aside className="min-h-full rounded-panel bg-settings-surface p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <WeixinLogo showBrandLogos={showBrandLogos} />
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-[18px] font-semibold leading-6 text-foreground">
              {displayName}
            </h3>
            <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
              {channelTx("description", "Use nanobot from WeChat conversations.")}
            </p>
            {missingSupport && feature.install_supported ? (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={enabledBusy}
                onClick={() => onAction("enable", feature.name)}
                className="mt-2 h-8 rounded-full px-3 text-[12px] font-semibold"
              >
                {enabledBusy ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                )}
                {tx("settings.nanobotFeatures.installSupport", "Install support")}
              </Button>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-1">
          <WeixinStatusBadge status={feature.runtime_status}>
            {defaultPairingOnly
              ? channelTx("custom.pairingOnly", "Awaiting Pair Code")
              : weixinStatusLabel(feature, tx)}
          </WeixinStatusBadge>
          {channelBusy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
          ) : null}
          <ToggleButton
            checked={toggleChecked}
            disabled={channelToggleDisabled}
            ariaLabel={toggleAriaLabel}
            label={toggleChecked ? onLabel : offLabel}
            onChange={(checked) => {
              if (checked && !channelChecked && feature.configured === false) {
                setConnectRequestId((current) => current + 1);
                return;
              }
              onAction(checked ? "enable" : "disable", feature.name);
            }}
          />
        </div>
      </div>

      {runtimeError ? (
        <div className="mt-4 rounded-control border border-destructive/20 bg-destructive/5 px-3 py-2 text-[12px] leading-5 text-destructive">
          {runtimeError}
        </div>
      ) : null}
      {defaultPairingOnly ? (
        <div className="mt-4 rounded-control border border-amber-500/30 bg-amber-500/5 px-3 py-3 text-[12px] text-amber-900 dark:text-amber-100">
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

      <div className="mt-4 space-y-4">
        <WeixinConnectFlow
          token={token}
          feature={feature}
          idleLabel={channelTx("setup.primaryAction", "Connect WeChat")}
          connectRequestId={connectRequestId}
          instanceId="default"
          mode="replace"
          onFeaturesUpdate={onFeaturesUpdate}
        />

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
                      onChange={(checked) => void toggleInstance(instance.id, checked)}
                    />
                  </div>
                  {expanded ? (
                    <div className="mt-3 border-t border-border/45 pt-3">
                      <WeixinConnectFlow
                        token={token}
                        feature={feature}
                        idleLabel={tx("settings.channels.reconnect", "Reconnect")}
                        instanceId={instance.id}
                        mode="replace"
                        onFeaturesUpdate={onFeaturesUpdate}
                      />
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

        {primaryFields.length ? (
          <CredentialForm
            fields={primaryFields}
            values={fieldValues}
            configuredFields={configuredFields}
            visibleSecrets={visibleSecrets}
            onChange={setFieldValue}
            onToggleSecret={(key) => {
              setVisibleSecrets((current) => ({ ...current, [key]: !current[key] }));
            }}
            compact
          />
        ) : null}

        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className={cn(
            "flex items-center justify-end gap-1.5 text-[11px] leading-4 text-muted-foreground",
            !saving && saveState !== "saved" && "sr-only",
          )}
        >
          {saving ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              {tx("settings.actions.saving", "Saving")}
            </>
          ) : saveState === "saved" ? (
            <>
              <Check className="h-3 w-3" aria-hidden />
              {tx("settings.channels.savedSettings", "Saved settings.")}
            </>
          ) : null}
        </div>

        {saveError ? (
          <div
            role="alert"
            className="rounded-control border border-destructive/20 bg-destructive/5 px-3 py-2 text-[12px] leading-5 text-destructive"
          >
            {saveError}
          </div>
        ) : null}

        {advancedFields.length ? (
          <details className="group text-[12px] leading-5 text-muted-foreground">
            <summary className="cursor-pointer list-none text-[12px] font-semibold text-foreground">
              <span className="inline-flex items-center gap-1.5">
                {tx("settings.channels.advanced", "Advanced")}
                <ChevronDown
                  className="h-3.5 w-3.5 transition-transform group-open:rotate-180"
                  aria-hidden
                />
              </span>
            </summary>
            <div className="mt-3">
              <CredentialForm
                fields={advancedFields}
                values={fieldValues}
                configuredFields={configuredFields}
                visibleSecrets={visibleSecrets}
                onChange={setFieldValue}
                onToggleSecret={(key) => {
                  setVisibleSecrets((current) => ({ ...current, [key]: !current[key] }));
                }}
                compact
              />
            </div>
          </details>
        ) : null}

        <div className="flex justify-end">
          <WeixinGuideLink
            url={docsUrl}
            label={channelTx("setup.docsLabel", "Open WeChat setup")}
          />
        </div>
      </div>
    </aside>
  );
}

function weixinSetupFields(
  feature: NanobotFeatureInfo,
  locale: string,
): { primary: ChannelConfigField[]; advanced: ChannelConfigField[] } {
  const fields = feature.setup?.fields ?? [];
  const fieldsByKey = new Map(fields.map((field) => [field.key, field]));
  const messages = channelLocaleMessages("weixin", normalizeLocale(locale))?.setup;
  const knownKeys = new Set<string>([
    ...WEIXIN_PRIMARY_FIELD_KEYS,
    ...WEIXIN_ADVANCED_FIELD_KEYS,
  ]);
  const extraKeys = fields
    .map((field) => field.key)
    .filter((key) => !knownKeys.has(key));
  const hydrate = (keys: readonly string[]) => keys.flatMap((key) => {
    const field = fieldsByKey.get(key);
    if (!field) return [];
    const copy = messages?.fields?.[channelFieldMessageKey("weixin", key)];
    return [weixinConfigField(field, copy)];
  });

  return {
    primary: hydrate(WEIXIN_PRIMARY_FIELD_KEYS),
    advanced: hydrate([...WEIXIN_ADVANCED_FIELD_KEYS, ...extraKeys]),
  };
}

function weixinConfigField(
  field: ChannelSetupContractField,
  copy: { label: string; placeholder?: string; help?: string; choices?: Record<string, string> }
    | undefined,
): ChannelConfigField {
  const choices = field.kind === "bool" ? ["true", "false"] : field.choices;
  return {
    key: field.key,
    label: copy?.label ?? fieldLabel(field.field),
    placeholder: copy?.placeholder,
    help: copy?.help,
    secret: field.kind === "secret",
    optional: !field.required,
    inputType: field.kind === "int" ? "number" : undefined,
    defaultValue: field.default_value,
    options:
      field.kind === "enum" || field.kind === "bool"
        ? choices.map((choice) => ({
            value: choice,
            label: copy?.choices?.[choice] ?? fieldLabel(choice),
          }))
        : undefined,
  };
}

function fieldLabel(value: string): string {
  const spaced = value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  return spaced ? spaced[0].toUpperCase() + spaced.slice(1) : value;
}

function WeixinLogo({ showBrandLogos }: { showBrandLogos: boolean }) {
  const logoUrls = useMemo(() => logoFallbackUrls("https://weixin.qq.com/favicon.ico"), []);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  if (showBrandLogos && logoUrl) {
    return (
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-control bg-background">
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-5.5 w-5.5 max-h-6 max-w-6 object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      </span>
    );
  }
  return (
    <span
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control bg-background text-[11px] font-bold"
      style={{ color: "#07C160" }}
      aria-hidden
    >
      WX
    </span>
  );
}

function WeixinGuideLink({ url, label }: { url: string; label: string }) {
  const logoUrls = useMemo(() => logoFallbackUrls("https://weixin.qq.com/favicon.ico"), []);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex max-w-full items-center gap-2 rounded-full bg-background/80 py-1 pl-1 pr-2.5 text-[11.5px] font-semibold text-foreground transition-colors hover:bg-background"
    >
      <span
        className="grid h-5 w-5 shrink-0 place-items-center overflow-hidden rounded-full bg-muted/70 text-[9px] font-bold"
        style={{ color: "#07C160" }}
        aria-hidden
      >
        {logoUrl ? (
          <img
            src={logoUrl}
            alt=""
            decoding="async"
            loading="lazy"
            className="h-3.5 w-3.5 object-contain"
            onLoad={onLogoLoad}
            onError={onLogoError}
          />
        ) : (
          "WX"
        )}
      </span>
      <span className="truncate">{label}</span>
      <ExternalLink className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
    </a>
  );
}

function WeixinStatusBadge({
  children,
  status,
}: {
  children: ReactNode;
  status?: ChannelRuntimeStatus;
}) {
  return (
    <span className={cn(
      "shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium leading-4",
      status === "failed"
        ? "bg-destructive/10 text-destructive"
        : status === "running"
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-200"
          : "bg-muted/75 text-muted-foreground",
    )}>
      {children}
    </span>
  );
}

function weixinStatusLabel(
  feature: NanobotFeatureInfo,
  tx: (key: string, fallback: string) => string,
): string {
  if (feature.runtime_status === "failed") {
    return tx("settings.channels.runtimeFailed", "Failed");
  }
  if (feature.runtime_status === "starting") {
    return tx("settings.channels.runtimeStarting", "Starting");
  }
  if (feature.runtime_status === "running") return tx("settings.values.on", "On");
  if (feature.enabled) return tx("settings.channels.runtimeStopped", "Not running");
  return tx("settings.values.off", "Off");
}

function weixinRuntimeError(
  error: string | undefined,
  tx: (key: string, fallback: string) => string,
): string | undefined {
  if (error === WEIXIN_AUTH_EXPIRED_MESSAGE) {
    return tx("custom.expired", error);
  }
  return error;
}

function localizeBooleanFields(
  fields: ChannelConfigField[],
  onLabel: string,
  offLabel: string,
): ChannelConfigField[] {
  return fields.map((field) => {
    const values = new Set(field.options?.map((option) => option.value));
    if (values.size !== 2 || !values.has("true") || !values.has("false")) return field;
    return {
      ...field,
      options: field.options?.map((option) => ({
        ...option,
        label: option.value === "true" ? onLabel : offLabel,
      })),
    };
  });
}
