import type { TFunction } from "i18next";

import type {
  ExtensionComponentKind,
  ExtensionSource,
  NanobotExtensionAction,
  NanobotExtensionExecution,
  NanobotExtensionLifecycle,
  NanobotExtensionTrust,
} from "@/lib/types";

/**
 * Canonical enum values are rendered through translated labels.
 *
 * The raw value is the fallback only so an enum added to the backend before its
 * translation still names itself instead of rendering blank.
 */
function label(t: TFunction, group: string, value: string, fallback: string): string {
  return t(`settings.extensions.${group}.${value}`, { defaultValue: fallback });
}

const LIFECYCLE_FALLBACKS: Record<NanobotExtensionLifecycle, string> = {
  discovered: "Discovered",
  unavailable: "Unavailable",
  disabled: "Disabled",
  enabling: "Enabling",
  enabled: "Enabled",
  reloading: "Reloading",
  failed: "Failed",
  restart_required: "Restart required",
};

const TRUST_FALLBACKS: Record<NanobotExtensionTrust, string> = {
  first_party: "First-party",
  operator_trusted: "Operator-trusted",
  workspace_content: "Workspace content",
  remote_service: "Remote service",
};

const EXECUTION_FALLBACKS: Record<NanobotExtensionExecution, string> = {
  data: "Data only",
  in_process: "In the nanobot process",
  child_process: "In a child process",
  remote: "On a remote service",
};

const SOURCE_FALLBACKS: Record<ExtensionSource, string> = {
  builtin: "Built-in",
  agent_plugin: "Agent Plugin",
  python_entry_point: "Python entry point",
  workspace: "Workspace",
  configured: "Configured",
  channel_package: "Channel package",
  provider_registry: "Provider registry",
  cli_app: "CLI app",
  optional_feature: "Optional feature",
};

const KIND_FALLBACKS: Record<ExtensionComponentKind, string> = {
  skill: "Skill",
  mcp_server: "MCP server",
  tool: "Tool",
  channel: "Channel",
  llm_provider: "Model provider",
  image_provider: "Image provider",
  transcription_provider: "Voice provider",
  hook: "Hook",
  cli_app: "CLI app",
  optional_feature: "Optional feature",
};

const ACTION_FALLBACKS: Record<NanobotExtensionAction, string> = {
  inspect: "Inspect",
  configure: "Configure",
  enable: "Enable",
  disable: "Disable",
  reload: "Reload",
  reconnect: "Reconnect",
  install: "Install",
  uninstall: "Uninstall",
  restart_required: "Restart required",
};

export function lifecycleLabel(t: TFunction, value: NanobotExtensionLifecycle): string {
  return label(t, "lifecycle", value, LIFECYCLE_FALLBACKS[value] ?? value);
}

export function trustLabel(t: TFunction, value: NanobotExtensionTrust): string {
  return label(t, "trust", value, TRUST_FALLBACKS[value] ?? value);
}

export function executionLabel(t: TFunction, value: NanobotExtensionExecution): string {
  return label(t, "execution", value, EXECUTION_FALLBACKS[value] ?? value);
}

export function sourceLabel(t: TFunction, value: ExtensionSource): string {
  return label(t, "source", value, SOURCE_FALLBACKS[value] ?? value);
}

export function kindLabel(t: TFunction, value: ExtensionComponentKind): string {
  return label(t, "kind", value, KIND_FALLBACKS[value] ?? value);
}

export function actionLabel(t: TFunction, value: NanobotExtensionAction): string {
  return label(t, "action", value, ACTION_FALLBACKS[value] ?? value);
}

/**
 * How the package's own isolation claim reads.
 *
 * `null` is a real third state: the owning adapter made no claim, which must not be
 * shown as isolation.
 */
export function isolationLabel(t: TFunction, isolated: boolean | null): string {
  if (isolated === false) {
    return t("settings.extensions.isolation.no", { defaultValue: "Not isolated" });
  }
  if (isolated === true) {
    return t("settings.extensions.isolation.yes", { defaultValue: "Isolated" });
  }
  return t("settings.extensions.isolation.unknown", {
    defaultValue: "No isolation claim",
  });
}

export const EXTENSION_LIFECYCLE_VALUES = Object.keys(
  LIFECYCLE_FALLBACKS,
) as NanobotExtensionLifecycle[];
export const EXTENSION_TRUST_VALUES = Object.keys(TRUST_FALLBACKS) as NanobotExtensionTrust[];
export const EXTENSION_SOURCE_VALUES = Object.keys(SOURCE_FALLBACKS) as ExtensionSource[];
export const EXTENSION_KIND_VALUES = Object.keys(KIND_FALLBACKS) as ExtensionComponentKind[];
