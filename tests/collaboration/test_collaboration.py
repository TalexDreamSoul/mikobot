from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from nanobot.agent.context import TranscriptInput
from nanobot.agent.loop import AgentLoop, TurnContext, TurnKind
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.collaboration import ProjectsTool
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import MCPToolWrapper
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.collaboration import (
    AsyncLocalCollaborationRepository,
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    CollaborationStore,
    CollaborationStoreFormatError,
    ConversationScopeKind,
    MembershipRole,
)
from nanobot.collaboration.links import IdentityLinkError, IdentityLinkStore
from nanobot.collaboration.pairing import CHANNEL_ASSIGNMENT_REQUIRED_METADATA_KEY
from nanobot.providers.base import LLMResponse


class _BuiltinTool(Tool):
    @property
    def name(self) -> str:
        return "builtin"

    @property
    def description(self) -> str:
        return "A non-MCP tool that must remain available."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **_: object) -> str:
        return "ok"


def _user_and_project(store: CollaborationStore, root: Path, name: str):
    user = store.create_user(name)
    project = store.create_project(user.id, f"{name} project", root / name)
    store.update_user_default_project(user.id, project.id)
    return user, project


def _pair(
    store: CollaborationStore,
    actor_id: str,
    *,
    project_id: str,
    channel_type: str = "weixin",
    instance_id: str = "release",
    sender_id: str = "owner-sender",
    assignee_user_id: str | None = None,
):
    """Run one full Pair Code round trip and return the consumed challenge."""
    challenge, code = store.create_pairing_challenge(
        actor_id, project_id=project_id, channel_type=channel_type,
        instance_id=instance_id, assignee_user_id=assignee_user_id,
    )
    store.verify_pairing_challenge(
        code, channel_type=channel_type, instance_id=instance_id, sender_id=sender_id
    )
    return store.consume_pairing_challenge(actor_id, challenge.id)


@pytest_asyncio.fixture
async def local_collaboration_repository(
    tmp_path: Path,
) -> AsyncIterator[tuple[CollaborationStore, AsyncLocalCollaborationRepository]]:
    store = CollaborationStore(tmp_path / "collaboration")
    repository = AsyncLocalCollaborationRepository(store)
    await repository.initialize()
    try:
        yield store, repository
    finally:
        await repository.aclose()


def _scope_request(scope, *, text: str = "") -> RequestContext:
    return RequestContext(
        channel="telegram",
        chat_id="chat",
        sender_id="sender",
        original_user_text=text,
        attributes={"collaboration_scope": scope},
    )


def test_store_keeps_projects_private_and_reloads_external_writes(tmp_path: Path) -> None:
    """A user cannot observe another user's project, and writes from another process are seen."""
    store = CollaborationStore(tmp_path / "collaboration")
    alice, alpha = _user_and_project(store, tmp_path, "alice")
    bob, beta = _user_and_project(store, tmp_path, "bob")

    assert store.get_project(bob.id, alpha.id) is None
    with pytest.raises(CollaborationPermissionError):
        store.list_members(alpha.id, bob.id)
    with pytest.raises(CollaborationPermissionError):
        store.update_project(alpha.id, bob.id, name="stolen")
    assert store.get_project(alice.id, alpha.id).name == "alice project"

    writer = CollaborationStore(tmp_path / "collaboration")
    writer.update_project(beta.id, bob.id, name="fresh external write")

    assert store.get_project(bob.id, beta.id).name == "fresh external write"


def test_store_rejects_user_record_with_unknown_field(tmp_path: Path) -> None:
    """Persisted records accept only known fields, not arbitrary state."""
    store = CollaborationStore(tmp_path / "collaboration")
    user, _ = store.ensure_local_owner(tmp_path / "agent")
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["users"][user.id]["unexpected"] = "not accepted"
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CollaborationStoreFormatError, match="unsupported shape"):
        CollaborationStore(store_path=store.path).get_user(user.id)


