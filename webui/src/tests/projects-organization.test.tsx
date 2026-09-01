import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { OrganizationManagement } from "@/components/projects/OrganizationManagement";
import { ProjectMembersPanel } from "@/components/projects/ProjectMembersPanel";
import { ProjectsView } from "@/components/projects/ProjectsView";
import { useCollaborationProjects } from "@/hooks/useCollaborationProjects";
import type {
  CollaborationOrganization,
  CollaborationOrganizationMember,
  CollaborationOrganizationPayload,
  CollaborationOrganizationRole,
  CollaborationProject,
  CollaborationProjectMember,
  CollaborationProjectPayload,
  CollaborationProjectRole,
} from "@/lib/types";

vi.mock("@/hooks/useCollaborationProjects", () => ({
  useCollaborationProjects: vi.fn(),
}));

const timestamp = 1_735_689_600_000;

function organization(id: string, name: string, isPersonal = false): CollaborationOrganization {
  return {
    id,
    name,
    is_personal: isPersonal,
    created_by_user_id: "user-1",
    created_at_ms: timestamp,
    updated_at_ms: timestamp,
  };
}

function member(userId: string, role: CollaborationOrganizationRole): CollaborationOrganizationMember {
  return {
    organization_id: "org-studio",
    user_id: userId,
    role,
    created_at_ms: timestamp,
  };
}

function projectMember(userId: string, role: CollaborationProjectRole): CollaborationProjectMember {
  return {
    project_id: "project-studio",
    user_id: userId,
    role,
    created_at_ms: timestamp,
  };
}

function projectDetail(members: CollaborationProjectMember[]): CollaborationProjectPayload {
  return {
    project: {
      id: "project-studio",
      name: "Team roadmap",
      organization_id: "org-studio",
      created_at_ms: timestamp,
      updated_at_ms: timestamp,
    },
    members,
    task_lists: [],
    tasks: [],
    extension_profile: { revision: 1, settings: {} },
    available: { skills: [], mcp_servers: [] },
    context_sources: [],
  };
}

