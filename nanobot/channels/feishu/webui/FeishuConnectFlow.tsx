import { useTranslation } from "react-i18next";

import { channelTranslator } from "@/channel-plugins/i18n";
import { ChannelQrConnectFlow } from "@/components/settings/channels/ChannelQrConnectFlow";
import type {
  NanobotChannelInstanceInfo,
  NanobotFeatureInfo,
  NanobotFeaturesPayload,
} from "@/lib/types";

export function FeishuConnectFlow({
  token,
  feature,
  instance,
  instanceId = "default",
  mode = "replace",
  idleLabel,
  connectRequestId,
  onFeaturesUpdate,
}: {
  token: string;
  feature: NanobotFeatureInfo;
  instance?: NanobotChannelInstanceInfo;
  instanceId?: string;
  mode?: "replace" | "create";
  idleLabel?: string;
  connectRequestId?: number;
  onFeaturesUpdate: (payload: NanobotFeaturesPayload) => void;
}) {
  const { t } = useTranslation();
  const tx = channelTranslator(t, "feishu");
  const actionTarget = {
    extensionId: instance?.extension_id
      ?? (instanceId === "default" ? feature.action_target_id : "")
      ?? "",
    expectedRevision: instance?.extension_revision
      ?? (instanceId === "default" ? feature.action_target_revision : "")
      ?? "",
    instanceId,
  };
  return (
    <ChannelQrConnectFlow
      feature={feature}
      token={token}
      channelName="feishu"
      startOptions={{ domain: "feishu", mode, ...actionTarget }}
      idleLabel={idleLabel}
      connectRequestId={connectRequestId}
      onFeaturesUpdate={onFeaturesUpdate}
      labels={{
        qrAlt: tx("custom.qrAlt", "Feishu connection QR code"),
        scanTitle: tx("custom.scanTitle", "Scan with Feishu"),
        scanDescription: tx(
          "custom.scanDescription",
          "Use Feishu or Lark on your phone to scan this code. nanobot will finish setup automatically after authorization.",
        ),
        waiting: tx("custom.waiting", "Waiting for authorization..."),
        connected: tx("custom.connected", "Feishu is connected."),
        stopped: tx("custom.stopped", "Connection stopped."),
        connecting: tx("custom.connecting", "Connecting..."),
        scanAgain: t("settings.channels.scanAgain", { defaultValue: "Scan again" }),
        connect: t("settings.channels.connect", { defaultValue: "Connect" }),
      }}
    />
  );
}
