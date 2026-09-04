import type { ExtensionInventoryPayload, ExtensionPackage } from "@/lib/types";

/**
 * Fixtures shaped exactly like `/api/settings/extensions` rows.
 *
 * They are deliberately not derived from the component's own helpers, so a change to
 * the surface cannot quietly change what the tests believe the gateway sends.
 */

export function agentPluginPackage(): ExtensionPackage {
  return {
    id: "ext:agent_plugin:desktop",
    name: "desktop",
    display_name: "Desktop Automation",
    description: "Drives the desktop session.",
    source: "agent_plugin",
    trust: "operator_trusted",
    execution: "child_process",
    isolated: false,
    lifecycle: "disabled",
    version: "1.4.0",
    revision: "plugin-r1",
    permissions: ["filesystem.write", "network.outbound"],
    permissions_enforced: false,
    risk_acknowledgement_required: true,
    actions: ["enable", "disable"],
    configuration: { section: "apps", item: "mcp" },
    diagnostic: null,
    components: [
      {
        id: "ext:agent_plugin:desktop/mcp_server:desktop-mcp",
        package_id: "ext:agent_plugin:desktop",
        kind: "mcp_server",
        name: "desktop-mcp",
        display_name: "Desktop MCP",
        description: "",
        capabilities: ["screen.capture"],
        execution: "child_process",
        lifecycle: "disabled",
        revision: "component-r1",
        actions: ["inspect"],
        configuration: { section: "apps", item: "mcp" },
        diagnostic: null,
      },
    ],
  };
}

export function channelPackage(): ExtensionPackage {
  return {
    id: "ext:channel_package:slack",
    name: "slack",
    display_name: "Slack",
    description: "",
    source: "channel_package",
    trust: "first_party",
    execution: "in_process",
    isolated: null,
    lifecycle: "failed",
    version: null,
    revision: "slack-r7",
    permissions: [],
    permissions_enforced: false,
    risk_acknowledgement_required: false,
    actions: ["inspect", "reconnect"],
    configuration: { section: "channels", item: "slack" },
    diagnostic: {
      owner_id: "ext:channel_package:slack",
      code: "channel_runtime_failed",
      message: "Channel runtime failed. Check gateway logs.",
    },
    components: [
      {
        id: "ext:channel_package:slack/channel:default",
        package_id: "ext:channel_package:slack",
        kind: "channel",
        name: "default",
        display_name: "Slack",
        description: "",
        capabilities: ["text", "images"],
        execution: "in_process",
        lifecycle: "failed",
        revision: "slack-default-r7",
        actions: ["inspect", "reconnect"],
        configuration: { section: "channels", item: "slack" },
        diagnostic: null,
      },
    ],
  };
}

export function workspaceSkillPackage(): ExtensionPackage {
  return {
    id: "ext:workspace:meeting-notes",
    name: "meeting-notes",
    display_name: "Meeting Notes",
    description: "Formats meeting notes.",
    source: "workspace",
    trust: "workspace_content",
    execution: "data",
    isolated: null,
    lifecycle: "enabled",
    version: null,
    revision: "skill-r1",
    permissions: [],
    permissions_enforced: false,
    risk_acknowledgement_required: false,
    actions: [],
    configuration: null,
    diagnostic: null,
    components: [
      {
        id: "ext:workspace:meeting-notes/skill:meeting-notes",
        package_id: "ext:workspace:meeting-notes",
        kind: "skill",
        name: "meeting-notes",
        display_name: "Meeting Notes",
        description: "",
        capabilities: [],
        execution: "data",
        lifecycle: "enabled",
        revision: null,
        actions: [],
        configuration: null,
        diagnostic: null,
      },
    ],
  };
}

export function extensionInventory(
  overrides: Partial<ExtensionInventoryPayload> = {},
): ExtensionInventoryPayload {
  return {
    available: true,
    packages: [agentPluginPackage(), channelPackage(), workspaceSkillPackage()],
    diagnostics: [],
    ...overrides,
  };
}
