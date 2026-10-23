import { useTranslation } from "react-i18next";

import type { ChannelPluginConnectFlowProps } from "@/channel-plugins/types";
import { ChannelQrConnectFlow } from "@/components/settings/channels/ChannelQrConnectFlow";

import { WeixinConnectFlowView, weixinConnectLabels } from "./WeixinConnectFlowView";
import { isVerificationChallenge, WEIXIN_AUTH_EXPIRED_MESSAGE } from "./presentation";

export { WEIXIN_AUTH_EXPIRED_MESSAGE } from "./presentation";

export function WeixinConnectFlow({
  token,
  feature,
  authRecoveryActive = false,
  idleLabel,
  connectRequestId,
  onFeaturesUpdate,
  onActiveChange,
  instanceId = "default",
  mode = "replace",
}: ChannelPluginConnectFlowProps & {
  instanceId?: string;
  mode?: "replace" | "create";
  authRecoveryActive?: boolean;
  onActiveChange?: (active: boolean) => void;
}) {
  const { t } = useTranslation();
  const authExpired = authRecoveryActive
    || feature.runtime_error === WEIXIN_AUTH_EXPIRED_MESSAGE;
  const scanAgainLabel = t("settings.channels.scanAgain", {
    defaultValue: "Scan again",
  });

  const instance = feature.instances?.find((item) => item.id === instanceId);
  const configured = instance?.configured ?? feature.configured;
  if (mode === "replace" && configured && !authExpired && !connectRequestId) return null;

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
      channelName="weixin"
      startOptions={{ force: mode === "replace" || authExpired, mode, ...actionTarget }}
      idleLabel={authExpired ? scanAgainLabel : idleLabel}
      connectRequestId={connectRequestId}
      autoStart={mode === "replace" && (!configured || authExpired)}
      minimalPending
      forceOnRepeat
      onFeaturesUpdate={onFeaturesUpdate}
      onActiveChange={onActiveChange}
      pausePolling={isVerificationChallenge}
      suppressSucceeded={feature.runtime_status === "failed"}
      labels={weixinConnectLabels(t)}
      renderView={(props) => <WeixinConnectFlowView {...props} />}
    />
  );
}
