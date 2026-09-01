import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Building2, Loader2, Plus, ShieldCheck, Trash2, UserPlus, Users } from "lucide-react";

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
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import type {
  CollaborationOrganization,
  CollaborationOrganizationMember,
  CollaborationOrganizationPayload,
  CollaborationOrganizationRole,
} from "@/lib/types";

const ORGANIZATION_ROLES: Array<{ value: CollaborationOrganizationRole; label: string }> = [
  { value: "member", label: "Member" },
  { value: "admin", label: "Admin" },
  { value: "owner", label: "Owner" },
];

function titleCaseRole(role: CollaborationOrganizationRole): string {
  return role.slice(0, 1).toUpperCase() + role.slice(1);
}

export function OrganizationManagement({
  open,
  onOpenChange,
  organizations,
  organizationId,
  detail,
  currentUserId,
  personalOrganizationId,
  projectCount,
  loading,
  error,
  busyKey,
  onSelectOrganization,
  onRefresh,
  onCreate,
  onRename,
  onDelete,
  onAddMember,
  onRemoveMember,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  organizations: CollaborationOrganization[];
  organizationId: string | null;
  detail: CollaborationOrganizationPayload | null;
  currentUserId: string;
  personalOrganizationId: string | null;
  projectCount: number;
  loading: boolean;
  error: string | null;
  busyKey: string | null;
  onSelectOrganization: (organizationId: string) => void;
  onRefresh: () => Promise<unknown>;
  onCreate: (name: string) => Promise<unknown>;
  onRename: (name: string) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
  onAddMember: (memberUserId: string, role: CollaborationOrganizationRole) => Promise<unknown>;
  onRemoveMember: (memberUserId: string) => Promise<unknown>;
}) {
  const [newOrganizationName, setNewOrganizationName] = useState("");
  const [renameName, setRenameName] = useState("");
  const [memberUserId, setMemberUserId] = useState("");
  const [memberRole, setMemberRole] = useState<CollaborationOrganizationRole>("member");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [memberToRemove, setMemberToRemove] = useState<CollaborationOrganizationMember | null>(null);
  const selectedDetail = detail?.organization.id === organizationId ? detail : null;
  const organization = selectedDetail?.organization ?? organizations.find((item) => item.id === organizationId) ?? null;
  const currentMembership = selectedDetail?.members.find((member) => member.user_id === currentUserId) ?? null;
  const ownerCount = selectedDetail?.members.filter((member) => member.role === "owner").length ?? 0;
  const isPersonal = organization?.id === personalOrganizationId;
  const isOwner = currentMembership?.role === "owner";
  const isAdmin = currentMembership?.role === "admin";
  const canAdminister = isOwner || isAdmin;
  const canDelete = isOwner && !isPersonal && projectCount === 0;
  const existingMember = selectedDetail?.members.find((member) => member.user_id === memberUserId.trim()) ?? null;
  const adminTargetBlocked = isAdmin && existingMember !== null && existingMember.role !== "member";
  const availableRoles = isOwner ? ORGANIZATION_ROLES : ORGANIZATION_ROLES.slice(0, 1);

  useEffect(() => {
    setRenameName(organization?.name ?? "");
  }, [organization?.id, organization?.name]);

  useEffect(() => {
    if (!isOwner) setMemberRole("member");
  }, [isOwner]);

  const deleteReason = useMemo(() => {
    if (currentMembership?.role !== "owner") return "Only an organization owner can delete this workspace.";
    if (isPersonal) return "Your personal organization is permanent.";
    if (projectCount > 0) return "Deletion is unavailable while this organization has projects visible to you.";
    return null;
  }, [currentMembership?.role, isPersonal, projectCount]);

  const createOrganization = async (event: FormEvent) => {
    event.preventDefault();
    const name = newOrganizationName.trim();
    if (!name || busyKey) return;
    try {
      await onCreate(name);
      setNewOrganizationName("");
    } catch {
      // The panel error region reports the authoritative backend response.
    }
  };

  const renameOrganization = async (event: FormEvent) => {
    event.preventDefault();
    const name = renameName.trim();
    if (!name || !canAdminister || busyKey || name === organization?.name) return;
    try {
      await onRename(name);
    } catch {
      // The panel error region reports the authoritative backend response.
    }
  };

  const addMember = async (event: FormEvent) => {
    event.preventDefault();
    const userId = memberUserId.trim();
    if (!userId || !canAdminister || adminTargetBlocked || busyKey) return;
    try {
      await onAddMember(userId, isOwner ? memberRole : "member");
      setMemberUserId("");
      setMemberRole("member");
    } catch {
      // The panel error region reports the authoritative backend response.
    }
  };

  const removeMember = async () => {
    if (!memberToRemove) return;
    try {
      await onRemoveMember(memberToRemove.user_id);
      setMemberToRemove(null);
    } catch {
      // The panel error region reports the authoritative backend response.
    }
  };

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="right"
          className="w-full gap-0 border-l border-border/70 p-0 sm:max-w-lg"
          closeButtonClassName="right-3 top-3 inline-flex h-11 w-11 items-center justify-center rounded-control sm:right-4 sm:top-4"
        >
          <header className="shrink-0 border-b border-border/55 px-5 py-5 pr-16">
            <SheetTitle>Organization settings</SheetTitle>
            <SheetDescription className="mt-1 leading-5">
              Choose where projects live and manage access by exact user ID.
            </SheetDescription>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-5">
            <div className="space-y-5">
              {error ? (
                <div role="alert" className="rounded-control border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                  {error}
                </div>
              ) : null}

              <section aria-labelledby="sharing-id-title" className="rounded-panel bg-settings-surface p-4">
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
                  <div className="min-w-0">
                    <h2 id="sharing-id-title" className="text-sm font-semibold">Your sharing ID</h2>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      Give this exact ID to an organization owner or admin. It does not expose your projects by itself.
                    </p>
                    <code className="mt-2 block select-all break-all rounded-compact bg-background px-3 py-2 text-xs text-foreground">
                      {currentUserId}
                    </code>
                  </div>
                </div>
              </section>

              <section aria-labelledby="create-organization-title">
                <div className="flex items-center gap-2">
                  <Building2 className="h-4 w-4 text-muted-foreground" aria-hidden />
                  <h2 id="create-organization-title" className="text-sm font-semibold">Create a shared organization</h2>
                </div>
                <form onSubmit={(event) => void createOrganization(event)} className="mt-3 flex gap-2">
                  <Input
                    value={newOrganizationName}
                    onChange={(event) => setNewOrganizationName(event.target.value)}
                    placeholder="Organization name"
                    aria-label="New organization name"
                    maxLength={256}
                    disabled={Boolean(busyKey)}
                    className="h-11"
                  />
                  <Button type="submit" disabled={!newOrganizationName.trim() || Boolean(busyKey)} className="h-11 shrink-0 px-3">
                    {busyKey === "organization:create" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Plus className="h-4 w-4" aria-hidden />}
                    <span className="sr-only">Create organization</span>
                  </Button>
                </form>
              </section>

              <div className="h-px bg-border/55" aria-hidden />

              <section aria-labelledby="organization-select-label">
                <label id="organization-select-label" htmlFor="organization-management-select" className="text-xs font-medium text-muted-foreground">
                  Organization to manage
                </label>
                <select
                  id="organization-management-select"
                  value={organizationId ?? ""}
                  onChange={(event) => onSelectOrganization(event.target.value)}
                  disabled={!organizations.length || Boolean(busyKey)}
                  className="mt-1.5 h-11 w-full rounded-control border border-input bg-background px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
                >
                  {organizations.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id === personalOrganizationId ? `${item.name} — Personal` : item.name}
                    </option>
                  ))}
                </select>
              </section>

              {loading && !selectedDetail ? (
                <div role="status" aria-live="polite" className="space-y-3 rounded-panel bg-settings-surface p-4 text-sm text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    Loading organization…
                  </div>
                  <div className="h-10 animate-pulse rounded-control bg-muted motion-reduce:animate-none" />
                  <div className="h-24 animate-pulse rounded-control bg-muted/70 motion-reduce:animate-none" />
                </div>
              ) : !selectedDetail ? (
                <div className="rounded-panel bg-settings-surface px-4 py-5 text-sm text-muted-foreground">
                  <p>Organization details are unavailable.</p>
                  <Button type="button" variant="outline" onClick={() => void onRefresh()} className="mt-3">
                    Try again
                  </Button>
                </div>
              ) : (
                <>
                  <section aria-labelledby="organization-details-title" className="rounded-panel bg-settings-surface p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 id="organization-details-title" className="truncate text-base font-semibold">{organization?.name}</h2>
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                            {isPersonal ? "Personal scope" : "Shared scope"}
                          </span>
                        </div>
                        <p className="mt-1 text-xs leading-5 text-muted-foreground">
                          Your role: {currentMembership ? titleCaseRole(currentMembership.role) : "Unknown"} · {projectCount} visible {projectCount === 1 ? "project" : "projects"}
                        </p>
                      </div>
                    </div>
                    <p className="mt-3 text-xs leading-5 text-muted-foreground">
                      {isPersonal
                        ? "This is your permanent personal organization. Organization membership alone does not grant access to its existing projects."
                        : "Projects created in this scope belong to the organization. Project access is still enforced separately by the server."}
                    </p>

                    <form onSubmit={(event) => void renameOrganization(event)} className="mt-4">
                      <label htmlFor="organization-rename" className="text-xs font-medium">Organization name</label>
                      <div className="mt-1.5 flex gap-2">
                        <Input
                          id="organization-rename"
                          value={renameName}
                          onChange={(event) => setRenameName(event.target.value)}
                          maxLength={256}
                          disabled={!canAdminister || Boolean(busyKey)}
                          className="h-11 bg-background"
                        />
                        <Button
                          type="submit"
                          variant="outline"
                          disabled={!canAdminister || !renameName.trim() || renameName.trim() === organization?.name || Boolean(busyKey)}
                          className="h-11 shrink-0"
                        >
                          {busyKey === "organization:update" ? "Saving…" : "Rename"}
                        </Button>
                      </div>
                      {!canAdminister ? (
                        <p className="mt-1.5 text-xs text-muted-foreground">Only owners and admins can rename this organization.</p>
                      ) : null}
                    </form>
                  </section>

                  <section aria-labelledby="organization-members-title" className="overflow-hidden rounded-panel bg-settings-surface">
                    <header className="flex items-start gap-3 px-4 py-4">
                      <Users className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
                      <div>
                        <h2 id="organization-members-title" className="text-sm font-semibold">Members</h2>
                        <p className="mt-1 text-xs leading-5 text-muted-foreground">
                          Roles govern organization administration. User IDs are shown because no public member directory is available.
                        </p>
                      </div>
                    </header>

                    {selectedDetail.members.length ? (
                      <ul className="border-t border-border/45">
                        {selectedDetail.members.map((member) => {
                          const soleOwner = member.role === "owner" && ownerCount === 1;
                          const protectedFromAdmin = isAdmin && member.role !== "member";
                          const removable = canAdminister && !soleOwner && !protectedFromAdmin && !busyKey;
                          return (
                            <li key={member.user_id} className="flex min-h-14 items-center gap-3 border-t border-border/45 px-4 py-3 first:border-t-0">
                              <div className="min-w-0 flex-1">
                                <p className="break-all text-sm font-medium">
                                  {member.user_id}
                                  {member.user_id === currentUserId ? <span className="ml-1 text-xs font-normal text-muted-foreground">(you)</span> : null}
                                </p>
                                <p className="mt-0.5 text-xs text-muted-foreground">{titleCaseRole(member.role)}</p>
                              </div>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                disabled={!removable}
                                title={soleOwner
                                  ? "At least one owner is required"
                                  : protectedFromAdmin
                                    ? "Admins can remove members only"
                                    : !canAdminister
                                      ? "Owner or admin role required"
                                      : "Remove member"}
                                aria-label={`Remove ${member.user_id} from organization`}
                                onClick={() => setMemberToRemove(member)}
                                className="h-11 w-11 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                              >
                                {busyKey === `organization:member:remove:${member.user_id}` ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Trash2 className="h-4 w-4" aria-hidden />}
                              </Button>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <p className="border-t border-border/45 px-4 py-5 text-sm text-muted-foreground">No members are visible.</p>
                    )}

                    <form onSubmit={(event) => void addMember(event)} className="border-t border-border/45 p-4">
                      <div className="flex items-center gap-2">
                        <UserPlus className="h-4 w-4 text-muted-foreground" aria-hidden />
                        <h3 className="text-sm font-semibold">Add or update a member</h3>
                      </div>
                      <label htmlFor="organization-member-user-id" className="mt-3 block text-xs font-medium">Exact user ID</label>
                      <Input
                        id="organization-member-user-id"
                        value={memberUserId}
                        onChange={(event) => setMemberUserId(event.target.value)}
                        placeholder="User ID"
                        autoComplete="off"
                        maxLength={128}
                        disabled={!canAdminister || Boolean(busyKey)}
                        className="mt-1.5 h-11 bg-background"
                      />
                      <label htmlFor="organization-member-role" className="mt-3 block text-xs font-medium">Role</label>
                      <select
                        id="organization-member-role"
                        value={memberRole}
                        onChange={(event) => setMemberRole(event.target.value as CollaborationOrganizationRole)}
                        disabled={!isOwner || Boolean(busyKey)}
                        className="mt-1.5 h-11 w-full rounded-control border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
                      >
                        {availableRoles.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}
                      </select>
                      <Button
                        type="submit"
                        disabled={!canAdminister || adminTargetBlocked || !memberUserId.trim() || Boolean(busyKey)}
                        className="mt-3 h-11 w-full"
                      >
                        {busyKey?.startsWith("organization:member:add:") ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
                        {existingMember ? "Update member role" : "Add member"}
                      </Button>
                      {!canAdminister ? (
                        <p className="mt-2 text-xs text-muted-foreground">Only owners and admins can manage members.</p>
                      ) : adminTargetBlocked ? (
                        <p className="mt-2 text-xs text-muted-foreground">Admins cannot change or remove another admin or owner.</p>
                      ) : isAdmin ? (
                        <p className="mt-2 text-xs text-muted-foreground">Admins can add, update, and remove members only. Owner role is required to grant admin or owner access.</p>
                      ) : null}
                    </form>
                  </section>

                  <section aria-labelledby="organization-danger-title" className="rounded-panel border border-destructive/20 p-4">
                    <h2 id="organization-danger-title" className="text-sm font-semibold">Delete organization</h2>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      Only empty shared organizations can be deleted. This action permanently removes its membership roster.
                    </p>
                    <Button
                      type="button"
                      variant="destructive"
                      disabled={!canDelete || Boolean(busyKey)}
                      title={deleteReason ?? "Delete organization"}
                      onClick={() => setDeleteOpen(true)}
                      className="mt-3 h-11 w-full sm:w-auto"
                    >
                      <Trash2 className="mr-2 h-4 w-4" aria-hidden />
                      {deleteReason ?? "Delete organization"}
                    </Button>
                  </section>
                </>
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>

      <AlertDialog open={Boolean(memberToRemove)} onOpenChange={(next) => !next && setMemberToRemove(null)}>
        <AlertDialogContent className="w-[min(calc(100vw-2rem),24rem)]">
          <AlertDialogHeader>
            <AlertDialogTitle>Remove organization member?</AlertDialogTitle>
            <AlertDialogDescription className="break-words">
              Remove {memberToRemove?.user_id} from {organization?.name}? The server will preserve owner and access invariants.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void removeMember()}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Remove member
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent className="w-[min(calc(100vw-2rem),24rem)]">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {organization?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes the empty organization and its membership roster. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                void onDelete().then(() => setDeleteOpen(false)).catch(() => undefined);
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete organization
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
