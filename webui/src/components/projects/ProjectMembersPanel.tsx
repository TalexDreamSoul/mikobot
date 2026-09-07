import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Shield, Trash2, UserPlus, Users } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type {
  CollaborationProjectMember,
  CollaborationProjectPayload,
  CollaborationProjectRole,
} from "@/lib/types";

const PROJECT_ROLES: CollaborationProjectRole[] = ["member", "owner"];

export function ProjectMembersPanel({
  detail,
  currentUserId,
  busyKey,
  onAddMember,
  onRemoveMember,
}: {
  detail: CollaborationProjectPayload;
  currentUserId: string;
  busyKey: string | null;
  onAddMember: (memberUserId: string, role: CollaborationProjectRole) => Promise<unknown>;
  onRemoveMember: (memberUserId: string) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const [memberUserId, setMemberUserId] = useState("");
  const [role, setRole] = useState<CollaborationProjectRole>("member");
  const [memberToRemove, setMemberToRemove] = useState<CollaborationProjectMember | null>(null);
  const currentMembership = detail.members.find((member) => member.user_id === currentUserId) ?? null;
  const canManage = detail.can_manage;
  const existingMember = detail.members.find((member) => member.user_id === memberUserId.trim()) ?? null;

  const addMember = async (event: FormEvent) => {
    event.preventDefault();
    const userId = memberUserId.trim();
    if (!userId || !canManage || busyKey) return;
    try {
      await onAddMember(userId, role);
      setMemberUserId("");
      setRole("member");
    } catch {
      // The shared project error region reports authoritative membership failures.
    }
  };

  const removeMember = async () => {
    if (!memberToRemove) return;
    try {
      await onRemoveMember(memberToRemove.user_id);
      setMemberToRemove(null);
    } catch {
      // The shared project error region reports authoritative membership failures.
    }
  };

  return (
    <>
      <section aria-labelledby="project-members-title" className="space-y-5">
        <div>
          <h2 id="project-members-title" className="text-lg font-semibold tracking-tight">{t("projects.members.title")}</h2>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
            {t("projects.members.description")}
          </p>
        </div>

        <section aria-labelledby="project-member-roster-title" className="overflow-hidden rounded-panel bg-settings-surface">
          <header className="flex items-start gap-3 px-4 py-4 sm:px-5">
            <Users className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
            <div className="min-w-0">
              <h3 id="project-member-roster-title" className="text-sm font-semibold">{t("projects.members.roster")}</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {t("projects.members.currentRole", {
                  role: currentMembership
                    ? t(`projects.members.roles.${currentMembership.role}`)
                    : t("projects.members.notAvailable"),
                })}
              </p>
            </div>
          </header>

          {detail.members.length ? (
            <ul className="border-t border-border/45">
              {detail.members.map((member) => (
                <li key={member.user_id} className="flex min-h-14 items-center gap-3 border-t border-border/45 px-4 py-3 first:border-t-0 sm:px-5">
                  <Shield className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="break-all text-sm font-medium">
                      {member.user_id}
                      {member.user_id === currentUserId ? <span className="ml-1 text-xs font-normal text-muted-foreground">({t("projects.members.you")})</span> : null}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t(`projects.members.roles.${member.role}`)}</p>
                  </div>
                  {canManage ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      disabled={Boolean(busyKey)}
                      onClick={() => setMemberToRemove(member)}
                      aria-label={t("projects.members.removeAria", { user: member.user_id })}
                      title={t("projects.members.removeTitle")}
                      className="h-11 w-11 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    >
                      {busyKey === `project:member:remove:${member.user_id}`
                        ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                        : <Trash2 className="h-4 w-4" aria-hidden />}
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="border-t border-border/45 px-4 py-5 text-sm text-muted-foreground sm:px-5">{t("projects.members.empty")}</p>
          )}
        </section>

        {canManage ? (
          <section aria-labelledby="add-project-member-title" className="rounded-panel bg-settings-surface p-4 sm:p-5">
            <div className="flex items-start gap-3">
              <UserPlus className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
              <div>
                <h3 id="add-project-member-title" className="text-sm font-semibold">{t("projects.members.add")}</h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {t("projects.members.addDescription")}
                </p>
              </div>
            </div>

            <form onSubmit={(event) => void addMember(event)} className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_9rem_auto] sm:items-end">
              <div className="min-w-0">
                <label htmlFor="project-member-user-id" className="block text-xs font-medium">{t("projects.members.exactUserId")}</label>
                <Input
                  id="project-member-user-id"
                  value={memberUserId}
                  onChange={(event) => setMemberUserId(event.target.value)}
                  placeholder={t("projects.members.userIdPlaceholder")}
                  autoComplete="off"
                  maxLength={128}
                  disabled={Boolean(busyKey)}
                  className="mt-1.5 h-11 bg-background"
                />
              </div>
              <div>
                <label htmlFor="project-member-role" className="text-xs font-medium">{t("projects.members.projectRole")}</label>
                <Select
                  id="project-member-role"
                  value={role}
                  onChange={(event) => setRole(event.target.value as CollaborationProjectRole)}
                  disabled={Boolean(busyKey)}
                  containerClassName="mt-1.5"
                  className="h-11"
                >
                  {PROJECT_ROLES.map((option) => (
                    <option key={option} value={option}>{t(`projects.members.roles.${option}`)}</option>
                  ))}
                </Select>
              </div>
              <Button type="submit" disabled={!memberUserId.trim() || Boolean(busyKey)} className="h-11 w-full sm:w-auto">
                {busyKey?.startsWith("project:member:add:") ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
                {existingMember ? t("projects.members.updateRole") : t("projects.members.addMember")}
              </Button>
            </form>
          </section>
        ) : (
          <section className="rounded-panel bg-settings-surface px-4 py-5 sm:px-5">
            <p className="text-sm font-medium">{t("projects.members.readOnly")}</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              {t("projects.members.readOnlyDescription")}
            </p>
          </section>
        )}
      </section>

      <AlertDialog open={Boolean(memberToRemove)} onOpenChange={(next) => !next && setMemberToRemove(null)}>
        <AlertDialogContent className="w-[min(calc(100vw-2rem),24rem)]">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("projects.members.removeConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription className="break-words">
              {t("projects.members.removeConfirmDescription", {
                user: memberToRemove?.user_id,
                project: detail.project.name,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="h-11">{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void removeMember()}
              className="h-11 bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {t("projects.members.removeMember")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
