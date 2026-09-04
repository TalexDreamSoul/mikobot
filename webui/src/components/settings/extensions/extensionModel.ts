import type { SettingsSectionKey } from "@/components/settings/contracts";
import type {
  ExtensionComponent,
  ExtensionComponentKind,
  ExtensionConfigurationTarget,
  ExtensionPackage,
  ExtensionSource,
  NanobotExtensionAction,
  NanobotExtensionExecution,
  NanobotExtensionLifecycle,
  NanobotExtensionTrust,
} from "@/lib/types";

export const ANY_FILTER = "any" as const;

export interface ExtensionFilters {
  query: string;
  kind: ExtensionComponentKind | typeof ANY_FILTER;
  source: ExtensionSource | typeof ANY_FILTER;
  trust: NanobotExtensionTrust | typeof ANY_FILTER;
  lifecycle: NanobotExtensionLifecycle | typeof ANY_FILTER;
}

export const EMPTY_EXTENSION_FILTERS: ExtensionFilters = {
  query: "",
  kind: ANY_FILTER,
  source: ANY_FILTER,
  trust: ANY_FILTER,
  lifecycle: ANY_FILTER,
};

/**
 * The lifecycle actions that are buttons.
 *
 * `inspect` is what the open detail already is, and `restart_required` is a state the
 * gateway reports rather than something an operator triggers, so neither becomes a
 * control. Everything here is still gated on the owning adapter having declared it.
 */
const MUTATING_ACTIONS: readonly NanobotExtensionAction[] = [
  "enable",
  "disable",
  "reload",
  "reconnect",
  "install",
  "uninstall",
];

/**
 * Which existing Settings section owns a configuration destination.
 *
 * An unmapped section resolves to null so the surface renders plain text instead of a
 * link that navigates nowhere. Nothing but the section key travels in the route.
 */
const CONFIGURATION_SECTIONS: Record<string, SettingsSectionKey> = {
  models: "models",
  image: "image",
  voice: "voice",
  channels: "channels",
  apps: "apps",
  skills: "skills",
  browser: "browser",
  runtime: "runtime",
};

export function extensionConfigurationSection(
  target: ExtensionConfigurationTarget | null | undefined,
): SettingsSectionKey | null {
  if (!target) return null;
  return CONFIGURATION_SECTIONS[target.section] ?? null;
}

/** Whether the package runs code in or beside the nanobot process. */
export function isExecutableExtension(pkg: ExtensionPackage): boolean {
  return pkg.execution === "in_process" || pkg.execution === "child_process";
}

/**
 * Whether the surface must disclose unisolated external execution for this package.
 *
 * This reads the payload rather than the source family, so a first-party package is
 * never warned about and an external one is never quietly exempted.
 */
export function disclosesExecutableRisk(pkg: ExtensionPackage): boolean {
  return isExecutableExtension(pkg) && pkg.trust !== "first_party";
}

/** Whether enabling or installing this package needs the acknowledgement dialog. */
export function requiresRiskAcknowledgement(
  pkg: ExtensionPackage,
  action: NanobotExtensionAction,
): boolean {
  if (action !== "enable" && action !== "install") return false;
  return pkg.risk_acknowledgement_required;
}

export function actionableExtensionActions(
  actions: readonly NanobotExtensionAction[],
): NanobotExtensionAction[] {
  return MUTATING_ACTIONS.filter((action) => actions.includes(action));
}

function matchesQuery(pkg: ExtensionPackage, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const haystacks = [pkg.display_name, pkg.name, pkg.id];
  for (const component of pkg.components) {
    haystacks.push(component.display_name, component.name);
  }
  return haystacks.some((value) => value.toLowerCase().includes(needle));
}

function matchesKind(pkg: ExtensionPackage, kind: ExtensionFilters["kind"]): boolean {
  if (kind === ANY_FILTER) return true;
  return pkg.components.some((component) => component.kind === kind);
}

/**
 * Compose every filter over the registry's own order.
 *
 * Ordering is never recomputed here: the gateway already returns the deterministic
 * snapshot order, and re-sorting would make two surfaces disagree about the same host.
 */
export function filterExtensionPackages(
  packages: readonly ExtensionPackage[],
  filters: ExtensionFilters,
): ExtensionPackage[] {
  return packages.filter((pkg) => (
    matchesQuery(pkg, filters.query)
    && matchesKind(pkg, filters.kind)
    && (filters.source === ANY_FILTER || pkg.source === filters.source)
    && (filters.trust === ANY_FILTER || pkg.trust === filters.trust)
    && (filters.lifecycle === ANY_FILTER || pkg.lifecycle === filters.lifecycle)
  ));
}

export function hasActiveExtensionFilters(filters: ExtensionFilters): boolean {
  return (
    filters.query.trim() !== ""
    || filters.kind !== ANY_FILTER
    || filters.source !== ANY_FILTER
    || filters.trust !== ANY_FILTER
    || filters.lifecycle !== ANY_FILTER
  );
}

/** The kinds actually present in this host, so a filter never offers an empty family. */
export function availableComponentKinds(
  packages: readonly ExtensionPackage[],
): ExtensionComponentKind[] {
  const kinds = new Set<ExtensionComponentKind>();
  for (const pkg of packages) {
    for (const component of pkg.components) kinds.add(component.kind);
  }
  return [...kinds].sort();
}

export function availableSources(packages: readonly ExtensionPackage[]): ExtensionSource[] {
  return [...new Set(packages.map((pkg) => pkg.source))].sort();
}

export function availableTrust(
  packages: readonly ExtensionPackage[],
): NanobotExtensionTrust[] {
  return [...new Set(packages.map((pkg) => pkg.trust))].sort();
}

export function availableLifecycles(
  packages: readonly ExtensionPackage[],
): NanobotExtensionLifecycle[] {
  return [...new Set(packages.map((pkg) => pkg.lifecycle))].sort();
}

/** Diagnostics that belong to this package or one of its components. */
export function diagnosticsForPackage(
  pkg: ExtensionPackage,
): { owner_id: string; code: string; message: string }[] {
  const rows = [];
  if (pkg.diagnostic) rows.push(pkg.diagnostic);
  for (const component of pkg.components) {
    if (component.diagnostic) rows.push(component.diagnostic);
  }
  return rows;
}

export function componentExecutionIsData(component: ExtensionComponent): boolean {
  return component.execution === "data";
}

export type ExtensionEnumKey =
  | NanobotExtensionLifecycle
  | NanobotExtensionTrust
  | NanobotExtensionExecution
  | ExtensionSource
  | ExtensionComponentKind
  | NanobotExtensionAction;
