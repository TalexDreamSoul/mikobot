import type {
  ApiServicePayload,
  AutomationsPayload,
  AutomationUpdatePayload,
  ChannelConfigurePayload,
  ChannelConnectPayload,
  ChannelValidationPayload,
  ChatSummary,
  CliAppsPayload,
  CollaborationBot,
  CollaborationBotCapabilityProfile,
  CollaborationBotPayload,
  CollaborationPairingPayload,
  CollaborationPairingPurpose,
  CollaborationContextSource,
  CollaborationEditableContextSourceKind,
  CollaborationExtensionProfile,
  CollaborationExtensionSettings,
  CollaborationOrganization,
  CollaborationOrganizationMember,
  CollaborationOrganizationPayload,
  CollaborationOrganizationRole,
  CollaborationOrganizationsPayload,
  CollaborationPayload,
  CollaborationProject,
  CollaborationProjectMember,
  CollaborationProjectPayload,
  CollaborationProjectRole,
  CollaborationTask,
  CollaborationTaskList,
  CollaborationTaskStatus,
  PersonalAssistantPayload,
  PersonalTask,
  PersonalTaskReviewState,
  PersonalVault,
  FilePreviewPayload,
  ImageGenerationSettingsUpdate,
  LoginSecurityPayload,
  LoginSecuritySettings,
  McpPresetsPayload,
  McpOAuthFlowPayload,
  MarketplaceProvider,
  NanobotFeaturesPayload,
  ModelConfigurationCreate,
  ModelConfigurationUpdate,
  NetworkSafetySettingsUpdate,
  PairingPayload,
  ProviderCreationUpdate,
  ProviderModelsPayload,
  ProviderOAuthCompletionResult,
  ProviderOAuthLoginResult,
  ProviderSettingsUpdate,
  RecoveryState,
  SessionDeleteResult,
  SessionHandle,
  SessionAutomationsPayload,
  SettingsPayload,
  SettingsUpdate,
  SidebarStatePayload,
  SkillDetail,
  SkillActionPayload,
  SkillInstallPayload,
  SkillsPayload,
  SkillsSearchPayload,
  SkillsTrendsPayload,
  SkillsTrendingPayload,
  SlashCommand,
  SlashCommandLifecycle,
  TranscriptionSettingsUpdate,
  WebSearchSettingsUpdate,
  WorkspacesPayload,
  WebuiThreadPersistedPayload,
  WorkspaceScopePayload,
} from "./types";
import { fetchWithTimeout } from "./http";

const API_READ_TIMEOUT_MS = 20_000;
const API_MUTATION_TIMEOUT_MS = 20_000;
const PACKAGE_MUTATION_TIMEOUT_MS = 150_000;
const SLASH_COMMAND_LIFECYCLES = new Set<SlashCommandLifecycle>([
  "side_channel",
  "finalize_active_turn",
  "stop_active_turn",
  "agent_turn",
  "agent_turn_with_args",
]);

function isSlashCommandLifecycle(value: unknown): value is SlashCommandLifecycle {
  return (
    typeof value === "string"
    && SLASH_COMMAND_LIFECYCLES.has(value as SlashCommandLifecycle)
  );
}
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export interface WebUIMutationTransport {
  requestMutation<T>(
    action: string,
    payload?: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T>;
}

async function request<T>(
  url: string,
  token: string,
  init?: RequestInit,
  timeoutMs: number = 0,
): Promise<T> {
  const res = await fetchWithTimeout(
    url,
    {
      ...(init ?? {}),
      headers: {
        ...(init?.headers ?? {}),
        Authorization: `Bearer ${token}`,
      },
      credentials: "same-origin",
    },
    timeoutMs,
  );
  if (!res.ok) {
    const text = typeof res.text === "function" ? (await res.text()).trim() : "";
    let message = text;
    if (text.startsWith("{")) {
      try {
        const payload: unknown = JSON.parse(text);
        if (payload && typeof payload === "object") {
          const error = (payload as { error?: unknown }).error;
          if (typeof error === "string" && error.trim()) message = error.trim();
        }
      } catch {
        // Preserve non-JSON error bodies exactly as returned by the gateway.
      }
    }
    throw new ApiError(res.status, message || `HTTP ${res.status}`);
  }
  const contentType = res.headers?.get?.("content-type") ?? "";
  if (contentType && !contentType.toLowerCase().includes("application/json")) {
    const text = typeof res.text === "function" ? await res.text() : "";
    const isHtml = text.trimStart().toLowerCase().startsWith("<!doctype");
    throw new ApiError(
      res.status,
      isHtml
        ? "Gateway returned WebUI HTML instead of JSON. Restart nanobot gateway and try again."
        : "Gateway returned a non-JSON response.",
    );
  }
  return (await res.json()) as T;
}

async function mutation<T>(
  transport: WebUIMutationTransport,
  action: string,
  payload: Record<string, unknown> = {},
  timeoutMs: number = API_MUTATION_TIMEOUT_MS,
): Promise<T> {
  try {
    return await transport.requestMutation<T>(action, payload, timeoutMs);
  } catch (reason) {
    const status = (
      typeof reason === "object"
      && reason !== null
      && "status" in reason
      && typeof reason.status === "number"
    ) ? reason.status : 500;
    const message = reason instanceof Error ? reason.message : "WebUI mutation failed";
    throw new ApiError(status, message);
  }
}

function compactMcpValues(values: Record<string, unknown>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  Object.entries(values).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) payload[key] = trimmed;
      return;
    }
    payload[key] = value;
  });
  return payload;
}

