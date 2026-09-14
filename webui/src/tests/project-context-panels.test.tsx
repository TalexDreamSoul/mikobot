import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectMaterialsPanel } from "@/components/projects/ProjectMaterialsPanel";
import { ProjectMemoryPanel } from "@/components/projects/ProjectMemoryPanel";
import { ProjectOverviewPanel } from "@/components/projects/ProjectOverviewPanel";
import i18n from "@/i18n";
import * as api from "@/lib/api";
import { detail, project, wrap } from "@/tests/collaboration-fixtures";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof api>();
  return {
    ...actual,
    fetchCollaborationProjectMaterials: vi.fn(),
    fetchCollaborationProjectMemory: vi.fn(),
  };
});

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  await i18n.changeLanguage("en");
});

beforeEach(() => {
  vi.mocked(api.fetchCollaborationProjectMaterials).mockReset();
  vi.mocked(api.fetchCollaborationProjectMemory).mockReset();
});

describe("ProjectOverviewPanel", () => {
  it("saves the introduction a manager edited", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      wrap(
        <ProjectOverviewPanel
          detail={detail({ project: { ...project, description: "Original context" } })}
          canManage
          busyKey={null}
          onSave={onSave}
        />,
      ),
    );

    const field = screen.getByRole("textbox", { name: "Project introduction and context" });
    expect(field).toHaveValue("Original context");
    expect(screen.getByRole("button", { name: "Save introduction" })).toBeDisabled();

    fireEvent.change(field, { target: { value: "  Ship every Friday.  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save introduction" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("Ship every Friday."));
  });

  it("keeps the introduction read-only for a member who cannot manage the project", () => {
    render(
      wrap(
        <ProjectOverviewPanel
          detail={detail({ project: { ...project, description: "Original context" } })}
          canManage={false}
          busyKey={null}
          onSave={vi.fn()}
        />,
      ),
    );

    expect(
      screen.getByRole("textbox", { name: "Project introduction and context" }),
    ).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save introduction" })).not.toBeInTheDocument();
    expect(screen.getByText("Only a project owner can edit the introduction.")).toBeInTheDocument();
  });
});

describe("ProjectMaterialsPanel", () => {
  const files = [
    { path: "brief.md", size: 30, previewable: true, modified_at_ms: 1 },
    { path: "assets/logo.png", size: 2048, previewable: false, modified_at_ms: 1 },
  ];

  it("lists shared files and previews the one a member opens", async () => {
    vi.mocked(api.fetchCollaborationProjectMaterials)
      .mockResolvedValueOnce({ files })
      .mockResolvedValueOnce({
        path: "brief.md",
        size: 24,
        previewable: true,
        truncated: false,
        content: "# Brief\nShip every Friday.",
      });

    render(wrap(<ProjectMaterialsPanel projectId={project.id} />));

    expect(await screen.findByText("brief.md")).toBeInTheDocument();
    expect(screen.getByText("assets/logo.png")).toBeInTheDocument();
    expect(api.fetchCollaborationProjectMaterials).toHaveBeenCalledWith("token", project.id);
    expect(screen.getByRole("button", { name: /assets\/logo\.png/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /brief\.md/ }));

    await waitFor(() =>
      expect(api.fetchCollaborationProjectMaterials).toHaveBeenLastCalledWith(
        "token",
        project.id,
        "brief.md",
      ),
    );
    expect(await screen.findByText(/# Brief/)).toBeInTheDocument();
  });

  it("surfaces a listing failure instead of showing an empty project", async () => {
    vi.mocked(api.fetchCollaborationProjectMaterials).mockRejectedValueOnce(
      new Error("Project materials unavailable"),
    );

    render(wrap(<ProjectMaterialsPanel projectId={project.id} />));

    expect(await screen.findByRole("alert")).toHaveTextContent("Project materials unavailable");
  });
});

describe("ProjectMemoryPanel", () => {
  it("loads and shows the project's shared MEMORY.md from the knowledge endpoint", async () => {
    vi.mocked(api.fetchCollaborationProjectMemory).mockResolvedValueOnce({
      path: "memory/MEMORY.md",
      size: 21,
      previewable: true,
      truncated: false,
      content: "Shared project facts.",
    });

    render(wrap(<ProjectMemoryPanel projectId={project.id} />));

    expect(api.fetchCollaborationProjectMemory).toHaveBeenCalledWith("token", project.id);
    expect(await screen.findByText("Shared project facts.")).toBeInTheDocument();
    expect(screen.getByText("memory/MEMORY.md")).toBeInTheDocument();
  });

  it("surfaces a knowledge load failure instead of swallowing it", async () => {
    vi.mocked(api.fetchCollaborationProjectMemory).mockRejectedValueOnce(
      new Error("Project knowledge unavailable"),
    );

    render(wrap(<ProjectMemoryPanel projectId={project.id} />));

    expect(await screen.findByRole("alert")).toHaveTextContent("Project knowledge unavailable");
  });

  it("shows the empty state when the project has no shared knowledge yet", async () => {
    vi.mocked(api.fetchCollaborationProjectMemory).mockResolvedValueOnce({
      path: "memory/MEMORY.md",
      size: 0,
      previewable: false,
      truncated: false,
      content: "",
    });

    render(wrap(<ProjectMemoryPanel projectId={project.id} />));

    expect(
      await screen.findByText("No shared project knowledge has been recorded yet."),
    ).toBeInTheDocument();
  });
});