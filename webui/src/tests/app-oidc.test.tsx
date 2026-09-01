import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConnectionStatus } from "@/lib/types";
import { jsonResponse, settingsPayload } from "@/tests/settings-test-utils";

const connectSpy = vi.fn();
const closeSpy = vi.fn();
const requestMutationSpy = vi.fn();
const refreshSpy = vi.fn();
const createChatSpy = vi.fn();
const forkChatSpy = vi.fn();
const getSessionAutomationsSpy = vi.fn();
const deleteChatSpy = vi.fn();
const statusHandlers = new Set<(status: ConnectionStatus) => void>();

vi.mock("@/hooks/useSessions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useSessions")>();
  return {
    ...actual,
    useSessions: () => ({
      sessions: [],
      loading: false,
      error: null,
      refresh: refreshSpy,
      createChat: createChatSpy,
      forkChat: forkChatSpy,
      getSessionAutomations: getSessionAutomationsSpy,
      deleteChat: deleteChatSpy,
    }),
  };
});

vi.mock("@/hooks/useTheme", async () => {
  const React = await import("react");
  return {
    ThemeProvider: ({ children }: { children: React.ReactNode }) =>
      React.createElement(React.Fragment, null, children),
    useTheme: () => ({ theme: "light" as const, toggle: vi.fn() }),
    useThemeValue: () => "light" as const,
  };
});

vi.mock("@/lib/bootstrap", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/bootstrap")>();
  return {
    ...actual,
    deriveWsUrl: vi.fn(() => "ws://test"),
    fetchBootstrap: vi.fn(),
  };
});

vi.mock("@/lib/nanobot-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/nanobot-client")>();
  class MockClient {
    status = "idle" as const;
    defaultChatId: string | null = null;
    connect = connectSpy;
    close = closeSpy;
    onStatus = (handler: (status: ConnectionStatus) => void) => {
      statusHandlers.add(handler);
      return () => statusHandlers.delete(handler);
    };
    onRuntimeModelUpdate = () => () => {};
    onError = () => () => {};
    onChat = () => () => {};
    onSessionUpdate = () => () => {};
    onSidebarStateUpdate = () => () => {};
    onRunStatus = () => () => {};
    getRunStartedAt = () => null;
    getRunTurnId = () => null;
    getGoalState = () => undefined;
    sendMessage = vi.fn();
    newChat = vi.fn();
    newTemporaryChat = vi.fn();
    attach = vi.fn();
    setSidebarState = vi.fn();
    requestMutation = requestMutationSpy;
    discardTemporaryChat = vi.fn();
    updateUrl = vi.fn();
    updateMaxFrameBytes = vi.fn();
  }

  return { ...actual, NanobotClient: MockClient };
});

import * as bootstrap from "@/lib/bootstrap";
import App from "@/App";

function authRequired(passwordEnabled: boolean, loginUrl = "/auth/login") {
  return new bootstrap.BootstrapAuthRequiredError("bootstrap failed: HTTP 401", {
    mode: "oidc",
    login_url: loginUrl,
    password_enabled: passwordEnabled,
  });
}

function oidcBootstrap(logoutUrl = "/auth/logout?return_to=%2F") {
  return {
    token: "oidc-websocket-token",
    api_token: "oidc-api-token",
    ws_path: "/",
    auth: {
      mode: "oidc" as const,
      logout_url: logoutUrl,
      logout_csrf_token: "logout-csrf-token",
      user: { name: "Avery Example", email: "avery@example.test" },
    },
  };
}

function storageText(): string {
  return Array.from({ length: localStorage.length }, (_, index) => {
    const key = localStorage.key(index) ?? "";
    return `${key}=${localStorage.getItem(key) ?? ""}`;
  }).join("\n");
}