function managementProps(overrides: Partial<ComponentProps<typeof OrganizationManagement>> = {}) {
  const studio = organization("org-studio", "Studio");
  const detail: CollaborationOrganizationPayload = {
    organization: studio,
    members: [member("user-1", "owner"), member("member-2", "member")],
  };
  return {
    open: true,
    onOpenChange: vi.fn(),
    organizations: [studio],
    organizationId: studio.id,
    detail,
    currentUserId: "user-1",
    personalOrganizationId: null,
    projectCount: 0,
    loading: false,
    error: null,
    busyKey: null,
    onSelectOrganization: vi.fn(),
    onRefresh: vi.fn().mockResolvedValue(undefined),
    onCreate: vi.fn().mockResolvedValue(undefined),
    onRename: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    onAddMember: vi.fn().mockResolvedValue(undefined),
    onRemoveMember: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe("OrganizationManagement", () => {
  it("lets admins manage members without granting owner or admin roles", async () => {
    const props = managementProps({
      detail: {
        organization: organization("org-studio", "Studio"),
        members: [member("user-1", "admin"), member("member-2", "member")],
      },
    });
    render(<OrganizationManagement {...props} />);

    fireEvent.change(screen.getByRole("textbox", { name: "New organization name" }), {
      target: { value: "  Design guild  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create organization" }));
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith("Design guild"));

    fireEvent.change(screen.getByRole("textbox", { name: "Organization name" }), {
      target: { value: "Studio North" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(props.onRename).toHaveBeenCalledWith("Studio North"));

    const roleSelector = screen.getByRole("combobox", { name: "Role" });
    expect(roleSelector).toBeDisabled();
    expect(within(roleSelector).getByRole("option", { name: "Member" })).toBeInTheDocument();
    expect(within(roleSelector).queryByRole("option", { name: "Admin" })).not.toBeInTheDocument();
    expect(within(roleSelector).queryByRole("option", { name: "Owner" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "Exact user ID" }), {
      target: { value: "  user-42  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    await waitFor(() => expect(props.onAddMember).toHaveBeenCalledWith("user-42", "member"));
  });

  it("prevents admins from changing or removing owner and admin targets", () => {
    const props = managementProps({
      detail: {
        organization: organization("org-studio", "Studio"),
        members: [
          member("user-1", "admin"),
          member("owner-2", "owner"),
          member("admin-2", "admin"),
          member("member-2", "member"),
        ],
      },
    });
    render(<OrganizationManagement {...props} />);

    fireEvent.change(screen.getByRole("textbox", { name: "Exact user ID" }), {
      target: { value: "owner-2" },
    });
    expect(screen.getByRole("button", { name: "Update member role" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove owner-2 from organization" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove admin-2 from organization" })).toBeDisabled();

    fireEvent.change(screen.getByRole("textbox", { name: "Exact user ID" }), {
      target: { value: "admin-2" },
    });
    expect(screen.getByRole("button", { name: "Update member role" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Exact user ID" }), {
      target: { value: "member-2" },
    });
    expect(screen.getByRole("button", { name: "Update member role" })).toBeEnabled();
  });

  it("updates an existing member's role and removes that exact member only after confirmation", async () => {
    const props = managementProps();
    render(<OrganizationManagement {...props} />);

    fireEvent.change(screen.getByRole("textbox", { name: "Exact user ID" }), {
      target: { value: "member-2" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Role" }), {
      target: { value: "admin" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update member role" }));
    await waitFor(() => expect(props.onAddMember).toHaveBeenCalledWith("member-2", "admin"));

    fireEvent.click(screen.getByRole("button", { name: "Remove member-2 from organization" }));
    const confirmation = screen.getByRole("alertdialog", { name: "Remove organization member?" });
    expect(within(confirmation).getByText(/Remove member-2 from Studio/)).toBeInTheDocument();
    expect(props.onRemoveMember).not.toHaveBeenCalled();

    fireEvent.click(within(confirmation).getByRole("button", { name: "Remove member" }));
    await waitFor(() => expect(props.onRemoveMember).toHaveBeenCalledWith("member-2"));
  });

  it("keeps member administration controls inert for members", () => {
    const props = managementProps({
      detail: {
        organization: organization("org-studio", "Studio"),
        members: [member("user-1", "member"), member("member-2", "member")],
      },
    });
    render(<OrganizationManagement {...props} />);

    expect(screen.getByRole("textbox", { name: "Organization name" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Exact user ID" })).toBeDisabled();
    const remove = screen.getByRole("button", { name: "Remove member-2 from organization" });
    expect(remove).toBeDisabled();
    fireEvent.click(remove);

    expect(props.onRemoveMember).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog", { name: "Remove organization member?" })).not.toBeInTheDocument();
  });

  it.each([
    { name: "an admin", role: "admin" as const, personalOrganizationId: null, projectCount: 0, buttonName: "Only an organization owner can delete this workspace." },
    { name: "a personal organization owner", role: "owner" as const, personalOrganizationId: "org-studio", projectCount: 0, buttonName: "Your personal organization is permanent." },
    { name: "an owner of an organization with projects", role: "owner" as const, personalOrganizationId: null, projectCount: 1, buttonName: "Deletion is unavailable while this organization has projects visible to you." },
  ])("does not offer deletion to $name", ({ role, personalOrganizationId, projectCount, buttonName }) => {
    const props = managementProps({
      detail: {
        organization: organization("org-studio", "Studio", personalOrganizationId !== null),
        members: [member("user-1", role)],
      },
      personalOrganizationId,
      projectCount,
    });
    render(<OrganizationManagement {...props} />);

    expect(screen.getByRole("button", { name: buttonName })).toBeDisabled();
    expect(props.onDelete).not.toHaveBeenCalled();
  });

  it("confirms deletion only for an empty shared organization owned by the current user", async () => {
    const props = managementProps();
    render(<OrganizationManagement {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "Delete organization" }));
    const confirmation = screen.getByRole("alertdialog", { name: "Delete Studio?" });
    fireEvent.click(within(confirmation).getByRole("button", { name: "Delete organization" }));

    await waitFor(() => expect(props.onDelete).toHaveBeenCalledTimes(1));
  });

  it("renders the backend's actionable error", () => {
    render(<OrganizationManagement {...managementProps({ error: "Only owners can remove the last owner." })} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Only owners can remove the last owner.");
  });
});

describe("ProjectMembersPanel", () => {
  it("lets a project owner add from the organization roster, update by exact ID, and confirm removal", async () => {
    const onAddMember = vi.fn().mockResolvedValue(undefined);
    const onRemoveMember = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectMembersPanel
        detail={projectDetail([projectMember("user-1", "owner"), projectMember("project-member", "member")])}
        currentUserId="user-1"
        organizationMembers={[member("roster-user", "member"), member("project-member", "member")]}
        organizationLoading={false}
        busyKey={null}
        onAddMember={onAddMember}
        onRemoveMember={onRemoveMember}
      />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Choose from organization" }), {
      target: { value: "roster-user" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Project role" }), {
      target: { value: "member" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    await waitFor(() => expect(onAddMember).toHaveBeenCalledWith("roster-user", "member"));

    fireEvent.change(screen.getByRole("textbox", { name: "Or exact user ID" }), {
      target: { value: "project-member" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Project role" }), {
      target: { value: "owner" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update role" }));
    await waitFor(() => expect(onAddMember).toHaveBeenCalledWith("project-member", "owner"));

    fireEvent.click(screen.getByRole("button", { name: "Remove project-member from project" }));
    const confirmation = screen.getByRole("alertdialog", { name: "Remove project member?" });
    expect(within(confirmation).getByText(/Remove project-member from Team roadmap/)).toBeInTheDocument();
    expect(onRemoveMember).not.toHaveBeenCalled();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Remove member" }));
    await waitFor(() => expect(onRemoveMember).toHaveBeenCalledWith("project-member"));
  });

  it("leaves project members read-only", () => {
    render(
      <ProjectMembersPanel
        detail={projectDetail([projectMember("user-1", "member"), projectMember("owner-2", "owner")])}
        currentUserId="user-1"
        organizationMembers={[member("owner-2", "owner")]}
        organizationLoading={false}
        busyKey={null}
        onAddMember={vi.fn().mockResolvedValue(undefined)}
        onRemoveMember={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByText("Read-only membership")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Or exact user ID" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove owner-2 from project" })).not.toBeInTheDocument();
  });
});

describe("ProjectsView organization scope", () => {
  it("keeps projects scoped to the selected organization and exposes the mobile selector by name", () => {
    const personal = organization("org-personal", "Ari's workspace", true);
    const studio = organization("org-studio", "Studio");
    const privateProject: CollaborationProject = {
      id: "project-private",
      name: "Private roadmap",
      organization_id: personal.id,
      created_at_ms: timestamp,
      updated_at_ms: timestamp,
    };
    const sharedProject: CollaborationProject = {
      id: "project-shared",
      name: "Team roadmap",
      organization_id: studio.id,
      created_at_ms: timestamp,
      updated_at_ms: timestamp,
    };
    const selectOrganization = vi.fn();

    vi.mocked(useCollaborationProjects).mockReturnValue({
      summary: {
        user: { id: "user-1", display_name: "Ari" },
        organizations: [personal, studio],
        projects: [privateProject, sharedProject],
        active_project_id: sharedProject.id,
      },
      organizationId: studio.id,
      personalOrganizationId: personal.id,
      projectId: sharedProject.id,
      detail: null,
      loading: false,
      detailLoading: false,
      organizationDetail: null,
      organizationLoading: false,
      organizationError: null,
      busyKey: null,
      error: "User is not a member of this organization.",
      setError: vi.fn(),
      selectOrganization,
      selectProject: vi.fn(),
      reload: vi.fn(),
      refreshOrganization: vi.fn(),
      refreshDetail: vi.fn(),
      createOrganization: vi.fn(),
      renameOrganization: vi.fn(),
      removeOrganization: vi.fn(),
      addOrganizationMember: vi.fn(),
      removeOrganizationMember: vi.fn(),
      addProjectMember: vi.fn(),
      removeProjectMember: vi.fn(),
      createProject: vi.fn(),
      createTaskList: vi.fn(),
      createTask: vi.fn(),
      updateTaskStatus: vi.fn(),
      deleteTask: vi.fn(),
      saveExtensions: vi.fn(),
      createContextSource: vi.fn(),
      toggleContextSource: vi.fn(),
      deleteContextSource: vi.fn(),
    } as never);

    render(<ProjectsView onToggleSidebar={vi.fn()} />);

    const mobileOrganizationSelector = document.getElementById("mobile-organization-switcher");
    expect(mobileOrganizationSelector).toHaveAccessibleName("Organization scope");
    fireEvent.change(mobileOrganizationSelector as HTMLSelectElement, { target: { value: personal.id } });
    expect(screen.getByRole("alert")).toHaveTextContent("User is not a member of this organization.");
    expect(selectOrganization).toHaveBeenCalledWith(personal.id);

    const projectSelector = screen.getByRole("combobox", { name: "Current project" });
    expect(within(projectSelector).getByRole("option", { name: "Team roadmap" })).toBeInTheDocument();
    expect(within(projectSelector).queryByRole("option", { name: "Private roadmap" })).not.toBeInTheDocument();
  });
});
