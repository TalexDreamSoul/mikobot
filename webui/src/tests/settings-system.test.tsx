import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { installedMcpPresetsFromPayload } from "@/lib/mcp-preset-events";
import { SkillsCatalogSettings } from "@/components/settings/SkillsCatalogSettings";
import { ClientProvider } from "@/providers/ClientProvider";
import { __clearLogoFallbackCacheForTests } from "@/hooks/useLogoFallback";
import { requestMutationMock, jsonResponse, settingsPayload, renderSettingsView, installSettingsViewTestHooks } from "@/tests/settings-test-utils";


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

function selectAutomationFilter(name: string) {
  fireEvent.pointerDown(screen.getByRole("button", { name: /^Filter/ }), { button: 0, ctrlKey: false });
  fireEvent.click(screen.getByRole("menuitemradio", { name }));
}

describe("Settings system domains", () => {
  installSettingsViewTestHooks();

  it("asks before leaving with pending changes and lets the user restart later", () => {
    const leave = vi.fn();
    renderSettingsView({ initialSection: "runtime", initialSettings: {
      ...settingsPayload(), requires_restart: true, restart_required_sections: ["runtime"],
    }, onBackToChat: leave });
    fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
    const dialog = screen.getByRole("dialog", { name: "Restart before leaving?" });
    expect(leave).not.toHaveBeenCalled();
    expect(within(dialog).getByText("Your changes are saved. Restart to apply them.")).toBeVisible();
    expect(within(dialog).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Restart later", "Restart",
    ]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Restart later" }));
    expect(leave).toHaveBeenCalledTimes(1);
    expect(requestMutationMock).not.toHaveBeenCalled();
  });

  it("restarts from the exit prompt", () => {
    const restart = vi.fn();
    renderSettingsView({ initialSection: "runtime", initialSettings: {
      ...settingsPayload(), requires_restart: true,
    }, onRestart: restart });
    fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Restart", exact: true }));
    expect(restart).toHaveBeenCalledTimes(1);
  });

  it("leaves directly when no restart is pending", () => {
    const leave = vi.fn();
    renderSettingsView({ initialSection: "runtime", initialSettings: settingsPayload(), onBackToChat: leave });
    fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
    expect(leave).toHaveBeenCalledTimes(1);
  });

  it("keeps one restart action and pending notice in the sidebar across settings pages", async () => {
    renderSettingsView({
      initialSection: "runtime",
      initialSettings: {
        ...settingsPayload(),
        requires_restart: true,
        restart_required_sections: ["runtime", "image"],
      },
    });
    const sidebar = screen.getByRole("complementary");
    const restart = within(sidebar).getByRole("button", { name: "Restart", exact: true });
    for (const section of ["Capabilities", "Models", "Advanced", "System"]) {
      fireEvent.click(within(sidebar).getByRole("button", { name: section, exact: true }));
      await waitFor(() => {
        expect(screen.getAllByRole("button", { name: "Restart", exact: true })).toEqual([restart]);
        expect(screen.getAllByText("Saved. Restart to apply changes.")).toHaveLength(1);
        expect(within(sidebar).getByText("Saved. Restart to apply changes.")).toBeVisible();
      });
    }
  });



  it("no longer renders an Agent Plugin row or its lifecycle control on the Apps page", async () => {
    // The gateway no longer injects these rows. This feeds one anyway, the exact shape
    // the removed projection produced, so the assertion proves the page stopped owning
    // Agent Plugin lifecycle rather than merely that the payload changed.
    await act(() => i18n.changeLanguage("en"));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(settingsPayload());
      if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
      if (url === "/api/settings/mcp-presets") {
        return jsonResponse({
          presets: [agentPlugin, configuredPluginPrefixMcp],
          installed_count: 1,
        });
      }
      return jsonResponse({});
    }));
    // Echo the catalog back unchanged so both rows survive the first action and the
    // second click is asserted against a real row rather than an emptied list.
    requestMutationMock.mockImplementation(async () => ({
      presets: [agentPlugin, configuredPluginPrefixMcp],
      installed_count: 1,
    }));

    renderSettingsView();

    fireEvent.click(await screen.findByRole("button", { name: "MCP" }));

    // A configured MCP server whose name merely begins with "plugin-" is ordinary MCP
    // and must still be enableable from here.
    const configuredHeading = await screen.findByRole("heading", { name: "Configured MCP" });
    fireEvent.click(
      within(configuredHeading.closest("article") as HTMLElement).getByRole("button", {
        name: "Configured MCP: Enable",
      }),
    );
    await waitFor(() => expect(requestMutationMock).toHaveBeenLastCalledWith(
      "settings.mcp.enable",
      { name: "plugin-configured-mcp" },
      20_000,
    ));

    // The plugin row is now an ordinary, inert catalog entry: acting on it opens no
    // acknowledgement dialog and sends no canonical extension identity through this
    // route, which is what makes `settings.extension.action` its only lifecycle path.
    const pluginHeading = screen.getByRole("heading", { name: "Demo Plugin" });
    fireEvent.click(
      within(pluginHeading.closest("article") as HTMLElement).getByRole("button", {
        name: "Demo Plugin: Enable",
      }),
    );
    await waitFor(() => expect(requestMutationMock).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    for (const [, values] of requestMutationMock.mock.calls) {
      expect(values).not.toHaveProperty("extension_id");
      expect(values).not.toHaveProperty("expected_revision");
      expect(values).not.toHaveProperty("risk_acknowledged");
    }
  });

  it("keeps the MCP composer attachment list to configured servers", () => {
    const configured = { ...configuredPluginPrefixMcp, enabled: true, available: true };
    expect(
      installedMcpPresetsFromPayload({ presets: [configured], installed_count: 1 }).map(
        (preset) => preset.name,
      ),
    ).toEqual(["plugin-configured-mcp"]);
  });

  it.each(["apps", "skills", "automations", "channels"] as const)(
    "uses the conversation-width content frame for the standalone %s page",
    (initialSection) => {
      renderSettingsView({ initialSection, initialSettings: settingsPayload(), showSidebar: false });

      expect(screen.getByTestId("settings-section-transition")).toHaveClass("settings-feature-page");
    },
  );

  it("keeps the regular settings page at its existing width", () => {
    renderSettingsView({ initialSection: "models", initialSettings: settingsPayload() });

    expect(screen.getByTestId("settings-section-transition")).toHaveClass("settings-grid");
    expect(screen.getByTestId("settings-section-transition")).not.toHaveClass("settings-feature-page");
  });

  it.each(["apps", "skills", "automations", "channels"] as const)(
    "keeps the standalone %s semantic heading and follows the main navigation state",
    (initialSection) => {
      renderSettingsView({ initialSection, initialSettings: settingsPayload(), showSidebar: false, mainNavigationExpanded: true });
      const page = screen.getByTestId("settings-section-transition");
      expect(page).toHaveAttribute("data-main-navigation-expanded", "true");
      const heading = within(page).getByRole("heading", { level: 1 });
      expect(heading.parentElement).toHaveClass("settings-feature-header");
      expect(heading).not.toHaveAttribute("aria-hidden");
    },
  );

  it("does not hide a standalone heading when the navigation is collapsed or absent", () => {
    renderSettingsView({ initialSection: "skills", initialSettings: settingsPayload(), showSidebar: false, mainNavigationExpanded: false });
    expect(screen.getByTestId("settings-section-transition")).toHaveAttribute("data-main-navigation-expanded", "false");
    expect(screen.getByRole("heading", { name: "Skills", level: 1 })).toBeVisible();
  });

  it("uses the inline section heading layout to align Apps titles and counts", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(settingsPayload());
      if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [installedAnyGen], installed_count: 1 });
      if (url === "/api/settings/mcp-presets") return jsonResponse({ presets: [], installed_count: 0 });
      return jsonResponse({});
    }));
    renderSettingsView({ initialSection: "apps", initialSettings: settingsPayload() });
    await screen.findByText("AnyGen");
    const title = screen.getByRole("heading", { name: "Tools" });
    expect(title.parentElement).toHaveClass("settings-section-heading");
    expect(title.nextElementSibling).toHaveTextContent("1");
    expect(title.nextElementSibling).toHaveClass("tabular-nums");
    expect(title.nextElementSibling).not.toHaveClass("rounded-full", "bg-muted");

    fireEvent.click(screen.getByRole("button", { name: "MCP", exact: true }));
    const mcpTitle = screen.getByRole("heading", { name: "MCP tools" });
    expect(mcpTitle.parentElement).toHaveClass("settings-section-heading");
    expect(mcpTitle.nextElementSibling).toHaveTextContent("0");
  });

  it.each(["cli", "mcp"] as const)(
    "fills the rounded %s app icon without an inset tile and preserves its fallback",
    async (kind) => {
      __clearLogoFallbackCacheForTests();
      const name = kind === "cli" ? "AnyGen" : "Linear";
      const logoUrl = `/test-${kind}-logo.svg`;
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(settingsPayload());
        if (url === "/api/settings/cli-apps") {
          return jsonResponse({ apps: [{ ...installedAnyGen, logo_url: logoUrl }], installed_count: 1 });
        }
        if (url === "/api/settings/mcp-presets") {
          return jsonResponse({ presets: [{
            ...agentPlugin,
            name: "linear",
            display_name: "Linear",
            source: "builtin",
            enabled: true,
            logo_url: logoUrl,
          }], installed_count: 1 });
        }
        return jsonResponse({});
      }));
      renderSettingsView({ initialSection: "apps", initialSettings: settingsPayload() });
      await screen.findByText("AnyGen");
      if (kind === "mcp") fireEvent.click(screen.getByRole("button", { name: "MCP", exact: true }));
      const row = (await screen.findByText(name)).closest("article")!;
      const image = row.querySelector("img")!;
      expect(image).toHaveAttribute("src", logoUrl);
      expect(image).toHaveClass("h-full", "w-full", "object-contain");
      fireEvent.load(image);
      expect(image.parentElement).toHaveClass("h-9", "w-9", "overflow-hidden", "rounded-[10px]");
      for (const tileClass of ["border", "bg-background", "bg-muted"]) {
        expect(image.parentElement).not.toHaveClass(tileClass);
      }

      fireEvent.error(image);
      expect(row.querySelector("img")).toBeNull();
      const fallback = within(row).getByText(name[0], { exact: true });
      expect(fallback).toBeVisible();
      expect(fallback.closest(".h-9")).toHaveClass("w-9", "rounded-[10px]");
    },
  );

  it("keeps skill group labels natural without changing grouping or filtering", () => {
    render(<ClientProvider client={{} as never} token="tok">
      <SkillsCatalogSettings skills={[
        { name: "pr-review", description: "Review pull requests", source: "workspace", available: true },
        { name: "cron", description: "Schedule reminders", source: "builtin", available: true },
        { name: "team-guide", description: "Team conventions", source: "team", available: true },
      ]} />
    </ClientProvider>);
    for (const name of ["Custom", "Built-in", "Other"]) {
      const title = screen.getByRole("heading", { name, exact: true });
      expect(title).toHaveClass("text-[13px]", "font-medium", "leading-5");
      expect(title).not.toHaveClass("uppercase", "tracking-[0.08em]");
      expect(title.nextElementSibling).toHaveTextContent("1");
      expect(title.nextElementSibling).toHaveClass("leading-5");
    }
    fireEvent.change(screen.getByRole("textbox", { name: "Search installed skills" }), { target: { value: "cron" } });
    expect(screen.queryByRole("heading", { name: "Custom" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Other" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Built-in" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Open details for cron" })).toBeVisible();
  });

  it("creates an automation from the standalone empty state", async () => {
    const onBackToChat = vi.fn();
    const onStartAutomationChat = vi.fn().mockResolvedValue(true);
    const settings = settingsPayload();
    settings.providers = [{
      name: "openai",
      label: "OpenAI",
      configured: true,
    }];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(settings);
      if (url === "/api/webui/automations") return jsonResponse({ jobs: [] });
      return jsonResponse({});
    }));

    renderSettingsView({
      initialSection: "automations",
      initialSettings: settings,
      showSidebar: false,
      onBackToChat,
      onStartAutomationChat,
    });

    expect(screen.getByRole("heading", { name: "Automations" })).toBeInTheDocument();
    await waitFor(() => expect(document.querySelectorAll(".automation-calendar-day").length).toBeGreaterThanOrEqual(35));
    expect(screen.queryByText("No automations yet.")).not.toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: "Describe an automation" });
    fireEvent.change(input, { target: { value: "Summarize updates every weekday at 9" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(onStartAutomationChat).toHaveBeenCalledWith(
      "Summarize updates every weekday at 9",
      undefined,
      { intent: "create_automation" },
      "primary",
    ));
    fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
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
            state: { next_run_at_ms: Date.now() + 60_000 },
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

    expect(await screen.findByRole("button", { name: /Daily summary/ })).toBeInTheDocument();
    selectAutomationFilter("Disabled 0");
    expect(screen.queryByRole("button", { name: /Daily summary/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Today" })).toBeVisible();
    selectAutomationFilter("All 1");
    expect(await screen.findByRole("button", { name: /Daily summary/ })).toBeInTheDocument();
  });

  it("reveals status filters on demand and keeps every option selectable", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/settings") return jsonResponse(settingsPayload());
      if (String(input) === "/api/webui/automations") {
        return jsonResponse({ jobs: [
          {
            id: "heartbeat", name: "heartbeat", enabled: true, protected: true,
            schedule: { kind: "every", every_ms: 1_800_000 },
            payload: { message: "System-managed automation" }, state: { next_run_at_ms: Date.now() + 60_000 },
          },
          {
            id: "paused-job", name: "Paused reminder", enabled: false,
            schedule: { kind: "every", every_ms: 86_400_000 },
            payload: { message: "Check the repo" },
            state: { last_run_at_ms: Date.now() - 60_000, last_status: "ok" },
          },
        ] });
      }
      return jsonResponse({});
    }));
    renderSettingsView({ initialSection: "automations", initialSettings: settingsPayload(), showSidebar: false });

    const trigger = await screen.findByRole("button", { name: "Filter", exact: true });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    const menu = screen.getByRole("menu", { name: "Filter" });
    expect(within(menu).getAllByRole("menuitemradio")).toHaveLength(4);
    expect(within(menu).getByRole("menuitemradio", { name: "All 1" })).toHaveAttribute("aria-checked", "true");
    expect(within(menu).queryByRole("menuitemradio", { name: /System/ })).not.toBeInTheDocument();
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: "Disabled 1" }));
    expect(screen.getByRole("button", { name: "Filter: Disabled" })).toBeVisible();
    expect(screen.getByRole("button", { name: /Paused reminder/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /heartbeat/ })).not.toBeInTheDocument();

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
    expect(screen.getByRole("button", { name: "Ready" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Apps" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "MCP" })).toBeInTheDocument();
  });

  it("installs optional channel support before enabling and disabling it", async () => {
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
            configured: true,
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
    requestMutationMock.mockImplementation(async (action: string, values: Record<string, unknown>) => {
      if (action === "settings.feature.enable") {
        const enabled = values.install_only !== true;
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
            enabled,
            running: enabled,
            runtime_status: enabled ? "running" : "stopped",
            installed: true,
            ready: true,
            status: enabled ? "enabled" : "not_enabled",
            install_supported: true,
            requires_restart: true,
          }],
          enabled_count: enabled ? 1 : 0,
          last_action: {
            ok: true,
            message: enabled ? "Enabled channel 'matrix'" : "Installed support for channel 'matrix'",
            enabled,
          },
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
    expect(matrixRow).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(matrixRow);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Matrix channel" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install Matrix" })).toHaveFocus();
    expect(requestMutationMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Install Matrix" }));

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.feature.enable",
        expect.objectContaining({ name: "matrix", install_only: true }),
        150_000,
      ),
    );
    const matrixToggle = await screen.findByRole("switch", { name: "Matrix channel" });
    expect(matrixToggle).toHaveAttribute("aria-checked", "false");
    expect(requestMutationMock).toHaveBeenCalledTimes(1);
    fireEvent.click(matrixToggle);

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.feature.enable",
        {
          name: "matrix",
          extension_id: "ext:channel_package:matrix/channel:default",
          expected_revision: "matrix-default-revision-2",
        },
        150_000,
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Matrix channel" })).toHaveAttribute("aria-checked", "true"),
    );

    expect(screen.getAllByText("On").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "View Matrix settings" }));
    expect(screen.getByLabelText("Homeserver")).toBeInTheDocument();
    expect(screen.getByLabelText("User ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Device ID")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close", exact: true }));
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
  });
});