function mockSettingsFetch(logoutStatus = 204) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/settings") return jsonResponse(settingsPayload());
    if (url === "/auth/logout?return_to=%2F") {
      return { ok: logoutStatus >= 200 && logoutStatus < 300, status: logoutStatus } as Response;
    }
    return { ok: false, status: 404, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("App OIDC authentication", () => {
  beforeEach(() => {
    cleanup();
    closeSpy.mockReset();
    connectSpy.mockReset();
    refreshSpy.mockReset();
    createChatSpy.mockReset();
    forkChatSpy.mockReset();
    getSessionAutomationsSpy.mockReset().mockResolvedValue([]);
    deleteChatSpy.mockReset();
    requestMutationSpy.mockReset();
    statusHandlers.clear();
    localStorage.clear();
    window.history.replaceState(null, "", "/");
    vi.mocked(bootstrap.fetchBootstrap).mockReset();
    vi.mocked(bootstrap.deriveWsUrl).mockReset().mockReturnValue("ws://test");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("offers an accessible keyboard-focusable SSO link without a password fallback when the challenge is SSO-only", async () => {
    vi.mocked(bootstrap.fetchBootstrap).mockRejectedValueOnce(
      authRequired(false, "/auth/login?return_to=%2Fsettings"),
    );
    const user = userEvent.setup();

    render(<App />);

    expect(await screen.findByRole("main", { name: "Sign in to nanobot" })).toBeInTheDocument();
    const continueWithSso = screen.getByRole("link", { name: "Continue with SSO" });
    expect(continueWithSso).toHaveAttribute("href", "/auth/login?return_to=%2Fsettings");
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();

    await user.tab();
    expect(continueWithSso).toHaveFocus();
  });

  it("blocks an absolute login URL instead of exposing a cross-origin SSO navigation link", async () => {
    vi.mocked(bootstrap.fetchBootstrap).mockRejectedValueOnce(
      authRequired(false, "https://identity.example/authorize"),
    );

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Single sign-on is unavailable because the server returned an invalid login address.",
    );
    expect(screen.queryByRole("link", { name: "Continue with SSO" })).not.toBeInTheDocument();
  });

  it("keeps password authentication available alongside an OIDC challenge when the gateway allows it", async () => {
    vi.mocked(bootstrap.fetchBootstrap).mockRejectedValueOnce(authRequired(true));

    render(<App />);

    expect(await screen.findByRole("link", { name: "Continue with SSO" })).toHaveAttribute(
      "href",
      "/auth/login",
    );
    expect(screen.getByText("or use a password")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Connect" })).toBeEnabled();
  });

  it("keeps OIDC credentials out of JavaScript-readable storage after a successful bootstrap", async () => {
    const originalCookie = document.cookie;
    vi.mocked(bootstrap.fetchBootstrap).mockResolvedValueOnce(oidcBootstrap());

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1));
    const persisted = storageText();
    expect(persisted).not.toContain("oidc-websocket-token");
    expect(persisted).not.toContain("oidc-api-token");
    expect(persisted).not.toContain("Avery Example");
    expect(persisted).not.toContain("avery@example.test");
    expect(document.cookie).toBe(originalCookie);
  });

  it("fetches the retained OIDC logout path with its bootstrap CSRF token before navigating home", async () => {
    localStorage.setItem("nanobot-webui.bootstrap-secret", "saved-password");
    vi.mocked(bootstrap.fetchBootstrap).mockResolvedValueOnce(oidcBootstrap());
    const clearSavedSecret = vi.spyOn(bootstrap, "clearSavedSecret");
    const fetchMock = mockSettingsFetch();
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await screen.findByRole("navigation", { name: "Settings sections" });

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(closeSpy).toHaveBeenCalledTimes(1);
    expect(clearSavedSecret).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("nanobot-webui.bootstrap-secret")).toBeNull();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/auth/logout?return_to=%2F",
      expect.objectContaining({
        method: "GET",
        credentials: "same-origin",
        headers: { "X-Nanobot-OIDC-CSRF": "logout-csrf-token" },
      }),
    ));
    await waitFor(() => expect(window.location.hash).toBe(""));
  });

  it("shows a logout failure instead of navigating home when the OIDC logout request is rejected", async () => {
    vi.mocked(bootstrap.fetchBootstrap).mockResolvedValueOnce(oidcBootstrap());
    const fetchMock = mockSettingsFetch(500);
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await screen.findByRole("navigation", { name: "Settings sections" });
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/auth/logout?return_to=%2F",
      expect.objectContaining({ method: "GET" }),
    ));
    expect(await screen.findByText("Sign out failed. Try again.")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/settings");
  });

  it("blocks an invalid OIDC logout URL instead of navigating away", async () => {
    vi.mocked(bootstrap.fetchBootstrap).mockResolvedValueOnce(
      oidcBootstrap("//identity.example/logout"),
    );
    mockSettingsFetch();
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await screen.findByRole("navigation", { name: "Settings sections" });
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByText(
      "Sign out was blocked because the server returned an invalid address.",
    )).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });

  it("returns local password users to the in-app password screen without an OIDC logout request", async () => {
    vi.mocked(bootstrap.fetchBootstrap).mockResolvedValueOnce({
      token: "local-websocket-token",
      api_token: "local-api-token",
      ws_path: "/",
    });
    const fetchMock = mockSettingsFetch();
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await screen.findByRole("navigation", { name: "Settings sections" });
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Password" })).toBeInTheDocument();
    expect(closeSpy).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.some(([input]) => (
      String(input) === "/auth/logout?return_to=%2F"
    ))).toBe(false);
    expect(window.location.pathname).toBe("/");
  });
});
