import type { ChannelPluginConnectFlowProps } from "@/channel-plugins/types";
import { ChannelQrConnectFlow } from "@/components/settings/channels/ChannelQrConnectFlow";

import { WhatsAppConnectFlowView, useWhatsAppConnectLabels } from "./WhatsAppConnectFlowView";

export function WhatsAppConnectFlow({
  token,
  feature,
  idleLabel,
  connectRequestId,
  onFeaturesUpdate,
}: ChannelPluginConnectFlowProps) {
  const labels = useWhatsAppConnectLabels();

  return (
    <ChannelQrConnectFlow
      feature={feature}
      token={token}
      channelName="whatsapp"
      startOptions={{
        extensionId: feature.action_target_id ?? "",
        expectedRevision: feature.action_target_revision ?? "",
        instanceId: "default",
      }}
      idleLabel={idleLabel}
      connectRequestId={connectRequestId}
      autoStart
      minimalPending
      forceOnRepeat
      labels={labels}
      renderView={(props) => <WhatsAppConnectFlowView {...props} />}
      onFeaturesUpdate={onFeaturesUpdate}
    />
  );
}
