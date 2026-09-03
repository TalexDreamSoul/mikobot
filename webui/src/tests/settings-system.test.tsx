import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { installedMcpPresetsFromPayload } from "@/lib/mcp-preset-events";
import {
  installSettingsViewTestHooks,
  jsonResponse,
  renderSettingsView,
  requestMutationMock,
  settingsPayload,
} from "@/tests/settings-test-utils";


const installedAnyGen = {
  name: "anygen",
  display_name: "AnyGen",
  category: "generation",
  description: "Generate docs, slides, websites and more via AnyGen cloud API",
  requires: "ANYGEN_API_KEY",
  source: "harness",
  entry_point: "cli-anything-anygen",
  install_supported: true,
  installed: true,
  available: true,
  status: "installed",
  logo_url: "https://www.google.com/s2/favicons?domain=anygen.io&sz=64",
  brand_color: "#111827",
  skill_installed: true,
};

const agentPlugin = {
  name: "ext:agent_plugin:demo-plugin",
  display_name: "Demo Plugin",
  category: "Plugin",
  description: "Control the desktop with a live preview.",
  requires: "screen-recording, accessibility",
  transport: "stdio",
  install_supported: false,
  installed: true,
  configured: true,
  enabled: false,
  available: false,
  status: "disabled",
  required_fields: [],
  source: "agent-plugin",
  extension_id: "ext:agent_plugin:demo-plugin",
  extension_revision: "revision-demo-plugin-1",
  extension_lifecycle: "disabled",
  extension_trust: "operator_trusted",
  extension_execution: "child_process",
  risk_acknowledgement_required: true,
  permissions_enforced: false,
};

const configuredPluginPrefixMcp = {
  name: "plugin-configured-mcp",
  display_name: "Configured MCP",
  category: "MCP",
  description: "A configured MCP server whose legacy name begins with plugin-.",
  requires: "",
  transport: "stdio",
  install_supported: false,
  installed: true,
  configured: true,
  enabled: false,
  available: false,
  status: "disabled",
  required_fields: [],
  source: "custom",
};

const dataOnlyAgentPlugin = {
  ...agentPlugin,
  name: "ext:agent_plugin:data-only",
  display_name: "Data Only Plugin",
  extension_id: "ext:agent_plugin:data-only",
  extension_revision: "revision-data-only-1",
  extension_execution: "data",
  risk_acknowledgement_required: false,
};

