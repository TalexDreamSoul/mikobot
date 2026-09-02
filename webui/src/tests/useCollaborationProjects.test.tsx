import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCollaborationProjects } from "@/hooks/useCollaborationProjects";
import * as api from "@/lib/api";
import type {
  CollaborationBot,
  CollaborationOrganization,
  CollaborationOrganizationPayload,
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
    fetchCollaborationBot: vi.fn(),
    fetchCollaborationOrganization: vi.fn(),
    fetchCollaborationProject: vi.fn(),
  };
});

const timestamp = 1_735_689_600_000;

function organization(
  id: string,
  name: string,
  isPersonal = false,
): CollaborationOrganization {
  return {
    id,
    name,
    is_personal: isPersonal,
    created_by_user_id: "user-1",
    created_at_ms: timestamp,
    updated_at_ms: timestamp,
  };
}

function project(id: string, name: string, organizationId: string): CollaborationProject {
  return {
    id,
    name,
    organization_id: organizationId,
    created_at_ms: timestamp,
    updated_at_ms: timestamp,
  };
}

function projectPayload(value: CollaborationProject): CollaborationProjectPayload {
  return {
    project: value,
    members: [],
    task_lists: [],
    tasks: [],
    extension_profile: { revision: 1, settings: {} },
    available: { skills: [], mcp_servers: [] },
    context_sources: [],
  };
}

function organizationPayload(value: CollaborationOrganization): CollaborationOrganizationPayload {
  return {
    organization: value,
    members: [{
      organization_id: value.id,
      user_id: "user-1",
      role: "owner",
      created_at_ms: timestamp,
    }],
  };
}

