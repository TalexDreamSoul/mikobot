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
from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.collaboration import ProjectsTool
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import MCPToolWrapper
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.collaboration import (
    BUILTIN_PROJECT_NAME,
    COLLABORATION_ASSIGNMENT_METADATA_KEY,
    AsyncLocalCollaborationRepository,
    CollaborationBuiltinProjectError,
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    CollaborationStore,
    CollaborationStoreFormatError,
    ConversationScope,
    ConversationScopeKind,
    MembershipRole,
    Project,
    ProjectAppGrant,
    private_memory_root_for_scope,
)
from nanobot.collaboration.links import IdentityLinkError, IdentityLinkStore
from nanobot.collaboration.models import (
    COLLABORATION_PROJECT_METADATA_KEY,
    COLLABORATION_USER_METADATA_KEY,
    TaskStatus,
)
from nanobot.collaboration.pairing import CHANNEL_ASSIGNMENT_REQUIRED_METADATA_KEY
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.security.private_media import user_private_memory_root


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
async def test_agent_loop_leaves_local_owner_alone_and_gives_proxy_sender_private_scope(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """The host owner's own turns bypass collaboration; proxy identities get a private scope."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    loop = _scope_loop(workspace, repository, _provider())
    local = await loop._conversation_scope_for_message(
        InboundMessage("websocket", "browser", "local-chat", "hello")
    )
    cli = await loop._conversation_scope_for_message(
        InboundMessage("cli", "user", "direct", "hello")
    )
    external = await loop._conversation_scope_for_message(
        InboundMessage("websocket", "proxy:remote-user", "remote-chat", "hello")
    )

    assert local is None
    assert cli is None
    assert external is not None
    assert external.kind is ConversationScopeKind.DIRECT
    assert Path(external.workspace_path or "").is_relative_to(store.root / "workspaces")
    assert await repository.resolve_identity("websocket", "browser") is None


@pytest.mark.asyncio
async def test_agent_loop_without_a_repository_runs_single_user(tmp_path: Path) -> None:
    """The CLI, SDK, and API compositions pass no repository and see no collaboration scope."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    with patch("nanobot.agent.loop.SubagentManager") as subagents:
        subagents.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=MessageBus(), provider=_provider(), workspace=workspace)

    message = InboundMessage("telegram", "alice", "alice", "hello", metadata={"direct": True})
    scope = await loop._conversation_scope_for_message(message)
    key = await loop._effective_session_key(message)
    turn_workspace = await loop._effective_workspace_scope(
        channel=message.channel,
        message_metadata=message.metadata,
        session_metadata=None,
        attributes={},
    )

    assert loop.collaboration is None
    assert scope is None
    assert key == "telegram:alice"
    assert turn_workspace.project_path == workspace.resolve()
    assert not loop.tools.has("projects")