function splitKey(key: string): { channel: string; chatId: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { channel: "", chatId: key };
  return { channel: key.slice(0, idx), chatId: key.slice(idx + 1) };
}

function normalizeSessionHandle(value: unknown): SessionHandle | null {
  if (!value || typeof value !== "object") return null;
  const handle = value as Partial<SessionHandle>;
  const id = typeof handle.id === "string" ? handle.id.trim() : "";
  const name = typeof handle.name === "string" ? handle.name.trim() : "";
  if (
    !/^handle_[a-f0-9]{32}$/i.test(id)
    || !name
    || !/^[\p{L}\p{N}_-]+$/u.test(name)
  ) return null;
  return { id, name };
}

export async function listSessions(
  token: string,
  base: string = "",
): Promise<ChatSummary[]> {
  type Row = {
    key: string;
    created_at: string | null;
    updated_at: string | null;
    title?: string;
    preview?: string;
    model_preset?: string | null;
    run_started_at?: number | null;
    recovery_state?: RecoveryState | null;
    workspace_scope?: WorkspaceScopePayload | null;
    handle?: SessionHandle | null;
  };
  const body = await request<{ sessions: Row[] }>(
    `${base}/api/sessions`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return body.sessions.map((s) => {
    const handle = normalizeSessionHandle(s.handle);
    return {
      key: s.key,
      ...splitKey(s.key),
      createdAt: s.created_at,
      updatedAt: s.updated_at,
      title: s.title ?? "",
      preview: s.preview ?? "",
      modelPreset: s.model_preset ?? null,
      runStartedAt: s.run_started_at ?? null,
      recoveryState: s.recovery_state ?? null,
      workspaceScope: s.workspace_scope ?? null,
      handle,
    };
  });
}

/** Disk-backed WebUI display thread snapshot (separate from agent session). */
export interface FetchWebuiThreadOptions {
  limit?: number;
  direction?: "latest";
  before?: string | null;
  signal?: AbortSignal;
}

export async function fetchWebuiThread(
  token: string,
  key: string,
  optionsOrBase?: FetchWebuiThreadOptions | string,
  base: string = "",
): Promise<WebuiThreadPersistedPayload | null> {
  const options = typeof optionsOrBase === "string" ? undefined : optionsOrBase;
  const resolvedBase = typeof optionsOrBase === "string" ? optionsOrBase : base;
  const params = new URLSearchParams();
  if (options?.limit !== undefined) params.set("limit", String(options.limit));
  if (options?.direction) params.set("direction", options.direction);
  if (options?.before) params.set("before", options.before);
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  const url = `${resolvedBase}/api/sessions/${encodeURIComponent(key)}/webui-thread${suffix}`;
  const res = await fetchWithTimeout(url, {
    headers: { Authorization: `Bearer ${token}` },
    credentials: "same-origin",
    cache: "no-store",
    signal: options?.signal,
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return (await res.json()) as WebuiThreadPersistedPayload;
}

export async function fetchFilePreview(
  token: string,
  key: string,
  path: string,
  base: string = "",
): Promise<FilePreviewPayload> {
  const query = new URLSearchParams();
  query.set("path", path);
  return request<FilePreviewPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-preview?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchFilePreviewAvailability(
  token: string,
  key: string,
  path: string,
  base: string = "",
): Promise<boolean> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("probe", "1");
  const payload = await request<{ available?: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-preview?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return payload.available !== false;
}

export async function fetchSessionAutomations(
  token: string,
  key: string,
  base: string = "",
): Promise<SessionAutomationsPayload> {
  return request<SessionAutomationsPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/automations`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchAutomations(
  token: string,
  base: string = "",
): Promise<AutomationsPayload> {
  return request<AutomationsPayload>(
    `${base}/api/webui/automations`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runAutomationAction(
  transport: WebUIMutationTransport,
  action: "enable" | "disable" | "delete" | "run",
  id: string,
): Promise<AutomationsPayload> {
  return mutation<AutomationsPayload>(transport, `automation.${action}`, { id });
}

export async function updateAutomation(
  transport: WebUIMutationTransport,
  id: string,
  values: AutomationUpdatePayload,
): Promise<AutomationsPayload> {
  return mutation<AutomationsPayload>(transport, "automation.update", { id, values });
}

export async function fetchSkills(
  token: string,
  base: string = "",
): Promise<SkillsPayload> {
  return request<SkillsPayload>(
    `${base}/api/webui/skills`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSkillDetail(
  token: string,
  name: string,
  base: string = "",
): Promise<SkillDetail> {
  return request<SkillDetail>(
    `${base}/api/webui/skills/${encodeURIComponent(name)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateSkillEnabled(
  transport: WebUIMutationTransport,
  name: string,
  enabled: boolean,
): Promise<SkillActionPayload> {
  return mutation<SkillActionPayload>(transport, "skill.update", { name, enabled });
}

export async function deleteSkill(
  transport: WebUIMutationTransport,
  name: string,
): Promise<SkillActionPayload> {
  return mutation<SkillActionPayload>(transport, "skill.delete", { name });
}

export async function searchMarketplaceSkills(
  token: string,
  query: string,
  provider: MarketplaceProvider = "all",
  base: string = "",
): Promise<SkillsSearchPayload> {
  const params = new URLSearchParams({ q: query, provider });
  return request<SkillsSearchPayload>(
    `${base}/api/webui/skills/search?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchTrendingMarketplaceSkills(
  token: string,
  provider: MarketplaceProvider = "all",
  base: string = "",
): Promise<SkillsTrendingPayload> {
  const params = new URLSearchParams({ provider });
  return request<SkillsTrendingPayload>(
    `${base}/api/webui/skills/trending?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMarketplaceSkillTrends(
  token: string,
  skillIds: string[],
  base: string = "",
): Promise<SkillsTrendsPayload> {
  const params = new URLSearchParams();
  skillIds.forEach((id) => params.append("id", id));
  return request<SkillsTrendsPayload>(
    `${base}/api/webui/skills/trends?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function installMarketplaceSkill(
  transport: WebUIMutationTransport,
  provider: Exclude<MarketplaceProvider, "all">,
  source: string,
  skill: string,
  version: string = "",
): Promise<SkillInstallPayload> {
  return mutation<SkillInstallPayload>(
    transport,
    "skill.install",
    { provider, source, skill, ...(version ? { version } : {}) },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function deleteSession(
  transport: WebUIMutationTransport,
  key: string,
  optionsOrBase?: { deleteAutomations?: boolean } | string,
): Promise<SessionDeleteResult> {
  const options = typeof optionsOrBase === "string" ? undefined : optionsOrBase;
  return mutation<SessionDeleteResult>(
    transport,
    "session.delete",
    {
      key,
      ...(options?.deleteAutomations ? { delete_automations: true } : {}),
    },
  );
}

export async function fetchSettings(
  token: string,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    `${base}/api/settings`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchLoginSecurity(
  token: string,
  base: string = "",
): Promise<LoginSecurityPayload> {
  return request<LoginSecurityPayload>(
    `${base}/api/settings/login-security`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateLoginSecurity(
  transport: WebUIMutationTransport,
  values: Partial<Omit<LoginSecuritySettings, "client_secret_configured" | "restart_required_after_save">> & {
    client_secret?: string;
    clear_client_secret?: boolean;
  },
): Promise<LoginSecurityPayload> {
  return mutation<LoginSecurityPayload>(
    transport,
    "settings.login_security.update",
    values,
  );
}

export async function fetchSettingsUsage(
  token: string,
  base: string = "",
): Promise<NonNullable<SettingsPayload["usage"]>> {
  return request<NonNullable<SettingsPayload["usage"]>>(
    `${base}/api/settings/usage`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export interface VersionCheckResult {
  updateAvailable: {
    currentVersion: string;
    latestVersion: string;
    pypiUrl?: string;
  } | null;
}

export async function checkVersion(
  token: string,
  base: string = "",
): Promise<VersionCheckResult> {
  return request<VersionCheckResult>(
    `${base}/api/settings/version-check`,
    token,
    undefined,
    10_000,
  );
}

export async function fetchWorkspaces(
  token: string,
  base: string = "",
): Promise<WorkspacesPayload> {
  return request<WorkspacesPayload>(
    `${base}/api/workspaces`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCollaboration(
  token: string,
  base: string = "",
): Promise<CollaborationPayload> {
  return request<CollaborationPayload>(`${base}/api/collaboration`, token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchCollaborationOrganizations(
  token: string,
  base: string = "",
): Promise<CollaborationOrganizationsPayload> {
  return request<CollaborationOrganizationsPayload>(
    `${base}/api/collaboration/organizations`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCollaborationOrganization(
  token: string,
  organizationId: string,
  base: string = "",
): Promise<CollaborationOrganizationPayload> {
  return request<CollaborationOrganizationPayload>(
    `${base}/api/collaboration/organizations/${encodeURIComponent(organizationId)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCollaborationProject(
  token: string,
  projectId: string,
  base: string = "",
): Promise<CollaborationProjectPayload> {
  return request<CollaborationProjectPayload>(
    `${base}/api/collaboration/projects/${encodeURIComponent(projectId)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCollaborationBot(
  token: string,
  botId: string,
  base: string = "",
): Promise<CollaborationBotPayload> {
  return request<CollaborationBotPayload>(
    `${base}/api/collaboration/bots/${encodeURIComponent(botId)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCollaborationPairing(
  token: string,
  challengeId: string,
  base: string = "",
): Promise<CollaborationPairingPayload> {
  return request<CollaborationPairingPayload>(
    `${base}/api/collaboration/pairing/${encodeURIComponent(challengeId)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function createCollaborationOrganization(
  transport: WebUIMutationTransport,
  name: string,
): Promise<{ organization: CollaborationOrganization }> {
  return mutation<{ organization: CollaborationOrganization }>(
    transport,
    "collaboration.organization.create",
    { name },
  );
}

export async function updateCollaborationOrganization(
  transport: WebUIMutationTransport,
  organizationId: string,
  name: string,
): Promise<{ organization: CollaborationOrganization }> {
  return mutation<{ organization: CollaborationOrganization }>(
    transport,
    "collaboration.organization.update",
    { organization_id: organizationId, name },
  );
}

export async function deleteCollaborationOrganization(
  transport: WebUIMutationTransport,
  organizationId: string,
): Promise<{ deleted: boolean }> {
  return mutation<{ deleted: boolean }>(
    transport,
    "collaboration.organization.delete",
    { organization_id: organizationId },
  );
}

export async function addCollaborationOrganizationMember(
  transport: WebUIMutationTransport,
  organizationId: string,
  memberUserId: string,
  role: CollaborationOrganizationRole,
): Promise<{ member: CollaborationOrganizationMember }> {
  return mutation<{ member: CollaborationOrganizationMember }>(
    transport,
    "collaboration.organization.member.add",
    { organization_id: organizationId, member_user_id: memberUserId, role },
  );
}

export async function removeCollaborationOrganizationMember(
  transport: WebUIMutationTransport,
  organizationId: string,
  memberUserId: string,
): Promise<{ deleted: boolean }> {
  return mutation<{ deleted: boolean }>(
    transport,
    "collaboration.organization.member.remove",
    { organization_id: organizationId, member_user_id: memberUserId },
  );
}

export async function updateCollaborationDefaults(
  transport: WebUIMutationTransport,
  values: { organizationId: string; botId: string; projectId?: string | null },
): Promise<{ user: CollaborationPayload["user"] }> {
  return mutation(transport, "collaboration.user.defaults", {
    organization_id: values.organizationId,
    bot_id: values.botId,
    ...(values.projectId ? { project_id: values.projectId } : {}),
  });
}

export async function createCollaborationBot(
  transport: WebUIMutationTransport,
  values: { organizationId: string; name: string; avatarUrl?: string; personaId?: string },
): Promise<{ bot: CollaborationBot }> {
  return mutation(transport, "collaboration.bot.create", {
    organization_id: values.organizationId,
    name: values.name,
    ...(values.avatarUrl ? { avatar_url: values.avatarUrl } : {}),
    ...(values.personaId ? { persona_id: values.personaId } : {}),
  });
}

export async function updateCollaborationBot(
  transport: WebUIMutationTransport,
  botId: string,
  values: { name?: string; avatarUrl?: string; personaId?: string; state?: "active" | "disabled" },
): Promise<{ bot: CollaborationBot }> {
  return mutation(transport, "collaboration.bot.update", {
    bot_id: botId,
    ...(values.name ? { name: values.name } : {}),
    ...(values.avatarUrl ? { avatar_url: values.avatarUrl } : {}),
    ...(values.personaId ? { persona_id: values.personaId } : {}),
    ...(values.state ? { state: values.state } : {}),
  });
}

export async function deleteCollaborationBot(
  transport: WebUIMutationTransport,
  botId: string,
): Promise<{ deleted: boolean }> {
  return mutation(transport, "collaboration.bot.delete", { bot_id: botId });
}

export async function updateCollaborationBotCapabilities(
  transport: WebUIMutationTransport,
  botId: string,
  settings: CollaborationExtensionSettings,
  options: { projectId?: string | null; revision?: number } = {},
): Promise<{ capability_profile: CollaborationBotCapabilityProfile }> {
  return mutation(transport, "collaboration.bot.capabilities.update", {
    bot_id: botId,
    settings,
    ...(options.projectId ? { project_id: options.projectId } : {}),
    ...(options.revision !== undefined ? { revision: options.revision } : {}),
  });
}

export async function createCollaborationPairingChallenge(
  transport: WebUIMutationTransport,
  values: {
    purpose: CollaborationPairingPurpose;
    organizationId: string;
    botId: string;
    channelType: string;
    instanceId: string;
    projectId?: string | null;
  },
): Promise<CollaborationPairingPayload> {
  return mutation(transport, "collaboration.pairing.create", {
    purpose: values.purpose,
    organization_id: values.organizationId,
    bot_id: values.botId,
    channel_type: values.channelType,
    instance_id: values.instanceId,
    ...(values.projectId ? { project_id: values.projectId } : {}),
  });
}

export async function consumeCollaborationPairingChallenge(
  transport: WebUIMutationTransport,
  challengeId: string,
): Promise<CollaborationPairingPayload> {
  return mutation(transport, "collaboration.pairing.consume", {
    challenge_id: challengeId,
  });
}

export async function createCollaborationProject(
  transport: WebUIMutationTransport,
  name: string,
  organizationId?: string | null,
): Promise<{ project: CollaborationProject }> {
  return mutation<{ project: CollaborationProject }>(
    transport,
    "collaboration.project.create",
    { name, ...(organizationId ? { organization_id: organizationId } : {}) },
  );
}

export async function addCollaborationProjectMember(
  transport: WebUIMutationTransport,
  projectId: string,
  memberUserId: string,
  role: CollaborationProjectRole,
): Promise<{ member: CollaborationProjectMember }> {
  return mutation<{ member: CollaborationProjectMember }>(
    transport,
    "collaboration.project.member.add",
    { project_id: projectId, member_user_id: memberUserId, role },
  );
}

export async function removeCollaborationProjectMember(
  transport: WebUIMutationTransport,
  projectId: string,
  memberUserId: string,
): Promise<{ deleted: boolean }> {
  return mutation<{ deleted: boolean }>(
    transport,
    "collaboration.project.member.remove",
    { project_id: projectId, member_user_id: memberUserId },
  );
}

export async function createCollaborationTaskList(
  transport: WebUIMutationTransport,
  projectId: string,
  name: string,
): Promise<{ task_list: CollaborationTaskList }> {
  return mutation<{ task_list: CollaborationTaskList }>(transport, "collaboration.task_list.create", {
    project_id: projectId,
    name,
  });
}

export async function createCollaborationTask(
  transport: WebUIMutationTransport,
  projectId: string,
  taskListId: string,
  title: string,
): Promise<{ task: CollaborationTask }> {
  return mutation<{ task: CollaborationTask }>(transport, "collaboration.task.create", {
    project_id: projectId,
    task_list_id: taskListId,
    title,
  });
}

export async function updateCollaborationTask(
  transport: WebUIMutationTransport,
  projectId: string,
  taskId: string,
  values: { status: CollaborationTaskStatus },
): Promise<{ task: CollaborationTask }> {
  return mutation<{ task: CollaborationTask }>(transport, "collaboration.task.update", {
    project_id: projectId,
    task_id: taskId,
    values,
  });
}

export async function deleteCollaborationTask(
  transport: WebUIMutationTransport,
  projectId: string,
  taskId: string,
): Promise<{ deleted: true }> {
  return mutation<{ deleted: true }>(transport, "collaboration.task.delete", {
    project_id: projectId,
    task_id: taskId,
  });
}

export async function updateCollaborationExtensions(
  transport: WebUIMutationTransport,
  projectId: string,
  expectedRevision: number,
  settings: CollaborationExtensionSettings,
): Promise<{ extension_profile: CollaborationExtensionProfile }> {
  return mutation<{ extension_profile: CollaborationExtensionProfile }>(transport, "collaboration.extensions.update", {
    project_id: projectId,
    revision: expectedRevision,
    settings,
  });
}

export async function createCollaborationContextSource(
  transport: WebUIMutationTransport,
  projectId: string,
  values: {
    name: string;
    kind: CollaborationEditableContextSourceKind;
    config: Record<string, string>;
    enabled: boolean;
  },
): Promise<{ context_source: CollaborationContextSource }> {
  return mutation<{ context_source: CollaborationContextSource }>(transport, "collaboration.context_source.create", {
    project_id: projectId,
    ...values,
  });
}

export async function updateCollaborationContextSource(
  transport: WebUIMutationTransport,
  projectId: string,
  sourceId: string,
  values: { enabled: boolean },
): Promise<{ context_source: CollaborationContextSource }> {
  return mutation<{ context_source: CollaborationContextSource }>(transport, "collaboration.context_source.update", {
    project_id: projectId,
    source_id: sourceId,
    values,
  });
}

export async function deleteCollaborationContextSource(
  transport: WebUIMutationTransport,
  projectId: string,
  sourceId: string,
): Promise<{ deleted: true }> {
  return mutation<{ deleted: true }>(transport, "collaboration.context_source.delete", {
    project_id: projectId,
    source_id: sourceId,
  });
}

export async function fetchPersonalAssistant(
  token: string,
  base: string = "",
): Promise<PersonalAssistantPayload> {
  return request<PersonalAssistantPayload>(`${base}/api/personal`, token, undefined, API_READ_TIMEOUT_MS);
}

export async function createPersonalTask(
  transport: WebUIMutationTransport,
  values: {
    vault_id: string;
    title: string;
    note?: string;
    due_at_ms?: number;
    priority?: number;
    review_state?: PersonalTaskReviewState;
  },
): Promise<{ task: PersonalTask }> {
  return mutation<{ task: PersonalTask }>(transport, "personal.task.create", values);
}

export async function updatePersonalTask(
  transport: WebUIMutationTransport,
  taskId: string,
  values: {
    status?: PersonalTask["status"];
    review_state?: PersonalTaskReviewState;
    priority?: number;
  },
): Promise<{ task: PersonalTask }> {
  return mutation<{ task: PersonalTask }>(transport, "personal.task.update", {
    task_id: taskId,
    ...values,
  });
}

export async function deletePersonalTask(
  transport: WebUIMutationTransport,
  taskId: string,
): Promise<{ deleted: boolean }> {
  return mutation<{ deleted: boolean }>(transport, "personal.task.delete", { task_id: taskId });
}

export async function createPersonalVault(
  transport: WebUIMutationTransport,
  name: string,
  kind: PersonalVault["kind"],
): Promise<{ vault: PersonalVault }> {
  return mutation<{ vault: PersonalVault }>(transport, "personal.vault.create", { name, kind });
}

export async function createPersonalPersona(
  transport: WebUIMutationTransport,
  values: { name: string; vault_id: string; instructions?: string },
): Promise<{ persona: { id: string; name: string; default_vault_id: string } }> {
  return mutation<{ persona: { id: string; name: string; default_vault_id: string } }>(
    transport,
    "personal.persona.create",
    values,
  );
}

export async function setDefaultPersonalPersona(
  transport: WebUIMutationTransport,
  personaId: string,
): Promise<{ default_persona_id: string }> {
  return mutation<{ default_persona_id: string }>(
    transport,
    "personal.persona.default",
    { persona_id: personaId },
  );
}

export async function fetchCliApps(
  token: string,
  base: string = "",
): Promise<CliAppsPayload> {
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchInstalledCliApps(
  token: string,
  base: string = "",
): Promise<CliAppsPayload> {
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps?installed_only=1`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchNanobotFeatures(
  token: string,
  base: string = "",
): Promise<NanobotFeaturesPayload> {
  return request<NanobotFeaturesPayload>(
    `${base}/api/settings/nanobot-features`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchApiService(token: string, base: string = ""): Promise<ApiServicePayload> {
  return request<ApiServicePayload>(`${base}/api/settings/api-service`, token);
}

export interface NanobotFeatureActionTarget {
  extensionId: string;
  expectedRevision: string;
  riskAcknowledged?: boolean;
}

export async function startApiService(
  transport: WebUIMutationTransport,
  values: { host: string; port: number; timeout: number; apiKey?: string },
  target: NanobotFeatureActionTarget,
): Promise<ApiServicePayload> {
  return mutation<ApiServicePayload>(
    transport,
    "settings.api_service.start",
    {
      host: values.host,
      port: values.port,
      timeout: values.timeout,
      ...(values.apiKey !== undefined ? { api_key: values.apiKey } : {}),
      ...nanobotFeatureActionPayload(target),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function stopApiService(
  transport: WebUIMutationTransport,
): Promise<ApiServicePayload> {
  return mutation<ApiServicePayload>(transport, "settings.api_service.stop");
}

export interface NanobotFeatureActionOptions extends NanobotFeatureActionTarget {
  instanceId?: string;
}

export interface ChannelConfigureOptions extends NanobotFeatureActionOptions {
  enable?: boolean;
}

function nanobotFeatureActionPayload(options: NanobotFeatureActionOptions): Record<string, unknown> {
  const extensionId = options.extensionId.trim();
  const expectedRevision = options.expectedRevision.trim();
  if (!extensionId || !expectedRevision) {
    throw new ApiError(400, "Extension action target is unavailable.");
  }
  return {
    extension_id: extensionId,
    expected_revision: expectedRevision,
    ...(options.instanceId ? { instance_id: options.instanceId } : {}),
    ...(options.riskAcknowledged ? { risk_acknowledged: true } : {}),
  };
}

export async function enableNanobotFeature(
  transport: WebUIMutationTransport,
  name: string,
  options: NanobotFeatureActionOptions,
): Promise<NanobotFeaturesPayload> {
  return mutation<NanobotFeaturesPayload>(
    transport,
    "settings.feature.enable",
    { name, ...nanobotFeatureActionPayload(options) },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function disableNanobotFeature(
  transport: WebUIMutationTransport,
  name: string,
  options: NanobotFeatureActionOptions,
): Promise<NanobotFeaturesPayload> {
  return mutation<NanobotFeaturesPayload>(
    transport,
    "settings.feature.disable",
    { name, ...nanobotFeatureActionPayload(options) },
  );
}

export async function fetchPairingRequests(
  token: string,
  base: string = "",
): Promise<PairingPayload> {
  return request<PairingPayload>(
    `${base}/api/settings/pairing`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runPairingAction(
  transport: WebUIMutationTransport,
  action: "approve" | "deny",
  code: string,
): Promise<PairingPayload> {
  return mutation<PairingPayload>(transport, `settings.pairing.${action}`, { code });
}

export type ChannelConnectTarget = Pick<
  NanobotFeatureActionOptions,
  "extensionId" | "expectedRevision" | "riskAcknowledged"
> & {
  instanceId: string;
};

export interface ChannelConnectStartOptions extends ChannelConnectTarget {
  domain?: string;
  mode?: "replace" | "create";
  force?: boolean;
}

function channelConnectTargetPayload(options: ChannelConnectTarget): Record<string, unknown> {
  const instanceId = options.instanceId.trim();
  if (!instanceId) {
    throw new ApiError(400, "Extension action target is unavailable.");
  }
  return {
    ...nanobotFeatureActionPayload(options),
    instance_id: instanceId,
  };
}

export async function startChannelConnect(
  transport: WebUIMutationTransport,
  channel: string,
  options: ChannelConnectStartOptions,
): Promise<ChannelConnectPayload> {
  return mutation<ChannelConnectPayload>(
    transport,
    "settings.channel.connect.start",
    {
      channel,
      ...channelConnectTargetPayload(options),
      ...(options.domain ? { domain: options.domain } : {}),
      ...(options.mode ? { mode: options.mode } : {}),
      ...(options.force ? { force: true } : {}),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function pollChannelConnect(
  transport: WebUIMutationTransport,
  channel: string,
  sessionId: string,
  options: ChannelConnectTarget,
  params: Readonly<Record<string, string>> = {},
): Promise<ChannelConnectPayload> {
  const values = Object.fromEntries(
    Object.entries(params).filter(([key]) => ![
      "extension_id",
      "expected_revision",
      "instance_id",
      "risk_acknowledged",
      "session_id",
    ].includes(key)),
  );
  return mutation<ChannelConnectPayload>(
    transport,
    "settings.channel.connect.poll",
    {
      channel,
      session_id: sessionId,
      ...channelConnectTargetPayload(options),
      ...values,
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function cancelChannelConnect(
  transport: WebUIMutationTransport,
  channel: string,
  sessionId: string,
  options: ChannelConnectTarget,
): Promise<ChannelConnectPayload> {
  return mutation<ChannelConnectPayload>(
    transport,
    "settings.channel.connect.cancel",
    {
      channel,
      session_id: sessionId,
      ...channelConnectTargetPayload(options),
    },
  );
}

export async function configureChannel(
  transport: WebUIMutationTransport,
  name: string,
  values: Record<string, string>,
  options: ChannelConfigureOptions,
): Promise<ChannelConfigurePayload> {
  return mutation<ChannelConfigurePayload>(
    transport,
    "settings.channel.configure",
    {
      name,
      values,
      ...(options.enable !== undefined ? { enable: options.enable } : {}),
      ...nanobotFeatureActionPayload(options),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function validateChannel(
  transport: WebUIMutationTransport,
  name: string,
  values: Record<string, string> = {},
  options: { instanceId?: string } = {},
): Promise<ChannelValidationPayload> {
  return mutation<ChannelValidationPayload>(
    transport,
    "settings.channel.validate",
    { name, values, ...(options.instanceId ? { instance_id: options.instanceId } : {}) },
  );
}

export async function runCliAppAction(
  transport: WebUIMutationTransport,
  action: "install" | "update" | "uninstall" | "test",
  name: string,
): Promise<CliAppsPayload> {
  return mutation<CliAppsPayload>(
    transport,
    `settings.cli_app.${action}`,
    { name },
    action === "install" || action === "update"
      ? PACKAGE_MUTATION_TIMEOUT_MS
      : API_MUTATION_TIMEOUT_MS,
  );
}

export async function fetchMcpPresets(
  token: string,
  base: string = "",
): Promise<McpPresetsPayload> {
  return request<McpPresetsPayload>(
    `${base}/api/settings/mcp-presets`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function startMcpOAuth(
  transport: WebUIMutationTransport,
  name: string,
  reset: boolean = false,
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_start",
    { name, ...(reset ? { reset: true } : {}) },
    30_000,
  );
}

export async function fetchMcpOAuthStatus(
  token: string,
  flowId: string,
  base: string = "",
): Promise<McpOAuthFlowPayload> {
  const query = new URLSearchParams({ flow_id: flowId });
  return request<McpOAuthFlowPayload>(
    `${base}/api/settings/mcp-oauth/status?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function completeMcpOAuth(
  transport: WebUIMutationTransport,
  flowId: string,
  callbackUrl: string,
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_complete",
    { flow_id: flowId, callback_url: callbackUrl },
  );
}

export async function cancelMcpOAuth(
  transport: WebUIMutationTransport,
  flowId: string,
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_cancel",
    { flow_id: flowId },
  );
}

export async function fetchProviderModels(
  token: string,
  provider: string,
  base: string = "",
): Promise<ProviderModelsPayload> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  return request<ProviderModelsPayload>(
    `${base}/api/settings/provider-models?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runMcpPresetAction(
  transport: WebUIMutationTransport,
  action: "enable" | "disable" | "remove" | "test" | "reconnect",
  name: string,
  values: Record<string, string> = {},
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    `settings.mcp.${action}`,
    { name, ...compactMcpValues(values) },
  );
}

export async function saveCustomMcpServer(
  transport: WebUIMutationTransport,
  values: Record<string, string>,
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    "settings.mcp.custom",
    compactMcpValues(values),
  );
}

export async function importMcpConfig(
  transport: WebUIMutationTransport,
  config: string,
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(transport, "settings.mcp.import", { config });
}

export async function updateMcpServerTools(
  transport: WebUIMutationTransport,
  name: string,
  enabledTools: string[],
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    "settings.mcp.tools",
    { name, enabled_tools: enabledTools },
  );
}

export async function listSlashCommands(
  token: string,
  base: string = "",
): Promise<SlashCommand[]> {
  type Row = {
    command: string;
    title: string;
    description: string;
    icon: string;
    arg_hint?: string;
    lifecycle?: unknown;
    accepts_args?: unknown;
  };
  const body = await request<{ commands: Row[] }>(
    `${base}/api/commands`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return body.commands
    .flatMap((command) => {
      if (!isSlashCommandLifecycle(command.lifecycle)) return [];
      return [{
        command: command.command,
        title: command.title,
        description: command.description,
        icon: command.icon,
        argHint: command.arg_hint ?? "",
        lifecycle: command.lifecycle,
        acceptsArgs: command.accepts_args === true,
      }];
    });
}

export async function fetchSidebarState(
  token: string,
  base: string = "",
): Promise<SidebarStatePayload> {
  return request<SidebarStatePayload>(
    `${base}/api/webui/sidebar-state`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateSidebarState(
  transport: WebUIMutationTransport,
  state: SidebarStatePayload,
): Promise<SidebarStatePayload> {
  return mutation<SidebarStatePayload>(transport, "sidebar.update", { state });
}

export async function updateSettings(
  transport: WebUIMutationTransport,
  update: SettingsUpdate,
): Promise<SettingsPayload> {
  const payload: Record<string, unknown> = {};
  if (update.modelPreset !== undefined) {
    payload.model_preset = update.modelPreset ?? "default";
  }
  if (update.model !== undefined) payload.model = update.model;
  if (update.provider !== undefined) payload.provider = update.provider;
  if (update.contextWindowTokens !== undefined) {
    payload.context_window_tokens = update.contextWindowTokens;
  }
  if (update.timezone !== undefined) payload.timezone = update.timezone;
  if (update.toolHintMaxLength !== undefined) {
    payload.tool_hint_max_length = update.toolHintMaxLength;
  }
  return mutation<SettingsPayload>(transport, "settings.agent.update", payload);
}

function modelGenerationSettingsPayload(
  configuration: Pick<
    ModelConfigurationCreate,
    "maxTokens" | "contextWindowTokens" | "temperature" | "reasoningEffort"
  >,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (configuration.maxTokens !== undefined) {
    payload.max_tokens = configuration.maxTokens;
  }
  if (configuration.contextWindowTokens !== undefined) {
    payload.context_window_tokens = configuration.contextWindowTokens;
  }
  if (configuration.temperature !== undefined) {
    payload.temperature = configuration.temperature;
  }
  if (configuration.reasoningEffort !== undefined) {
    payload.reasoning_effort = configuration.reasoningEffort ?? "";
  }
  return payload;
}

export async function createModelConfiguration(
  transport: WebUIMutationTransport,
  configuration: ModelConfigurationCreate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.create",
    {
      name: configuration.name,
      provider: configuration.provider,
      model: configuration.model,
      ...modelGenerationSettingsPayload(configuration),
    },
  );
}

export async function updateModelConfiguration(
  transport: WebUIMutationTransport,
  configuration: ModelConfigurationUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.update",
    {
      name: configuration.name,
      ...(configuration.newName !== undefined ? { new_name: configuration.newName } : {}),
      ...(configuration.provider !== undefined ? { provider: configuration.provider } : {}),
      ...(configuration.model !== undefined ? { model: configuration.model } : {}),
      ...modelGenerationSettingsPayload(configuration),
    },
  );
}

export async function deleteModelConfiguration(
  transport: WebUIMutationTransport,
  name: string,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.delete",
    { name },
  );
}

export async function migrateModelConfigurations(
  transport: WebUIMutationTransport,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.model_configuration.migrate");
}

export async function updateModelCallOrder(
  transport: WebUIMutationTransport,
  order: string[],
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.model_call_order.update", { order });
}

export async function updateProviderSettings(
  transport: WebUIMutationTransport,
  update: ProviderSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.update", { ...update });
}

export async function createProviderSettings(
  transport: WebUIMutationTransport,
  update: ProviderCreationUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.create", { ...update });
}

export async function loginProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
  remoteBrowserAccess: boolean = false,
): Promise<ProviderOAuthLoginResult> {
  return mutation<ProviderOAuthLoginResult>(
    transport,
    "settings.provider.oauth_login",
    { provider, ...(remoteBrowserAccess ? { remote_browser: true } : {}) },
  );
}

export async function completeProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
  flowId: string,
  authorizationResponse?: string,
): Promise<ProviderOAuthCompletionResult> {
  return mutation<ProviderOAuthCompletionResult>(
    transport,
    "settings.provider.oauth_complete",
    {
      provider,
      flow_id: flowId,
      ...(authorizationResponse ? { authorization_response: authorizationResponse } : {}),
    },
  );
}

export async function logoutProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.oauth_logout", { provider });
}

export async function updateWebSearchSettings(
  transport: WebUIMutationTransport,
  update: WebSearchSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.web_search.update",
    {
      provider: update.provider,
      ...(update.apiKey !== undefined ? { api_key: update.apiKey } : {}),
      ...(update.baseUrl !== undefined ? { base_url: update.baseUrl } : {}),
      ...(update.maxResults !== undefined ? { max_results: update.maxResults } : {}),
      ...(update.timeout !== undefined ? { timeout: update.timeout } : {}),
      ...(update.useJinaReader !== undefined
        ? { use_jina_reader: update.useJinaReader }
        : {}),
    },
  );
}

export async function updateNetworkSafetySettings(
  transport: WebUIMutationTransport,
  update: NetworkSafetySettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.network_safety.update",
    {
      webui_allow_local_service_access: update.webuiAllowLocalServiceAccess,
      webui_default_access_mode: update.webuiDefaultAccessMode,
    },
  );
}

export async function updateImageGenerationSettings(
  transport: WebUIMutationTransport,
  update: ImageGenerationSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.image_generation.update",
    {
      enabled: update.enabled,
      provider: update.provider,
      model: update.model,
      default_aspect_ratio: update.defaultAspectRatio,
      default_image_size: update.defaultImageSize,
      max_images_per_turn: update.maxImagesPerTurn,
    },
  );
}

export async function updateTranscriptionSettings(
  transport: WebUIMutationTransport,
  update: TranscriptionSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.transcription.update",
    {
      enabled: update.enabled,
      provider: update.provider,
      model: update.model,
      language: update.language,
      max_duration_sec: update.maxDurationSec,
      max_upload_mb: update.maxUploadMb,
    },
  );
}
