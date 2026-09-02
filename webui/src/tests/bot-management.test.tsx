import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/Sidebar";
import { BotManagementPanel } from "@/components/projects/BotManagementPanel";
import i18n from "@/i18n";
import type { CollaborationBot, CollaborationOrganization, CollaborationProject } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", () => ({
  fetchCollaborationPairing: vi.fn(),
  fetchNanobotFeatures: vi.fn(),
  fetchSkillDetail: vi.fn(),
  fetchSkills: vi.fn(),
}));

import { fetchCollaborationPairing, fetchNanobotFeatures, fetchSkillDetail, fetchSkills } from "@/lib/api";

const timestamp = 1_735_689_600_000;
const studio: CollaborationOrganization = {
  id: "org-studio",
  name: "Studio",
  is_personal: false,
  created_by_user_id: "user-1",
  created_at_ms: timestamp,
  updated_at_ms: timestamp,
};
const personal: CollaborationOrganization = {
  id: "org-personal",
  name: "Ari",
  is_personal: true,
  created_by_user_id: "user-1",
  created_at_ms: timestamp,
  updated_at_ms: timestamp,
};
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
const personalBot: CollaborationBot = {
  ...releaseBot,
  id: "bot-personal",
  organization_id: personal.id,
  name: "Personal bot",
};
const project: CollaborationProject = {
  id: "project-release",
  name: "Release train",
  organization_id: studio.id,
  created_at_ms: timestamp,
  updated_at_ms: timestamp,
};

function renderSidebar(overrides: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const props = {
    sessions: [],
    organizations: [personal, studio],
    bots: [personalBot, releaseBot],
    activeOrganizationId: studio.id,
    activeBotId: releaseBot.id,
    activeKey: null,
    loading: false,
    newChatActive: false,
    onNewChat: vi.fn(),
    onSelect: vi.fn(),
    onRequestDelete: vi.fn(),
    onTogglePin: vi.fn(),
    onRequestRename: vi.fn(),
    onToggleArchive: vi.fn(),
    onToggleGroup: vi.fn(),
    onRequestRenameProject: vi.fn(),
    onNewChatInProject: vi.fn(),
    onOpenSettings: vi.fn(),
    onOpenProjects: vi.fn(),
    onOpenApps: vi.fn(),
    onOpenSkills: vi.fn(),
    onOpenAutomations: vi.fn(),
    onOpenSearch: vi.fn(),
    onToggleArchived: vi.fn(),
    onCollapse: vi.fn(),
    ...overrides,
  };
  render(
    <ClientProvider
      client={{ status: "open", onStatus: () => () => {} } as never}
      token="token"
    >
      <Sidebar {...props} />
    </ClientProvider>,
  );
  return props;
}

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  await i18n.changeLanguage("en");
});

