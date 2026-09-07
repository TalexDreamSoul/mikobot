import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCollaborationProjects } from "@/hooks/useCollaborationProjects";
import * as api from "@/lib/api";
import type {
  CollaborationPayload,
  CollaborationProject,
  CollaborationProjectPayload,
} from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchCollaboration: vi.fn(),
    fetchCollaborationProject: vi.fn(),
  };
});

const timestamp = 1_735_689_600_000;

function project(id: string, name: string): CollaborationProject {
  return {
    id,
    name,
    created_by_user_id: "user-1",
    allowed_skills: null,
    allowed_mcp_servers: null,
    created_at_ms: timestamp,
    updated_at_ms: timestamp,
  };
}

function projectPayload(value: CollaborationProject): CollaborationProjectPayload {
  return {
    project: value,
    members: [{ project_id: value.id, user_id: "user-1", role: "owner", created_at_ms: timestamp }],
    assignments: [],
    available: { skills: [], mcp_servers: [] },
    can_manage: true,
  };
}

function collaboration(
  projects: CollaborationProject[],
  activeProjectId: string | null = null,
  isAdmin = false,
): CollaborationPayload {
  return {
    user: { id: "user-1", display_name: "Ari", is_admin: isAdmin, default_project_id: activeProjectId },
    is_admin: isAdmin,
    projects,
    assignments: [],
    active_project_id: activeProjectId,
    manageable_project_ids: projects.map((item) => item.id),
  };
}

interface FakeClient {
  requestMutation: {
    <T>(action: string, payload?: Record<string, unknown>, timeoutMs?: number): Promise<T>;
    mockResolvedValue(value: unknown): void;
    mockRejectedValue(value: unknown): void;
  };
}

function fakeClient(): FakeClient {
  return {
    requestMutation: vi.fn() as FakeClient["requestMutation"],
  };
}

function wrap(client: FakeClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <ClientProvider
        client={client as unknown as import("@/lib/nanobot-client").NanobotClient}
        token="tok"
      >
        {children}
      </ClientProvider>
    );
  };
}

describe("useCollaborationProjects", () => {
  beforeEach(() => {
    vi.mocked(api.fetchCollaboration).mockReset();
    vi.mocked(api.fetchCollaborationProject).mockReset();
  });

  it("selects the active project and exposes administration from the summary", async () => {
    const first = project("project-1", "First");
    const second = project("project-2", "Second");
    vi.mocked(api.fetchCollaboration).mockResolvedValue(collaboration([first, second], second.id, true));
    vi.mocked(api.fetchCollaborationProject).mockImplementation(async (_token, id) => (
      projectPayload(id === first.id ? first : second)
    ));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(fakeClient()) });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.projectId).toBe(second.id);
    await waitFor(() => expect(result.current.detail?.project.id).toBe(second.id));
  });

  it("switches projects, persists the default, and clears stale detail", async () => {
    const first = project("project-1", "First");
    const second = project("project-2", "Second");
    let resolveSecond!: (value: CollaborationProjectPayload) => void;
    const client = fakeClient();
    client.requestMutation.mockResolvedValue({ user: collaboration([first, second]).user });
    vi.mocked(api.fetchCollaboration).mockResolvedValue(collaboration([first, second], first.id));
    vi.mocked(api.fetchCollaborationProject).mockImplementation((_token, id) => {
      if (id === second.id) {
        return new Promise<CollaborationProjectPayload>((resolve) => {
          resolveSecond = resolve;
        });
      }
      return Promise.resolve(projectPayload(first));
    });

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.detail?.project.id).toBe(first.id));

    act(() => {
      result.current.selectProject(second.id);
    });

    await waitFor(() => expect(result.current.projectId).toBe(second.id));
    expect(result.current.detail).toBeNull();
    await waitFor(() => expect(client.requestMutation).toHaveBeenCalledWith(
      "collaboration.user.defaults",
      { project_id: second.id },
      expect.any(Number),
    ));
    await act(async () => {
      resolveSecond(projectPayload(second));
    });
    await waitFor(() => expect(result.current.detail?.project.id).toBe(second.id));
  });

  it("creates a project and selects it", async () => {
    const existing = project("project-1", "First");
    const created = project("project-2", "Release plan");
    const client = fakeClient();
    client.requestMutation.mockResolvedValue({ project: created });
    vi.mocked(api.fetchCollaboration)
      .mockResolvedValueOnce(collaboration([existing], existing.id))
      .mockResolvedValue(collaboration([existing, created], existing.id));
    vi.mocked(api.fetchCollaborationProject).mockImplementation(async (_token, id) => (
      projectPayload(id === created.id ? created : existing)
    ));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.projectId).toBe(existing.id));

    await act(async () => {
      await result.current.createProject("Release plan");
    });

    expect(client.requestMutation).toHaveBeenCalledWith(
      "collaboration.project.create",
      { name: "Release plan" },
      expect.any(Number),
    );
    await waitFor(() => expect(result.current.projectId).toBe(created.id));
  });

  it("keeps the backend final-owner error after refreshing project membership", async () => {
    const value = project("project-1", "Team roadmap");
    const client = fakeClient();
    client.requestMutation.mockRejectedValue(Object.assign(
      new Error("A project must retain at least one owner."),
      { status: 409 },
    ));
    vi.mocked(api.fetchCollaboration).mockResolvedValue(collaboration([value], value.id));
    vi.mocked(api.fetchCollaborationProject).mockResolvedValue(projectPayload(value));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.detail?.project.id).toBe(value.id));

    await act(async () => {
      await expect(result.current.removeProjectMember("user-1"))
        .rejects.toThrow("A project must retain at least one owner.");
    });

    await waitFor(() => expect(result.current.error).toBe("A project must retain at least one owner."));
  });

  it("sends capability allowlists for the selected project", async () => {
    const value = project("project-1", "Team roadmap");
    const client = fakeClient();
    client.requestMutation.mockResolvedValue({ project: { ...value, allowed_skills: ["docs"] } });
    vi.mocked(api.fetchCollaboration).mockResolvedValue(collaboration([value], value.id));
    vi.mocked(api.fetchCollaborationProject).mockResolvedValue(projectPayload(value));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.detail?.project.id).toBe(value.id));

    await act(async () => {
      await result.current.saveCapabilities({ allowed_skills: ["docs"] });
    });

    expect(client.requestMutation).toHaveBeenCalledWith(
      "collaboration.project.update",
      { project_id: value.id, capabilities: { allowed_skills: ["docs"] } },
      expect.any(Number),
    );
  });
});
