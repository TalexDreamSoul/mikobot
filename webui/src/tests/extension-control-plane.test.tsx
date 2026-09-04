import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExtensionsSettings } from "@/components/settings/extensions/ExtensionsSettings";
import i18n from "@/i18n";
import { ClientProvider } from "@/providers/ClientProvider";
import {
  agentPluginPackage,
  channelPackage,
  extensionInventory,
  workspaceSkillPackage,
} from "@/tests/extension-fixtures";
import {
  installSettingsViewTestHooks,
  renderSettingsView,
  settingsPayload,
} from "@/tests/settings-test-utils";
import type { ExtensionInventoryPayload } from "@/lib/types";

const requestMutation = vi.fn();
const onOpenSection = vi.fn();

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

function stubInventory(...payloads: ExtensionInventoryPayload[]) {
  let call = 0;
  const fetchMock = vi.fn(async () => {
    const payload = payloads[Math.min(call, payloads.length - 1)];
    call += 1;
    return jsonResponse(payload);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderSurface() {
  render(
    <ClientProvider client={{ requestMutation } as never} token="tok">
      <ExtensionsSettings onOpenSection={onOpenSection} />
    </ClientProvider>,
  );
}

async function openPackage(name: string) {
  await userEvent.setup().click(await screen.findByRole("button", { name: new RegExp(name) }));
}

function detail() {
  return screen.getByRole("region", { name: "Extension details" });
}

beforeEach(() => {
  requestMutation.mockReset();
  onOpenSection.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Extensions inventory", () => {
  it("lists every package the gateway reported, in the gateway's order", async () => {
    stubInventory(extensionInventory());
    renderSurface();

    const list = await screen.findByRole("list", { name: "Installed extension packages" });
    const rows = within(list).getAllByRole("button");
    expect(rows.map((row) => row.textContent?.split("\n")[0])).toEqual([
      expect.stringContaining("Desktop Automation"),
      expect.stringContaining("Slack"),
      expect.stringContaining("Meeting Notes"),
    ]);
    expect(screen.getByText("Showing 3 of 3 packages")).toBeInTheDocument();
  });

  it("distinguishes an absent registry from an empty host", async () => {
    stubInventory({ available: false, packages: [], diagnostics: [] });
    renderSurface();

    expect(await screen.findByText(/composed no extension registry/)).toBeInTheDocument();
    expect(screen.queryByText("This gateway reports no extension packages.")).toBeNull();
  });

  it("distinguishes an empty host from a filtered-away result", async () => {
    stubInventory({ available: true, packages: [], diagnostics: [] });
    renderSurface();

    expect(await screen.findByText("This gateway reports no extension packages.")).toBeInTheDocument();
    expect(screen.queryByText("No package matches these filters.")).toBeNull();
  });

  it("surfaces a broken adapter as an attributable region while other rows stay listed", async () => {
    stubInventory(
      extensionInventory({
        packages: [workspaceSkillPackage()],
        diagnostics: [
          {
            owner_id: "channels",
            code: "adapter_snapshot_failed",
            message: "Channel inventory failed.",
          },
        ],
      }),
    );
    renderSurface();

    const region = await screen.findByRole("alert", {
      name: "Extension families that could not report",
    });
    expect(within(region).getByText("channels")).toBeInTheDocument();
    expect(within(region).getByText("Channel inventory failed.")).toBeInTheDocument();
    expect(await screen.findByText("Showing 1 of 1 packages")).toBeInTheDocument();
  });
});

describe("Extensions filters", () => {
  it("composes a facet filter with search and reflects the visible count", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await screen.findByRole("list", { name: "Installed extension packages" });
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText("Source"), "workspace");
    expect(await screen.findByText("Showing 1 of 3 packages")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Search extensions"), "desktop");
    expect(await screen.findByText("Showing 0 of 3 packages")).toBeInTheDocument();
    expect(screen.getByText("No package matches these filters.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("Showing 3 of 3 packages")).toBeInTheDocument();
  });

  it("filters by component family, not by package name", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await screen.findByRole("list", { name: "Installed extension packages" });

    await userEvent.setup().selectOptions(screen.getByLabelText("Family"), "mcp_server");

    expect(await screen.findByText("Showing 1 of 3 packages")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Installed extension packages" });
    expect(within(list).getByText("Desktop Automation")).toBeInTheDocument();
  });
});

describe("Extensions detail disclosure", () => {
  it("renders isolation, trust, execution, and the unenforced permission fact from the payload", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Desktop Automation");

    const panel = detail();
    expect(within(panel).getByText("Not isolated")).toBeInTheDocument();
    expect(within(panel).getAllByText("Operator-trusted").length).toBeGreaterThan(0);
    expect(within(panel).getAllByText("In a child process").length).toBeGreaterThan(0);
    expect(within(panel).getByText(/Self-declared by the package and not enforced/))
      .toBeInTheDocument();
    expect(within(panel).getByText("filesystem.write")).toBeInTheDocument();
    expect(within(panel).getByText("plugin-r1")).toBeInTheDocument();
    expect(within(panel).getByRole("note")).toHaveTextContent(
      /may read files, credentials, network/,
    );
  });

  it("renders the payload's isolation claim rather than a fixed one", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Slack");

    // The channel adapter makes no isolation claim, which is not the same as isolated.
    expect(within(detail()).getByText("No isolation claim")).toBeInTheDocument();
    expect(within(detail()).queryByText("Not isolated")).toBeNull();
  });

  it("does not label a data-only package executable", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Meeting Notes");

    const panel = detail();
    expect(within(panel).queryByRole("note")).toBeNull();
    expect(within(panel).getByText(/Data only — this package runs no code/)).toBeInTheDocument();
    expect(within(panel).getByText("This package declares no permissions.")).toBeInTheDocument();
  });

  it("renders only the actions the adapter declared", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Slack");

    const panel = detail();
    expect(within(panel).getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Enable" })).toBeNull();
    expect(within(panel).queryByRole("button", { name: "Disable" })).toBeNull();
    expect(within(panel).queryByRole("button", { name: "Uninstall" })).toBeNull();
  });

  it("offers no lifecycle control for a package whose owner declared none", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Meeting Notes");

    expect(
      within(detail()).getByText("This package's owner declares no lifecycle action here."),
    ).toBeInTheDocument();
  });

  it("links a configuration destination to its existing section without carrying state", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Slack");

    await userEvent.setup().click(
      within(detail()).getAllByRole("button", { name: "Open Channels" })[0],
    );

    expect(onOpenSection).toHaveBeenCalledTimes(1);
    expect(onOpenSection).toHaveBeenCalledWith("channels");
  });

  it("shows a package-owned diagnostic in the detail", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Slack");

    expect(within(detail()).getByRole("status"))
      .toHaveTextContent("Channel runtime failed. Check gateway logs.");
  });

  it("returns to the list from the detail", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Meeting Notes");

    await userEvent.setup().click(
      within(detail()).getByRole("button", { name: "Back to all extensions" }),
    );

    await waitFor(() => expect(screen.queryByRole("region", { name: "Extension details" })).toBeNull());
    expect(
      screen.getByText("Select a package to inspect its components and trust."),
    ).toBeInTheDocument();
  });
});