def test_store_scopes_direct_messages_binds_exact_threads_and_isolates_other_groups(
    tmp_path: Path,
) -> None:
    """Only a direct message or its exact explicit thread binding gets a project."""
    store = CollaborationStore(tmp_path / "collaboration")
    user, project = store.ensure_identity_user("telegram", "alice", tmp_path / "agent")

    direct = store.resolve_scope("telegram", "alice", "alice", {}, tmp_path / "agent")
    store.bind_conversation("telegram", "whole-team", project.id, user.id)
    bound_group = store.resolve_scope("telegram", "alice", "whole-team", {}, tmp_path / "agent")
    unbound_group = store.resolve_scope(
        "telegram", "alice", "team-chat", {"thread_id": "root-1"}, tmp_path / "agent"
    )
    store.bind_conversation("telegram", "team-chat", project.id, user.id, thread_id="root-1")
    bound_thread = store.resolve_scope(
        "telegram", "alice", "team-chat", {"thread_id": "root-1"}, tmp_path / "agent"
    )
    sibling_thread = store.resolve_scope(
        "telegram", "alice", "team-chat", {"thread_id": "root-2"}, tmp_path / "agent"
    )

    assert direct.kind is ConversationScopeKind.DIRECT
    assert direct.project_id == project.id
    assert bound_group.kind is ConversationScopeKind.BOUND
    assert bound_group.project_id == project.id
    assert unbound_group.is_isolated
    assert bound_thread.kind is ConversationScopeKind.BOUND
    assert sibling_thread.is_isolated
    assert sibling_thread.workspace_path is None


@pytest.mark.asyncio
async def test_identity_link_codes_are_single_use_and_expire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """A leaked or stale linking proof cannot be replayed to claim an identity."""
    store, repository = local_collaboration_repository
    user, _ = _user_and_project(store, tmp_path, "alice")
    links = IdentityLinkStore(repository)
    monkeypatch.setattr("nanobot.collaboration.links.time.time", lambda: 1_000)

    one_time = await links.create(user.id)
    assert await links.consume(one_time, channel="slack", sender_id="external") == user.id
    assert (await repository.resolve_identity("slack", "external")).id == user.id
    with pytest.raises(IdentityLinkError, match="invalid or expired"):
        await links.consume(one_time, channel="slack", sender_id="attacker")

    expired = await links.create(user.id, ttl_seconds=60)
    monkeypatch.setattr("nanobot.collaboration.links.time.time", lambda: 1_061)
    with pytest.raises(IdentityLinkError, match="invalid or expired"):
        await links.consume(expired, channel="discord", sender_id="late")
    assert await repository.resolve_identity("discord", "late") is None


def _scope_loop(
    workspace: Path, repository: AsyncLocalCollaborationRepository, provider: object
) -> AgentLoop:
    """Build the real scope logic without starting unrelated subagent machinery."""
    with patch("nanobot.agent.loop.SubagentManager") as subagents:
        subagents.return_value.cancel_by_session = AsyncMock(return_value=0)
        return AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=workspace,
            collaboration_repository=repository,
        )


def _provider() -> SimpleNamespace:
    return SimpleNamespace(
        get_default_model=lambda: "test-model",
        generation=SimpleNamespace(max_tokens=4096, temperature=0.1, reasoning_effort=None),
    )