describe("Settings system domains", () => {
  installSettingsViewTestHooks();

  it("keeps enabled Agent Plugins out of MCP composer attachments", () => {
    const enabled = { ...agentPlugin, enabled: true, available: true, status: "enabled" };
    expect(installedMcpPresetsFromPayload({ presets: [enabled], installed_count: 1 })).toEqual([]);
  });

  it("requires explicit plugin risk confirmation while preserving canonical and configured MCP payloads", async () => {
    await act(() => i18n.changeLanguage("en"));
    try {
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(settingsPayload());
        if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
        if (url === "/api/settings/mcp-presets") {
          return jsonResponse({ presets: [agentPlugin, dataOnlyAgentPlugin, configuredPluginPrefixMcp], installed_count: 1 });
        }
        return jsonResponse({});
      }));
      requestMutationMock.mockImplementation(async (action: string, values: Record<string, string>) => {
        const pluginEnabled = action === "settings.mcp.enable" && values.name === agentPlugin.name;
        const pluginDisabled = action === "settings.mcp.disable" && values.name === agentPlugin.name;
        const enabled = pluginEnabled ? true : pluginDisabled ? false : agentPlugin.enabled;
        return {
          presets: [{
            ...agentPlugin,
            enabled,
            available: enabled,
            status: enabled ? "enabled" : "disabled",
          }, dataOnlyAgentPlugin, configuredPluginPrefixMcp],
          installed_count: Number(enabled),
        };
      });

      renderSettingsView();

      const pluginHeading = await screen.findByRole("heading", { name: "Demo Plugin" });
      const pluginRow = pluginHeading.closest("article");
      expect(pluginRow).not.toBeNull();
      const enablePlugin = within(pluginRow as HTMLElement).getByRole("button", { name: "Demo Plugin: Enable" });
      fireEvent.click(enablePlugin);

      const warning = await screen.findByRole("alertdialog", { name: "Enable Demo Plugin?" });
      expect(within(warning).getByText(/operator-trusted, unisolated executable code/)).toBeInTheDocument();
      await act(() => i18n.changeLanguage("zh-CN"));
      const chineseWarning = screen.getByRole("alertdialog", { name: "启用 Demo Plugin？" });
      expect(within(chineseWarning).getByText(
        "此 Agent Plugin 是由操作员信任、未隔离的可执行代码。它可能访问 nanobot 进程可见的文件、凭据、网络及其他资源。nanobot 不会验证此插件，也不会使其变得安全。",
      )).toBeInTheDocument();
      fireEvent.click(within(chineseWarning).getByRole("button", { name: "取消" }));
      expect(requestMutationMock).not.toHaveBeenCalled();

      await act(() => i18n.changeLanguage("en"));
      fireEvent.click(enablePlugin);
      const confirmation = await screen.findByRole("alertdialog", { name: "Enable Demo Plugin?" });
      fireEvent.click(within(confirmation).getByRole("button", { name: "Enable plugin" }));
      await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.mcp.enable",
        {
          name: "ext:agent_plugin:demo-plugin",
          extension_id: "ext:agent_plugin:demo-plugin",
          expected_revision: "revision-demo-plugin-1",
          risk_acknowledged: "true",
        },
        20_000,
      ));
      expect(requestMutationMock).toHaveBeenCalledTimes(1);

      const enabledButton = await screen.findByRole("button", { name: "Demo Plugin: Enabled" });
      fireEvent.pointerDown(enabledButton, { button: 0, ctrlKey: false });
      fireEvent.click(await screen.findByRole("menuitem", { name: "Disable" }));
      await waitFor(() => expect(requestMutationMock).toHaveBeenLastCalledWith(
        "settings.mcp.disable",
        {
          name: "ext:agent_plugin:demo-plugin",
          extension_id: "ext:agent_plugin:demo-plugin",
          expected_revision: "revision-demo-plugin-1",
        },
        20_000,
      ));
      expect(requestMutationMock).toHaveBeenCalledTimes(2);
      const dataOnlyHeading = await screen.findByRole("heading", { name: "Data Only Plugin" });
      const dataOnlyRow = dataOnlyHeading.closest("article");
      expect(dataOnlyRow).not.toBeNull();
      fireEvent.click(within(dataOnlyRow as HTMLElement).getByRole("button", { name: "Data Only Plugin: Enable" }));
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      await waitFor(() => expect(requestMutationMock).toHaveBeenLastCalledWith(
        "settings.mcp.enable",
        {
          name: "ext:agent_plugin:data-only",
          extension_id: "ext:agent_plugin:data-only",
          expected_revision: "revision-data-only-1",
        },
        20_000,
      ));
      expect(requestMutationMock).toHaveBeenCalledTimes(3);

      fireEvent.click(screen.getByRole("button", { name: "MCP" }));
      const configuredHeading = await screen.findByRole("heading", { name: "Configured MCP" });
      const configuredRow = configuredHeading.closest("article");
      expect(configuredRow).not.toBeNull();
      fireEvent.click(within(configuredRow as HTMLElement).getByRole("button", { name: "Configured MCP: Enable" }));
      await waitFor(() => expect(requestMutationMock).toHaveBeenLastCalledWith(
        "settings.mcp.enable",
        { name: "plugin-configured-mcp" },
        20_000,
      ));
      expect(requestMutationMock).toHaveBeenCalledTimes(4);
    } finally {
      await act(() => i18n.changeLanguage("en"));
    }
  });

  it("binds plugin confirmation to the opened revision and restores focus on cancel", async () => {
    await act(() => i18n.changeLanguage("en"));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const listedPlugin = { ...agentPlugin };
    const replacementPlugin = {
      ...agentPlugin,
      display_name: "Replacement Plugin",
      extension_revision: "revision-demo-plugin-2",
    };
    const refreshingMcp = {
      ...configuredPluginPrefixMcp,
      name: "refreshing-mcp",
      display_name: "Refreshing MCP",
      enabled: undefined,
      runtime_status: "connecting",
    };
    let catalogPass = 0;
    try {
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(settingsPayload());
        if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
        if (url === "/api/settings/mcp-presets") {
          const plugin = catalogPass++ === 0 ? listedPlugin : replacementPlugin;
          return jsonResponse({ presets: [plugin, refreshingMcp], installed_count: 1 });
        }
        return jsonResponse({});
      }));
      requestMutationMock.mockResolvedValue({
        presets: [replacementPlugin, refreshingMcp],
        installed_count: 1,
      });

      renderSettingsView();

      const pluginHeading = await screen.findByRole("heading", { name: "Demo Plugin" });
      const enablePlugin = within(pluginHeading.closest("article") as HTMLElement).getByRole(
        "button",
        { name: "Demo Plugin: Enable" },
      );
      fireEvent.click(enablePlugin);
      const warning = await screen.findByRole("alertdialog", { name: "Enable Demo Plugin?" });
      const cancel = within(warning).getByRole("button", { name: "Cancel" });
      await waitFor(() => expect(cancel).toHaveFocus());
      fireEvent.keyDown(document, { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      await waitFor(() => expect(enablePlugin).toHaveFocus());

      fireEvent.click(enablePlugin);
      await screen.findByRole("alertdialog", { name: "Enable Demo Plugin?" });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      await screen.findByText("Replacement Plugin", { selector: "h3" });
      expect(screen.getByRole("alertdialog", { name: "Enable Demo Plugin?" })).toBeInTheDocument();
      fireEvent.click(within(screen.getByRole("alertdialog", { name: "Enable Demo Plugin?" })).getByRole("button", { name: "Enable plugin" }));
      await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.mcp.enable",
        {
          name: "ext:agent_plugin:demo-plugin",
          extension_id: "ext:agent_plugin:demo-plugin",
          expected_revision: "revision-demo-plugin-1",
          risk_acknowledged: "true",
        },
        20_000,
      ));
    } finally {
      vi.useRealTimers();
      await act(() => i18n.changeLanguage("en"));
    }
  });


  it("does not show the Settings kicker on the standalone Automations surface", async () => {
    const onBackToChat = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(settingsPayload());
      if (url === "/api/webui/automations") return jsonResponse({ jobs: [] });
      return jsonResponse({});
    }));

    renderSettingsView({
      initialSection: "automations",
      initialSettings: settingsPayload(),
      showSidebar: false,
      onBackToChat,
    });

    expect(screen.getByRole("heading", { name: "Automations" })).toBeInTheDocument();
    expect(await screen.findByText("No automations yet.")).toBeInTheDocument();
    expect(screen.queryByText("Settings")).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("Search task, message, linked chat, or schedule"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open a chat" }));
    expect(onBackToChat).toHaveBeenCalledTimes(1);
  });

  it("offers a way out of an empty automations filter", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(settingsPayload());
      if (url === "/api/webui/automations") {
        return jsonResponse({
          jobs: [{
            id: "job-1",
            name: "Daily summary",
            enabled: true,
            schedule: { kind: "cron", expr: "0 9 * * *" },
            payload: { message: "Summarize the day" },
            state: {},
          }],
        });
      }
      return jsonResponse({});
    }));

    renderSettingsView({
      initialSection: "automations",
      initialSettings: settingsPayload(),
      showSidebar: false,
    });

    expect(await screen.findByRole("heading", { name: "Daily summary" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Paused 0" }));
    expect(await screen.findByText("No automations match this view.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByRole("heading", { name: "Daily summary" })).toBeInTheDocument();
  });

  it("coalesces focus refreshes while automations are already loading", async () => {
    let resolveAutomations!: (response: Response) => void;
    const pendingAutomations = new Promise<Response>((resolve) => {
      resolveAutomations = resolve;
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(settingsPayload());
      if (url === "/api/webui/automations") return pendingAutomations;
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSettingsView({
      initialSection: "automations",
      initialSettings: settingsPayload(),
      showSidebar: false,
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([input]) => (
        String(input) === "/api/webui/automations"
      ))).toHaveLength(1);
    });
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("focus"));

    expect(fetchMock.mock.calls.filter(([input]) => (
      String(input) === "/api/webui/automations"
    ))).toHaveLength(1);
    await act(async () => {
      resolveAutomations(jsonResponse({ jobs: [] }));
      await pendingAutomations;
    });
  });

  it("does not start an unavailable API service until its install is confirmed", async () => {
    const base = settingsPayload();
    const stopped = {
      installed: false,
      running: false,
      managed: false,
      host: "127.0.0.1",
      port: 8900,
      timeout: 120,
      api_key_hint: null,
      endpoint: "http://127.0.0.1:8900/v1",
      command: "nanobot serve",
    };
    const apiFeature = {
      name: "api",
      display_name: "API",
      type: "feature",
      extension_id: "ext:optional_feature:api",
      extension_revision: "api-package-revision",
      extension_actions: ["install"],
      extension_lifecycle: "unavailable",
      action_target_id: "ext:optional_feature:api",
      action_target_revision: "api-package-revision",
      action_target_actions: ["install"],
      enabled: false,
      installed: false,
      ready: false,
      status: "missing_dependency",
      install_supported: true,
      requires_restart: false,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(base);
      if (url === "/api/settings/api-service") return jsonResponse(stopped);
      if (url === "/api/settings/nanobot-features") {
        return jsonResponse({ features: [apiFeature], enabled_count: 0 });
      }
      return jsonResponse({});
    }));
    requestMutationMock.mockResolvedValue({ ...stopped, installed: true, running: true, managed: true });

    renderSettingsView({ initialSection: "runtime", initialSettings: base, showSidebar: true });

    const startButton = await screen.findByRole("button", { name: "Start API server" });
    await waitFor(() => expect(startButton).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Local network" }));
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "9137" } });
    fireEvent.change(screen.getByPlaceholderText("Enter an API key"), {
      target: { value: "edited-network-secret" },
    });
    fireEvent.click(startButton);
    const confirmation = await screen.findByRole("dialog", { name: "Install support for API?" });
    fireEvent.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    expect(requestMutationMock).not.toHaveBeenCalled();

    fireEvent.click(startButton);
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Install support for API?" })).getByRole(
      "button",
      { name: "Install and enable" },
    ));
    await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
      "settings.api_service.start",
      {
        host: "0.0.0.0",
        port: 9137,
        timeout: 120,
        api_key: "edited-network-secret",
        extension_id: "ext:optional_feature:api",
        expected_revision: "api-package-revision",
        risk_acknowledged: true,
      },
      150_000,
    ));
  });

  it("starts an installed API service with its exact target but no acknowledgement", async () => {
    const base = settingsPayload();
    const stopped = {
      installed: false,
      running: false,
      managed: false,
      host: "127.0.0.1",
      port: 8900,
      timeout: 120,
      api_key_hint: null,
      endpoint: "http://127.0.0.1:8900/v1",
      command: "nanobot serve",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(base);
      if (url === "/api/settings/api-service") return jsonResponse(stopped);
      if (url === "/api/settings/nanobot-features") {
        return jsonResponse({
          features: [{
            name: "api",
            display_name: "API",
            type: "feature",
            extension_id: "ext:optional_feature:api",
            extension_revision: "api-package-revision",
            action_target_id: "ext:optional_feature:api",
            action_target_revision: "api-package-revision",
            action_target_actions: ["install"],
            enabled: false,
            installed: true,
            ready: true,
            status: "installed",
            install_supported: true,
            requires_restart: false,
          }],
          enabled_count: 0,
        });
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);
    requestMutationMock.mockResolvedValueOnce({
      ...stopped,
      installed: true,
      running: true,
      managed: true,
    });

    renderSettingsView({ initialSection: "runtime", initialSettings: base, showSidebar: true });

    const startButton = await screen.findByRole("button", { name: "Start API server" });
    await waitFor(() => expect(startButton).toBeEnabled());
    fireEvent.click(startButton);
    await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
      "settings.api_service.start",
      {
        host: "127.0.0.1",
        port: 8900,
        timeout: 120,
        extension_id: "ext:optional_feature:api",
        expected_revision: "api-package-revision",
      },
      150_000,
    ));
  });

  it("shows a visible uninstall button for installed CLI apps and calls uninstall", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") {
        return jsonResponse(settingsPayload());
      }
      if (url === "/api/settings/cli-apps") {
        return jsonResponse({
          apps: [installedAnyGen],
          installed_count: 1,
          catalog_updated_at: "2026-04-18",
        });
      }
      if (url === "/api/settings/mcp-presets") {
        return jsonResponse({ presets: [], installed_count: 0 });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    requestMutationMock.mockResolvedValueOnce({
      apps: [{ ...installedAnyGen, installed: false, status: "available" }],
      installed_count: 0,
      catalog_updated_at: "2026-04-18",
      last_action: {
        ok: true,
        message: "Uninstalled CLI for AnyGen.",
        still_available: false,
      },
    });

    renderSettingsView();

    expect(screen.queryByRole("heading", { name: "Apps" })).not.toBeInTheDocument();
    expect(await screen.findByText("AnyGen")).toBeInTheDocument();
    const uninstall = screen.getByRole("button", { name: "Uninstall app" });

    fireEvent.click(uninstall);

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.cli_app.uninstall",
        { name: "anygen" },
        20_000,
      ),
    );
    expect(await screen.findByText("Uninstalled CLI for AnyGen.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByText("Uninstalled CLI for AnyGen.")).not.toBeInTheDocument();
  });

  it("keeps runtime dependencies out of Apps and explains chat mentions", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(settingsPayload());
      if (url === "/api/settings/cli-apps") {
        return jsonResponse({
          apps: [{ ...installedAnyGen, installed: false, status: "available" }],
          installed_count: 0,
        });
      }
      if (url === "/api/settings/mcp-presets") {
        return jsonResponse({ presets: [], installed_count: 0 });
      }
      if (url === "/api/settings/nanobot-features") {
        return jsonResponse({
          features: [
            {
              name: "api",
              display_name: "Api",
              type: "feature",
              enabled: true,
              installed: true,
              ready: true,
              status: "enabled",
              install_supported: true,
              requires_restart: true,
            },
          ],
          enabled_count: 1,
        });
      }
      return jsonResponse({});
    }));

    renderSettingsView({ initialSection: "apps" });

    expect(await screen.findByText("AnyGen")).toBeInTheDocument();
    expect(
      screen.queryByText("Add tools to nanobot, then @ them in chat."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ready" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Apps" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "MCP" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Plugins" })).not.toBeInTheDocument();
    expect(screen.queryByText("Api")).not.toBeInTheDocument();
    expect(screen.queryByText("0 ready")).not.toBeInTheDocument();
  });

  it("shows nanobot optional features and enables one", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(settingsPayload());
      if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
      if (url === "/api/settings/mcp-presets") return jsonResponse({ presets: [], installed_count: 0 });
      if (url === "/api/settings/nanobot-features") {
        return jsonResponse({
          features: [{
            name: "matrix",
            display_name: "Matrix",
            webui: "webui/index.ts",
            type: "channel",
            extension_id: "ext:channel_package:matrix",
            extension_revision: "matrix-package-revision",
            extension_actions: ["inspect"],
            extension_lifecycle: "unavailable",
            action_target_id: "ext:channel_package:matrix/channel:default",
            action_target_revision: "matrix-default-revision",
            action_target_actions: ["configure", "enable", "install"],
            enabled: false,
            installed: false,
            ready: false,
            status: "missing_dependency",
            install_supported: true,
            requires_restart: true,
          }],
          enabled_count: 0,
        });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    requestMutationMock.mockImplementation(async (action: string) => {
      if (action === "settings.feature.enable") {
        return {
          features: [{
            name: "matrix",
            display_name: "Matrix",
            webui: "webui/index.ts",
            type: "channel",
            extension_id: "ext:channel_package:matrix",
            extension_revision: "matrix-package-revision-2",
            extension_actions: ["inspect"],
            extension_lifecycle: "enabled",
            action_target_id: "ext:channel_package:matrix/channel:default",
            action_target_revision: "matrix-default-revision-2",
            action_target_actions: ["configure", "disable"],
            enabled: true,
            running: true,
            runtime_status: "running",
            installed: true,
            ready: true,
            status: "enabled",
            install_supported: true,
            requires_restart: true,
          }],
          enabled_count: 1,
          last_action: { ok: true, message: "Enabled channel 'matrix'", enabled: true },
        };
      }
      if (action === "settings.feature.disable") {
        return {
          features: [{
            name: "matrix",
            display_name: "Matrix",
            webui: "webui/index.ts",
            type: "channel",
            extension_id: "ext:channel_package:matrix",
            extension_revision: "matrix-package-revision-3",
            extension_actions: ["inspect"],
            extension_lifecycle: "disabled",
            action_target_id: "ext:channel_package:matrix/channel:default",
            action_target_revision: "matrix-default-revision-3",
            action_target_actions: ["configure", "enable"],
            enabled: false,
            installed: true,
            ready: false,
            status: "not_enabled",
            install_supported: true,
            requires_restart: true,
          }],
          enabled_count: 0,
          requires_restart: true,
          last_action: { ok: true, message: "Disabled channel 'matrix'", enabled: false },
        };
      }
      return settingsPayload();
    });

    renderSettingsView({ initialSection: "channels" });

    const matrixRow = await screen.findByRole("button", { name: "View Matrix settings" });
    expect(matrixRow).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByText("Matrix")).toHaveLength(2);
    expect(screen.getAllByText("Use nanobot from Matrix rooms.")).toHaveLength(2);
    expect(screen.queryByText(/Enabling Nanobot features may install Python packages/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Matrix channel" }));
    expect(screen.getByRole("dialog", { name: "Install support for Matrix?" })).toBeInTheDocument();
    expect(screen.getByText("nanobot will add what Matrix needs, then turn it on. Continue?")).toBeInTheDocument();
    expect(requestMutationMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Install and enable" }));

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.feature.enable",
        {
          name: "matrix",
          extension_id: "ext:channel_package:matrix/channel:default",
          expected_revision: "matrix-default-revision",
          risk_acknowledged: true,
        },
        150_000,
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Matrix channel" })).toHaveAttribute("aria-checked", "true"),
    );
    expect(screen.queryByText("Enabled channel 'matrix'")).not.toBeInTheDocument();
    expect(screen.queryByText("Restart nanobot to apply updated channel support.")).not.toBeInTheDocument();
    expect(screen.getAllByText("On").length).toBeGreaterThan(0);

    expect(screen.getByLabelText("Homeserver")).toBeInTheDocument();
    expect(screen.getByLabelText("User ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Device ID")).toBeInTheDocument();
    expect(screen.queryByText("channels.matrix.homeserver")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("switch", { name: "Matrix channel" }));

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.feature.disable",
        {
          name: "matrix",
          extension_id: "ext:channel_package:matrix/channel:default",
          expected_revision: "matrix-default-revision-2",
        },
        20_000,
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Matrix channel" })).toHaveAttribute("aria-checked", "false"),
    );
    expect(screen.queryByText("Disabled channel 'matrix'")).not.toBeInTheDocument();
  });
});