describe("Executable risk acknowledgement", () => {
  it("cancelling the warning sends no mutation and changes nothing", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Desktop Automation");
    const user = userEvent.setup();

    await user.click(within(detail()).getByRole("button", { name: "Enable" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/self-declared and not enforced/)).toBeInTheDocument();
    expect(within(dialog).getByText("plugin-r1")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(requestMutation).not.toHaveBeenCalled();
    expect(within(detail()).getAllByText("Disabled").length).toBeGreaterThan(0);
  });

  it("returns focus to the control that opened the warning", async () => {
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Desktop Automation");
    const user = userEvent.setup();
    const enable = within(detail()).getByRole("button", { name: "Enable" });

    await user.click(enable);
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(enable));
  });

  it("confirming submits the exact revision the dialog displayed", async () => {
    stubInventory(extensionInventory());
    requestMutation.mockResolvedValue({
      ok: true,
      actor_id: "operator",
      action: "enable",
      package_id: "ext:agent_plugin:desktop",
      target_id: "ext:agent_plugin:desktop",
      lifecycle: "enabled",
      message: "Enabled.",
      package: { ...agentPluginPackage(), lifecycle: "enabled" },
    });
    renderSurface();
    await openPackage("Desktop Automation");
    const user = userEvent.setup();

    await user.click(within(detail()).getByRole("button", { name: "Enable" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: /Enable anyway/ }));

    await waitFor(() => expect(requestMutation).toHaveBeenCalledTimes(1));
    expect(requestMutation).toHaveBeenCalledWith(
      "settings.extension.action",
      {
        target_id: "ext:agent_plugin:desktop",
        action: "enable",
        expected_revision: "plugin-r1",
        risk_acknowledged: true,
      },
      expect.any(Number),
    );
  });

  it("never demands acknowledgement for a declared action that is not enable or install", async () => {
    stubInventory(extensionInventory());
    requestMutation.mockResolvedValue({
      ok: true,
      actor_id: "operator",
      action: "reconnect",
      package_id: "ext:channel_package:slack",
      target_id: "ext:channel_package:slack",
      lifecycle: "enabled",
      message: "Reconnected.",
      package: channelPackage(),
    });
    renderSurface();
    await openPackage("Slack");

    await userEvent.setup().click(within(detail()).getByRole("button", { name: "Reconnect" }));

    await waitFor(() => expect(requestMutation).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(requestMutation.mock.calls[0][1]).toEqual({
      target_id: "ext:channel_package:slack",
      action: "reconnect",
      expected_revision: "slack-r7",
    });
  });
});

describe("Stale revision conflict", () => {
  it("instructs the operator to reopen the detail and does not retry", async () => {
    stubInventory(extensionInventory());
    requestMutation.mockRejectedValue(
      Object.assign(new Error("This extension changed. Reopen its details and try again."), {
        status: 409,
      }),
    );
    renderSurface();
    await openPackage("Slack");
    const user = userEvent.setup();

    await user.click(within(detail()).getByRole("button", { name: "Reconnect" }));

    const conflict = await screen.findByRole("alert");
    expect(conflict).toHaveTextContent("This extension changed");
    expect(requestMutation).toHaveBeenCalledTimes(1);
    // The action is blocked until the operator reloads, so no silent retry is possible.
    expect(within(detail()).getByRole("button", { name: "Reconnect" })).toBeDisabled();

    await user.click(within(conflict).getByRole("button", { name: "Reload details" }));
    await waitFor(() =>
      expect(within(detail()).getByRole("button", { name: "Reconnect" })).toBeEnabled(),
    );
    expect(requestMutation).toHaveBeenCalledTimes(1);
  });
});

describe("Simplified Chinese", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("renders the surface with no English literal left behind", async () => {
    await i18n.changeLanguage("zh-CN");
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Desktop Automation");

    expect(screen.getByText("扩展")).toBeInTheDocument();
    expect(screen.getByLabelText("搜索扩展")).toBeInTheDocument();
    expect(screen.getByLabelText("来源")).toBeInTheDocument();
    const panel = screen.getByRole("region", { name: "扩展详情" });
    expect(within(panel).getByText("未隔离")).toBeInTheDocument();
    expect(within(panel).getAllByText("运营者信任").length).toBeGreaterThan(0);
    expect(within(panel).getAllByText("在子进程中运行").length).toBeGreaterThan(0);
    expect(within(panel).getByText(/由扩展包自行声明，nanobot 并不强制执行/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "启用" })).toBeInTheDocument();
    expect(within(panel).queryByText("Not isolated")).toBeNull();
    expect(within(panel).queryByText("Operator-trusted")).toBeNull();
  });

  it("translates the acknowledgement dialog", async () => {
    await i18n.changeLanguage("zh-CN");
    stubInventory(extensionInventory());
    renderSurface();
    await openPackage("Desktop Automation");

    await userEvent.setup().click(
      within(screen.getByRole("region", { name: "扩展详情" })).getByRole("button", {
        name: "启用",
      }),
    );

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/确认启用 Desktop Automation？/)).toBeInTheDocument();
    expect(within(dialog).getByText(/并未被强制执行/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "仍然启用" })).toBeInTheDocument();
  });
});

describe("Settings navigation", () => {
  installSettingsViewTestHooks();

  // The whole Settings page is mounted here, so every sibling read has to answer too.
  function stubSettingsPage() {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings/extensions") return jsonResponse(extensionInventory());
      if (url === "/api/settings") return jsonResponse(settingsPayload());
      return jsonResponse({});
    }));
  }

  it("renders the Extensions section from the settings navigation", async () => {
    stubSettingsPage();
    renderSettingsView({ initialSection: "overview", initialSettings: settingsPayload() });

    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    await userEvent.setup().click(
      within(nav).getAllByRole("button", { name: "Extensions" })[0],
    );

    expect(await screen.findByRole("list", { name: "Installed extension packages" }))
      .toBeInTheDocument();
  });

  it("opens directly on the Extensions section when routed there", async () => {
    stubSettingsPage();
    renderSettingsView({ initialSection: "extensions", initialSettings: settingsPayload() });

    expect(await screen.findByRole("list", { name: "Installed extension packages" }))
      .toBeInTheDocument();
  });
});
