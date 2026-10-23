import { useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { NanobotFeatureInstallDialog } from "@/components/settings/shared/SettingsControls";
import {
  configureChannel,
  disableNanobotFeature,
  enableNanobotFeature,
} from "@/lib/api";
import type { NanobotChannelInstanceInfo, NanobotFeatureInfo, NanobotFeaturesPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import {
  ChannelInstancesPanelView,
  type ChannelInstancesPanelCustomization,
  type ChannelInstancesPanelViewProps,
} from "./ChannelInstancesPanelView";

export type { ChannelInstancesPanelCustomization } from "./ChannelInstancesPanelView";

export function ChannelInstancesPanel({
  feature,
  showBrandLogos,
  instances,
  onFeaturesUpdate,
  customization,
  renderView,
}: {
  feature: NanobotFeatureInfo;
  showBrandLogos: boolean;
  instances?: NanobotChannelInstanceInfo[];
  onFeaturesUpdate: (payload: NanobotFeaturesPayload) => void;
  customization?: ChannelInstancesPanelCustomization;
  renderView?: (props: ChannelInstancesPanelViewProps) => ReactNode;
}) {
  const { client } = useClient();
  const { t } = useTranslation();
  const [installConfirm, setInstallConfirm] = useState(false);
  const installDecisionRef = useRef<((confirmed: boolean) => void) | null>(null);
  const requiresInstallConfirm = !feature.installed && feature.install_supported;
  const confirmInstall = () => new Promise<boolean>((resolve) => {
    installDecisionRef.current?.(false);
    installDecisionRef.current = resolve;
    setInstallConfirm(true);
  });
  const settleInstall = (confirmed: boolean) => {
    const resolve = installDecisionRef.current;
    installDecisionRef.current = null;
    setInstallConfirm(false);
    resolve?.(confirmed);
  };
  const actionOptions = (instance: NanobotChannelInstanceInfo, riskAcknowledged: boolean) => ({
    extensionId: instance.extension_id ?? "",
    expectedRevision: instance.extension_revision ?? "",
    instanceId: instance.id,
    ...(riskAcknowledged ? { riskAcknowledged: true } : {}),
  });
  const viewProps: ChannelInstancesPanelViewProps = {
    feature,
    showBrandLogos,
    instances,
    customization,
    onToggleInstance: async (instance, checked) => {
      if (instance.pairing_only) {
        throw new Error(t("settings.channels.pairingRequiredDescription", {
          instance: instance.id,
          defaultValue: "Assign instance {{instance}} to a project with a one-time Pair Code before it can receive messages.",
        }));
      }
      const riskAcknowledged = checked && requiresInstallConfirm;
      if (riskAcknowledged && !await confirmInstall()) return;
      const options = actionOptions(instance, riskAcknowledged);
      const payload = checked
        ? await enableNanobotFeature(client, feature.name, options)
        : await disableNanobotFeature(client, feature.name, options);
      onFeaturesUpdate(payload);
    },
    onSaveInstance: async (instance, values) => {
      const riskAcknowledged = instance.enabled && requiresInstallConfirm;
      // An empty message keeps a declined install silent instead of reporting a save.
      if (riskAcknowledged && !await confirmInstall()) throw new Error("");
      const payload = await configureChannel(client, feature.name, values, {
        enable: instance.enabled,
        ...actionOptions(instance, riskAcknowledged),
      });
      if (payload.nanobot_features) onFeaturesUpdate(payload.nanobot_features);
    },
  };
  return (
    <>
      <NanobotFeatureInstallDialog
        feature={installConfirm ? feature : null}
        installing={false}
        onOpenChange={(open) => { if (!open) settleInstall(false); }}
        onConfirm={() => settleInstall(true)}
      />
      {renderView ? renderView(viewProps) : <ChannelInstancesPanelView {...viewProps} />}
    </>
  );
}