@pytest.mark.asyncio
async def test_agent_loop_project_allowlists_filter_skills_and_mcp_servers(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """Project allowlists suppress only excluded explicit skill instructions and MCP tools."""
    workspace = tmp_path / "agent"
    store, repository = local_collaboration_repository
    user, project = store.ensure_identity_user("telegram", "alice", workspace)
    alice_skills = Path(project.workspace_path) / "skills"
    (alice_skills / "allowed").mkdir(parents=True)
    (alice_skills / "blocked").mkdir(parents=True)
    (alice_skills / "allowed" / "SKILL.md").write_text(
        "---\nname: allowed\ndescription: allowed behavior\n---\nALLOWED SKILL", encoding="utf-8"
    )
    (alice_skills / "blocked" / "SKILL.md").write_text(
        "---\nname: blocked\ndescription: blocked behavior\n---\nBLOCKED SKILL", encoding="utf-8"
    )
    store.update_project(
        project.id, user.id, allowed_skills=["allowed"], allowed_mcp_servers=["approved"]
    )
    restricted_scope = await repository.resolve_scope("telegram", "alice", "alice", {}, workspace)
    bob, bob_project = store.ensure_identity_user("telegram", "bob", workspace)
    bob_skills = Path(bob_project.workspace_path) / "skills" / "blocked"
    bob_skills.mkdir(parents=True)
    (bob_skills / "SKILL.md").write_text(
        "---\nname: blocked\ndescription: blocked behavior\n---\nBLOCKED SKILL", encoding="utf-8"
    )
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
    (workspace / "SOUL.md").write_text("HOST_GROUP_SOUL_SECRET", encoding="utf-8")
    (workspace / "USER.md").write_text("HOST_GROUP_USER_SECRET", encoding="utf-8")
    host_skill = workspace / "skills" / "host-private" / "SKILL.md"
    host_skill.parent.mkdir(parents=True)
    host_skill.write_text(
        "---\nname: host-private\ndescription: Host-only skill\n---\nHOST_GROUP_SKILL_SECRET",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(
        "nanobot.agent.loop.get_runtime_subdir", lambda name: runtime_root / name
    )
    _store, repository = local_collaboration_repository
    provider = _turn_provider()
    loop = _scope_loop(workspace, repository, provider)
    tools = ToolRegistry()
    tools.register(_BuiltinTool())
    definition = SimpleNamespace(name="query", description="query", inputSchema={"type": "object"})
    tools.register(MCPToolWrapper(object(), "host-private", definition))
    loop.tools = tools
    message = InboundMessage("telegram", "alice", "team-chat", "$host-private hello team")

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
    await loop._process_message(message)
    prompt = str(provider.chat_with_retry.await_args.kwargs["messages"][0]["content"])
    assert "HOST_GROUP_SOUL_SECRET" not in prompt
    assert "HOST_GROUP_USER_SECRET" not in prompt
    assert "HOST_GROUP_SKILL_SECRET" not in prompt
    definitions = provider.chat_with_retry.await_args.kwargs["tools"]
    assert all(item["function"]["name"] != "mcp_host-private_query" for item in definitions)


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
async def test_projects_tool_maintains_the_conversations_task_board(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """The assistant works the board of the conversation's project, and no other."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    user, project = store.ensure_identity_user("telegram", "sender", workspace)
    other_user, other_project = _user_and_project(store, tmp_path, "other")
    store.update_user_default_project(user.id, project.id)
    scope = ConversationScope(
        ConversationScopeKind.BOUND, user.id, project.id, user, project, None, None,
        str(project.workspace_path), "-project",
    )
    request = RequestContext(
        channel="telegram", chat_id="chat", sender_id="sender",
        attributes={"collaboration_scope": scope},
    )
    tool = ProjectsTool(repository)

    with request_context(request):
        created = json.loads(await tool.execute("create_task", title="Ship the board"))
        task_id = created["task"]["id"]
        assert created["task"]["project_id"] == project.id
        assert json.loads(await tool.execute("update_task", task_id=task_id, status="done"))[
            "task"]["status"] == "done"
        listed = json.loads(await tool.execute("tasks"))
        assert [(item["id"], item["status"]) for item in listed["tasks"]] == [(task_id, "done")]
        assert json.loads(await tool.execute("delete_task", task_id=task_id)) == {"deleted": True}

        assert json.loads(await tool.execute("tasks"))["tasks"] == []
        assert "status must be one of" in await tool.execute(
            "update_task", task_id="tsk_missing", status="archived"
        )
        assert "requires task_id" in await tool.execute("delete_task")
        assert "requires a title" in await tool.execute("create_task")

    other_scope = ConversationScope(
        ConversationScopeKind.BOUND, other_user.id, other_project.id, other_user, other_project,
        None, None, str(other_project.workspace_path), "-project",
    )
    other_request = RequestContext(
        channel="telegram", chat_id="other", sender_id="other",
        attributes={"collaboration_scope": other_scope},
    )
    kept = store.create_task(other_project.id, other_user.id, "Other project's card")
    foreign = store.create_task(project.id, user.id, "Original project's card")
    with request_context(other_request):
        assert [item["id"] for item in json.loads(await tool.execute("tasks"))["tasks"]] == [kept.id]
        assert await tool.execute("update_task", task_id=foreign.id, status="done") == (
            "Error managing projects: project membership is required"
        )
        assert await tool.execute("delete_task", task_id=foreign.id) == (
            "Error managing projects: only the author or a project manager may delete"
        )
        assert await tool.execute("tasks", project_id=project.id) == (
            "Error managing projects: project membership is required"
        )
        assert await tool.execute("create_task", project_id=project.id, title="Outside") == (
            "Error managing projects: project membership is required"
        )
        assert json.loads(await tool.execute("create_task", title="Mine only"))[
            "task"]["project_id"] == other_project.id

    isolated = ConversationScope(
        ConversationScopeKind.ISOLATED, other_user.id, None, other_user, None, None, None,
        str(workspace), "",
    )
    isolated_request = RequestContext(
        channel="telegram", chat_id="isolated", sender_id="other",
        attributes={"collaboration_scope": isolated},
    )
    with request_context(isolated_request):
        assert "not in a project" in await tool.execute("tasks")


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

    moved_files = [path for path in (runtime_root / "users" / scope.user_id / "media").rglob("*") if path.is_file()]
    assert caller_file.exists()
    assert not managed.exists()
    assert len(moved_files) == 1
    assert moved_files[0].read_text(encoding="utf-8") == "managed attachment"
    assert str(caller_file) not in ctx.msg.content
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
    alice, alice_project = store.ensure_identity_user("telegram", "alice", workspace)
    loop = _scope_loop(workspace, repository, _provider())

    scoped = await loop._effective_session_key(
        InboundMessage("telegram", "alice", "alice", "hi", metadata={"direct": True})
    )
    local = await loop._effective_session_key(
        InboundMessage("websocket", "browser", "local-chat", "hi")
    )

    assert scoped == f"user:{alice.id}:project:{alice_project.id}:telegram:alice"
    assert local == "websocket:local-chat"


@pytest.mark.asyncio
async def test_the_host_owner_keeps_one_conversation_across_their_own_channels(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """The owner reaching in over a chat channel is not a tenant of their own host."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    owner, _project = store.ensure_local_owner(workspace)
    store.bind_identity("weixin", "owner-wechat-id", owner.id)
    loop = _scope_loop(workspace, repository, _provider())
    message = InboundMessage(
        "weixin", "owner-wechat-id", "owner-wechat-id", "what is on my list today?",
        metadata={"direct": True},
    )

    scope = await loop._conversation_scope_for_message(message)
    key = await loop._effective_session_key(message)

    assert scope is not None and scope.is_local_owner is True
    assert scope.project_id is not None
    # Their history stays where every earlier turn wrote it, rather than moving
    # to a per-user namespace that holds nothing.
    assert key == "weixin:owner-wechat-id"

    stranger, stranger_project = store.ensure_identity_user("weixin", "someone-else", workspace)
    stranger_key = await loop._effective_session_key(
        InboundMessage("weixin", "someone-else", "someone-else", "hello", metadata={"direct": True})
    )
    assert stranger_key == f"user:{stranger.id}:project:{stranger_project.id}:weixin:someone-else"


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
    # The owner's default project becomes the built-in one, keeping its name.
    assert project.is_builtin is True
    assert project.name == "Roadmap"
    with pytest.raises(CollaborationBuiltinProjectError):
        store.delete_project("proj", "owner")
    assert persisted["schemaVersion"] == 13
    assert set(persisted) == {
        "schemaVersion", "localOwnerId", "users", "identities", "projects", "memberships",
        "channelProvisions", "channelAssignments", "pairingChallenges", "conversationBindings",
        "tasks",
    }
    assert (root / "collaboration.v8.bak.json").exists()
    assert json.loads((root / "collaboration.v8.bak.json").read_text())["schemaVersion"] == 8


def test_bootstrap_creates_the_builtin_project_and_refuses_to_delete_it(
    tmp_path: Path,
) -> None:
    """Everything unassigned defaults into one named project, so it is permanent."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = store.ensure_local_owner(tmp_path / "workspace")

    assert project.name == BUILTIN_PROJECT_NAME
    assert project.is_builtin is True
    assert store.get_user(owner.id).default_project_id == project.id

    with pytest.raises(CollaborationBuiltinProjectError):
        store.delete_project(project.id, owner.id)
    assert store.get_project(owner.id, project.id) is not None

    # A project the owner creates themselves stays deletable.
    extra = store.create_project(owner.id, "Release train", tmp_path / "release")
    assert extra.is_builtin is False
    assert store.delete_project(extra.id, owner.id) is True


def test_generated_default_project_name_migrates_to_the_builtin_one(
    tmp_path: Path,
) -> None:
    """The pre-rename name is replaced; a project someone renamed is left alone."""
    root = tmp_path / "collaboration"
    root.mkdir()
    (root / "collaboration.json").write_text(json.dumps({
        "schemaVersion": 9,
        "localOwnerId": "owner",
        "users": {
            "owner": {
                "id": "owner", "displayName": "Local owner", "defaultProjectId": "default",
                "isAdmin": True, "createdAtMs": 1, "updatedAtMs": 1,
            },
        },
        "identities": {},
        "projects": {
            "default": {
                "id": "default", "name": "Local project",
                "workspacePath": str(tmp_path / "workspace"),
                "createdByUserId": "owner", "allowedSkills": None,
                "allowedMcpServers": None, "createdAtMs": 1, "updatedAtMs": 1,
            },
        },
        "memberships": {
            "default\x00owner": {
                "projectId": "default", "userId": "owner", "role": "owner", "createdAtMs": 1,
            },
        },
        "channelProvisions": {},
        "channelAssignments": {},
        "pairingChallenges": {},
        "conversationBindings": {},
    }), encoding="utf-8")

    store = CollaborationStore(root)
    project = store.get_project("owner", "default")

    assert project is not None
    assert (project.name, project.is_builtin) == (BUILTIN_PROJECT_NAME, True)
    assert (root / "collaboration.v9.bak.json").exists()
    with pytest.raises(CollaborationBuiltinProjectError):
        store.delete_project("default", "owner")


def test_owner_default_project_is_not_converted_into_the_builtin(tmp_path: Path) -> None:
    """A default the owner points at their own project is never made the instance's home."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, builtin = store.ensure_local_owner(tmp_path / "workspace")
    created = store.create_project(owner.id, "Release train", tmp_path / "release")
    store.update_user_default_project(owner.id, created.id)

    again_owner, again_builtin = store.ensure_local_owner(tmp_path / "workspace")

    assert again_builtin.id == builtin.id
    assert again_owner.default_project_id == created.id
    kept = store.get_project(owner.id, created.id)
    assert (kept.is_builtin, kept.workspace_path) == (False, created.workspace_path)
    home = store.get_project(owner.id, builtin.id)
    assert (home.is_builtin, home.workspace_path) == (True, builtin.workspace_path)


def test_builtin_workspace_follows_the_agent_workspace(tmp_path: Path) -> None:
    """The instance's home tracks the agent workspace, whatever the owner's default is."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, builtin = store.ensure_local_owner(tmp_path / "first")
    assert builtin.workspace_path == str((tmp_path / "first").resolve())

    moved = store.ensure_local_owner(tmp_path / "second")[1]
    assert (moved.id, moved.workspace_path) == (
        builtin.id, str((tmp_path / "second").resolve()))

    created = store.create_project(owner.id, "Release train", tmp_path / "release")
    store.update_user_default_project(owner.id, created.id)
    elsewhere = store.ensure_local_owner(tmp_path / "third")[1]

    assert (elsewhere.id, elsewhere.workspace_path) == (
        builtin.id, str((tmp_path / "third").resolve()))
    assert store.get_project(owner.id, created.id).workspace_path == created.workspace_path


def test_two_flagged_projects_collapse_to_the_oldest_one(tmp_path: Path) -> None:
    """A store left with two homes is repaired: the oldest stays, the other keeps its own."""
    root = tmp_path / "collaboration"
    root.mkdir()
    (root / "collaboration.json").write_text(json.dumps({
        "schemaVersion": 12,
        "localOwnerId": "owner",
        "users": {
            "owner": {
                "id": "owner", "displayName": "Local owner", "defaultProjectId": "converted",
                "isAdmin": False, "createdAtMs": 1, "updatedAtMs": 1,
            },
        },
        "identities": {},
        "projects": {
            "home": {
                "id": "home", "name": BUILTIN_PROJECT_NAME,
                "workspacePath": str(tmp_path / "old-workspace"),
                "createdByUserId": "owner", "allowedSkills": None, "allowedMcpServers": None,
                "createdAtMs": 1, "updatedAtMs": 1, "isBuiltin": True, "appGrants": {},
            },
            "converted": {
                "id": "converted", "name": "Release train",
                "workspacePath": str(tmp_path / "release"),
                "createdByUserId": "owner", "allowedSkills": None, "allowedMcpServers": None,
                "createdAtMs": 2, "updatedAtMs": 2, "isBuiltin": True, "appGrants": {},
            },
        },
        "memberships": {
            "home\x00owner": {
                "projectId": "home", "userId": "owner", "role": "owner", "createdAtMs": 1,
            },
            "converted\x00owner": {
                "projectId": "converted", "userId": "owner", "role": "owner", "createdAtMs": 2,
            },
        },
        "channelProvisions": {},
        "channelAssignments": {},
        "pairingChallenges": {},
        "conversationBindings": {},
        "tasks": {},
    }), encoding="utf-8")

    store = CollaborationStore(root)
    owner, builtin = store.ensure_local_owner(tmp_path / "agent")

    assert builtin.id == "home"
    assert builtin.workspace_path == str((tmp_path / "agent").resolve())
    projects = {project.id: project for project in store.list_all_projects(owner.id)}
    assert [project.id for project in projects.values() if project.is_builtin] == ["home"]
    demoted = projects["converted"]
    assert (demoted.name, demoted.workspace_path) == (
        "Release train", str((tmp_path / "release").resolve()))
    assert store.get_user(owner.id).default_project_id == "converted"


def test_approved_app_grants_survive_ensure_local_owner(tmp_path: Path) -> None:
    """An approved app keeps its pinned revision and capabilities when the home moves."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, builtin = store.ensure_local_owner(tmp_path / "first")
    store.update_project(builtin.id, owner.id, app_grants=[
        ProjectAppGrant("notes", "rev-1", skills=("write_file",), mcp_servers=("files",)),
    ])

    store.ensure_local_owner(tmp_path / "second")

    grant = store.get_project(owner.id, builtin.id).app_grant("notes")
    assert grant is not None
    assert (grant.revision, grant.skills, grant.mcp_servers) == (
        "rev-1", ("write_file",), ("files",))


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
    # Every project, in no guaranteed order: projects created in the same
    # millisecond only differ by id.
    assert {project.id for project in store.list_all_projects(local_owner.id)} == {
        alice_project.id,
        store.get_user(local_owner.id).default_project_id,
    }
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


def test_project_tasks_belong_to_the_project_not_their_author(tmp_path: Path) -> None:
    """Every member shares one board, and a task outlives the member who wrote it."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    member = store.create_user("member")
    store.add_member(project.id, owner.id, member.id)
    outsider, _ = _user_and_project(store, tmp_path, "outsider")

    first = store.create_task(project.id, member.id, "Draft the launch note")
    second = store.create_task(
        project.id, member.id, "Wire the banner",
        detail="line one\nline two", status=TaskStatus.DOING,
    )
    assert [task.id for task in store.list_tasks(project.id, owner.id)] == [first.id, second.id]
    assert store.list_tasks(project.id, member.id)[1].detail == "line one\nline two"

    moved = store.update_task(second.id, owner.id, status=TaskStatus.DONE)
    assert (moved.status, moved.created_by_user_id) == (TaskStatus.DONE, member.id)
    assert store.list_tasks(project.id, member.id)[1].status is TaskStatus.DONE

    store.remove_member(project.id, owner.id, member.id)
    assert [task.title for task in store.list_tasks(project.id, owner.id)] == [
        "Draft the launch note", "Wire the banner",
    ]
    with pytest.raises(CollaborationPermissionError, match="membership is required"):
        store.list_tasks(project.id, outsider.id)
    with pytest.raises(CollaborationPermissionError, match="membership is required"):
        store.create_task(project.id, outsider.id, "Not mine")


def test_only_a_task_author_or_manager_may_discard_it(tmp_path: Path) -> None:
    """Editing the shared board is open to members; discarding a card is not."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    author = store.create_user("author")
    peer = store.create_user("peer")
    store.add_member(project.id, owner.id, author.id)
    store.add_member(project.id, owner.id, peer.id)

    mine = store.create_task(project.id, author.id, "Author's card")
    theirs = store.create_task(project.id, peer.id, "Peer's card")
    store.update_task(mine.id, peer.id, title="Peer edited the author's card")

    with pytest.raises(CollaborationPermissionError, match="author or a project manager"):
        store.delete_task(mine.id, peer.id)
    assert store.delete_task(mine.id, author.id) is True
    assert store.delete_task(theirs.id, owner.id) is True
    assert store.list_tasks(project.id, owner.id) == []

    with pytest.raises(CollaborationNotFoundError, match="task not found"):
        store.update_task(mine.id, owner.id, status=TaskStatus.DONE)
    with pytest.raises(ValueError, match="at least one task field"):
        store.update_task(theirs.id, owner.id)
    with pytest.raises(CollaborationStoreFormatError):
        store.create_task(project.id, author.id, "bad\ntitle")


def test_project_deletion_removes_its_tasks(tmp_path: Path) -> None:
    """A board never outlives the project that owns it."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    task = store.create_task(project.id, owner.id, "Ship the release")
    other, other_project = _user_and_project(store, tmp_path, "other")
    kept = store.create_task(other_project.id, other.id, "Unrelated card")

    assert store.delete_project(project.id, owner.id) is True

    reloaded = CollaborationStore(store_path=store.path)
    with pytest.raises(CollaborationPermissionError, match="membership is required"):
        reloaded.list_tasks(project.id, owner.id)
    assert [item.id for item in reloaded.list_tasks(other_project.id, other.id)] == [kept.id]
    assert task.id not in json.loads(store.path.read_text(encoding="utf-8"))["tasks"]


def test_task_board_migrates_from_the_previous_store_schema(tmp_path: Path) -> None:
    """A store written before tasks existed gains an empty board instead of failing."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.create_task(project.id, owner.id, "Keep me")
    document = json.loads(store.path.read_text(encoding="utf-8"))
    document["schemaVersion"] = 10
    document.pop("tasks")
    for record in document["projects"].values():
        record.pop("appGrants")
    store.path.write_text(json.dumps(document), encoding="utf-8")

    migrated = CollaborationStore(store_path=store.path)
    assert migrated.list_tasks(project.id, owner.id) == []
    assert migrated.get_project(owner.id, project.id).app_grants == ()
    assert json.loads(store.path.read_text(encoding="utf-8"))["schemaVersion"] == 13


def test_project_description_migrates_empty_and_round_trips(tmp_path: Path) -> None:
    """A v12 store gains an empty description, and a saved introduction survives later updates."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_project(project.id, owner.id, description="Ship the release train on Friday.")
    document = json.loads(store.path.read_text(encoding="utf-8"))
    document["schemaVersion"] = 12
    for record in document["projects"].values():
        record.pop("description")
    store.path.write_text(json.dumps(document), encoding="utf-8")

    migrated = CollaborationStore(store_path=store.path)

    assert migrated.get_project(owner.id, project.id).description == ""
    assert json.loads(store.path.read_text(encoding="utf-8"))["schemaVersion"] == 13

    migrated.update_project(project.id, owner.id, description="Shared roadmap context.")
    renamed = migrated.update_project(project.id, owner.id, name="Renamed train")

    assert renamed.description == "Shared roadmap context."
    reloaded = CollaborationStore(store_path=store.path)
    assert reloaded.get_project(owner.id, project.id).description == "Shared roadmap context."


def test_project_description_must_stay_bounded(tmp_path: Path) -> None:
    """An oversized introduction is refused instead of being persisted into every prompt."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")

    with pytest.raises(CollaborationStoreFormatError):
        store.update_project(project.id, owner.id, description="x" * 20_000)

    assert store.get_project(owner.id, project.id).description == ""


def test_store_rejects_a_task_without_a_live_project_or_author(tmp_path: Path) -> None:
    """A hand-written card cannot reference a project or author that does not exist."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.create_task(project.id, owner.id, "Real card")
    document = json.loads(store.path.read_text(encoding="utf-8"))
    task_id = next(iter(document["tasks"]))
    document["tasks"][task_id]["projectId"] = "col_missing"
    store.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CollaborationStoreFormatError, match="invalid task references"):
        CollaborationStore(store_path=store.path).list_users()

    document["tasks"][task_id]["projectId"] = project.id
    document["tasks"][task_id]["createdByUserId"] = "usr_missing"
    store.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CollaborationStoreFormatError, match="invalid task references"):
        CollaborationStore(store_path=store.path).list_users()


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

def test_assignment_moves_between_projects_only_for_a_manager_of_both(tmp_path: Path) -> None:
    """Reassigning an instance needs authority over the project it leaves and the one it joins."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    member, member_project = _user_and_project(store, tmp_path, "member")
    store.add_member(project.id, owner.id, member.id)
    _pair(store, owner.id, project_id=project.id, assignee_user_id=member.id)

    with pytest.raises(CollaborationPermissionError, match="owner"):
        store.update_channel_assignment(
            member.id, channel_type="weixin", instance_id="release",
            project_id=member_project.id,
        )

    moved = store.update_channel_assignment(
        owner.id, channel_type="weixin", instance_id="release", project_id=member_project.id,
    )

    assert moved.project_id == member_project.id
    assert moved.assignee_user_id == member.id
    assert moved.enabled is True
    # The assignee follows the instance, exactly as consuming a Pair Code would.
    assert any(
        item.user_id == member.id for item in store.list_members(member_project.id, member.id)
    )
    with pytest.raises(ValueError, match="at least one"):
        store.update_channel_assignment(owner.id, channel_type="weixin", instance_id="release")


def test_manageable_projects_name_what_the_ui_may_offer(tmp_path: Path) -> None:
    """An administrator manages every project; anyone else manages the ones they own."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    member, member_project = _user_and_project(store, tmp_path, "member")
    store.add_member(project.id, owner.id, member.id)

    assert set(store.manageable_project_ids(owner.id)) == {project.id, member_project.id}
    assert store.manageable_project_ids(member.id) == [member_project.id]



@pytest.mark.asyncio
async def test_agent_loop_automation_turns_never_provision_an_identity(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """Cron and heartbeat never provision an identity or become host when their scope is absent."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    loop = _scope_loop(workspace, repository, _provider())
    before = {user.id for user in store.list_users()}

    cron = InboundMessage(
        "weixin",
        "cron",
        "wx-open-id",
        "run the scheduled job",
        session_key_override="weixin:wx-open-id",
        source="runtime",
    )
    heartbeat = InboundMessage(
        "weixin",
        "runtime",
        "wx-open-id",
        "heartbeat check",
        session_key_override="weixin:wx-open-id",
        source="runtime",
    )
    assert await loop._conversation_scope_for_message(cron) is None
    assert await loop._conversation_scope_for_message(heartbeat) is None
    assert await loop._effective_session_key(cron) == "weixin:wx-open-id"
    assert await loop._effective_session_key(heartbeat) == "weixin:wx-open-id"
    # No user, project, or identity was invented for either runtime sender.
    assert {user.id for user in store.list_users()} == before
    assert await repository.resolve_identity("weixin", "cron") is None
    assert await repository.resolve_identity("weixin", "runtime") is None

    # A session that records a project keeps the automation inside it.
    session = loop.sessions.get_or_create("weixin:wx-open-id")
    session.metadata[COLLABORATION_USER_METADATA_KEY] = owner.id
    session.metadata[COLLABORATION_PROJECT_METADATA_KEY] = project.id
    session.metadata[COLLABORATION_ASSIGNMENT_METADATA_KEY] = False
    session.metadata["collaboration_channel"] = "weixin"
    session.metadata["collaboration_chat_id"] = "wx-open-id"
    loop.sessions.save(session)
    scoped = await loop._conversation_scope_for_message(cron)

    assert scoped is not None
    assert scoped.project_id == project.id
    assert scoped.workspace_path == project.workspace_path

    # Losing that membership refuses the turn rather than silently widening it.
    store.delete_project(project.id, owner.id)
    with pytest.raises(CollaborationPermissionError):
        await loop._conversation_scope_for_message(cron)


@pytest.mark.asyncio
async def test_external_cron_label_with_session_override_stays_external(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """A chat user named cron cannot claim runtime or host authority through an override."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    _store, repository = local_collaboration_repository
    loop = _scope_loop(workspace, repository, _provider())
    message = InboundMessage(
        "weixin",
        "cron",
        "external-chat",
        "human message",
        metadata={"direct": True},
        session_key_override="weixin:external-chat:thread",
        source="external",
    )

    scope = await loop._conversation_scope_for_message(message)
    key = await loop._effective_session_key(message)

    assert scope is not None
    assert not scope.is_local_owner
    assert scope.workspace_path != str(workspace)
    assert key.startswith(f"user:{scope.user_id}:project:{scope.project_id}:")


def test_session_scope_revalidates_required_assignment_and_exact_binding(tmp_path: Path) -> None:
    """A persisted member turn loses access when its instance or exact binding changes."""
    store = CollaborationStore(tmp_path / "collaboration")
    owner, project_a = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    project_b = store.create_project(owner.id, "second project", tmp_path / "second-project")
    member, _member_project = _user_and_project(store, tmp_path, "member")
    store.add_member(project_a.id, owner.id, member.id)
    _pair(store, owner.id, project_id=project_a.id, assignee_user_id=member.id)
    binding = store.bind_conversation("weixin.release", "member-sender", project_a.id, member.id)

    def resolve(project_id: str, *, binding_id: str | None = binding.id):
        return store.resolve_session_scope(
            member.id,
            project_id,
            channel="weixin.release",
            chat_id="member-sender",
            assignment_required=True,
            binding_id=binding_id,
        )

    resolved = resolve(project_a.id)
    assert resolved is not None
    assert resolved.assignment is not None
    assert resolved.assignment.instance_id == "release"

    assert store.delete_channel_assignment(owner.id, channel_type="weixin", instance_id="release")
    assert resolve(project_a.id) is None

    _pair(store, owner.id, project_id=project_a.id, assignee_user_id=member.id)
    store.update_channel_assignment(owner.id, channel_type="weixin", instance_id="release", enabled=False)
    assert resolve(project_a.id) is None
    store.update_channel_assignment(owner.id, channel_type="weixin", instance_id="release", enabled=True)
    assert resolve(project_a.id) is not None

    store.update_channel_assignment(
        owner.id,
        channel_type="weixin",
        instance_id="release",
        project_id=project_b.id,
    )
    assert resolve(project_a.id) is None
    assert resolve(project_b.id) is None

    assert store.unbind_conversation("weixin.release", "member-sender", owner.id)
    replacement = store.bind_conversation("weixin.release", "member-sender", project_b.id, member.id)
    assert replacement.id != binding.id
    assert resolve(project_b.id) is None
    rebound = resolve(project_b.id, binding_id=replacement.id)
    assert rebound is not None
    assert rebound.binding == replacement

    assert store.remove_member(project_b.id, owner.id, member.id)
    assert resolve(project_b.id, binding_id=replacement.id) is None


def test_session_scope_without_assignment_preserves_personal_webui_authorization(tmp_path: Path) -> None:
    """Personal WebUI provenance remains valid without a channel assignment."""
    store = CollaborationStore(tmp_path / "collaboration")
    user, project = _user_and_project(store, tmp_path, "member")

    scope = store.resolve_session_scope(
        user.id,
        project.id,
        channel="websocket",
        chat_id="personal-chat",
        assignment_required=False,
    )

    assert scope is not None
    assert scope.project_id == project.id


def _turn_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(
        max_tokens=4096,
        temperature=0.1,
        reasoning_effort=None,
    )
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="safe response", tool_calls=[], usage=None)
    )
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", ["proxy:member", "oidc:member"])
async def test_runtime_turns_revalidate_persisted_member_provenance(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
    principal: str,
) -> None:
    """Proxy and OIDC members keep their project for internal turns or fail closed after revocation."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    owner, project = _user_and_project(store, tmp_path, "owner")
    member, _personal_project = store.ensure_identity_user("websocket", principal, workspace)
    store.add_member(project.id, owner.id, member.id)
    store.update_user_default_project(member.id, project.id)
    provider = _turn_provider()
    loop = _scope_loop(workspace, repository, provider)
    human = InboundMessage("websocket", principal, "member-chat", "human request")

    await loop._process_message(human)
    key = await loop._effective_session_key(human)
    session = loop.sessions.get_or_create(key)
    assert session.metadata[COLLABORATION_USER_METADATA_KEY] == member.id
    assert session.metadata[COLLABORATION_PROJECT_METADATA_KEY] == project.id

    for sender_id in ("cron", "system:continuation", "system:recovery"):
        internal = InboundMessage(
            "websocket",
            sender_id,
            "member-chat",
            "continue work",
            session_key_override=key,
            source="runtime",
        )
        scope = await loop._conversation_scope_for_message(internal)
        assert scope is not None
        assert (scope.user_id, scope.project_id) == (member.id, project.id)

    session.metadata.pop(COLLABORATION_PROJECT_METADATA_KEY)
    loop.sessions.save(session)
    with pytest.raises(CollaborationPermissionError):
        await loop._conversation_scope_for_message(
            InboundMessage(
                "websocket",
                "cron",
                "member-chat",
                "missing project provenance",
                session_key_override=key,
                source="runtime",
            )
        )

    session.metadata[COLLABORATION_PROJECT_METADATA_KEY] = project.id
    loop.sessions.save(session)
    assert store.remove_member(project.id, owner.id, member.id)
    for sender_id in ("cron", "system:continuation", "system:recovery"):
        with pytest.raises(CollaborationPermissionError):
            await loop._conversation_scope_for_message(
                InboundMessage(
                    "websocket",
                    sender_id,
                    "member-chat",
                    "revoked continuation",
                    session_key_override=key,
                    source="runtime",
                )
            )


@pytest.mark.asyncio
async def test_assigned_human_turn_records_required_assignment_for_later_automation(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """A cron turn cannot reuse an assigned member session after that assignment is deleted."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    owner, project = _user_and_project(store, tmp_path, "owner")
    store.update_user_admin(owner.id, True)
    member, _member_project = _user_and_project(store, tmp_path, "member")
    store.add_member(project.id, owner.id, member.id)
    _pair(
        store,
        owner.id,
        project_id=project.id,
        assignee_user_id=member.id,
        sender_id="member-sender",
    )
    loop = _scope_loop(workspace, repository, _turn_provider())
    human = InboundMessage(
        "weixin.release",
        "member-sender",
        "member-sender",
        "assigned request",
        metadata={"direct": True},
    )

    key = await loop._effective_session_key(human)
    human.session_key_override = key
    await loop._process_message(human)
    session = loop.sessions.get_or_create(key)
    assert session.metadata[COLLABORATION_ASSIGNMENT_METADATA_KEY] is True

    assert store.delete_channel_assignment(owner.id, channel_type="weixin", instance_id="release")
    with pytest.raises(CollaborationPermissionError):
        await loop._conversation_scope_for_message(
            InboundMessage(
                "weixin.release",
                "cron",
                "member-sender",
                "scheduled work",
                session_key_override=key,
                source="runtime",
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("unified_session", [False, True])
async def test_member_history_stays_private_when_their_default_project_changes(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
    unified_session: bool,
) -> None:
    """Changing a member's project selects a new durable history, never the old project's turn."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    owner, project_a = _user_and_project(store, tmp_path, "owner")
    project_b = store.create_project(owner.id, "second project", tmp_path / "second-project")
    member, _personal_project = store.ensure_identity_user("weixin", "member", workspace)
    store.add_member(project_a.id, owner.id, member.id)
    store.add_member(project_b.id, owner.id, member.id)
    store.update_user_default_project(member.id, project_a.id)
    loop = _scope_loop(workspace, repository, _turn_provider())
    loop._unified_session = unified_session
    first = InboundMessage("weixin", "member", "member-chat", "A_SECRET_MARKER", metadata={"direct": True})
    key_a = await loop._effective_session_key(first)
    first.session_key_override = key_a
    await loop._process_message(first)
    expected_a = (
        f"unified:{member.id}:project:{project_a.id}"
        if unified_session
        else f"user:{member.id}:project:{project_a.id}:weixin:member-chat"
    )
    assert key_a == expected_a
    assert any(message["content"] == "A_SECRET_MARKER" for message in loop.sessions.get_or_create(key_a).messages)
    store.update_user_default_project(member.id, project_b.id)

    second = InboundMessage("weixin", "member", "member-chat", "B_SECRET_MARKER", metadata={"direct": True})
    key_b = await loop._effective_session_key(second)
    second.session_key_override = key_b
    await loop._process_message(second)
    expected_b = (
        f"unified:{member.id}:project:{project_b.id}"
        if unified_session
        else f"user:{member.id}:project:{project_b.id}:weixin:member-chat"
    )

    assert key_b == expected_b
    assert key_b != key_a
    history_b = loop.sessions.get_or_create(key_b).messages
    assert any(message["content"] == "B_SECRET_MARKER" for message in history_b)
    assert all(message["content"] != "A_SECRET_MARKER" for message in history_b)


@pytest.mark.asyncio
async def test_member_prompt_archive_and_dream_keep_same_project_members_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """Project instructions are shared, but each member owns an isolated profile, journal, archive, and Dream."""
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: runtime_root / name,
    )
    workspace = tmp_path / "host-workspace"
    project_workspace = tmp_path / "project-workspace"
    workspace.mkdir()
    project_workspace.mkdir()
    (workspace / "SOUL.md").write_text("HOST_SOUL_SECRET", encoding="utf-8")
    (workspace / "USER.md").write_text("HOST_USER_SECRET", encoding="utf-8")
    MemoryStore(workspace).write_memory("HOST_MEMORY_SECRET")
    (workspace / "AGENTS.md").write_text("HOST_ARCHIVE_AGENTS_SECRET", encoding="utf-8")
    (project_workspace / "AGENTS.md").write_text("PROJECT_INSTRUCTIONS_MARKER", encoding="utf-8")
    shared_skill = project_workspace / "skills" / "shared" / "SKILL.md"
    shared_skill.parent.mkdir(parents=True)
    shared_skill.write_text(
        "---\nname: shared\ndescription: Shared project behavior\nalways: true\n---\nSHARED_SKILL_MARKER",
        encoding="utf-8",
    )
    store, repository = local_collaboration_repository
    owner = store.create_user("owner")
    project = store.create_project(owner.id, "project", project_workspace)
    alice, _alice_personal = store.ensure_identity_user("weixin", "alice-sender", workspace)
    bob, _bob_personal = store.ensure_identity_user("weixin", "bob-sender", workspace)
    store.add_member(project.id, owner.id, alice.id)
    store.add_member(project.id, owner.id, bob.id)
    store.update_user_default_project(alice.id, project.id)
    store.update_user_default_project(bob.id, project.id)
    alice_key = f"user:{alice.id}:project:{project.id}:weixin:member-chat"
    alice_memory_root = user_private_memory_root(alice.id, project_id=project.id)
    bob_memory_root = user_private_memory_root(bob.id, project_id=project.id)
    MemoryStore(workspace).append_history("HOST_HISTORY_SECRET", session_key=alice_key)
    alice_memory = MemoryStore(alice_memory_root)
    alice_memory.write_memory("ALICE_MEMORY_MARKER")
    alice_memory.append_history("ALICE_HISTORY_MARKER", session_key=alice_key)
    bob_memory = MemoryStore(bob_memory_root)
    bob_memory.write_memory("BOB_MEMORY_SECRET")
    bob_memory.append_history(
        "BOB_HISTORY_SECRET",
        session_key=f"user:{bob.id}:project:{project.id}:weixin:member-chat",
    )
    provider = _turn_provider()
    loop = _scope_loop(workspace, repository, provider)
    archive_definition = SimpleNamespace(name="query", description="query", inputSchema={"type": "object"})
    loop.tools.register(MCPToolWrapper(object(), "host-archive", archive_definition))

    human = InboundMessage(
        "weixin",
        "alice-sender",
        "member-chat",
        "ALICE_REQUEST_MARKER",
        metadata={"direct": True},
    )
    alice_key = await loop._effective_session_key(human)
    human.session_key_override = alice_key
    await loop._process_message(human)
    session = loop.sessions.get_or_create(alice_key)
    private_memory = await loop.memory_store_for_session(session)
    assert private_memory is not None
    assert private_memory.workspace == alice_memory_root
    archived = await loop.consolidator.archive_session(
        session,
        archive_end=1,
        runtime=loop.llm_runtime(),
    )
    assert archived == "safe response"
    archive_call = provider.chat_with_retry.await_args_list[-1].kwargs
    archive_prompt = str(archive_call["messages"][0]["content"])
    assert "HOST_ARCHIVE_AGENTS_SECRET" not in archive_prompt
    assert all(
        "host-archive" not in item["function"]["name"]
        for item in archive_call["tools"]
    )
    private_memory.raw_archive(
        [{"role": "assistant", "content": "ALICE_ARCHIVE_SECRET"}],
        session_key=session.key,
    )
    dream = private_memory.build_dream_prompt()
    assert dream is not None
    dream_prompt, _cursor = dream
    assert "ALICE_ARCHIVE_SECRET" in dream_prompt
    assert "HOST_HISTORY_SECRET" not in dream_prompt
    assert "BOB_HISTORY_SECRET" not in dream_prompt
    assert "ALICE_ARCHIVE_SECRET" not in bob_memory.history_file.read_text(encoding="utf-8")

    messages = provider.chat_with_retry.await_args.kwargs["messages"]
    prompt = str(messages[0]["content"])
    assert "PROJECT_INSTRUCTIONS_MARKER" in prompt
    assert "ALICE_MEMORY_MARKER" in prompt
    assert "SHARED_SKILL_MARKER" in prompt
    assert "ALICE_HISTORY_MARKER" in prompt
    assert "HOST_SOUL_SECRET" not in prompt
    assert "HOST_USER_SECRET" not in prompt
    assert "HOST_MEMORY_SECRET" not in prompt
    assert "HOST_HISTORY_SECRET" not in prompt
    assert "BOB_MEMORY_SECRET" not in prompt
    assert "BOB_HISTORY_SECRET" not in prompt

    history_before_revoke = private_memory.history_file.read_text(encoding="utf-8")
    provider_calls_before_revoke = provider.chat_with_retry.await_count
    assert store.remove_member(project.id, owner.id, alice.id)
    with pytest.raises(CollaborationPermissionError):
        await loop.consolidator.archive_session(
            session,
            archive_end=1,
            runtime=loop.llm_runtime(),
        )
    assert provider.chat_with_retry.await_count == provider_calls_before_revoke
    assert private_memory.history_file.read_text(encoding="utf-8") == history_before_revoke
    with pytest.raises(CollaborationPermissionError):
        await loop.memory_store_for_session(session)


@pytest.mark.asyncio
async def test_member_revocation_before_tool_execution_prevents_side_effect(
    tmp_path: Path,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """An already-admitted member turn reauthorizes before an LLM-requested tool can mutate state."""
    workspace = tmp_path / "agent"
    workspace.mkdir()
    store, repository = local_collaboration_repository
    owner, project = _user_and_project(store, tmp_path, "owner")
    member, _personal_project = store.ensure_identity_user("weixin", "member-sender", workspace)
    store.add_member(project.id, owner.id, member.id)
    store.update_user_default_project(member.id, project.id)
    mutations: list[str] = []

    class MutatingTool(Tool):
        @property
        def name(self) -> str:
            return "mutate_marker"

        @property
        def description(self) -> str:
            return "Mutate a test marker."

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> str:
            mutations.append("executed")
            return "mutation completed"

    provider = _turn_provider()
    calls = 0

    async def chat_with_retry(**_kwargs: object) -> LLMResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            assert store.remove_member(project.id, owner.id, member.id)
            return LLMResponse(
                content="attempting mutation",
                tool_calls=[ToolCallRequest(id="mutation-1", name="mutate_marker", arguments={})],
                usage=None,
            )
        return LLMResponse(content="access revoked", tool_calls=[], usage=None)

    provider.chat_with_retry = AsyncMock(side_effect=chat_with_retry)
    loop = _scope_loop(workspace, repository, provider)
    tools = ToolRegistry()
    tools.register(MutatingTool())
    loop.tools = tools

    await loop._process_message(
        InboundMessage(
            "weixin",
            "member-sender",
            "member-chat",
            "change the marker",
            metadata={"direct": True},
        )
    )

    assert mutations == []
    assert calls >= 1


# ---------------------------------------------------------------------------
# Private memory boundary for project-scoped turns
#
# The read path must resolve the same private per-user root the consolidation
# path does, for every conversation carrying project provenance.  The pre-fix
# read path inlined the rule and exempted the host owner, so an owner's channel
# turn inside a project read the host's own profile and long-term memory.  The
# rule now lives in one shared ``private_memory_root_for_scope`` that the turn
# path and the subagent path both call, so the two cannot drift apart again; it
# keeps the host store for a turn with no collaboration scope and for the
# instance's built-in home project, whose workspace *is* the host's workspace.
# ---------------------------------------------------------------------------


def _unit_loop(workspace: Path) -> AgentLoop:
    """Build a loop without collaboration wiring, for the consolidation-store comparison."""
    return AgentLoop(bus=MessageBus(), provider=_provider(), workspace=workspace)


def _scope_project(
    *, project_id: str, project_path: Path, is_builtin: bool = False
) -> Project:
    """Build the project entity a resolved scope carries, as the store builds it."""
    return Project(
        id=project_id,
        name="shared project",
        workspace_path=str(project_path),
        created_by_user_id="member-user",
        created_at_ms=0,
        updated_at_ms=0,
        is_builtin=is_builtin,
    )


def _scoped_scope(
    *,
    user_id: str | None,
    project_id: str | None,
    project_path: Path,
    is_local_owner: bool,
    project: Project | None,
) -> ConversationScope:
    return ConversationScope(
        ConversationScopeKind.ISOLATED if project_id is None else ConversationScopeKind.DIRECT,
        user_id,
        project_id,
        None,
        project,
        None,
        None,
        str(project_path),
        "-private",
        is_local_owner=is_local_owner,
    )


@pytest.mark.parametrize("is_local_owner", [False, True])
def test_project_scope_reads_the_private_member_root_not_the_shared_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_local_owner: bool,
) -> None:
    """A project-scoped turn reads its private per-user root, for members and the host owner alike.

    The scope carries the non-built-in project entity the store resolved, which is exactly what
    moves even the host owner out of the host store.  Fails when the pre-fix ``is_local_owner``
    exemption is reintroduced: it returned ``None`` (the host's own store) for the owner, so the
    equality below compares a private root against ``None``.  It also fails if the read path starts
    pointing at the shared project directory.
    """
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: tmp_path / "runtime" / name,
    )
    project_path = tmp_path / "project"
    project_path.mkdir()
    scope = _scoped_scope(
        user_id="member-user",
        project_id="member-project",
        project_path=project_path,
        is_local_owner=is_local_owner,
        project=_scope_project(project_id="member-project", project_path=project_path),
    )

    root = private_memory_root_for_scope(scope, project_path=project_path)

    assert root is not None
    assert root == user_private_memory_root("member-user", project_id="member-project")


@pytest.mark.parametrize(
    ("is_local_owner", "expects_private_root"),
    [
        pytest.param(True, False, id="owner-keeps-the-host-store"),
        pytest.param(False, True, id="member-reads-the-private-root"),
    ],
)
def test_builtin_project_keeps_the_host_store_for_its_owner_and_still_isolates_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_local_owner: bool,
    expects_private_root: bool,
) -> None:
    """The instance's home project leaves its owner on the host store and still isolates members.

    The built-in project's workspace *is* the host's own workspace, so the owner's turn there keeps
    the host's SOUL.md, USER.md, MEMORY.md, and history (``None``) instead of being moved into an
    empty per-user root.  A member classified into that same project is not the host and must never
    read those files, so they still get their own private root.

    Fails when the built-in exemption is widened to every scope in that project
    (``if scope.is_local_owner or scope.project.is_builtin``): the member row would return ``None``
    and read the host owner's profile and long-term memory.  Fails when the ``is_builtin`` operand
    is dropped (``if scope.is_local_owner and scope.project is None``): the owner row would return a
    private root and take their own memory away.
    """
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: tmp_path / "runtime" / name,
    )
    project_path = tmp_path / "project"
    project_path.mkdir()
    scope = _scoped_scope(
        user_id="member-user",
        project_id="member-project",
        project_path=project_path,
        is_local_owner=is_local_owner,
        project=_scope_project(
            project_id="member-project", project_path=project_path, is_builtin=True
        ),
    )

    root = private_memory_root_for_scope(scope, project_path=project_path)

    expected = (
        user_private_memory_root("member-user", project_id="member-project")
        if expects_private_root
        else None
    )
    assert root == expected


