import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QRCode from "qrcode";

import { ChannelQrConnectFlow } from "@/components/settings/channels/ChannelQrConnectFlow";
import type { NanobotFeatureInfo } from "@/lib/types";

const { requestMutation } = vi.hoisted(() => ({ requestMutation: vi.fn() }));
vi.mock("@/providers/ClientProvider", () => {
  const client = { requestMutation };
  return { useClient: () => ({ client }) };
});

const feature: NanobotFeatureInfo = {
  name: "plugin-chat", display_name: "Plugin Chat", type: "channel",
  installed: true, configured: true, enabled: false, running: false,
  ready: true, status: "not_enabled", install_supported: true, requires_restart: false,
};
const target = {
  extensionId: "ext:channel_package:plugin-chat/channel:office",
  expectedRevision: "office-r7", instanceId: "office",
};
const wireTarget = {
  extension_id: target.extensionId, expected_revision: target.expectedRevision,
  instance_id: target.instanceId,
};
const labels = {
  qrAlt: "Connection QR code", scanTitle: "Scan to connect", scanDescription: "Scan with your app",
  waiting: "Waiting", connected: "Connected", stopped: "Stopped", connecting: "Connecting",
  scanAgain: "Scan again", connect: "Connect",
};
const props = {
  token: "tok", channelName: "plugin-chat", feature, startOptions: target,
  labels, onFeaturesUpdate: vi.fn(),
};
beforeEach(() => {
  requestMutation.mockReset().mockResolvedValue({ session_id: "link-1", status: "succeeded" });
  props.onFeaturesUpdate.mockReset();
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe("channel authorization", () => {
  it.each([false, true])("renders a requested QR authorization on minimal surface=%s", async (minimalPending) => {
    vi.spyOn(QRCode, "toDataURL").mockResolvedValue("data:image/png;base64,preview");
    requestMutation.mockResolvedValueOnce({
      session_id: "qr-session", status: "pending", qr_url: "https://example.com/connect",
    });
    render(<ChannelQrConnectFlow {...props} showQrCode minimalPending={minimalPending} />);
    fireEvent.click(screen.getByRole("button", { name: "Connect", exact: true }));
    expect(await screen.findByRole("img", { name: labels.qrAlt }))
      .toHaveAttribute("src", "data:image/png;base64,preview");
  });

  it("cancels browser-only authorization against its original instance and revision", async () => {
    const generateQr = vi.spyOn(QRCode, "toDataURL").mockRejectedValue(new Error("QR must not run"));
    requestMutation
      .mockResolvedValueOnce({ session_id: "browser-session", status: "pending", qr_url: "https://example.com/authorize" })
      .mockResolvedValueOnce({ session_id: "browser-session", status: "cancelled" });
    const view = render(<ChannelQrConnectFlow {...props} showQrCode={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Connect", exact: true }));
    expect(await screen.findByText("Waiting")).toBeVisible();
    view.rerender(<ChannelQrConnectFlow {...props} showQrCode={false}
      startOptions={{ ...target, instanceId: "other", expectedRevision: "other-r8" }} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel", exact: true }));
    expect(await screen.findByText("Stopped")).toBeVisible();
    expect(requestMutation).toHaveBeenLastCalledWith("settings.channel.connect.cancel", {
      channel: "plugin-chat", session_id: "browser-session", ...wireTarget,
    }, 20_000);
    expect(generateQr).not.toHaveBeenCalled();
  });

  it("polls browser authorization to completion using the captured session target", async () => {
    vi.useFakeTimers();
    const generateQr = vi.spyOn(QRCode, "toDataURL").mockRejectedValue(new Error("QR must not run"));
    requestMutation
      .mockResolvedValueOnce({ session_id: "browser-session", status: "pending", qr_url: "https://example.com/authorize" })
      .mockResolvedValueOnce({ session_id: "browser-session", status: "succeeded" });
    const view = render(<ChannelQrConnectFlow {...props} showQrCode={false} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Connect", exact: true })); });
    view.rerender(<ChannelQrConnectFlow {...props} showQrCode={false}
      startOptions={{ ...target, instanceId: "other", expectedRevision: "other-r8" }} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(900); });
    expect(screen.getByText("Connected")).toBeVisible();
    expect(requestMutation).toHaveBeenLastCalledWith("settings.channel.connect.poll", {
      channel: "plugin-chat", session_id: "browser-session", ...wireTarget,
    }, 150_000);
    expect(generateQr).not.toHaveBeenCalled();
  });

  it("does not restart on target refresh and uses the refreshed target for explicit forced retry", async () => {
    const view = render(<ChannelQrConnectFlow {...props} autoStart forceOnRepeat />);
    await screen.findByText("Connected");
    expect(requestMutation).toHaveBeenCalledTimes(1);
    view.rerender(<ChannelQrConnectFlow {...props} autoStart forceOnRepeat
      startOptions={{ ...target, expectedRevision: "office-r8" }} />);
    expect(requestMutation).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Scan again" }));
    await screen.findByText("Connected");
    expect(requestMutation).toHaveBeenLastCalledWith("settings.channel.connect.start", {
      channel: "plugin-chat", ...wireTarget, expected_revision: "office-r8", force: true,
    }, 150_000);
  });
});
