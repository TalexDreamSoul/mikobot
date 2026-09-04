import {
  ANY_FILTER,
  actionableExtensionActions,
  availableComponentKinds,
  availableLifecycles,
  availableSources,
  availableTrust,
  diagnosticsForPackage,
  disclosesExecutableRisk,
  extensionConfigurationSection,
  filterExtensionPackages,
  hasActiveExtensionFilters,
  isExecutableExtension,
  requiresRiskAcknowledgement,
  EMPTY_EXTENSION_FILTERS,
  type ExtensionFilters,
} from "@/components/settings/extensions/extensionModel";
import {
  agentPluginPackage,
  channelPackage,
  workspaceSkillPackage,
} from "@/tests/extension-fixtures";
import type { ExtensionPackage } from "@/lib/types";

const PACKAGES: ExtensionPackage[] = [
  agentPluginPackage(),
  channelPackage(),
  workspaceSkillPackage(),
];

function withFilters(patch: Partial<ExtensionFilters>): ExtensionFilters {
  return { ...EMPTY_EXTENSION_FILTERS, ...patch };
}

describe("extension inventory filtering", () => {
  it("returns every package in the gateway's own order when nothing is filtered", () => {
    expect(filterExtensionPackages(PACKAGES, EMPTY_EXTENSION_FILTERS).map((p) => p.id))
      .toEqual(PACKAGES.map((p) => p.id));
  });

  it("filters by the component family a package actually contributes", () => {
    expect(filterExtensionPackages(PACKAGES, withFilters({ kind: "channel" })).map((p) => p.id))
      .toEqual([channelPackage().id]);
    expect(filterExtensionPackages(PACKAGES, withFilters({ kind: "mcp_server" })).map((p) => p.id))
      .toEqual([agentPluginPackage().id]);
  });

  it("filters by source, trust, and lifecycle independently", () => {
    expect(filterExtensionPackages(PACKAGES, withFilters({ source: "workspace" })).map((p) => p.id))
      .toEqual([workspaceSkillPackage().id]);
    expect(
      filterExtensionPackages(PACKAGES, withFilters({ trust: "operator_trusted" })).map((p) => p.id),
    ).toEqual([agentPluginPackage().id]);
    expect(filterExtensionPackages(PACKAGES, withFilters({ lifecycle: "failed" })).map((p) => p.id))
      .toEqual([channelPackage().id]);
  });

  it("composes filters instead of applying only the last one", () => {
    const both = filterExtensionPackages(
      PACKAGES,
      withFilters({ source: "agent_plugin", lifecycle: "failed" }),
    );
    expect(both).toEqual([]);
  });

  it("searches display name, package name, and component name", () => {
    expect(filterExtensionPackages(PACKAGES, withFilters({ query: "Desktop" })).map((p) => p.id))
      .toEqual([agentPluginPackage().id]);
    expect(filterExtensionPackages(PACKAGES, withFilters({ query: "meeting-notes" })).map((p) => p.id))
      .toEqual([workspaceSkillPackage().id]);
    // "desktop-mcp" names only a component, so a package-only search would miss it.
    expect(filterExtensionPackages(PACKAGES, withFilters({ query: "desktop-mcp" })).map((p) => p.id))
      .toEqual([agentPluginPackage().id]);
  });

  it("composes search with a facet filter", () => {
    expect(
      filterExtensionPackages(PACKAGES, withFilters({ query: "desktop", source: "workspace" })),
    ).toEqual([]);
  });

  it("reports whether any filter is active so an empty result can be explained", () => {
    expect(hasActiveExtensionFilters(EMPTY_EXTENSION_FILTERS)).toBe(false);
    expect(hasActiveExtensionFilters(withFilters({ query: "  " }))).toBe(false);
    expect(hasActiveExtensionFilters(withFilters({ query: "x" }))).toBe(true);
    expect(hasActiveExtensionFilters(withFilters({ trust: "first_party" }))).toBe(true);
  });

  it("offers only the facet values this host actually reports", () => {
    expect(availableComponentKinds(PACKAGES)).toEqual(["channel", "mcp_server", "skill"]);
    expect(availableSources(PACKAGES)).toEqual(["agent_plugin", "channel_package", "workspace"]);
    expect(availableTrust(PACKAGES)).toEqual([
      "first_party",
      "operator_trusted",
      "workspace_content",
    ]);
    expect(availableLifecycles(PACKAGES)).toEqual(["disabled", "enabled", "failed"]);
  });
});

describe("executable trust disclosure", () => {
  it("labels an external in-process or child-process package executable", () => {
    expect(isExecutableExtension(agentPluginPackage())).toBe(true);
    expect(disclosesExecutableRisk(agentPluginPackage())).toBe(true);
  });

  it("never labels a data-only package executable", () => {
    expect(isExecutableExtension(workspaceSkillPackage())).toBe(false);
    expect(disclosesExecutableRisk(workspaceSkillPackage())).toBe(false);
  });

  it("does not warn about first-party code that also runs in-process", () => {
    const builtin = { ...channelPackage(), execution: "in_process" as const };
    expect(isExecutableExtension(builtin)).toBe(true);
    expect(disclosesExecutableRisk(builtin)).toBe(false);
  });

  it("demands acknowledgement only for enable and install of a flagged package", () => {
    const pkg = agentPluginPackage();
    expect(requiresRiskAcknowledgement(pkg, "enable")).toBe(true);
    expect(requiresRiskAcknowledgement(pkg, "install")).toBe(true);
    expect(requiresRiskAcknowledgement(pkg, "disable")).toBe(false);
    expect(requiresRiskAcknowledgement(workspaceSkillPackage(), "enable")).toBe(false);
  });
});

describe("declared actions and configuration destinations", () => {
  it("turns only mutating declared actions into controls", () => {
    expect(actionableExtensionActions(["inspect", "enable", "disable", "restart_required"]))
      .toEqual(["enable", "disable"]);
    expect(actionableExtensionActions(["inspect"])).toEqual([]);
  });

  it("keeps the mutating order stable rather than echoing the payload order", () => {
    expect(actionableExtensionActions(["disable", "enable"])).toEqual(["enable", "disable"]);
  });

  it("resolves every destination the adapters actually emit", () => {
    expect(extensionConfigurationSection({ section: "apps", item: "mcp" })).toBe("apps");
    expect(extensionConfigurationSection({ section: "models", item: "openai" })).toBe("models");
    expect(extensionConfigurationSection({ section: "image", item: null })).toBe("image");
    expect(extensionConfigurationSection({ section: "voice", item: null })).toBe("voice");
    expect(extensionConfigurationSection({ section: "channels", item: "slack" })).toBe("channels");
  });

  it("degrades an unknown destination to no link at all", () => {
    expect(extensionConfigurationSection({ section: "nowhere", item: null })).toBeNull();
    expect(extensionConfigurationSection(null)).toBeNull();
  });
});

describe("package-scoped diagnostics", () => {
  it("collects the package diagnostic and every component diagnostic", () => {
    expect(diagnosticsForPackage(channelPackage()).map((row) => row.code))
      .toEqual(["channel_runtime_failed"]);
    expect(diagnosticsForPackage(workspaceSkillPackage())).toEqual([]);
  });
});

describe("filter defaults", () => {
  it("starts with every facet unset", () => {
    expect(EMPTY_EXTENSION_FILTERS).toEqual({
      query: "",
      kind: ANY_FILTER,
      source: ANY_FILTER,
      trust: ANY_FILTER,
      lifecycle: ANY_FILTER,
    });
  });
});
