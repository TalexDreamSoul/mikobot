import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChannelsView } from "@/components/channels/ChannelsView";
import i18n from "@/i18n";
import {
  assignment,
  controller,
  project,
  secondProject,
  timestamp,
  wrap,
} from "@/tests/collaboration-fixtures";

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

const claimable = {
  channels: [{
    channel_type: "weixin",
    channel_display_name: "WeChat",
    instance_id: "sales",
    display_name: "Sales",
    status: "running",
  }],
};

function pendingChallenge(overrides: Record<string, unknown> = {}) {
  return {
    id: "challenge-sales",
    project_id: project.id,
    assignee_user_id: "user-3",
    channel_type: "weixin",
    instance_id: "sales",
    expires_at_ms: Date.now() + 60_000,
    verified: false,
    consumed: false,
    created_at_ms: timestamp,
    ...overrides,
  };
}

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  await i18n.changeLanguage("en");
});

describe("ChannelsView", () => {
  it("names each assigned instance and lets an administrator pause, move, and remove it", async () => {
    vi.mocked(fetchCollaborationClaimableChannels).mockResolvedValue(claimable);
    const projects = controller();

    render(wrap(<ChannelsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(screen.getByText("WeChat · Support desk bot")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("switch", { name: "Disable support" }));
    expect(projects.setAssignmentEnabled).toHaveBeenCalledWith("weixin", "support", false);

    const projectPicker = screen.getByRole("combobox", { name: "Project" });
    expect(projectPicker).toHaveValue(project.id);
    fireEvent.change(projectPicker, { target: { value: secondProject.id } });
    expect(projects.moveAssignment).toHaveBeenCalledWith("weixin", "support", secondProject.id);

    fireEvent.click(screen.getByRole("button", { name: "Remove assignment for support" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove assignment" }));
    await waitFor(() => expect(projects.removeAssignment).toHaveBeenCalledWith("weixin", "support"));
  });

  it("assigns a claimable channel to a known project member instead of accepting a free-form identity", async () => {
    vi.mocked(fetchCollaborationClaimableChannels).mockResolvedValue(claimable);
    const beginPairing = vi.fn().mockResolvedValue({ pairing: pendingChallenge({ code: "ABCD-EFGH" }) });
    const projects = controller({ beginPairing });

    render(wrap(<ChannelsView projects={projects} onToggleSidebar={vi.fn()} />));

    const assignee = await screen.findByRole("combobox", { name: "Assign to project member" });
    expect((assignee as HTMLSelectElement).value).toBe("user-1");
    expect(Array.from((assignee as HTMLSelectElement).options, (option) => option.value))
      .toEqual(["user-1", "user-2"]);
    expect(screen.queryByRole("textbox", { name: /Assign/ })).not.toBeInTheDocument();
    fireEvent.change(assignee, { target: { value: "user-2" } });
    fireEvent.change(await screen.findByRole("combobox", { name: "Channel instance" }), {
      target: { value: "weixin:sales" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate Pair Code" }));

    await waitFor(() => expect(beginPairing).toHaveBeenCalledWith({
      channelType: "weixin",
      instanceId: "sales",
      projectId: project.id,
      assigneeUserId: "user-2",
    }));
  });

  it("keeps the Pair Code on screen while polling reports status without it", async () => {
    vi.mocked(fetchCollaborationClaimableChannels).mockResolvedValue(claimable);
    // Only the issuing response carries the code; every status read omits it.
    vi.mocked(fetchCollaborationPairing).mockResolvedValue({ pairing: pendingChallenge() });
    const beginPairing = vi.fn().mockResolvedValue({
      pairing: pendingChallenge({ code: "ABCD-EFGH" }),
    });
    const projects = controller({ beginPairing });

    render(wrap(<ChannelsView projects={projects} onToggleSidebar={vi.fn()} />));

    const picker = await screen.findByRole("combobox", { name: "Channel instance" });
    await waitFor(() => expect(
      within(picker).getByRole("option", { name: "WeChat · Sales · running" }),
    ).toBeInTheDocument());
    fireEvent.change(picker, { target: { value: "weixin:sales" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate Pair Code" }));

    await waitFor(() => expect(beginPairing).toHaveBeenCalledWith({
      channelType: "weixin",
      instanceId: "sales",
      projectId: project.id,
      assigneeUserId: "user-1",
    }));
    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();

    await waitFor(() => expect(fetchCollaborationPairing).toHaveBeenCalled(), { timeout: 3_000 });
    await waitFor(
      () => expect(vi.mocked(fetchCollaborationPairing).mock.calls.length).toBeGreaterThan(1),
      { timeout: 3_000 },
    );
    expect(screen.getByText("ABCD-EFGH")).toBeInTheDocument();
  });

  it("finishes the assignment once the channel verifies the code", async () => {
    vi.mocked(fetchCollaborationClaimableChannels).mockResolvedValue(claimable);
    vi.mocked(fetchCollaborationPairing).mockResolvedValue({
      pairing: pendingChallenge({ verified: true }),
    });
    const beginPairing = vi.fn().mockResolvedValue({
      pairing: pendingChallenge({ code: "ABCD-EFGH" }),
    });
    const finishPairing = vi.fn().mockResolvedValue({
      pairing: pendingChallenge({ verified: true, consumed: true }),
      channel_activation: { ok: false, message: "Channel activation could not start." },
    });
    const projects = controller({ beginPairing, finishPairing, isAdmin: false });

    render(wrap(<ChannelsView projects={projects} onToggleSidebar={vi.fn()} />));

    // A member sees no assignee field, and picks from projects they manage.
    expect(screen.queryByRole("textbox", { name: /Assign to user ID/ })).not.toBeInTheDocument();

    const picker = await screen.findByRole("combobox", { name: "Channel instance" });
    await waitFor(() => expect(
      within(picker).getByRole("option", { name: "WeChat · Sales · running" }),
    ).toBeInTheDocument());
    fireEvent.change(picker, { target: { value: "weixin:sales" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate Pair Code" }));

    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    expect(beginPairing).toHaveBeenCalledWith({
      channelType: "weixin",
      instanceId: "sales",
      projectId: project.id,
      assigneeUserId: null,
    });
    await waitFor(() => expect(finishPairing).toHaveBeenCalledWith("challenge-sales"), { timeout: 3_000 });
    expect(await screen.findByRole("alert")).toHaveTextContent("Channel activation could not start.");
  });

  it("distinguishes a refusal to list instances from an empty list", async () => {
    vi.mocked(fetchCollaborationClaimableChannels).mockRejectedValue(new ApiError(403, "forbidden"));
    const projects = controller({
      isAdmin: false,
      summary: {
        user: { id: "user-9", display_name: "Sam", is_admin: false, default_project_id: null },
        is_admin: false,
        projects: [],
        assignments: [],
        active_project_id: null,
        manageable_project_ids: [],
      },
    });

    render(wrap(<ChannelsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(await screen.findByText(/not permitted to list channel instances/)).toBeInTheDocument();
    expect(screen.queryByText(/No channel instances are waiting/)).not.toBeInTheDocument();
    expect(screen.getByText("No channel instance is assigned yet.")).toBeInTheDocument();
  });

  it("shows a member the project without offering controls they cannot use", async () => {
    vi.mocked(fetchCollaborationClaimableChannels).mockResolvedValue({ channels: [] });
    const projects = controller({
      isAdmin: false,
      summary: {
        user: { id: "user-2", display_name: "Robin", is_admin: false, default_project_id: project.id },
        is_admin: false,
        projects: [project],
        assignments: [assignment],
        active_project_id: project.id,
        manageable_project_ids: [],
      },
    });

    render(wrap(<ChannelsView projects={projects} onToggleSidebar={vi.fn()} />));

    expect(await screen.findByText(/No channel instances are waiting/)).toBeInTheDocument();
    expect(screen.getByText("WeChat · Support desk bot")).toBeInTheDocument();
    expect(screen.getByText("Release train")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Project" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove assignment for/ })).not.toBeInTheDocument();
  });
});
