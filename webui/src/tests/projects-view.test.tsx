import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CapabilitiesPanel } from "@/components/projects/CapabilitiesPanel";
import { ProjectsView } from "@/components/projects/ProjectsView";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import i18n from "@/i18n";
import {
  controller,
  detail,
  project,
  wrap,
} from "@/tests/collaboration-fixtures";

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  await i18n.changeLanguage("en");
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
  it("shows the project sections, marks administrators, and confirms deletion", async () => {
    const projects = controller();

    render(wrap(<ProjectsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(screen.getByText("Ari · Administrator")).toBeInTheDocument();
    const sections = screen.getByRole("navigation", { name: "Project sections" });
    // Channels are their own surface now, so this view offers only the two
    // panels that are genuinely about the project itself.
    expect(within(sections).queryByRole("button", { name: "Channels" })).not.toBeInTheDocument();
    expect(within(sections).getByRole("button", { name: "Members" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { name: "Members" })).toBeInTheDocument();
    fireEvent.click(within(sections).getByRole("button", { name: "Capabilities" }));
    expect(screen.getByRole("heading", { name: "Capabilities" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete project" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete project", hidden: false }));
    await waitFor(() => expect(projects.removeProject).toHaveBeenCalled());
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
