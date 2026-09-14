import type { ReactNode } from "react";
import { vi } from "vitest";

import type { CollaborationProjectsController } from "@/hooks/useCollaborationProjects";
import type {
  CollaborationChannelAssignment,
  CollaborationProject,
  CollaborationProjectApp,
  CollaborationProjectPayload,
  CollaborationProjectTask,
} from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

export const timestamp = 1_735_689_600_000;

export const project: CollaborationProject = {
  id: "project-release",
  name: "Release train",
  created_by_user_id: "user-1",
  allowed_skills: null,
  allowed_mcp_servers: null,
  created_at_ms: timestamp,
  updated_at_ms: timestamp,
  is_builtin: false,
};

export const secondProject: CollaborationProject = {
  ...project,
  id: "project-support",
  name: "Support desk",
};

export const assignment: CollaborationChannelAssignment = {
  channel_type: "weixin",
  instance_id: "support",
  project_id: project.id,
  assignee_user_id: "user-2",
  enabled: true,
  created_by_user_id: "user-1",
  created_at_ms: timestamp,
  updated_at_ms: timestamp,
  channel_display_name: "WeChat",
  display_name: "Support desk bot",
  status: "running",
};

export const builtinAutomation = {
  id: "heartbeat:project-release",
  name: "heartbeat",
  enabled: true,
  protected: true,
  schedule: { kind: "every", every_ms: 1_800_000, at_ms: null, expr: null, tz: "UTC" },
  payload: { message: "", kind: "system_event" },
  state: { next_run_at_ms: timestamp + 1_800_000, last_run_at_ms: null, last_status: null },
};

export const openTask: CollaborationProjectTask = {
  id: "task-1",
  project_id: project.id,
  title: "Draft the launch note",
  detail: "Cover the pricing change.",
  status: "todo",
  created_by_user_id: "user-2",
  created_at_ms: timestamp,
  updated_at_ms: timestamp,
};

export const doneTask: CollaborationProjectTask = {
  ...openTask,
  id: "task-2",
  title: "Wire the banner",
  detail: "",
  status: "done",
  created_by_user_id: "user-1",
};

export const approvedApp: CollaborationProjectApp = {
  name: "studio",
  display_name: "Studio",
  description: "Poster studio.",
  revision: "rev-1",
  enabled: true,
  skills: ["poster"],
  mcp_servers: [],
  approved_revision: "rev-1",
  approved: true,
  drifted: false,
};

export const driftedApp: CollaborationProjectApp = {
  ...approvedApp,
  name: "banner-kit",
  display_name: "Banner kit",
  revision: "rev-2",
  approved_revision: "rev-1",
  drifted: true,
};

export const availableApp: CollaborationProjectApp = {
  ...approvedApp,
  name: "charts",
  display_name: "Charts",
  approved_revision: null,
  approved: false,
};

export function detail(
  overrides: Partial<CollaborationProjectPayload> = {},
): CollaborationProjectPayload {
  return {
    project,
    members: [
      { project_id: project.id, user_id: "user-1", role: "owner", created_at_ms: timestamp },
      { project_id: project.id, user_id: "user-2", role: "member", created_at_ms: timestamp },
    ],
    assignments: [assignment],
    automations: [builtinAutomation],
    tasks: [openTask, doneTask],
    apps: [approvedApp, driftedApp, availableApp],
    available: {
      skills: [{ id: "architecture", name: "Architecture", description: "System design guidance." }],
      mcp_servers: [{ id: "filesystem", name: "Filesystem MCP" }],
    },
    can_manage: true,
    ...overrides,
  };
}

export function controller(
  overrides: Partial<CollaborationProjectsController> = {},
): CollaborationProjectsController {
  return {
    summary: {
      user: { id: "user-1", display_name: "Ari", is_admin: true, default_project_id: project.id },
      is_admin: true,
      projects: [project, secondProject],
      assignments: [assignment],
      active_project_id: project.id,
      manageable_project_ids: [project.id, secondProject.id],
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
    setActiveProject: vi.fn(),
    reload: vi.fn(),
    refreshDetail: vi.fn(),
    createProject: vi.fn(),
    renameProject: vi.fn(),
    removeProject: vi.fn(),
    saveCapabilities: vi.fn(),
    createTask: vi.fn(),
    updateTask: vi.fn(),
    deleteTask: vi.fn(),
    joinProjectApp: vi.fn(),
    leaveProjectApp: vi.fn(),
    addProjectMember: vi.fn(),
    removeProjectMember: vi.fn(),
    beginPairing: vi.fn(),
    finishPairing: vi.fn(),
    setAssignmentEnabled: vi.fn(),
    moveAssignment: vi.fn(),
    removeAssignment: vi.fn(),
    ...overrides,
  } as unknown as CollaborationProjectsController;
}

export function wrap(node: ReactNode) {
  return (
    <ClientProvider
      client={{ status: "open", onStatus: () => () => {} } as never}
      token="token"
    >
      {node}
    </ClientProvider>
  );
}