def test_member_scope_with_provenance_but_no_project_entity_reads_its_private_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provenance alone moves a member out of the shared project directory.

    A member scope that carries both ids is a project turn even when it carries no project entity,
    so it reads its own private root rather than ``project_path``.  Fails if the project-directory
    fallback is taken before the ids branch: the member's profile and memory would then sit in the
    shared project directory every member of that project can read.
    """
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: tmp_path / "runtime" / name,
    )
    project_path = tmp_path / "project"
    project_path.mkdir()
    scope = _scoped_scope(
        user_id="member-user",
        project_id="member-project",
        project_path=project_path,
        is_local_owner=False,
        project=None,
    )

    assert private_memory_root_for_scope(scope, project_path=project_path) == (
        user_private_memory_root("member-user", project_id="member-project")
    )


def test_owner_scope_without_a_resolved_project_keeps_the_host_store(tmp_path: Path) -> None:
    """An owner scope carrying ids but no project entity stays conservatively on the host store.

    Only a *known* non-built-in project entity moves an owner into a private root, so an owner
    scope whose project did not resolve keeps their own profile and memory instead of silently
    becoming an empty per-user root.  Fails if the ``scope.project is None`` operand is dropped or
    the ids branch is evaluated before it, both of which redirect the owner.
    """
    project_path = tmp_path / "project"
    project_path.mkdir()
    scope = _scoped_scope(
        user_id="member-user",
        project_id="member-project",
        project_path=project_path,
        is_local_owner=True,
        project=None,
    )

    assert private_memory_root_for_scope(scope, project_path=project_path) is None


def test_turn_without_collaboration_provenance_keeps_the_host_store(tmp_path: Path) -> None:
    """A turn with no collaboration scope keeps the host's own store.

    Fails if ``scope=None`` starts returning a path: host turns (CLI, SDK, and plain WebUI) would
    read an empty per-user root instead of the host's own profile and long-term memory.
    """
    assert private_memory_root_for_scope(None, project_path=tmp_path / "project") is None


@pytest.mark.parametrize(
    ("user_id", "project_id", "is_local_owner"),
    [
        ("member-user", None, False),
        ("member-user", None, True),
        (None, "member-project", False),
        (None, None, False),
        (None, None, True),
    ],
)
def test_partially_resolved_scope_falls_back_to_the_authorized_project_directory(
    tmp_path: Path,
    user_id: str | None,
    project_id: str | None,
    is_local_owner: bool,
) -> None:
    """A scope missing either id reads the authorized project directory, never the host store.

    Every row carries a non-built-in project entity, so the owner's home-project exemption cannot
    apply.  Fails if that fallback returns ``None`` (the host store): an isolated or
    partially-resolved member would then read the host's profile — the exact leak this boundary
    exists to prevent.
    """
    project_path = tmp_path / "project"
    project_path.mkdir()
    scope = _scoped_scope(
        user_id=user_id,
        project_id=project_id,
        project_path=project_path,
        is_local_owner=is_local_owner,
        project=_scope_project(project_id="member-project", project_path=project_path),
    )

    assert private_memory_root_for_scope(scope, project_path=project_path) == project_path


@pytest.mark.parametrize("is_local_owner", [False, True])
def test_read_root_and_consolidation_store_resolve_to_the_same_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_local_owner: bool,
) -> None:
    """Reads and consolidation share one root for the same persisted provenance.

    ``_memory_store_for_session`` has always consolidated every project session into
    ``user_private_memory_root(user_id, project_id)``; the pre-fix read path derived its root with
    an inline rule that exempted the host owner.  Comparing the two derivations for the same
    provenance fails whenever they drift apart again — for the owner it fails against ``None``.

    Limit: this pins the invariant *given* provenance persisted for the scope's ids.  A host
    owner's channel session does not persist provenance today (``_persist_conversation_scope``
    returns early for ``is_local_owner``), so its consolidation store remains the host store; that
    asymmetry is outside this helper's contract and is deliberately not asserted here.
    """
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: tmp_path / "runtime" / name,
    )
    project_path = tmp_path / "project"
    project_path.mkdir()
    loop = _unit_loop(tmp_path / "agent")
    scope = _scoped_scope(
        user_id="member-user",
        project_id="member-project",
        project_path=project_path,
        is_local_owner=is_local_owner,
        project=_scope_project(project_id="member-project", project_path=project_path),
    )
    session = loop.sessions.get_or_create("weixin:member-chat")
    session.metadata[COLLABORATION_USER_METADATA_KEY] = scope.user_id
    session.metadata[COLLABORATION_PROJECT_METADATA_KEY] = scope.project_id

    store = loop._memory_store_for_session(session)

    assert store is not None
    assert store.workspace == private_memory_root_for_scope(scope, project_path=project_path)


@pytest.mark.asyncio
async def test_host_owner_channel_turn_reads_its_private_project_root_not_the_host_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_collaboration_repository: tuple[CollaborationStore, AsyncLocalCollaborationRepository],
) -> None:
    """The host owner's own channel turn inside a project reads that project's private root.

    The pre-fix read path exempted ``is_local_owner``, so a channel instance handed to a project
    still built this owner's turn from the host workspace: the host's SOUL.md, USER.md, MEMORY.md,
    and history all reached the model.  Distinct sentinels in the host and private roots make the
    leak unambiguous — with that exemption restored this test fails on the ``HOST_*`` assertions
    and on the missing private memory marker.
    """
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: runtime_root / name,
    )
    workspace = tmp_path / "host-workspace"
    project_workspace = tmp_path / "project-workspace"
    workspace.mkdir()
    project_workspace.mkdir()
    (workspace / "SOUL.md").write_text("HOST_SOUL_SECRET", encoding="utf-8")
    (workspace / "USER.md").write_text("HOST_USER_SECRET", encoding="utf-8")
    (project_workspace / "AGENTS.md").write_text("PROJECT_INSTRUCTIONS_MARKER", encoding="utf-8")
    host_memory = MemoryStore(workspace)
    host_memory.write_memory("HOST_MEMORY_SECRET")

    store, repository = local_collaboration_repository
    owner, _builtin = store.ensure_local_owner(workspace)
    project = store.create_project(owner.id, "shared project", project_workspace)
    store.update_user_default_project(owner.id, project.id)
    store.bind_identity("weixin", "owner-wechat-id", owner.id)
    private_memory = MemoryStore(user_private_memory_root(owner.id, project_id=project.id))
    private_memory.write_memory("OWNER_PRIVATE_MEMORY_MARKER")

    provider = _turn_provider()
    loop = _scope_loop(workspace, repository, provider)
    message = InboundMessage(
        "weixin",
        "owner-wechat-id",
        "owner-wechat-id",
        "OWNER_REQUEST_MARKER",
        metadata={"direct": True},
    )
    owner_key = await loop._effective_session_key(message)
    host_memory.append_history("HOST_HISTORY_SECRET", session_key=owner_key)
    private_memory.append_history("OWNER_PRIVATE_HISTORY_MARKER", session_key=owner_key)

    await loop._process_message(message)

    prompt = str(provider.chat_with_retry.await_args.kwargs["messages"][0]["content"])
    assert "PROJECT_INSTRUCTIONS_MARKER" in prompt
    assert "OWNER_PRIVATE_MEMORY_MARKER" in prompt
    assert "OWNER_PRIVATE_HISTORY_MARKER" in prompt
    assert "HOST_SOUL_SECRET" not in prompt
    assert "HOST_USER_SECRET" not in prompt
    assert "HOST_MEMORY_SECRET" not in prompt
    assert "HOST_HISTORY_SECRET" not in prompt