@pytest.mark.asyncio
async def test_agent_loop_keeps_local_websocket_owner_and_gives_proxy_sender_private_scope(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """Proxy identities cannot inherit the local owner's project workspace."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    loop = _scope_loop(workspace, repository, _provider())
    local = await loop._conversation_scope_for_message(
        InboundMessage("websocket", "browser", "local-chat", "hello")
    )
    external = await loop._conversation_scope_for_message(
        InboundMessage("websocket", "proxy:remote-user", "remote-chat", "hello")
    )

    assert local.user_id == (await repository.ensure_local_owner(workspace))[0].id
    assert Path(local.workspace_path or "").resolve() == workspace.resolve()
    assert external.kind is ConversationScopeKind.DIRECT
    assert external.user_id != local.user_id
    assert external.project_id != local.project_id
    assert Path(external.workspace_path or "").is_relative_to(store.root / "workspaces")


@pytest.mark.asyncio
async def test_agent_loop_project_allowlists_filter_skills_and_mcp_servers(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """Project allowlists suppress only excluded explicit skill instructions and MCP tools."""
    workspace = tmp_path / "agent"
    (workspace / "skills" / "allowed").mkdir(parents=True)
    (workspace / "skills" / "blocked").mkdir(parents=True)
    (workspace / "skills" / "allowed" / "SKILL.md").write_text(
        "---\nname: allowed\ndescription: allowed behavior\n---\nALLOWED SKILL", encoding="utf-8"
    )
    (workspace / "skills" / "blocked" / "SKILL.md").write_text(
        "---\nname: blocked\ndescription: blocked behavior\n---\nBLOCKED SKILL", encoding="utf-8"
    )
    store, repository = local_collaboration_repository
    user, project = store.ensure_identity_user("telegram", "alice", workspace)
    store.update_project(
        project.id, user.id, allowed_skills=["allowed"], allowed_mcp_servers=["approved"]
    )
    restricted_scope = await repository.resolve_scope("telegram", "alice", "alice", {}, workspace)
    store.ensure_identity_user("telegram", "bob", workspace)
    unrestricted_scope = await repository.resolve_scope("telegram", "bob", "bob", {}, workspace)
    loop = _scope_loop(workspace, repository, _provider())
    registry = ToolRegistry()
    registry.register(_BuiltinTool())
    definition = SimpleNamespace(name="query", description="query", inputSchema={"type": "object"})
    registry.register(MCPToolWrapper(object(), "approved", definition))
    registry.register(MCPToolWrapper(object(), "blocked", definition))

    filtered = loop._tools_for_conversation_scope(registry, restricted_scope)
    restricted_blocks = await loop._resolve_runtime_context_for_request(
        _scope_request(restricted_scope, text="$allowed $blocked"), filtered
    )
    unrestricted_blocks = await loop._resolve_runtime_context_for_request(
        _scope_request(unrestricted_scope, text="$blocked"), registry
    )

    assert filtered.has("builtin")
    assert filtered.has("mcp_approved_query")
    assert not filtered.has("mcp_blocked_query")
    restricted_content = "\n".join(
        block.content for block in restricted_blocks if block.source == "explicit_skills"
    )
    unrestricted_content = "\n".join(
        block.content for block in unrestricted_blocks if block.source == "explicit_skills"
    )
    assert "ALLOWED SKILL" in restricted_content
    assert "BLOCKED SKILL" not in restricted_content
    assert "BLOCKED SKILL" in unrestricted_content


@pytest.mark.asyncio
async def test_agent_loop_gives_unbound_external_groups_a_private_restricted_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """An unbound group cannot operate in the process-wide workspace."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(
        "nanobot.agent.loop.get_runtime_subdir", lambda name: runtime_root / name
    )
    _store, repository = local_collaboration_repository
    loop = _scope_loop(workspace, repository, _provider())
    message = InboundMessage("telegram", "alice", "team-chat", "hello team")

    scope = await loop._conversation_scope_for_message(message)
    turn_workspace = await loop._effective_workspace_scope(
        channel=message.channel,
        message_metadata=message.metadata,
        session_metadata=None,
        attributes={"collaboration_scope": scope},
    )

    assert scope.is_isolated
    assert turn_workspace.restrict_to_workspace
    assert turn_workspace.project_path != workspace
    assert turn_workspace.project_path.is_relative_to(
        runtime_root / "collaboration" / "workspaces" / "isolated"
    )


