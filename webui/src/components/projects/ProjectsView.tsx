import { useState, type FormEvent } from "react";
import {
  Brain,
  Building2,
  ChevronDown,
  Files,
  FolderKanban,
  ListTodo,
  Loader2,
  Menu,
  Plus,
  Settings2,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";

import { ContextSourcesPanel } from "@/components/projects/ContextSourcesPanel";
import { ExtensionsPanel } from "@/components/projects/ExtensionsPanel";
import { OrganizationManagement } from "@/components/projects/OrganizationManagement";
import { PersonalTasksPanel } from "@/components/projects/PersonalTasksPanel";
import { ProjectMembersPanel } from "@/components/projects/ProjectMembersPanel";
import { TaskBoard } from "@/components/projects/TaskBoard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCollaborationProjects } from "@/hooks/useCollaborationProjects";
import type { CollaborationOrganization, CollaborationProject } from "@/lib/types";
import { cn } from "@/lib/utils";

type ProjectPanel = "personal" | "tasks" | "members" | "extensions" | "context";

const PROJECT_PANELS: Array<{
  id: ProjectPanel;
  label: string;
  icon: LucideIcon;
}> = [
  { id: "personal", label: "My day", icon: ListTodo },
  { id: "tasks", label: "Tasks", icon: ListTodo },
  { id: "members", label: "Members", icon: Users },
  { id: "extensions", label: "Extensions", icon: Brain },
  { id: "context", label: "Context", icon: Files },
];

function OrganizationSelector({
  id,
  organizations,
  selectedId,
  personalOrganizationId,
  onSelect,
}: {
  id: string;
  organizations: CollaborationOrganization[];
  selectedId: string | null;
  personalOrganizationId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="relative min-w-0 flex-1">
      <label htmlFor={id} className="sr-only">Organization scope</label>
      <select
        id={id}
        value={selectedId ?? ""}
        onChange={(event) => onSelect(event.target.value)}
        disabled={!organizations.length}
        className="h-11 w-full appearance-none truncate rounded-control border border-input bg-background py-2 pl-3 pr-9 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
      >
        {organizations.map((organization) => (
          <option key={organization.id} value={organization.id}>
            {organization.id === personalOrganizationId ? `Personal — ${organization.name}` : `Organization — ${organization.name}`}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-3 top-3.5 h-4 w-4 text-muted-foreground" aria-hidden />
    </div>
  );
}

function ProjectList({
  projects,
  selectedId,
  onSelect,
}: {
  projects: CollaborationProject[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (!projects.length) {
    return (
      <div className="min-h-0 flex-1 px-3 py-5 text-xs leading-5 text-muted-foreground">
        No projects in this organization yet.
      </div>
    );
  }
  return (
    <nav aria-label="Projects in selected organization" className="min-h-0 flex-1 space-y-1 overflow-y-auto">
      {projects.map((project) => {
        const selected = project.id === selectedId;
        return (
          <button
            key={project.id}
            type="button"
            aria-current={selected ? "page" : undefined}
            onClick={() => onSelect(project.id)}
            className={cn(
              "touch-target flex min-h-10 w-full items-center gap-2 rounded-control px-3 py-2 text-left text-sm font-medium transition-colors",
              selected
                ? "bg-sidebar-selected text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-muted/55 hover:text-foreground",
            )}
          >
            <FolderKanban className="h-4 w-4 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1 truncate">{project.name}</span>
          </button>
        );
      })}
    </nav>
  );
}

function NewProjectForm({
  busy,
  organizationName,
  personal,
  onCancel,
  onCreate,
}: {
  busy: boolean;
  organizationName: string;
  personal: boolean;
  onCancel: () => void;
  onCreate: (name: string) => Promise<unknown>;
}) {
  const [name, setName] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = name.trim();
    if (!value || busy) return;
    try {
      await onCreate(value);
      setName("");
      onCancel();
    } catch {
      // The shared project error region reports mutation failures.
    }
  };

  return (
    <form onSubmit={(event) => void submit(event)} className="rounded-panel bg-settings-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor="new-project-name" className="text-sm font-semibold">New project</label>
        <Button type="button" variant="ghost" size="icon" onClick={onCancel} disabled={busy} className="h-9 w-9">
          <X className="h-4 w-4" aria-hidden />
          <span className="sr-only">Cancel new project</span>
        </Button>
      </div>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        This project will be created in {personal ? "your personal organization" : organizationName}.
      </p>
      <div className="mt-3 flex gap-2">
        <Input
          id="new-project-name"
          value={name}
          maxLength={512}
          autoFocus
          onChange={(event) => setName(event.target.value)}
          placeholder="Project name"
          disabled={busy}
          className="min-w-0 flex-1 bg-background"
        />
        <Button type="submit" disabled={!name.trim() || busy} className="shrink-0">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : "Create"}
        </Button>
      </div>
    </form>
  );
}

function LoadingSurface() {
  return (
    <div aria-busy="true" className="space-y-5 p-5 sm:p-8">
      <span className="sr-only">Loading projects</span>
      <div className="h-8 w-48 animate-pulse rounded-compact bg-muted motion-reduce:animate-none" />
      <div className="h-12 w-full animate-pulse rounded-control bg-muted/70 motion-reduce:animate-none" />
      <div className="h-48 w-full animate-pulse rounded-panel bg-muted/55 motion-reduce:animate-none" />
    </div>
  );
}

export function ProjectsView({
  onToggleSidebar,
  hostChromeInset = false,
}: {
  onToggleSidebar: () => void;
  hostChromeInset?: boolean;
}) {
  const projects = useCollaborationProjects();
  const [panel, setPanel] = useState<ProjectPanel>("tasks");
  const [showNewProject, setShowNewProject] = useState(false);
  const [showOrganizationManagement, setShowOrganizationManagement] = useState(false);
  const organizations = projects.summary?.organizations ?? [];
  const selectedOrganization = organizations.find((organization) => organization.id === projects.organizationId) ?? null;
  const organizationProjects = (projects.summary?.projects ?? []).filter((project) => (
    project.organization_id === projects.organizationId
  ));
  const selectedProject = organizationProjects.find((project) => project.id === projects.projectId) ?? null;
  const personalScope = selectedOrganization?.id === projects.personalOrganizationId;

  return (
    <>
      <div className="flex h-full min-h-0 bg-background">
        <aside className={cn(
          "hidden w-64 shrink-0 flex-col border-r border-border/45 bg-settings-surface px-3 pb-4 lg:flex",
          hostChromeInset ? "pt-16" : "pt-4",
        )}>
          <div className="mb-4 px-2">
            <p className="text-xs font-medium text-muted-foreground">Project workspace</p>
            <h1 className="mt-1 text-lg font-semibold tracking-tight">Projects</h1>
            {projects.summary?.user.display_name ? (
              <p className="mt-1 truncate text-xs text-muted-foreground">{projects.summary.user.display_name}</p>
            ) : null}
          </div>
          <div className="mb-3 space-y-2">
            <OrganizationSelector
              id="desktop-organization-switcher"
              organizations={organizations}
              selectedId={projects.organizationId}
              personalOrganizationId={projects.personalOrganizationId}
              onSelect={projects.selectOrganization}
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => setShowOrganizationManagement(true)}
              className="h-10 w-full justify-start px-3 text-muted-foreground"
            >
              <Settings2 className="mr-2 h-4 w-4" aria-hidden />
              Manage organizations
            </Button>
          </div>
          <ProjectList
            projects={organizationProjects}
            selectedId={projects.projectId}
            onSelect={projects.selectProject}
          />
          <Button
            type="button"
            variant="ghost"
            onClick={() => setShowNewProject(true)}
            disabled={!selectedOrganization}
            className="mt-2 h-10 w-full justify-start px-3 text-muted-foreground"
          >
            <Plus className="mr-2 h-4 w-4" aria-hidden />
            New project
          </Button>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className={cn(
            "shrink-0 border-b border-border/45 bg-background px-3 pb-3 sm:px-5",
            hostChromeInset ? "pt-12" : "pt-3",
          )}>
            <div className="flex min-h-11 items-center gap-2">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={onToggleSidebar}
                aria-label="Open navigation"
                className="h-11 w-11 shrink-0 lg:hidden"
              >
                <Menu className="h-5 w-5" aria-hidden />
              </Button>
              <div className="flex min-w-0 flex-1 gap-2 lg:hidden">
                <OrganizationSelector
                  id="mobile-organization-switcher"
                  organizations={organizations}
                  selectedId={projects.organizationId}
                  personalOrganizationId={projects.personalOrganizationId}
                  onSelect={projects.selectOrganization}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => setShowOrganizationManagement(true)}
                  aria-label="Manage organizations"
                  className="h-11 w-11 shrink-0"
                >
                  <Settings2 className="h-4 w-4" aria-hidden />
                </Button>
              </div>
              <div className="hidden min-w-0 flex-1 lg:block">
                <div className="flex min-w-0 items-center gap-2">
                  <h2 className="truncate text-lg font-semibold tracking-tight">{selectedProject?.name ?? selectedOrganization?.name ?? "Projects"}</h2>
                  {selectedOrganization ? (
                    <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                      {personalScope ? "Personal" : "Shared"}
                    </span>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="mt-2 flex gap-2 lg:hidden">
              <div className="relative min-w-0 flex-1">
                <label htmlFor="mobile-project-switcher" className="sr-only">Current project</label>
                <select
                  id="mobile-project-switcher"
                  value={projects.projectId ?? ""}
                  onChange={(event) => projects.selectProject(event.target.value)}
                  disabled={!organizationProjects.length}
                  className="h-11 w-full appearance-none truncate rounded-control border border-input bg-background py-2 pl-3 pr-9 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
                >
                  {!organizationProjects.length ? <option value="">No projects in this organization</option> : null}
                  {organizationProjects.map((project) => (
                    <option key={project.id} value={project.id}>{project.name}</option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-3 top-3.5 h-4 w-4 text-muted-foreground" aria-hidden />
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={() => setShowNewProject((open) => !open)}
                disabled={!selectedOrganization}
                aria-label="New project"
                className="h-11 w-11 shrink-0 px-0 sm:w-auto sm:px-3"
              >
                <Plus className="h-4 w-4" aria-hidden />
                <span className="ml-2 hidden sm:inline">New project</span>
              </Button>
            </div>

            {selectedProject ? (
              <nav aria-label="Project sections" className="mt-3 flex gap-1 overflow-x-auto rounded-control bg-muted p-1">
                {PROJECT_PANELS.map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    type="button"
                    aria-current={panel === id ? "page" : undefined}
                    onClick={() => setPanel(id)}
                    className={cn(
                      "flex h-11 min-w-fit flex-1 items-center justify-center gap-2 rounded-compact px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      panel === id ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    <Icon className="h-4 w-4" aria-hidden />
                    {label}
                  </button>
                ))}
              </nav>
            ) : null}
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-5xl space-y-5 p-4 pb-10 sm:p-6 lg:p-8">
              {projects.error ? (
                <div role="alert" className="flex items-start justify-between gap-3 rounded-control border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                  <span>{projects.error}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => projects.setError(null)}
                    aria-label="Dismiss error"
                    className="-mr-2 -mt-2 h-9 w-9 shrink-0 text-destructive"
                  >
                    <X className="h-4 w-4" aria-hidden />
                  </Button>
                </div>
              ) : null}

              {showNewProject && selectedOrganization ? (
                <NewProjectForm
                  busy={projects.busyKey === "project:create"}
                  organizationName={selectedOrganization.name}
                  personal={personalScope}
                  onCancel={() => setShowNewProject(false)}
                  onCreate={projects.createProject}
                />
              ) : null}

              {projects.loading && !projects.summary ? (
                <LoadingSurface />
              ) : !organizations.length ? (
                <div className="rounded-panel bg-settings-surface px-5 py-10 text-center">
                  <Building2 className="mx-auto h-6 w-6 text-muted-foreground" aria-hidden />
                  <h2 className="mt-3 text-base font-semibold">No organization is available</h2>
                  <p className="mx-auto mt-1 max-w-md text-sm leading-6 text-muted-foreground">
                    Open organization settings to create a shared workspace or retry loading your personal scope.
                  </p>
                  <Button type="button" onClick={() => setShowOrganizationManagement(true)} className="mt-5">
                    <Settings2 className="mr-2 h-4 w-4" aria-hidden />
                    Organization settings
                  </Button>
                </div>
              ) : !organizationProjects.length ? (
                <div className="rounded-panel bg-settings-surface px-5 py-10 text-center">
                  <FolderKanban className="mx-auto h-6 w-6 text-muted-foreground" aria-hidden />
                  <h2 className="mt-3 text-base font-semibold">No projects in {selectedOrganization?.name}</h2>
                  <p className="mx-auto mt-1 max-w-md text-sm leading-6 text-muted-foreground">
                    Create a project in this {personalScope ? "personal" : "shared"} scope. Other organizations’ projects stay separate.
                  </p>
                  {!showNewProject ? (
                    <Button type="button" onClick={() => setShowNewProject(true)} className="mt-5">
                      <Plus className="mr-2 h-4 w-4" aria-hidden />
                      New project
                    </Button>
                  ) : null}
                </div>
              ) : projects.detailLoading ? (
                <LoadingSurface />
              ) : !projects.detail ? (
                <div className="rounded-panel bg-settings-surface px-5 py-8 text-center">
                  <p className="text-sm font-medium">Project details are unavailable</p>
                  <p className="mx-auto mt-1 max-w-md text-sm leading-6 text-muted-foreground">
                    Check the connection or your project access, then try again.
                  </p>
                  <Button type="button" variant="outline" onClick={() => void projects.refreshDetail()} className="mt-4">
                    Try again
                  </Button>
                </div>
              ) : panel === "personal" ? (
                <PersonalTasksPanel />
              ) : panel === "tasks" ? (
                <TaskBoard
                  detail={projects.detail}
                  busyKey={projects.busyKey}
                  onCreateList={projects.createTaskList}
                  onCreateTask={projects.createTask}
                  onUpdateTaskStatus={projects.updateTaskStatus}
                  onDeleteTask={projects.deleteTask}
                />
              ) : panel === "members" ? (
                <ProjectMembersPanel
                  detail={projects.detail}
                  currentUserId={projects.summary?.user.id ?? ""}
                  organizationMembers={projects.organizationDetail?.organization.id === projects.detail.project.organization_id
                    ? projects.organizationDetail.members
                    : []}
                  organizationLoading={projects.organizationLoading}
                  busyKey={projects.busyKey}
                  onAddMember={projects.addProjectMember}
                  onRemoveMember={projects.removeProjectMember}
                />
              ) : panel === "extensions" ? (
                <ExtensionsPanel
                  detail={projects.detail}
                  busy={projects.busyKey === "extensions:save"}
                  onSave={projects.saveExtensions}
                />
              ) : (
                <ContextSourcesPanel
                  detail={projects.detail}
                  busyKey={projects.busyKey}
                  onCreate={projects.createContextSource}
                  onToggle={projects.toggleContextSource}
                  onDelete={projects.deleteContextSource}
                />
              )}
            </div>
          </div>
        </div>
      </div>

      <OrganizationManagement
        open={showOrganizationManagement}
        onOpenChange={setShowOrganizationManagement}
        organizations={organizations}
        organizationId={projects.organizationId}
        detail={projects.organizationDetail}
        currentUserId={projects.summary?.user.id ?? ""}
        personalOrganizationId={projects.personalOrganizationId}
        projectCount={organizationProjects.length}
        loading={projects.organizationLoading}
        error={projects.organizationError ?? projects.error}
        busyKey={projects.busyKey}
        onSelectOrganization={projects.selectOrganization}
        onRefresh={projects.refreshOrganization}
        onCreate={projects.createOrganization}
        onRename={projects.renameOrganization}
        onDelete={projects.removeOrganization}
        onAddMember={projects.addOrganizationMember}
        onRemoveMember={projects.removeOrganizationMember}
      />
    </>
  );
}
