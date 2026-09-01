import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BootstrapAuthRequiredError,
  consumeUrlBootstrapSecret,
  deriveWsUrl,
  fetchBootstrap,
  normalizeSameOriginAuthUrl,
} from "@/lib/bootstrap";

describe("bootstrap helpers", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("prefers the server-provided websocket URL over the current dev host", () => {
    expect(deriveWsUrl("/", "tok en", "ws://127.0.0.1:8765/")).toBe(
      "ws://127.0.0.1:8765/?token=tok%20en",
    );
  });

  it("overrides the server-provided websocket URL when on dev server port 5173", () => {
    vi.stubGlobal("window", {
      location: {
        port: "5173",
        hostname: "192.168.1.100",
        protocol: "http:",
      },
    });
    expect(deriveWsUrl("/", "tok", "ws://127.0.0.1:8765/")).toBe(
      "ws://192.168.1.100:8765/?token=tok",
    );
  });

  it("keeps the gateway websocket port when Vite proxies a custom target", () => {
    vi.stubGlobal("window", {
      location: {
        port: "5173",
        hostname: "127.0.0.1",
        protocol: "http:",
      },
    });
    expect(deriveWsUrl("/ws", "tok", "ws://127.0.0.1:8899/ws")).toBe(
      "ws://127.0.0.1:8899/ws?token=tok",
    );
  });

  it("preserves the host socket bridge URL", () => {
    expect(deriveWsUrl("/", "tok en", "nanobot-host://engine/")).toBe(
      "nanobot-host://engine/?token=tok%20en",
    );
  });

  it("falls back to the current window host for legacy bootstrap payloads", () => {
    expect(deriveWsUrl("/", "tok")).toBe(
      "ws://localhost:3000/?token=tok",
    );
  });

  it("does not append a token for trusted-proxy websocket URLs", () => {
    expect(deriveWsUrl("/", undefined, "wss://proxy.example/")).toBe(
      "wss://proxy.example/",
    );
  });

  it("times out when the bootstrap endpoint never responds", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    const pending = expect(fetchBootstrap("", "", 25)).rejects.toThrow(
      "Request timed out after 25ms",
    );
    await vi.advanceTimersByTimeAsync(25);

    await pending;
  });

  it("accepts tokenless trusted-proxy bootstrap responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ ws_path: "/", ws_url: "wss://proxy.example/" }),
      })),
    );

    await expect(fetchBootstrap()).resolves.toMatchObject({
      ws_path: "/",
      ws_url: "wss://proxy.example/",
    });
  });

  it("surfaces the OIDC challenge from an authentication-required bootstrap response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 401,
        headers: new Headers({ "content-length": "140" }),
        text: async () => JSON.stringify({
          error: "authentication_required",
          auth: {
            mode: "oidc",
            login_url: "/auth/login?return_to=%2F",
            password_enabled: false,
          },
        }),
      })),
    );

    try {
      await fetchBootstrap();
      throw new Error("expected bootstrap to require authentication");
    } catch (error) {
      expect(error).toBeInstanceOf(BootstrapAuthRequiredError);
      expect(error).toMatchObject({
        message: "bootstrap failed: HTTP 401",
        auth: {
          mode: "oidc",
          login_url: "/auth/login?return_to=%2F",
          password_enabled: false,
        },
      });
    }
  });

  it("permits only same-origin relative OIDC navigation paths", () => {
    expect(normalizeSameOriginAuthUrl("/auth/login?return_to=%2Fsettings#continue")).toBe(
      "/auth/login?return_to=%2Fsettings#continue",
    );

    for (const value of [
      "https://identity.example/authorize",
      "//identity.example/authorize",
      "https://user:password@localhost:3000/auth/login",
      "/auth\\login",
      "http://identity.example/logout",
    ]) {
      expect(normalizeSameOriginAuthUrl(value)).toBeNull();
    }
  });

  it("consumes bootstrap secrets from the URL fragment", () => {
    window.history.replaceState(
      null,
      "",
      "/#/settings?bootstrapSecret=s3cret&section=models",
    );

    expect(consumeUrlBootstrapSecret()).toBe("s3cret");
    expect(window.location.hash).toBe("#/settings?section=models");
  });
});
