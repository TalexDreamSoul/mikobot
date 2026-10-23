import { ChannelQrConnectFlow } from "@/components/settings/channels/ChannelQrConnectFlow";
import type {
  NanobotChannelInstanceInfo,
  NanobotFeatureInfo,
  NanobotFeaturesPayload,
} from "@/lib/types";

import { FeishuConnectFlowView, useFeishuConnectLabels } from "./FeishuConnectFlowView";

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
  const labels = useFeishuConnectLabels();
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
      labels={labels}
      renderView={(props) => <FeishuConnectFlowView {...props} />}
    />
  );
}
