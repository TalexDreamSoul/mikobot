import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { SettingsPayload } from "@/lib/types";
import {
  installSettingsViewTestHooks,
  jsonResponse,
  renderSettingsView,
  requestMutationMock,
  settingsPayload,
} from "@/tests/settings-test-utils";

function missingFeature(name: string, displayName: string) {
  return {
    name,
    display_name: displayName,
    type: "feature",
    extension_id: `ext:optional_feature:${name}`,
    extension_revision: `${name}-package-revision`,
    extension_actions: ["install"],
    extension_lifecycle: "unavailable",
    enabled: false,
    installed: false,
    ready: false,
    status: "missing_dependency",
    install_supported: true,
    requires_restart: true,
  };
}

function stubSettingsFetch(payload: SettingsPayload, featureName: string, displayName: string) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/settings") return jsonResponse(payload);
    if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
    if (url === "/api/settings/mcp-presets") return jsonResponse({ presets: [], installed_count: 0 });
    if (url === "/api/settings/api-service") return jsonResponse(stoppedApiService);
    if (url === "/api/settings/nanobot-features") {
      return jsonResponse({
        features: [missingFeature(featureName, displayName)],
        enabled_count: 0,
      });
    }
    return jsonResponse({});
  }));
}

const stoppedApiService = {
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

const bedrockProvider = {
  name: "bedrock",
  label: "Amazon Bedrock",
  configured: true,
  api_key_required: false,
  api_key_hint: null,
  api_base: null,
  model_catalog: "builtin",
  advanced_fields: ["region", "profile"],
};

describe("Capability install risk acknowledgement", () => {
  installSettingsViewTestHooks();

  it("installs an optional capability only after the operator acknowledges the risk", async () => {
    const base = settingsPayload();
    stubSettingsFetch(base, "langfuse", "Langfuse");

    renderSettingsView({ initialSection: "runtime", initialSettings: base, showSidebar: true });

    const enable = await screen.findByRole("button", { name: "Enable tracing support" });
    fireEvent.click(enable);

    const confirmation = await screen.findByRole("dialog", {
      name: "Install support for Langfuse?",
    });
    expect(requestMutationMock).not.toHaveBeenCalled();

    fireEvent.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Install support for Langfuse?" }))
        .not.toBeInTheDocument(),
    );
    expect(requestMutationMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Enable tracing support" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Enable tracing support" }));
    fireEvent.click(
      within(await screen.findByRole("dialog", { name: "Install support for Langfuse?" }))
        .getByRole("button", { name: "Install and enable" }),
    );

    await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
      "settings.feature.enable",
      {
        name: "langfuse",
        extension_id: "ext:optional_feature:langfuse",
        expected_revision: "langfuse-package-revision",
        risk_acknowledged: true,
      },
      150_000,
    ));
    expect(requestMutationMock).toHaveBeenCalledTimes(1);
  });

  it("asks before installing provider support instead of saving the provider", async () => {
    const base: SettingsPayload = { ...settingsPayload(), providers: [bedrockProvider] };
    stubSettingsFetch(base, "bedrock", "Bedrock");

    renderSettingsView({ initialSection: "models", initialSettings: base });

    fireEvent.click(await screen.findByRole("button", { name: /Amazon Bedrock/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Save provider" }));

    const confirmation = await screen.findByRole("dialog", {
      name: "Install support for Bedrock?",
    });
    expect(requestMutationMock).not.toHaveBeenCalled();

    fireEvent.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Install support for Bedrock?" }))
        .not.toBeInTheDocument(),
    );
    expect(requestMutationMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save provider" }));
    fireEvent.click(
      within(await screen.findByRole("dialog", { name: "Install support for Bedrock?" }))
        .getByRole("button", { name: "Install and enable" }),
    );

    await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
      "settings.feature.enable",
      {
        name: "bedrock",
        extension_id: "ext:optional_feature:bedrock",
        expected_revision: "bedrock-package-revision",
        risk_acknowledged: true,
      },
      150_000,
    ));
    // The unacknowledged provider save never reached the gateway, so confirming
    // the install must not smuggle it through either.
    expect(requestMutationMock).toHaveBeenCalledTimes(1);
  });

  it("asks before installing search provider support instead of saving web search", async () => {
    const base = settingsPayload();
    const payload: SettingsPayload = {
      ...base,
      web_search: {
        ...base.web_search,
        provider: "duckduckgo",
        providers: [
          { name: "duckduckgo", label: "DuckDuckGo", credential: "none" as const },
          { name: "olostep", label: "Olostep", credential: "api_key" as const },
        ],
      },
    };
    stubSettingsFetch(payload, "olostep", "Olostep");

    renderSettingsView({ initialSection: "browser", initialSettings: payload });

    fireEvent.pointerDown(await screen.findByRole("button", { name: /DuckDuckGo/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Olostep" }));
    expect(
      await screen.findByText(
        "Saving asks you to confirm the Olostep support install first. Save again once it finishes.",
      ),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Enter API key"), {
      target: { value: "olostep-key" },
    });
    const save = screen
      .getAllByRole("button", { name: "Save" })
      .find((button) => !(button as HTMLButtonElement).disabled);
    if (!save) throw new Error("enabled Save button was not found");
    fireEvent.click(save);

    const confirmation = await screen.findByRole("dialog", {
      name: "Install support for Olostep?",
    });
    expect(requestMutationMock).not.toHaveBeenCalled();

    fireEvent.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Install support for Olostep?" }))
        .not.toBeInTheDocument(),
    );
    expect(requestMutationMock).not.toHaveBeenCalled();

    fireEvent.click(save);
    fireEvent.click(
      within(await screen.findByRole("dialog", { name: "Install support for Olostep?" }))
        .getByRole("button", { name: "Install and enable" }),
    );

    await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
      "settings.feature.enable",
      {
        name: "olostep",
        extension_id: "ext:optional_feature:olostep",
        expected_revision: "olostep-package-revision",
        risk_acknowledged: true,
      },
      150_000,
    ));
    expect(requestMutationMock).toHaveBeenCalledTimes(1);
  });

  it("saves without a prompt when the capability is already installed", async () => {
    const base: SettingsPayload = { ...settingsPayload(), providers: [bedrockProvider] };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(base);
      if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
      if (url === "/api/settings/mcp-presets") return jsonResponse({ presets: [], installed_count: 0 });
      if (url === "/api/settings/nanobot-features") {
        return jsonResponse({
          features: [{
            ...missingFeature("bedrock", "Bedrock"),
            enabled: true,
            installed: true,
            ready: true,
            status: "enabled",
            extension_lifecycle: "enabled",
          }],
          enabled_count: 1,
        });
      }
      return jsonResponse({});
    }));

    renderSettingsView({ initialSection: "models", initialSettings: base });

    fireEvent.click(await screen.findByRole("button", { name: /Amazon Bedrock/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Save provider" }));

    await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
      "settings.provider.update",
      expect.objectContaining({ provider: "bedrock" }),
      20_000,
    ));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