describe("bot and organization controls", () => {
  it("filters the bot switcher by organization and persists explicit switcher choices", async () => {
    await i18n.changeLanguage("zh-CN");
    const onSelectBot = vi.fn();
    const onSelectOrganization = vi.fn();
    renderSidebar({ onSelectBot, onSelectOrganization });

    const botSwitcher = screen.getByRole("combobox", { name: "切换机器人" });
    const organizationSwitcher = screen.getByRole("combobox", { name: "切换组织" });
    expect(within(botSwitcher).getByRole("option", { name: "Release bot" })).toBeInTheDocument();
    expect(within(botSwitcher).queryByRole("option", { name: "Personal bot" })).not.toBeInTheDocument();
    expect(within(organizationSwitcher).getByRole("option", { name: "Ari — 个人组织" })).toBeInTheDocument();

    fireEvent.change(botSwitcher, { target: { value: releaseBot.id } });
    fireEvent.change(organizationSwitcher, { target: { value: personal.id } });
    expect(onSelectBot).toHaveBeenCalledWith(releaseBot.id);
    expect(onSelectOrganization).toHaveBeenCalledWith(personal.id);
    expect(
      organizationSwitcher.compareDocumentPosition(screen.getByRole("button", { name: "设置" }))
        & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows the purpose-bound Pair Code for the selected channel instance", async () => {
    await i18n.changeLanguage("zh-CN");
    vi.mocked(fetchNanobotFeatures).mockResolvedValue({
      features: [{
        name: "weixin",
        display_name: "微信",
        type: "channel",
        enabled: true,
        configured: true,
        instances: [{
          id: "support",
          name: "Support",
          enabled: true,
          configured: true,
          config_values: {},
          configured_fields: [],
          runtime_status: "running",
        }],
      }],
    } as never);
    vi.mocked(fetchSkills).mockResolvedValue({
      skills: [{
        name: "release-notes",
        description: "Release checklist",
        source: "workspace",
        enabled: true,
        logical_path: "skills/release-notes/SKILL.md",
      }],
    } as never);
    vi.mocked(fetchSkillDetail).mockResolvedValue({
      name: "release-notes",
      description: "Release checklist",
      source: "workspace",
      enabled: true,
      logical_path: "skills/release-notes/SKILL.md",
      requirements: {},
      files: [{ path: "guides/release.md", size: 512 }],
    } as never);
    const beginPairing = vi.fn().mockResolvedValue({
      pairing: {
        id: "challenge-support",
        purpose: "claim_channel",
        organization_id: studio.id,
        bot_id: releaseBot.id,
        project_id: null,
        channel_type: "weixin",
        instance_id: "support",
        expires_at_ms: Date.now() + 60_000,
        verified: false,
        consumed: false,
        created_at_ms: timestamp,
        code: "ABCD-EFGH",
      },
    });
    const projects = {
      summary: { bots: [releaseBot], projects: [project] },
      organizationId: studio.id,
      botId: releaseBot.id,
      projectId: project.id,
      botDetail: { channels: [], projects: [], capability_profiles: [] },
      botDetailLoading: false,
      busyKey: null,
      selectBot: vi.fn(),
      createBot: vi.fn(),
      beginPairing,
      finishPairing: vi.fn(),
    };

    render(
      <ClientProvider client={{ requestMutation: vi.fn() } as never} token="token">
        <BotManagementPanel projects={projects as never} />
      </ClientProvider>,
    );

    const channel = await screen.findByLabelText("渠道实例");
    fireEvent.change(channel, { target: { value: "weixin:support" } });
    fireEvent.click(screen.getByRole("button", { name: "认领渠道" }));

    await waitFor(() => expect(beginPairing).toHaveBeenCalledWith({
      purpose: "claim_channel",
      channelType: "weixin",
      instanceId: "support",
    }));
    expect(screen.getByRole("heading", { name: "验证配对码" })).toBeInTheDocument();
    expect(screen.getByText("ABCD-EFGH")).toBeInTheDocument();
    expect(screen.getByText("此代码用于把指定渠道实例认领到当前机器人。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /release-notes/ }));
    expect(await screen.findByText("guides/release.md")).toBeInTheDocument();
  });

  it("waits for pairing consumption before replacing the visible code with completion", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-01T12:00:00Z"));
    const now = Date.now();
    vi.mocked(fetchNanobotFeatures).mockResolvedValue({
      features: [{
        name: "weixin",
        display_name: "WeChat",
        type: "channel",
        enabled: true,
        configured: true,
        instances: [{
          id: "support",
          name: "Support",
          enabled: true,
          configured: true,
          config_values: {},
          configured_fields: [],
          runtime_status: "running",
        }],
      }],
    } as never);
    vi.mocked(fetchSkills).mockResolvedValue({ skills: [] } as never);
    const initialPairing = {
      id: "challenge-support",
      purpose: "claim_channel" as const,
      organization_id: studio.id,
      bot_id: releaseBot.id,
      project_id: null,
      channel_type: "weixin",
      instance_id: "support",
      expires_at_ms: now + 60_000,
      verified: false,
      consumed: false,
      created_at_ms: now,
      code: "ABCD-EFGH",
    };
    let finish!: (value: { pairing: typeof initialPairing }) => void;
    const finishPairing = vi.fn(() => new Promise<{ pairing: typeof initialPairing }>((resolve) => {
      finish = resolve;
    }));
    vi.mocked(fetchCollaborationPairing).mockResolvedValue({
      pairing: { ...initialPairing, verified: true },
    });
    const projects = {
      summary: { bots: [releaseBot], projects: [project] },
      organizationId: studio.id,
      botId: releaseBot.id,
      projectId: project.id,
      botDetail: { channels: [], projects: [], capability_profiles: [] },
      botDetailLoading: false,
      busyKey: null,
      selectBot: vi.fn(),
      createBot: vi.fn(),
      beginPairing: vi.fn().mockResolvedValue({ pairing: initialPairing }),
      finishPairing,
    };

    render(
      <ClientProvider client={{ requestMutation: vi.fn() } as never} token="token">
        <BotManagementPanel projects={projects as never} />
      </ClientProvider>,
    );
    await act(async () => { await Promise.resolve(); });
    fireEvent.change(screen.getByLabelText("Channel instance"), { target: { value: "weixin:support" } });
    fireEvent.click(screen.getByRole("button", { name: "Claim channel" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(800); });
    finish({ pairing: { ...initialPairing, verified: true, consumed: true } });
    await act(async () => { await Promise.resolve(); });

    const completion = screen.getByRole("heading", { name: "Pairing and assignment complete" });
    expect(completion.closest("section")).not.toHaveTextContent("ABCD-EFGH");
    expect(completion.closest("section")?.querySelector(".animate-spin")).toBeNull();
  });

  it("uses the explicitly selected claimed channel for project assignment", async () => {
    vi.mocked(fetchNanobotFeatures).mockResolvedValue({ features: [] } as never);
    vi.mocked(fetchSkills).mockResolvedValue({ skills: [] } as never);
    const beginPairing = vi.fn().mockResolvedValue({
      pairing: {
        id: "challenge-engineering",
        purpose: "assign_bot_project",
        organization_id: studio.id,
        bot_id: releaseBot.id,
        project_id: project.id,
        channel_type: "feishu",
        instance_id: "engineering",
        expires_at_ms: Date.now() + 60_000,
        verified: false,
        consumed: false,
        created_at_ms: timestamp,
        code: "EFGH-IJKL",
      },
    });
    const projects = {
      summary: { bots: [releaseBot], projects: [project] },
      organizationId: studio.id,
      botId: releaseBot.id,
      projectId: project.id,
      botDetail: {
        bot: releaseBot,
        channels: [
          { bot_id: releaseBot.id, channel_type: "weixin", instance_id: "support", claimed_by_user_id: "user-1", created_at_ms: timestamp },
          { bot_id: releaseBot.id, channel_type: "feishu", instance_id: "engineering", claimed_by_user_id: "user-1", created_at_ms: timestamp },
        ],
        projects: [],
        capability_profiles: [],
      },
      botDetailLoading: false,
      busyKey: null,
      selectBot: vi.fn(),
      createBot: vi.fn(),
      beginPairing,
      finishPairing: vi.fn(),
      saveBotCapabilities: vi.fn(),
      updateBotState: vi.fn(),
    };

    render(
      <ClientProvider client={{ requestMutation: vi.fn() } as never} token="token">
        <BotManagementPanel projects={projects as never} />
      </ClientProvider>,
    );
    const channel = await screen.findByLabelText("Claimed channel used for project verification");
    fireEvent.change(channel, { target: { value: "feishu:engineering" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign bot to current project" }));

    await waitFor(() => expect(beginPairing).toHaveBeenCalledWith({
      purpose: "assign_bot_project",
      channelType: "feishu",
      instanceId: "engineering",
      projectId: project.id,
    }));
  });

  it("saves the remaining sorted Skills while retaining unrelated capability settings", async () => {
    vi.mocked(fetchNanobotFeatures).mockResolvedValue({ features: [] } as never);
    vi.mocked(fetchSkills).mockResolvedValue({
      skills: [
        { name: "alpha", description: "Alpha", source: "workspace", enabled: true },
        { name: "beta", description: "Beta", source: "workspace", enabled: true },
      ],
    } as never);
    const saveBotCapabilities = vi.fn().mockResolvedValue(undefined);
    const projects = {
      summary: { bots: [releaseBot], projects: [project] },
      organizationId: studio.id,
      botId: releaseBot.id,
      projectId: project.id,
      botDetail: {
        bot: releaseBot,
        channels: [],
        projects: [],
        capability_profiles: [{
          bot_id: releaseBot.id, project_id: null, revision: 2,
          settings: { mcpServers: ["docs"], skills: ["beta"] }, updated_at_ms: timestamp,
        }],
      },
      botDetailLoading: false,
      busyKey: null,
      selectBot: vi.fn(),
      createBot: vi.fn(),
      beginPairing: vi.fn(),
      finishPairing: vi.fn(),
      saveBotCapabilities,
    };

    render(
      <ClientProvider client={{ requestMutation: vi.fn() } as never} token="token">
        <BotManagementPanel projects={projects as never} />
      </ClientProvider>,
    );
    fireEvent.click(await screen.findByRole("checkbox", { name: "Not allowed for this bot" }));
    fireEvent.click(screen.getByRole("button", { name: "Save Skill access" }));

    await waitFor(() => expect(saveBotCapabilities).toHaveBeenCalledWith({
      mcpServers: ["docs"],
      skills: ["alpha", "beta"],
    }));
  });

  it.each([
    { state: "active" as const, label: "Disable", expectedState: "disabled" as const },
    { state: "disabled" as const, label: "Enable", expectedState: "active" as const },
  ])("requests $expectedState when toggling a $state bot", async ({ state, label, expectedState }) => {
    vi.mocked(fetchNanobotFeatures).mockResolvedValue({ features: [] } as never);
    vi.mocked(fetchSkills).mockResolvedValue({ skills: [] } as never);
    const bot = { ...releaseBot, state };
    const updateBotState = vi.fn().mockResolvedValue(undefined);
    const projects = {
      summary: { bots: [bot], projects: [project] },
      organizationId: studio.id, botId: bot.id, projectId: project.id,
      botDetail: { bot, channels: [], projects: [], capability_profiles: [] },
      botDetailLoading: false, busyKey: null, selectBot: vi.fn(), createBot: vi.fn(),
      beginPairing: vi.fn(), finishPairing: vi.fn(), saveBotCapabilities: vi.fn(), updateBotState,
    };
    render(<ClientProvider client={{ requestMutation: vi.fn() } as never} token="token">
      <BotManagementPanel projects={projects as never} />
    </ClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: label }));
    await waitFor(() => expect(updateBotState).toHaveBeenCalledWith(expectedState));
  });
});