function collaboration(
  organizations: CollaborationOrganization[],
  projects: CollaborationProject[],
  activeProjectId: string | null = null,
  bots: CollaborationBot[] = [],
): CollaborationPayload {
  return {
    user: { id: "user-1", display_name: "Ari" },
    organizations,
    projects,
    bots,
    active_organization_id: null,
    active_bot_id: null,
    active_project_id: activeProjectId,
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
    vi.mocked(api.fetchCollaborationOrganization).mockReset();
    vi.mocked(api.fetchCollaborationProject).mockReset();
    vi.mocked(api.fetchCollaborationBot).mockReset();
  });

  it("selects the explicitly personal organization when organization timestamps collide", async () => {
    const shared = organization("org-shared", "Studio");
    const personal = organization("org-personal", "Ari's workspace", true);
    vi.mocked(api.fetchCollaboration).mockResolvedValue(collaboration([shared, personal], []));
    vi.mocked(api.fetchCollaborationOrganization).mockImplementation(async (_token, id) => (
      organizationPayload(id === personal.id ? personal : shared)
    ));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(fakeClient()) });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.personalOrganizationId).toBe("org-personal");
    expect(result.current.organizationId).toBe("org-personal");
  });

  it("switches to that organization's project and clears stale project detail", async () => {
    const personal = organization("org-personal", "Ari's workspace", true);
    const shared = organization("org-shared", "Studio");
    const personalProject = project("project-personal", "Private roadmap", personal.id);
    const sharedProject = project("project-shared", "Team roadmap", shared.id);
    const summary = collaboration([personal, shared], [personalProject, sharedProject], personalProject.id);
    let resolveSharedDetail!: (value: CollaborationProjectPayload) => void;

    vi.mocked(api.fetchCollaboration).mockResolvedValue(summary);
    vi.mocked(api.fetchCollaborationOrganization).mockImplementation(async (_token, id) => (
      organizationPayload(id === personal.id ? personal : shared)
    ));
    vi.mocked(api.fetchCollaborationProject).mockImplementation((_token, id) => {
      if (id === sharedProject.id) {
        return new Promise<CollaborationProjectPayload>((resolve) => {
          resolveSharedDetail = resolve;
        });
      }
      return Promise.resolve(projectPayload(personalProject));
    });

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(fakeClient()) });
    await waitFor(() => expect(result.current.detail?.project.id).toBe(personalProject.id));

    act(() => {
      result.current.selectOrganization(shared.id);
    });

    await waitFor(() => expect(result.current.projectId).toBe(sharedProject.id));
    await waitFor(() => expect(api.fetchCollaborationProject).toHaveBeenLastCalledWith("tok", sharedProject.id));
    expect(result.current.detail).toBeNull();

    await act(async () => {
      resolveSharedDetail(projectPayload(sharedProject));
    });
    await waitFor(() => expect(result.current.detail?.project.id).toBe(sharedProject.id));
  });

  it("creates projects in the selected organization", async () => {
    const personal = organization("org-personal", "Ari's workspace", true);
    const shared = organization("org-shared", "Studio");
    const existing = project("project-personal", "Private roadmap", personal.id);
    const created = project("project-shared", "Release plan", shared.id);
    const summary = collaboration([personal, shared], [existing]);
    const client = fakeClient();
    client.requestMutation.mockResolvedValue({ project: created });

    vi.mocked(api.fetchCollaboration).mockResolvedValue(summary);
    vi.mocked(api.fetchCollaborationOrganization).mockImplementation(async (_token, id) => (
      organizationPayload(id === personal.id ? personal : shared)
    ));
    vi.mocked(api.fetchCollaborationProject).mockResolvedValue(projectPayload(existing));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.organizationId).toBe(personal.id));

    act(() => {
      result.current.selectOrganization(shared.id);
    });
    await waitFor(() => expect(result.current.organizationId).toBe(shared.id));

    await act(async () => {
      await result.current.createProject("Release plan");
    });

    expect(client.requestMutation).toHaveBeenCalledWith(
      "collaboration.project.create",
      { name: "Release plan", organization_id: shared.id },
      expect.any(Number),
    );
  });

  it("surfaces authoritative organization mutation failures", async () => {
    const personal = organization("org-personal", "Ari's workspace", true);
    const client = fakeClient();
    client.requestMutation.mockRejectedValue(new Error("Only owners can rename this organization."));

    vi.mocked(api.fetchCollaboration).mockResolvedValue(collaboration([personal], []));
    vi.mocked(api.fetchCollaborationOrganization).mockResolvedValue(organizationPayload(personal));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.organizationId).toBe(personal.id));

    await act(async () => {
      await expect(result.current.renameOrganization("Changed name"))
        .rejects.toThrow("Only owners can rename this organization.");
    });
    await waitFor(() => expect(result.current.error).toBe("Only owners can rename this organization."));
  });

  it("surfaces a backend rejection when a project member belongs to another organization", async () => {
    const studio = organization("org-studio", "Studio");
    const projectValue = project("project-studio", "Team roadmap", studio.id);
    const client = fakeClient();
    client.requestMutation.mockRejectedValue(new Error("User is not a member of this organization."));

    vi.mocked(api.fetchCollaboration).mockResolvedValue(collaboration([studio], [projectValue], projectValue.id));
    vi.mocked(api.fetchCollaborationOrganization).mockResolvedValue(organizationPayload(studio));
    vi.mocked(api.fetchCollaborationProject).mockResolvedValue(projectPayload(projectValue));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.detail?.project.id).toBe(projectValue.id));

    await act(async () => {
      await expect(result.current.addProjectMember("outside-user", "member"))
        .rejects.toThrow("User is not a member of this organization.");
    });

    await waitFor(() => expect(result.current.error).toBe("User is not a member of this organization."));
  });

  it("keeps the active project when switching bots in the same organization", async () => {
    const studio = organization("org-studio", "Studio");
    const projectValue = project("project-studio", "Team roadmap", studio.id);
    const releaseBot: CollaborationBot = {
      id: "bot-release",
      organization_id: studio.id,
      owner_user_id: "user-1",
      name: "Release bot",
      avatar_url: null,
      persona_id: null,
      state: "active",
      created_at_ms: timestamp,
      updated_at_ms: timestamp,
    };
    const reviewBot: CollaborationBot = { ...releaseBot, id: "bot-review", name: "Review bot" };
    const summary = collaboration([studio], [projectValue], projectValue.id, [releaseBot, reviewBot]);
    const client = fakeClient();
    client.requestMutation.mockResolvedValue({ user: summary.user });

    vi.mocked(api.fetchCollaboration).mockResolvedValue(summary);
    vi.mocked(api.fetchCollaborationOrganization).mockResolvedValue(organizationPayload(studio));
    vi.mocked(api.fetchCollaborationProject).mockResolvedValue(projectPayload(projectValue));
    vi.mocked(api.fetchCollaborationBot).mockResolvedValue({
      bot: reviewBot,
      channels: [],
      projects: [],
      project_channels: [],
      capability_profiles: [],
    });

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.projectId).toBe(projectValue.id));

    act(() => {
      result.current.selectBot(reviewBot.id);
    });

    await waitFor(() => expect(result.current.botId).toBe(reviewBot.id));
    expect(result.current.organizationId).toBe(studio.id);
    expect(result.current.projectId).toBe(projectValue.id);
    await waitFor(() => expect(client.requestMutation).toHaveBeenCalledWith(
      "collaboration.user.defaults",
      {
        organization_id: studio.id,
        bot_id: reviewBot.id,
        project_id: projectValue.id,
      },
      expect.any(Number),
    ));
  });

  it("keeps the backend final-owner error after refreshing project membership", async () => {
    const studio = organization("org-studio", "Studio");
    const projectValue = project("project-studio", "Team roadmap", studio.id);
    const client = fakeClient();
    client.requestMutation.mockRejectedValue(Object.assign(
      new Error("A project must retain at least one owner."),
      { status: 409 },
    ));

    vi.mocked(api.fetchCollaboration).mockResolvedValue(collaboration([studio], [projectValue], projectValue.id));
    vi.mocked(api.fetchCollaborationOrganization).mockResolvedValue(organizationPayload(studio));
    vi.mocked(api.fetchCollaborationProject).mockResolvedValue(projectPayload(projectValue));

    const { result } = renderHook(useCollaborationProjects, { wrapper: wrap(client) });
    await waitFor(() => expect(result.current.detail?.project.id).toBe(projectValue.id));

    await act(async () => {
      await expect(result.current.removeProjectMember("user-1"))
        .rejects.toThrow("A project must retain at least one owner.");
    });

    await waitFor(() => expect(result.current.error).toBe("A project must retain at least one owner."));
  });
});
