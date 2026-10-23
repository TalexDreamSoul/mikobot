import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { channelTranslator } from "@/channel-plugins/i18n";
import { ChannelQrConnectFlow } from "@/components/settings/channels/ChannelQrConnectFlow";
import type { ChannelPluginConnectFlowProps } from "@/channel-plugins/types";

import { LinearConnectFlowView, linearConnectLabels } from "./LinearConnectFlowView";

export function LinearConnectFlow({
  token,
  feature,
  idleLabel,
  connectRequestId,
  connected = feature.runtime_status === "running",
  onFeaturesUpdate,
  onActiveChange,
  renderActions,
}: ChannelPluginConnectFlowProps & {
  onActiveChange?: (active: boolean) => void;
  connected?: boolean;
  renderActions?: (connectButton: ReactNode) => ReactNode;
}) {
  const { t } = useTranslation();
  const tx = channelTranslator(t, "linear");
  return <ChannelQrConnectFlow token={token} feature={feature} channelName="linear" idleLabel={idleLabel}
    connectRequestId={connectRequestId}
    startOptions={{ extensionId: feature.action_target_id ?? "", expectedRevision: feature.action_target_revision ?? "", instanceId: "default", force: Boolean(connectRequestId) }}
    forceOnRepeat showQrCode={false} connected={connected}
    suppressSucceeded={feature.runtime_status === "running" || feature.runtime_status === "failed"}
    onFeaturesUpdate={onFeaturesUpdate} onActiveChange={onActiveChange} renderActions={renderActions}
    labels={linearConnectLabels(tx)} renderView={(props) => <LinearConnectFlowView {...props} />} />;
}
