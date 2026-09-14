import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilitiesPanel } from "@/components/projects/CapabilitiesPanel";
import { ProjectsView } from "@/components/projects/ProjectsView";
import { useCollaborationProjects } from "@/hooks/useCollaborationProjects";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import * as api from "@/lib/api";
import type { CollaborationPayload } from "@/lib/types";
import i18n from "@/i18n";
import { ClientProvider } from "@/providers/ClientProvider";
import {
  controller,
  detail,
  project,
  secondProject,
  wrap,
} from "@/tests/collaboration-fixtures";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchCollaboration: vi.fn(),
    fetchCollaborationProject: vi.fn(),
  };
});

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  await i18n.changeLanguage("en");
});

beforeEach(() => {
  vi.mocked(api.fetchCollaboration).mockReset();
  vi.mocked(api.fetchCollaborationProject).mockReset();
});

describe("CapabilitiesPanel", () => {
  it("saves explicit allowlists and lifts them back to unrestricted", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <CapabilitiesPanel
        detail={detail({ project: { ...project, allowed_skills: ["architecture"], allowed_mcp_servers: null } })}
        busy={false}
        canManage
        onSave={onSave}
      />,
    );

    expect(screen.getByRole("button", { name: "Save capabilities" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Architecture/ })).toBeChecked();

    fireEvent.click(screen.getByRole("checkbox", { name: /Architecture/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save capabilities" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ allowed_skills: [], allowed_mcp_servers: null }));

    const [skillsUnrestricted] = screen.getAllByRole("checkbox", { name: "Unrestricted" });
    fireEvent.click(skillsUnrestricted);
    fireEvent.click(screen.getByRole("button", { name: "Save capabilities" }));
    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith({ allowed_skills: null, allowed_mcp_servers: null }));
  });

  it("is read-only for members who cannot manage the project", () => {
    render(
      <CapabilitiesPanel detail={detail({ can_manage: false })} busy={false} canManage={false} onSave={vi.fn()} />,
    );

    expect(screen.queryByRole("button", { name: "Save capabilities" })).not.toBeInTheDocument();
    screen.getAllByRole("checkbox").forEach((box) => expect(box).toBeDisabled());
  });
});

