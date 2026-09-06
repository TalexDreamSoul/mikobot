import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CapabilitiesPanel } from "@/components/projects/CapabilitiesPanel";
import { ChannelAssignmentsPanel } from "@/components/projects/ChannelAssignmentsPanel";
import { ProjectsView } from "@/components/projects/ProjectsView";
import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import i18n from "@/i18n";
import type {
  CollaborationChannelAssignment,
  CollaborationProject,
  CollaborationProjectPayload,
} from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ApiError: actual.ApiError,
    fetchCollaborationClaimableChannels: vi.fn(),
    fetchCollaborationPairing: vi.fn(),
  };
});

import {
  ApiError,
  fetchCollaborationClaimableChannels,
  fetchCollaborationPairing,
} from "@/lib/api";

const timestamp = 1_735_689_600_000;

const project: CollaborationProject = {
  id: "project-release",
  name: "Release train",
  created_by_user_id: "user-1",
  allowed_skills: null,
  allowed_mcp_servers: null,
  created_at_ms: timestamp,
  updated_at_ms: timestamp,
};

const assignment: CollaborationChannelAssignment = {
  channel_type: "weixin",
  instance_id: "support",
  project_id: project.id,
  assignee_user_id: "user-2",
  enabled: true,
  created_by_user_id: "user-1",
  created_at_ms: timestamp,
  updated_at_ms: timestamp,
};

function detail(overrides: Partial<CollaborationProjectPayload> = {}): CollaborationProjectPayload {
  return {
    project,
    members: [
      { project_id: project.id, user_id: "user-1", role: "owner", created_at_ms: timestamp },
      { project_id: project.id, user_id: "user-2", role: "member", created_at_ms: timestamp },
    ],
    assignments: [assignment],
    available: {
      skills: [{ id: "architecture", name: "Architecture", description: "System design guidance." }],
      mcp_servers: [{ id: "filesystem", name: "Filesystem MCP" }],
    },
    can_manage: true,
    ...overrides,
  };
}

function controller(overrides: Partial<CollaborationProjectsController> = {}): CollaborationProjectsController {
  return {
    summary: {
      user: { id: "user-1", display_name: "Ari", is_admin: true, default_project_id: project.id },
      is_admin: true,
      projects: [project],
      assignments: [assignment],
      active_project_id: project.id,
    },
    isAdmin: true,
    projectId: project.id,
    detail: detail(),
    loading: false,
    detailLoading: false,
    busyKey: null,
    error: null,
    setError: vi.fn(),
    selectProject: vi.fn(),
    reload: vi.fn(),
    refreshDetail: vi.fn(),
    createProject: vi.fn(),
    renameProject: vi.fn(),
    removeProject: vi.fn(),
    saveCapabilities: vi.fn(),
    addProjectMember: vi.fn(),
    removeProjectMember: vi.fn(),
    beginPairing: vi.fn(),
    finishPairing: vi.fn(),
    setAssignmentEnabled: vi.fn(),
    removeAssignment: vi.fn(),
    ...overrides,
  } as unknown as CollaborationProjectsController;
}

function wrap(node: React.ReactNode) {
  return (
    <ClientProvider
      client={{ status: "open", onStatus: () => () => {} } as never}
      token="token"
    >
      {node}
    </ClientProvider>
  );
}

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  await i18n.changeLanguage("en");
});

