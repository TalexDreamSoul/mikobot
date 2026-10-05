import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { channelTranslator } from "@/channel-plugins/i18n";
import { ProjectChannelsPanel } from "@/components/projects/ProjectChannelsPanel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useCollaborationProjects } from "@/hooks/useCollaborationProjects";
import { fetchNanobotFeatures, runExtensionAction } from "@/lib/api";
import type { NanobotChannelInstanceInfo, NanobotFeatureInfo, NanobotFeaturesPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { WeixinConnectFlow } from "./WeixinConnectFlow";

type InstanceAction = "deleteInstance" | "rescan" | "rePair";

export function WeixinInstanceControls({ token, feature, instance, onFeaturesUpdate }: {
  token: string;
  feature: NanobotFeatureInfo;
  instance: NanobotChannelInstanceInfo;
  onFeaturesUpdate: (payload: NanobotFeaturesPayload) => void;
}) {
  const { t } = useTranslation();
  const tx = channelTranslator(t, "weixin");
  const { client, getToken } = useClient();
  const [action, setAction] = useState<InstanceAction | null>(null);
  const [replacementRequest, setReplacementRequest] = useState(0);
  const [actionInstance, setActionInstance] = useState(instance);
  const [pairingOpen, setPairingOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [connectionActive, setConnectionActive] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const identity = actionInstance.display_name?.trim() || actionInstance.name || actionInstance.id;
  const confirm = async () => {
    if (!action || busyRef.current) return;
    if (action === "rescan") {
      setReplacementRequest((value) => value + 1);
      setAction(null);
      return;
    }
    if (action === "rePair") {
      setPairingOpen(true);
      setAction(null);
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await runExtensionAction(client, {
        targetId: actionInstance.extension_id ?? "",
        expectedRevision: actionInstance.extension_revision ?? "",
        action: "delete_instance",
        riskAcknowledged: true,
      });
      if (!result.ok) throw new Error(result.message);
      onFeaturesUpdate(await fetchNanobotFeatures(getToken()));
      setAction(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  const warnings = {
    deleteInstance: "Remove this exact instance, its account and project binding? Other instances and historical conversations are preserved.",
    rescan: "Scan a new QR code to replace this instance's account. The current account remains saved until replacement succeeds; cancellation or failure preserves it.",
    rePair: "Keep this account logged in, but unlink its current project assignment when the new Pair Code is issued. Normal delivery will be suspended until the new code is verified and consumed. Choose the project and permitted assignee next.",
  };
  const labels = { deleteInstance: "Delete instance", rescan: "Rescan account", rePair: "Re-pair project" };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {(["rescan", "rePair", "deleteInstance"] as const).map((next) => (
          <Button key={next} type="button" size="sm" variant="outline" disabled={busy || pairingOpen || connectionActive || (next === "deleteInstance" && !instance.extension_actions?.includes("delete_instance"))}
            onClick={() => { setError(null); setActionInstance(instance); setAction(next); }}>
            {tx(`custom.${next}`, labels[next])}
          </Button>
        ))}
      </div>
      <Dialog open={action !== null} onOpenChange={(open) => { if (!open && !busyRef.current) setAction(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{action ? tx(`custom.${action}Title`, labels[action]) : ""} · {identity} · {actionInstance.id}</DialogTitle>
            <DialogDescription>{action ? tx(`custom.${action}Warning`, warnings[action]) : ""}</DialogDescription>
          </DialogHeader>
          {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={() => setAction(null)}>{t("settings.actions.cancel")}</Button>
            <Button type="button" disabled={busy} onClick={() => void confirm()}>{action ? tx(`custom.${action}Confirm`, labels[action]) : ""}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {replacementRequest > 0 ? <WeixinConnectFlow token={token} feature={feature} instanceId={instance.id}
        mode="replace" connectRequestId={replacementRequest} onActiveChange={setConnectionActive} onFeaturesUpdate={onFeaturesUpdate} /> : null}
      {pairingOpen ? <WeixinProjectPairing instance={instance} onFeaturesUpdate={onFeaturesUpdate} onClose={() => setPairingOpen(false)} /> : null}
      {error && !action ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    </div>
  );
}

function WeixinProjectPairing({ instance, onFeaturesUpdate, onClose }: {
  instance: NanobotChannelInstanceInfo;
  onFeaturesUpdate: (payload: NanobotFeaturesPayload) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { getToken } = useClient();
  const projects = useCollaborationProjects();
  const manageable = projects.summary?.projects.filter((project) => projects.summary?.manageable_project_ids.includes(project.id)) ?? [];
  const selected = manageable.find((project) => project.id === projects.projectId);
  const refreshProjection = useCallback(async () => onFeaturesUpdate(await fetchNanobotFeatures(getToken())), [getToken, onFeaturesUpdate]);
  return (
    <div className="space-y-3 rounded-control border border-border/60 p-3">
      <label className="block space-y-1 text-sm">
        <span>{t("channels.project")}</span>
        <select className="h-10 w-full rounded-control border border-input bg-background px-3" value={selected?.id ?? ""}
          disabled={Boolean(projects.busyKey)} onChange={(event) => projects.selectProject(event.target.value)}>
          <option value="">{t("channels.project")}</option>
          {manageable.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
      </label>
      {projects.error ? <p role="alert" className="text-sm text-destructive">{projects.error}</p> : null}
      {selected && projects.detail?.project.id === selected.id ? (
        <ProjectChannelsPanel key={`${selected.id}:${instance.id}`} detail={projects.detail} projects={projects}
          pairingTarget={{ channelType: "weixin", instanceId: instance.id, label: instance.display_name || instance.name || instance.id }}
          replaceAssignment onPairingCreated={refreshProjection} onPaired={refreshProjection} />
      ) : null}
      <Button type="button" variant="outline" disabled={Boolean(projects.busyKey)} onClick={onClose}>{t("common.close")}</Button>
    </div>
  );
}