describe("ProjectsView", () => {
  function activeProjectSummary(defaultProjectId: string): CollaborationPayload {
    return {
      user: { id: "user-1", display_name: "Ari", is_admin: true, default_project_id: defaultProjectId },
      is_admin: true,
      projects: [project, secondProject],
      assignments: [],
      active_project_id: defaultProjectId,
      manageable_project_ids: [project.id, secondProject.id],
    };
  }

  function ProjectsScreen() {
    const projects = useCollaborationProjects();
    return <ProjectsView projects={projects} onToggleSidebar={vi.fn()} />;
  }

  it("moves the active project only through the explicit header control", async () => {
    const requestMutation = vi.fn().mockResolvedValue(undefined);
    vi.mocked(api.fetchCollaboration)
      .mockResolvedValueOnce(activeProjectSummary(project.id))
      .mockResolvedValue(activeProjectSummary(secondProject.id));
    vi.mocked(api.fetchCollaborationProject).mockImplementation(async (_token, id) => (
      detail({ project: id === secondProject.id ? secondProject : project })
    ));

    render(
      <ClientProvider client={{ requestMutation } as never} token="token">
        <ProjectsScreen />
      </ClientProvider>,
    );

    // The project the user is already on is reported as a badge, never as a control
    // that could "re-activate" it.
    expect(await screen.findByRole("heading", { name: "Release train" })).toBeInTheDocument();
    expect(screen.getByText("Active project")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set as active project" })).not.toBeInTheDocument();

    // Looking at the other project is not activation: no default-project write, and
    // the header now offers the control instead of claiming it is active.
    fireEvent.click(screen.getByRole("button", { name: "Support desk" }));
    expect(await screen.findByRole("heading", { name: "Support desk" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set as active project" })).toBeInTheDocument();
    expect(requestMutation).not.toHaveBeenCalled();

    // Only the control re-homes the default, and the header follows the new state.
    fireEvent.click(screen.getByRole("button", { name: "Set as active project" }));
    await waitFor(() => expect(requestMutation).toHaveBeenCalledTimes(1));
    expect(requestMutation).toHaveBeenCalledWith(
      "collaboration.user.defaults",
      { project_id: secondProject.id },
      expect.any(Number),
    );
    await waitFor(() => expect(screen.getByText("Active project")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Set as active project" })).not.toBeInTheDocument();
  });

  it("shows the project sections, marks administrators, and confirms deletion", async () => {
    const projects = controller();

    render(wrap(<ProjectsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(screen.getByText("Ari · Administrator")).toBeInTheDocument();
    const sections = screen.getByRole("navigation", { name: "Project sections" });
    // Channels serve a project, so assigning them belongs to the project itself.
    expect(within(sections).getByRole("button", { name: "Channels" })).toBeInTheDocument();
    // The board is where a project's work lives, so it opens first.
    expect(within(sections).getByRole("button", { name: "Tasks" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { name: "Task board" })).toBeInTheDocument();
    fireEvent.click(within(sections).getByRole("button", { name: "Members" }));
    expect(screen.getByRole("heading", { name: "Members" })).toBeInTheDocument();
    fireEvent.click(within(sections).getByRole("button", { name: "Capabilities" }));
    expect(screen.getByRole("heading", { name: "Capabilities" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete project" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete project", hidden: false }));
    await waitFor(() => expect(projects.removeProject).toHaveBeenCalled());
  });

  it("renames the project for every member", async () => {
    const renameProject = vi.fn().mockResolvedValue(undefined);
    const projects = controller({ renameProject });

    render(wrap(<ProjectsView projects={projects} onToggleSidebar={vi.fn()} />));

    fireEvent.click(screen.getByRole("button", { name: "Rename project" }));
    const field = await screen.findByRole("textbox");
    expect(field).toHaveValue("Release train");
    fireEvent.change(field, { target: { value: "  Support train  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(renameProject).toHaveBeenCalledWith("Support train"));
  });

  it("keeps the built-in project permanent while still allowing a rename", () => {
    const builtin = { ...project, id: "project-builtin", name: "MikoAssistant", is_builtin: true };
    const projects = controller({
      projectId: builtin.id,
      summary: {
        user: { id: "user-1", display_name: "Ari", is_admin: true, default_project_id: builtin.id },
        is_admin: true,
        projects: [builtin],
        assignments: [],
        active_project_id: builtin.id,
        manageable_project_ids: [builtin.id],
      },
      detail: detail({ project: builtin }),
    } as Partial<CollaborationProjectsController>);

    render(wrap(<ProjectsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(screen.getByRole("heading", { name: "MikoAssistant" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rename project" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete project" })).not.toBeInTheDocument();
    expect(screen.getByText(/cannot be deleted/)).toBeInTheDocument();
  });

  it("shows the project's built-in automations without offering to remove them", async () => {
    const projects = controller();

    render(wrap(<ProjectsView projects={projects} onToggleSidebar={vi.fn()} />));

    const sections = screen.getByRole("navigation", { name: "Project sections" });
    fireEvent.click(within(sections).getByRole("button", { name: "Automations" }));

    expect(await screen.findByText("Heartbeat")).toBeInTheDocument();
    expect(screen.getByText("Protected")).toBeInTheDocument();
    expect(screen.getByText(/Reads this project's HEARTBEAT\.md/)).toBeInTheDocument();
    const panel = screen.getByRole("heading", { name: "Automations" }).closest("section");
    expect(panel).not.toBeNull();
    // Built-in automations are read-only here: no delete, pause, or run control.
    expect(within(panel as HTMLElement).queryByRole("button")).not.toBeInTheDocument();
    expect(within(panel as HTMLElement).queryByRole("switch")).not.toBeInTheDocument();
  });

  it("offers project creation when no project exists", () => {
    const projects = controller({
      summary: {
        user: { id: "user-1", display_name: "Ari", is_admin: false, default_project_id: null },
        is_admin: false,
        projects: [],
        assignments: [],
        active_project_id: null,
        manageable_project_ids: [],
      },
      isAdmin: false,
      projectId: null,
      detail: null,
    } as Partial<CollaborationProjectsController>);

    render(wrap(<ProjectsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(screen.getByRole("heading", { name: "No projects yet." })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "New project" })[0]);
    expect(screen.getByRole("textbox", { name: "New project" })).toBeInTheDocument();
  });
});