describe("ChannelAssignmentsPanel", () => {
  it("lists assignments, lets a manager pause or remove them, and pairs a chosen instance", async () => {
    await i18n.changeLanguage("zh-CN");
    vi.mocked(fetchCollaborationClaimableChannels).mockResolvedValue({
      channels: [{
        channel_type: "weixin",
        channel_display_name: "微信",
        instance_id: "sales",
        display_name: "Sales",
        status: "running",
      }],
    });
    const beginPairing = vi.fn().mockResolvedValue({
      pairing: {
        id: "challenge-sales",
        project_id: project.id,
        assignee_user_id: "user-3",
        channel_type: "weixin",
        instance_id: "sales",
        expires_at_ms: Date.now() + 60_000,
        verified: false,
        consumed: false,
        created_at_ms: timestamp,
        code: "ABCD-EFGH",
      },
    });
    vi.mocked(fetchCollaborationPairing).mockResolvedValue({
      pairing: {
        id: "challenge-sales",
        project_id: project.id,
        assignee_user_id: "user-3",
        channel_type: "weixin",
        instance_id: "sales",
        expires_at_ms: Date.now() + 60_000,
        verified: false,
        consumed: false,
        created_at_ms: timestamp,
      },
    });
    const projects = controller({ beginPairing });

    render(wrap(<ChannelAssignmentsPanel detail={detail()} projects={projects} />));

    expect(screen.getByText("weixin · support")).toBeInTheDocument();
    expect(screen.getByText(/分配给 user-2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停用 support" }));
    expect(projects.setAssignmentEnabled).toHaveBeenCalledWith("weixin", "support", false);
    fireEvent.click(screen.getByRole("button", { name: "解除 support 的分配" }));
    expect(projects.removeAssignment).toHaveBeenCalledWith("weixin", "support");

    const picker = await screen.findByRole("combobox", { name: "渠道实例" });
    await waitFor(() => expect(within(picker).getByRole("option", { name: "微信 · Sales · running" })).toBeInTheDocument());
    fireEvent.change(picker, { target: { value: "weixin:sales" } });
    fireEvent.change(screen.getByRole("textbox", { name: "分配给用户 ID（可选）" }), {
      target: { value: "user-3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成配对码" }));

    await waitFor(() => expect(beginPairing).toHaveBeenCalledWith({
      channelType: "weixin",
      instanceId: "sales",
      assigneeUserId: "user-3",
    }));
    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    expect(screen.getByText("此代码用于把渠道实例分配到 Release train。")).toBeInTheDocument();
  });

  it("finishes the assignment once the channel verifies the code", async () => {
    vi.mocked(fetchCollaborationClaimableChannels).mockResolvedValue({
      channels: [{
        channel_type: "weixin",
        channel_display_name: "WeChat",
        instance_id: "sales",
        display_name: "Sales",
        status: "running",
      }],
    });
    const pending = {
      id: "challenge-1",
      project_id: project.id,
      assignee_user_id: "user-1",
      channel_type: "weixin",
      instance_id: "sales",
      expires_at_ms: Date.now() + 60_000,
      verified: false,
      consumed: false,
      created_at_ms: timestamp,
      code: "ABCD-EFGH",
    };
    const beginPairing = vi.fn().mockResolvedValue({ pairing: pending });
    const finishPairing = vi.fn().mockResolvedValue({
      pairing: { ...pending, verified: true, consumed: true },
    });
    vi.mocked(fetchCollaborationPairing).mockResolvedValue({ pairing: { ...pending, verified: true } });
    const projects = controller({ beginPairing, finishPairing, isAdmin: false });

    render(wrap(<ChannelAssignmentsPanel detail={detail({ can_manage: false })} projects={projects} />));

    // A member sees no manager controls and no assignee field.
    expect(screen.queryByRole("button", { name: /Disable support/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /Assign to user ID/ })).not.toBeInTheDocument();

    const picker = await screen.findByRole("combobox", { name: "Channel instance" });
    await waitFor(() => expect(within(picker).getByRole("option", { name: "WeChat · Sales · running" })).toBeInTheDocument());
    fireEvent.change(picker, { target: { value: "weixin:sales" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate Pair Code" }));

    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    expect(beginPairing).toHaveBeenCalledWith({
      channelType: "weixin",
      instanceId: "sales",
      assigneeUserId: null,
    });
    await waitFor(() => expect(finishPairing).toHaveBeenCalledWith("challenge-1"), { timeout: 3_000 });
    expect(await screen.findByText("Assignment complete")).toBeInTheDocument();
  });

  it("distinguishes a refusal to list instances from an empty list", async () => {
    vi.mocked(fetchCollaborationClaimableChannels).mockRejectedValue(new ApiError(403, "forbidden"));

    render(wrap(<ChannelAssignmentsPanel detail={detail({ assignments: [] })} projects={controller({ isAdmin: false })} />));

    expect(await screen.findByText(/not permitted to list channel instances/)).toBeInTheDocument();
    expect(screen.queryByText(/No channel instances are waiting/)).not.toBeInTheDocument();
    expect(screen.getByText("No channel instances are assigned to this project.")).toBeInTheDocument();
  });
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
    vi.mocked(fetchCollaborationClaimableChannels).mockResolvedValue({ channels: [] });
    const projects = controller();

    render(wrap(<ProjectsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(screen.getByText("Ari · Administrator")).toBeInTheDocument();
    const sections = screen.getByRole("navigation", { name: "Project sections" });
    expect(within(sections).getByRole("button", { name: "Channels" })).toHaveAttribute("aria-current", "page");
    fireEvent.click(within(sections).getByRole("button", { name: "Members" }));
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
      },
      isAdmin: false,
      projectId: null,
      detail: null,
    });

    render(wrap(<ProjectsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(screen.getByRole("heading", { name: "No projects yet." })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "New project" })[0]);
    expect(screen.getByRole("textbox", { name: "New project" })).toBeInTheDocument();
  });
});
