import type { NanobotFeaturesPayload, SettingsPayload } from "@/lib/types";

export type SettingsSectionKey =
  | "overview"
  | "appearance"
  | "models"
  | "image"
  | "voice"
  | "browser"
  | "channels"
  | "login-security"
  | "apps"
  | "automations"
  | "skills"
  | "extensions"
  | "runtime"
  | "advanced";

type PendingRestartSection = "runtime" | "browser" | "image";
export type PendingRestartSections = Record<PendingRestartSection, boolean>;

export type RestartAwarePayload = {
  requires_restart?: boolean;
  surface?: SettingsPayload["surface"];
  runtime_surface?: SettingsPayload["runtime_surface"];
  runtime_capabilities?: SettingsPayload["runtime_capabilities"];
};

export type ApplySettingsPayload = (
  payload: SettingsPayload,
  options?: { preserveAgentForm?: boolean },
) => void;

export type MaybeRestartHostEngine = (payload: RestartAwarePayload) => Promise<void>;

/**
 * The gateway withholds the host extension inventory from anyone it cannot prove is a
 * system administrator, and marks that projection `restricted` so the UI can say so
 * instead of rendering a host that merely looks empty.
 */
export function nanobotFeaturesRestricted(
  payload: NanobotFeaturesPayload | null | undefined,
): boolean {
  return payload?.restricted === true;
}