@pytest.mark.asyncio
async def test_queued_message_with_other_authorization_is_redispatched_fresh(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """A queued sender cannot receive the active turn's project tools."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    alice, alice_project = store.ensure_identity_user("telegram", "alice", workspace)
    bob, bob_project = store.ensure_identity_user("telegram", "bob", workspace)
    store.update_project(alice_project.id, alice.id, allowed_mcp_servers=["alice"])
    store.update_project(bob_project.id, bob.id, allowed_mcp_servers=["bob"])
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(
        max_tokens=4096, temperature=0.1, reasoning_effort=None
    )
    calls: list[dict[str, object]] = []

    async def chat_with_retry(**kwargs: object) -> LLMResponse:
        calls.append(dict(kwargs))
        return LLMResponse(content="ok", tool_calls=[], usage=None)

    provider.chat_with_retry = AsyncMock(side_effect=chat_with_retry)
    loop = _scope_loop(workspace, repository, provider)
    registry = ToolRegistry()
    definition = SimpleNamespace(name="query", description="query", inputSchema={"type": "object"})
    registry.register(MCPToolWrapper(object(), "alice", definition))
    registry.register(MCPToolWrapper(object(), "bob", definition))
    loop.tools = registry
    active = InboundMessage(
        "telegram", "alice", "shared-chat", "active request", metadata={"direct": True}
    )
    active_scope = await loop._conversation_scope_for_message(active)
    active_tools = loop._tools_for_conversation_scope(registry, active_scope)
    runtime = loop.llm_runtime()
    request = RequestContext(
        channel=active.channel,
        chat_id=active.chat_id,
        sender_id=active.sender_id,
        original_user_text=active.content,
        runtime=runtime,
        metadata=dict(active.metadata),
        attributes={"collaboration_scope": active_scope},
        workspace=Path(active_scope.workspace_path or workspace),
    )
    queued = asyncio.Queue[InboundMessage]()
    await queued.put(
        InboundMessage(
            "telegram", "bob", "shared-chat", "bob request", metadata={"direct": True}
        )
    )
    dispatched: list[InboundMessage] = []
    fresh_tasks: list[asyncio.Task[None]] = []

    async def fresh_dispatch(message: InboundMessage) -> None:
        dispatched.append(message)
        await loop._process_message(message)

    def schedule_and_capture(coro: object) -> None:
        assert asyncio.iscoroutine(coro)
        fresh_tasks.append(asyncio.create_task(coro))

    loop._dispatch = fresh_dispatch  # type: ignore[method-assign]
    loop.schedule_background = schedule_and_capture  # type: ignore[method-assign]

    await loop._run_agent_loop(
        TranscriptInput(history=[{"role": "user", "content": active.content}], current_message=None),
        runtime=runtime,
        request_context=request,
        pending_queue=queued,
        tools=active_tools,
    )

    assert len(fresh_tasks) == 1
    await fresh_tasks[0]

    assert len(calls) == 2
    assert len(dispatched) == 1
    assert dispatched[0].sender_id == "bob"

    def tool_names(call: dict[str, object]) -> set[str]:
        definitions = call["tools"]
        assert isinstance(definitions, list)
        return {item["function"]["name"] for item in definitions}

    assert "mcp_alice_query" in tool_names(calls[0])
    assert "mcp_bob_query" not in tool_names(calls[0])
    assert "mcp_bob_query" in tool_names(calls[1])
    assert "mcp_alice_query" not in tool_names(calls[1])


@pytest.mark.asyncio
async def test_feishu_p2p_binding_uses_source_chat_id_for_later_scope_lookup(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """A Feishu P2P binding survives transport chat-id changes."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    user, project = store.ensure_identity_user("feishu", "open-id", workspace)
    loop = _scope_loop(workspace, repository, _provider())
    binding_scope = await loop._conversation_scope_for_message(
        InboundMessage(
            "feishu",
            "open-id",
            "transport-one",
            "bind this conversation",
            metadata={"direct": True, "source_chat_id": "oc-stable-p2p"},
        )
    )
    request = RequestContext(
        channel="feishu",
        chat_id="transport-one",
        sender_id="open-id",
        metadata={"direct": True, "source_chat_id": "oc-stable-p2p"},
        attributes={"collaboration_scope": binding_scope},
    )

    with request_context(request):
        result = await ProjectsTool(repository).execute("bind_current", project_id=project.id)

    later_scope = await loop._conversation_scope_for_message(
        InboundMessage(
            "feishu",
            "open-id",
            "transport-two",
            "later P2P delivery",
            metadata={"direct": True, "source_chat_id": "oc-stable-p2p"},
        )
    )

    assert json.loads(result)["project_id"] == project.id
    assert later_scope.kind is ConversationScopeKind.BOUND
    assert later_scope.project_id == project.id


@pytest.mark.asyncio
async def test_media_relocation_keeps_caller_file_and_moves_managed_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """Only channel-managed media moves into the owner's private directory."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    managed_root = tmp_path / "managed-media"
    managed_root.mkdir()
    managed = managed_root / "channel-upload.txt"
    managed.write_text("managed attachment", encoding="utf-8")
    caller_dir = tmp_path / "caller-files"
    caller_dir.mkdir()
    caller_file = caller_dir / "local-note.txt"
    caller_file.write_text("caller attachment", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    _store, repository = local_collaboration_repository
    loop = _scope_loop(workspace, repository, _provider())
    monkeypatch.setattr("nanobot.agent.loop.get_media_dir", lambda: managed_root)
    monkeypatch.setattr("nanobot.security.private_media.get_media_dir", lambda: managed_root)
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir", lambda _name: runtime_root / _name
    )
    message = InboundMessage(
        "telegram", "alice", "alice", "review attachments",
        media=[str(managed), str(caller_file)],
    )
    scope = await loop._conversation_scope_for_message(message)
    ctx = TurnContext(
        msg=message,
        session_key=message.session_key,
        turn_id="turn-media",
        runtime=loop.llm_runtime(),
        kind=TurnKind.USER,
        delivery=loop.turn_delivery_factory.create(message, message.session_key),
    )

    await loop._restore_turn(ctx)

    moved_files = list((runtime_root / "users" / scope.user_id / "media").iterdir())
    assert caller_file.exists()
    assert not managed.exists()
    assert len(moved_files) == 1
    assert moved_files[0].read_text(encoding="utf-8") == "managed attachment"
    assert str(caller_file) in ctx.msg.content
    assert str(moved_files[0]) in ctx.msg.content


@pytest.mark.asyncio
async def test_agent_loop_session_keys_are_user_scoped_for_channel_senders(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """External senders get a per-user session namespace; local owners keep legacy keys."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    alice, _ = store.ensure_identity_user("telegram", "alice", workspace)
    loop = _scope_loop(workspace, repository, _provider())

    scoped = await loop._effective_session_key(
        InboundMessage("telegram", "alice", "alice", "hi", metadata={"direct": True})
    )
    local = await loop._effective_session_key(
        InboundMessage("websocket", "browser", "local-chat", "hi")
    )

    assert scoped == f"user:{alice.id}:telegram:alice"
    assert local == "websocket:local-chat"


def test_legacy_multi_tenant_store_collapses_to_projects_and_assignments(tmp_path: Path) -> None:
    """A v8 document keeps users, projects, and claims; bot, org, and vault state is dropped."""
    root = tmp_path / "collaboration"
    root.mkdir()
    legacy = {
        "schemaVersion": 8,
        "localOwnerId": "owner",
        "users": {
            "owner": {
                "id": "owner", "displayName": "Owner", "defaultProjectId": "proj",
                "defaultVaultId": "vault-owner", "defaultPersonaId": None,
                "defaultOrganizationId": "org", "defaultBotId": "bot",
                "createdAtMs": 1, "updatedAtMs": 1,
            },
            "member": {
                "id": "member", "displayName": "Member", "defaultProjectId": "proj",
                "defaultVaultId": "vault-member", "defaultPersonaId": None,
                "defaultOrganizationId": "org", "defaultBotId": None,
                "createdAtMs": 1, "updatedAtMs": 1,
            },
        },
        "identities": {
            "weixin.release\x00sender": {
                "userId": "member", "channel": "weixin.release", "senderId": "sender",
                "createdAtMs": 1,
            },
        },
        "vaults": {
            "vault-owner": {"id": "vault-owner", "ownerUserId": "owner", "name": "Private",
                            "kind": "private", "createdAtMs": 1, "updatedAtMs": 1},
            "vault-member": {"id": "vault-member", "ownerUserId": "member", "name": "Private",
                             "kind": "private", "createdAtMs": 1, "updatedAtMs": 1},
        },
        "personas": {},
        "bots": {
            "bot": {"id": "bot", "organizationId": "org", "ownerUserId": "owner",
                    "name": "Release bot", "avatarUrl": None, "personaId": None,
                    "state": "active", "createdAtMs": 1, "updatedAtMs": 1},
        },
        "botProjectAssignments": {
            "bot\x00proj": {"botId": "bot", "projectId": "proj", "assignedByUserId": "owner",
                            "createdAtMs": 1},
        },
        "botChannelAssignments": {
            "weixin\x00release": {"botId": "bot", "channelType": "weixin",
                                  "instanceId": "release", "claimedByUserId": "member",
                                  "createdAtMs": 5},
            "weixin\x00staged": {"botId": "bot", "channelType": "weixin",
                                 "instanceId": "staged", "claimedByUserId": "member",
                                 "createdAtMs": 6},
        },
        "botProjectChannels": {
            "bot\x00proj\x00weixin\x00release": {
                "botId": "bot", "projectId": "proj", "channelType": "weixin",
                "instanceId": "release", "enabled": True, "updatedAtMs": 5,
            },
        },
        "botCapabilityProfiles": {
            "bot\x00proj": {"botId": "bot", "projectId": "proj", "revision": 2,
                            "settings": {"skills": ["release-notes"]}, "updatedAtMs": 5},
        },
        "channelProvisions": {
            "weixin\x00release": {"channelType": "weixin", "instanceId": "release",
                                  "organizationId": "org", "createdByUserId": "member",
                                  "createdAtMs": 4},
        },
        "pairingChallenges": {},
        "shareGrants": {},
        "personalTasks": {},
        "organizations": {
            "org": {"id": "org", "name": "Studio", "createdByUserId": "owner",
                    "createdAtMs": 1, "updatedAtMs": 1, "isPersonal": False},
        },
        "organizationMemberships": {
            "org\x00owner": {"organizationId": "org", "userId": "owner", "role": "owner",
                             "createdAtMs": 1},
            "org\x00member": {"organizationId": "org", "userId": "member", "role": "member",
                              "createdAtMs": 1},
        },
        "projects": {
            "proj": {"id": "proj", "name": "Roadmap", "workspacePath": str(tmp_path / "roadmap"),
                     "createdByUserId": "owner", "organizationId": "org",
                     "createdAtMs": 1, "updatedAtMs": 1},
        },
        "memberships": {
            "proj\x00owner": {"projectId": "proj", "userId": "owner", "role": "owner",
                              "createdAtMs": 1},
            "proj\x00member": {"projectId": "proj", "userId": "member", "role": "member",
                               "createdAtMs": 1},
        },
        "taskLists": {},
        "tasks": {},
        "conversationBindings": {},
        "extensionProfiles": {},
        "contextSources": {},
    }
    (root / "collaboration.json").write_text(json.dumps(legacy), encoding="utf-8")

    store = CollaborationStore(root)
    users = {user.id: user for user in store.list_users()}
    project = store.get_project("owner", "proj")
    release = store.resolve_channel_assignment("weixin", "release")
    staged = store.resolve_channel_assignment("weixin", "staged")
    persisted = json.loads(store.path.read_text(encoding="utf-8"))

    assert set(users) == {"owner", "member"}
    assert users["member"].default_project_id == "proj"
    assert project is not None and project.allowed_skills == ("release-notes",)
    assert release is not None
    assert (release.project_id, release.assignee_user_id, release.enabled) == ("proj", "member", True)
    assert staged is not None and staged.enabled is False
    assert store.resolve_identity("weixin.release", "sender").id == "member"
    assert [item.instance_id for item in store.list_claimable_channels("member")] == []
    assert persisted["schemaVersion"] == 9
    assert set(persisted) == {
        "schemaVersion", "localOwnerId", "users", "identities", "projects", "memberships",
        "channelProvisions", "channelAssignments", "pairingChallenges", "conversationBindings",
    }
    assert (root / "collaboration.v8.bak.json").exists()
    assert json.loads((root / "collaboration.v8.bak.json").read_text())["schemaVersion"] == 8


def test_pairing_assigns_an_instance_to_a_project_and_binds_the_sender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an exact, fresh, actor-bound Pair Code hands an instance to a project."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    member = store.create_user("member")
    outsider = store.create_user("outsider")
    store.add_member(project.id, owner.id, member.id)

    challenge, code = store.create_pairing_challenge(
        owner.id, project_id=project.id, channel_type="weixin", instance_id="release",
        assignee_user_id=member.id,
    )
    with pytest.raises(CollaborationNotFoundError, match="not found or expired"):
        store.verify_pairing_challenge(
            code, channel_type="weixin", instance_id="other", sender_id="member-sender"
        )
    store.bind_identity("weixin.release", "outsider-sender", outsider.id)
    with pytest.raises(CollaborationConflictError, match="belongs to another user"):
        store.verify_pairing_challenge(
            code, channel_type="weixin", instance_id="release", sender_id="outsider-sender"
        )
    with pytest.raises(CollaborationConflictError, match="not verified"):
        store.consume_pairing_challenge(owner.id, challenge.id)

    verified = store.verify_pairing_challenge(
        code, channel_type="weixin", instance_id="release", sender_id="member-sender"
    )
    assert verified.id == challenge.id
    with pytest.raises(CollaborationConflictError, match="another sender"):
        store.verify_pairing_challenge(
            code, channel_type="weixin", instance_id="release", sender_id="second-sender"
        )
    with pytest.raises(CollaborationPermissionError, match="another user"):
        store.consume_pairing_challenge(member.id, challenge.id)
    consumed = store.consume_pairing_challenge(owner.id, challenge.id)
    assert consumed.consumed_at_ms is not None
    with pytest.raises(CollaborationNotFoundError, match="not found or expired"):
        store.verify_pairing_challenge(
            code, channel_type="weixin", instance_id="release", sender_id="member-sender"
        )

    assignment = store.resolve_channel_assignment("weixin", "release")
    assert assignment is not None
    assert (assignment.project_id, assignment.assignee_user_id, assignment.enabled) == (
        project.id, member.id, True
    )
    assert store.resolve_identity("weixin.release", "member-sender").id == member.id
    assert store.get_user(member.id).default_project_id == project.id

    expired, expired_code = store.create_pairing_challenge(
        owner.id, project_id=project.id, channel_type="weixin", instance_id="expired"
    )
    monkeypatch.setattr("nanobot.collaboration.store._now", lambda: expired.expires_at_ms + 1)
    with pytest.raises(CollaborationNotFoundError, match="not found or expired"):
        store.verify_pairing_challenge(
            expired_code, channel_type="weixin", instance_id="expired", sender_id="owner-sender"
        )


def test_assigned_instance_routes_members_and_denies_everyone_else(tmp_path: Path) -> None:
    """An assigned instance serves exactly its project's members; unassigned gated instances deny."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    other, other_project = _user_and_project(store, tmp_path, "other")
    gated = {CHANNEL_ASSIGNMENT_REQUIRED_METADATA_KEY: True}

    missing = store.resolve_scope("weixin.release", "owner-sender", "owner-sender", gated, tmp_path)
    assert missing.is_isolated and missing.route_denied

    _pair(store, owner.id, project_id=project.id)
    routed = store.resolve_scope("weixin.release", "owner-sender", "owner-sender", gated, tmp_path)
    assert (routed.kind, routed.project_id, routed.route_denied) == (
        ConversationScopeKind.DIRECT, project.id, False
    )
    assert routed.assignment is not None and routed.assignment.instance_id == "release"

    store.bind_identity("weixin.release", "other-sender", other.id)
    stranger = store.resolve_scope("weixin.release", "other-sender", "other-sender", gated, tmp_path)
    assert stranger.is_isolated and stranger.route_denied
    assert stranger.project_id != other_project.id

    store.add_member(project.id, owner.id, other.id)
    admitted = store.resolve_scope("weixin.release", "other-sender", "other-sender", gated, tmp_path)
    assert admitted.project_id == project.id

    store.update_channel_assignment(owner.id, channel_type="weixin", instance_id="release", enabled=False)
    disabled = store.resolve_scope("weixin.release", "owner-sender", "owner-sender", gated, tmp_path)
    assert disabled.is_isolated and disabled.route_denied

    assert store.delete_channel_assignment(owner.id, channel_type="weixin", instance_id="release")
    assert store.resolve_channel_assignment("weixin", "release") is None


def test_members_may_pair_only_instances_they_connected_for_themselves(tmp_path: Path) -> None:
    """Self-service pairing never becomes a way to take over someone else's instance."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    member = store.create_user("member")
    colleague = store.create_user("colleague")
    store.add_member(project.id, owner.id, member.id)
    store.add_member(project.id, owner.id, colleague.id)
    store.record_channel_provision(member.id, channel_type="weixin", instance_id="wechat-a1b2c3")

    assert [
        (item.channel_type, item.instance_id) for item in store.list_claimable_channels(member.id)
    ] == [("weixin", "wechat-a1b2c3")]
    assert store.list_claimable_channels(colleague.id) == []
    with pytest.raises(CollaborationConflictError, match="provisioned by another user"):
        store.record_channel_provision(colleague.id, channel_type="weixin", instance_id="wechat-a1b2c3")
    with pytest.raises(CollaborationPermissionError, match="did not connect"):
        store.create_pairing_challenge(
            colleague.id, project_id=project.id, channel_type="weixin", instance_id="wechat-a1b2c3"
        )
    with pytest.raises(CollaborationPermissionError, match="did not connect"):
        store.create_pairing_challenge(
            member.id, project_id=project.id, channel_type="weixin", instance_id="wechat-a1b2c3",
            assignee_user_id=colleague.id,
        )

    _pair(store, member.id, project_id=project.id, instance_id="wechat-a1b2c3", sender_id="member-sender")
    assert store.list_claimable_channels(member.id) == []
    assignment = store.resolve_channel_assignment("weixin", "wechat-a1b2c3")
    assert assignment is not None and assignment.assignee_user_id == member.id

    # An administrator may reassign the instance to another member; a member may not manage it.
    with pytest.raises(CollaborationPermissionError, match="owner"):
        store.update_channel_assignment(
            member.id, channel_type="weixin", instance_id="wechat-a1b2c3", enabled=False
        )
    # A project owner without administration cannot pair an instance they did not connect.
    with pytest.raises(CollaborationPermissionError, match="administrator"):
        store.create_pairing_challenge(
            colleague.id, project_id=store.create_project(colleague.id, "Mine", tmp_path / "mine").id,
            channel_type="weixin", instance_id="wechat-a1b2c3",
        )
    _pair(
        store, owner.id, project_id=project.id, instance_id="wechat-a1b2c3",
        sender_id="colleague-sender", assignee_user_id=colleague.id,
    )
    reassigned = store.resolve_channel_assignment("weixin", "wechat-a1b2c3")
    assert reassigned is not None and reassigned.assignee_user_id == colleague.id
    with pytest.raises(CollaborationConflictError, match="another project"):
        other_project = store.create_project(owner.id, "Other", tmp_path / "other")
        _pair(store, owner.id, project_id=other_project.id, instance_id="wechat-a1b2c3", sender_id="x")


def test_system_administrator_manages_every_project(tmp_path: Path) -> None:
    """The local owner and flagged administrators reach projects they are not members of."""
    store = CollaborationStore(tmp_path / "collaboration")
    local_owner, _ = store.ensure_local_owner(tmp_path / "agent")
    alice, alice_project = _user_and_project(store, tmp_path, "alice")
    bob = store.create_user("bob")

    assert store.get_project(local_owner.id, alice_project.id) is not None
    assert [project.id for project in store.list_all_projects(local_owner.id)] == [alice_project.id, store.get_user(local_owner.id).default_project_id]
    assert store.add_member(alice_project.id, local_owner.id, bob.id).role is MembershipRole.MEMBER
    with pytest.raises(CollaborationPermissionError, match="administrator"):
        store.list_all_projects(bob.id)

    store.update_user_admin(bob.id, True)
    assert store.get_project(bob.id, alice_project.id) is not None
    assert len(store.list_all_projects(bob.id)) == 2
    assert store.get_user(bob.id).is_admin is True
    store.update_user_admin(bob.id, False)
    with pytest.raises(CollaborationPermissionError, match="administrator"):
        store.list_all_projects(bob.id)


def test_project_deletion_and_member_removal_cascade_assignments(tmp_path: Path) -> None:
    """Assignments, bindings, and challenges never outlive their project or member."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    member = store.create_user("member")
    store.add_member(project.id, owner.id, member.id)
    store.update_user_default_project(member.id, project.id)
    store.bind_identity("telegram", "member", member.id)
    store.bind_conversation("telegram", "team-chat", project.id, member.id)
    _pair(store, owner.id, project_id=project.id, sender_id="member-sender", assignee_user_id=member.id)
    pending, _code = store.create_pairing_challenge(
        owner.id, project_id=project.id, channel_type="weixin", instance_id="staged",
        assignee_user_id=member.id,
    )

    with pytest.raises(CollaborationConflictError, match="retain an owner"):
        store.remove_member(project.id, owner.id, owner.id)
    assert store.remove_member(project.id, owner.id, member.id)
    assert store.resolve_channel_assignment("weixin", "release") is None
    assert store.resolve_binding("telegram", "team-chat") is None
    assert store.get_pairing_challenge(owner.id, pending.id) is None
    assert store.get_user(member.id).default_project_id is None

    assert store.delete_project(project.id, owner.id) is True
    reloaded = CollaborationStore(store_path=store.path)
    assert reloaded.get_project(owner.id, project.id) is None
    assert reloaded.get_user(owner.id).default_project_id is None


def test_pairing_challenge_limit_is_per_user_not_global(tmp_path: Path) -> None:
    """One user cannot exhaust Pair Codes for another user."""
    store = CollaborationStore(tmp_path / "collaboration")
    first, first_project = _user_and_project(store, tmp_path, "first")
    second, second_project = _user_and_project(store, tmp_path, "second")
    store.update_user_admin(first.id, True)
    store.update_user_admin(second.id, True)

    for index in range(32):
        store.create_pairing_challenge(
            first.id, project_id=first_project.id, channel_type="weixin",
            instance_id=f"first-{index}",
        )
    with pytest.raises(CollaborationConflictError, match="too many active pairing challenges"):
        store.create_pairing_challenge(
            first.id, project_id=first_project.id, channel_type="weixin",
            instance_id="first-over-limit",
        )

    challenge, _code = store.create_pairing_challenge(
        second.id, project_id=second_project.id, channel_type="weixin", instance_id="second-first"
    )
    assert challenge.requested_by_user_id == second.id


def test_store_rejects_tampered_assignment_without_membership(tmp_path: Path) -> None:
    """A hand-written assignment cannot route an instance to a user outside the project."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    outsider = store.create_user("outsider")
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["channelAssignments"]["weixin\x00tampered"] = {
        "channelType": "weixin", "instanceId": "tampered", "projectId": project.id,
        "assigneeUserId": outsider.id, "enabled": True, "createdByUserId": owner.id,
        "createdAtMs": 0, "updatedAtMs": 0,
    }
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CollaborationStoreFormatError, match="project membership"):
        CollaborationStore(store_path=store.path).list_users()
