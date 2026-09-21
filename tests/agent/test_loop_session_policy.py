import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.session_messages import ListSessionsTool
from nanobot.agent.tools.sessions import ReadSessionTool, SearchSessionsTool
from nanobot.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_SESSION_DISCARD,
    InboundMessage,
)
from nanobot.bus.queue import MessageBus
from nanobot.collaboration import ConversationScope, ConversationScopeKind
from nanobot.providers.base import GenerationSettings, LLMResponse
from nanobot.session.keys import UNIFIED_SESSION_KEY
from nanobot.session.manager import Session
from nanobot.session.privacy import (
    SESSION_ACCESS_KIND_METADATA_KEY,
    SESSION_ACCESS_PROJECT_METADATA_KEY,
    SESSION_ACCESS_USER_METADATA_KEY,
    SessionAccessScope,
    session_access_allowed,
    session_access_scope,
)
from nanobot.session.session_handles import SessionHandleResolver


def _message(key: str, content: str) -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id=key.removeprefix("websocket:"),
        content=content,
        session_key_override=key,
        require_existing_session=True,
    )


def _loop(tmp_path, responses: list[str], **kwargs) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    provider.chat_with_retry = AsyncMock(
        side_effect=[LLMResponse(content=response, usage=None) for response in responses]
    )
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        cron_service=MagicMock(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_transient_session_keeps_history_without_persisting_or_durable_tools(tmp_path) -> None:
    loop = _loop(tmp_path, ["first answer", "second answer"])
    loop.context.memory.write_memory("private durable memory")
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock()
    key = "websocket:transient-test"
    loop.sessions.get_or_create_transient(
        key,
        disabled_tools={"create_goal", "update_goal", "spawn", "cron"},
    )

    await loop._process_message(_message(key, "first question"))
    await loop._process_message(_message(key, "second question"))

    calls = loop.provider.chat_with_retry.await_args_list
    assert "private durable memory" not in str(calls[0].kwargs["messages"])
    tool_names = {item["function"]["name"] for item in calls[0].kwargs["tools"]}
    assert "read_session" in tool_names
    assert {"create_goal", "update_goal", "spawn", "cron"}.isdisjoint(tool_names)
    assert "first answer" in str(calls[1].kwargs["messages"])
    session = loop.sessions.get_cached(key)
    assert session is not None
    assert [message["role"] for message in session.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert loop.sessions.read_session_file(key) is None
    loop.consolidator.maybe_consolidate_by_tokens.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_session_stays_outside_unified_session(tmp_path) -> None:
    loop = _loop(tmp_path, ["private answer"], unified_session=True)
    durable = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    durable.add_message("user", "durable question")
    loop.sessions.save(durable)
    key = "websocket:transient-unified"
    transient = loop.sessions.get_or_create_transient(key)

    await loop._dispatch(_message(key, "private question"))

    assert [message["content"] for message in transient.messages] == [
        "private question",
        "private answer",
    ]
    assert [message["content"] for message in durable.messages] == ["durable question"]
    assert loop.sessions.read_session_file(key) is None


@pytest.mark.asyncio
async def test_missing_required_session_cannot_fall_back_to_disk(tmp_path) -> None:
    loop = _loop(tmp_path, [])
    key = "websocket:transient-stale"
    loop.sessions.get_or_create_transient(key)
    loop.sessions.invalidate(key)

    with pytest.raises(RuntimeError, match="required session is not active"):
        await loop._process_message(_message(key, "stale private message"))

    loop.provider.chat_with_retry.assert_not_awaited()
    assert loop.sessions.read_session_file(key) is None


@pytest.mark.asyncio
async def test_session_discard_control_cancels_active_turn(tmp_path, monkeypatch) -> None:
    provider_started = asyncio.Event()

    async def block_provider(**_kwargs: object) -> LLMResponse:
        provider_started.set()
        await asyncio.Event().wait()
        raise AssertionError("provider blocker unexpectedly released")

    loop = _loop(tmp_path, [])

    async def wait_for_discard(key: str) -> None:
        while loop.sessions.get_cached(key) is not None or key in loop._discarding_sessions:
            await asyncio.sleep(0)

    loop.provider.chat_with_retry = AsyncMock(side_effect=block_provider)
    monkeypatch.setattr(loop, "aclose", AsyncMock())
    terminate_exec_sessions = AsyncMock(return_value=1)
    monkeypatch.setattr(
        loop._exec_session_manager,
        "terminate_by_owner",
        terminate_exec_sessions,
    )
    key = "websocket:transient-cancelled"
    previous_file_state = loop._file_state_store.for_session(key)
    loop.sessions.get_or_create_transient(
        key,
        disabled_tools={"create_goal", "update_goal", "spawn", "cron"},
    )
    run_task = asyncio.create_task(loop.run())
    await loop.bus.publish_inbound(_message(key, "private"))
    await asyncio.wait_for(provider_started.wait(), timeout=2)
    active_task = next(iter(loop._active_tasks[key]))

    await loop.bus.publish_inbound(
        InboundMessage(
            channel="websocket",
            sender_id="webui",
            chat_id="transient-cancelled",
            content="",
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_SESSION_DISCARD,
            },
            session_key_override=key,
        )
    )

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(active_task, timeout=2)
    await asyncio.wait_for(wait_for_discard(key), timeout=2)
    assert loop.sessions.get_cached(key) is None
    assert loop._file_state_store.for_session(key) is not previous_file_state
    terminate_exec_sessions.assert_awaited_once_with(key)

    loop.stop()
    await loop.bus.publish_inbound(_message(key, "wake"))
    await asyncio.wait_for(run_task, timeout=2)


def _owner_scope(*, project_id: str, workspace: Path) -> ConversationScope:
    """The resolved scope of the host owner's own channel turn."""
    return ConversationScope(
        kind=ConversationScopeKind.DIRECT,
        user_id="owner",
        project_id=project_id,
        user=None,
        project=None,
        binding=None,
        assignment=None,
        workspace_path=str(workspace),
        session_suffix=project_id,
        is_local_owner=True,
    )


def _isolated_scope() -> ConversationScope:
    """The resolved scope of a group conversation that reached no project."""
    return ConversationScope(
        kind=ConversationScopeKind.ISOLATED,
        user_id="external-user",
        project_id=None,
        user=None,
        project=None,
        binding=None,
        assignment=None,
        workspace_path=None,
        session_suffix="unrouted-group",
    )


class _FixedScopes:
    """Collaboration repository whose resolved scope comes from a conversation table."""

    def __init__(self, scopes: dict[str, ConversationScope]) -> None:
        self._scopes = scopes

    async def initialize(self) -> None:
        return None

    async def ensure_local_owner(self, workspace: Path) -> None:
        return None

    async def ensure_identity_user(
        self,
        channel: str,
        sender_id: str,
        workspace: Path,
        *,
        local_owner: bool = False,
    ) -> tuple[None, None]:
        return (None, None)

    async def resolve_scope(
        self,
        channel: str,
        sender_id: str,
        conversation_id: str,
        metadata: Mapping[str, object],
        workspace: Path,
    ) -> ConversationScope:
        return self._scopes[conversation_id]


def _scoped_loop(tmp_path, scopes: dict[str, ConversationScope]) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="acknowledged", usage=None)
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path / "agent",
        model="test-model",
        cron_service=MagicMock(),
        collaboration_repository=_FixedScopes(scopes),
    )
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock()
    return loop


def _channel_message(channel: str, chat_id: str, content: str) -> InboundMessage:
    return InboundMessage(channel=channel, sender_id="owner", chat_id=chat_id, content=content)


def _persisted_session(loop: AgentLoop, key: str) -> Session:
    """Read the session back from persistence, not from the loop's live cache."""
    loop.sessions.invalidate(key)
    session = loop.sessions.peek(key)
    assert session is not None
    return session


@pytest.mark.asyncio
async def test_owner_turn_records_access_scope_without_member_provenance(tmp_path) -> None:
    """The host owner's channel turn records its project for access decisions only."""
    workspace = tmp_path / "projects" / "qingqi"
    workspace.mkdir(parents=True)
    loop = _scoped_loop(
        tmp_path,
        {"owner-chat": _owner_scope(project_id="qingqi", workspace=workspace)},
    )

    await loop._process_message(_channel_message("feishu", "owner-chat", "hello"))

    session = _persisted_session(loop, "feishu:owner-chat")
    assert session.metadata[SESSION_ACCESS_KIND_METADATA_KEY] == "direct"
    assert session.metadata[SESSION_ACCESS_USER_METADATA_KEY] == "owner"
    assert session.metadata[SESSION_ACCESS_PROJECT_METADATA_KEY] == "qingqi"
    assert not any(key.startswith("collaboration_") for key in session.metadata)
    assert session_access_scope(session.metadata, "feishu:owner-chat") == SessionAccessScope(
        "owner", "qingqi"
    )
    assert session_access_allowed(
        session.metadata, "feishu:owner-chat", session.metadata, "feishu:owner-chat"
    )


@pytest.mark.asyncio
async def test_isolated_turn_records_isolation_without_member_provenance(
    tmp_path, monkeypatch
) -> None:
    """An unrouted group turn is recorded as isolated, with no project and no member provenance."""
    monkeypatch.setattr(
        "nanobot.agent.loop.get_runtime_subdir", lambda name: tmp_path / "runtime" / name
    )
    loop = _scoped_loop(tmp_path, {"team-group": _isolated_scope()})

    await loop._process_message(_channel_message("feishu", "team-group", "hello"))

    session = _persisted_session(loop, "feishu:team-group")
    assert session.metadata[SESSION_ACCESS_KIND_METADATA_KEY] == "isolated"
    assert session.metadata[SESSION_ACCESS_USER_METADATA_KEY] == "external-user"
    assert session.metadata[SESSION_ACCESS_PROJECT_METADATA_KEY] is None
    assert not any(key.startswith("collaboration_") for key in session.metadata)
    assert session_access_scope(session.metadata, "feishu:team-group") == SessionAccessScope(
        "external-user", None, isolated=True
    )
    assert not session_access_allowed(
        session.metadata, "feishu:team-group", session.metadata, "feishu:team-group"
    )


@pytest.mark.asyncio
async def test_owner_channel_turns_cannot_reach_another_project_through_session_tools(
    tmp_path,
) -> None:
    """The reported leak stays closed end to end: resolved owner turns mark their sessions."""
    qingqi = tmp_path / "projects" / "qingqi"
    local = tmp_path / "projects" / "local"
    qingqi.mkdir(parents=True)
    local.mkdir(parents=True)
    loop = _scoped_loop(
        tmp_path,
        {
            "owner-chat": _owner_scope(project_id="qingqi", workspace=qingqi),
            "peer-chat": _owner_scope(project_id="qingqi", workspace=qingqi),
            "weixin-chat": _owner_scope(project_id="local", workspace=local),
        },
    )

    await loop._process_message(_channel_message("feishu", "owner-chat", "SECRET_MARKER feishu"))
    await loop._process_message(_channel_message("feishu", "peer-chat", "SECRET_MARKER peer"))
    await loop._process_message(_channel_message("weixin", "weixin-chat", "SECRET_MARKER weixin"))

    for key, project_id in (
        ("feishu:owner-chat", "qingqi"),
        ("feishu:peer-chat", "qingqi"),
        ("weixin:weixin-chat", "local"),
    ):
        session = _persisted_session(loop, key)
        assert session.metadata[SESSION_ACCESS_KIND_METADATA_KEY] == "direct"
        assert session.metadata[SESSION_ACCESS_USER_METADATA_KEY] == "owner"
        assert session.metadata[SESSION_ACCESS_PROJECT_METADATA_KEY] == project_id
        assert not any(name.startswith("collaboration_") for name in session.metadata)

    peer_handle = SessionHandleResolver(loop.sessions).handle_for_session("feishu:peer-chat")
    assert peer_handle is not None

    with request_context(RequestContext(
        channel="feishu",
        chat_id="owner-chat",
        session_key="feishu:owner-chat",
    )):
        discovered = json.loads(await SearchSessionsTool(loop.sessions).execute(
            query="SECRET_MARKER"
        ))
        listed = json.loads(await ListSessionsTool(loop.sessions).execute())
        refused = await ReadSessionTool(loop.sessions).execute(session_key="weixin:weixin-chat")
        allowed = json.loads(await ReadSessionTool(loop.sessions).execute(
            session_key="feishu:peer-chat"
        ))

    assert [row["session_key"] for row in discovered["results"]] == ["feishu:peer-chat"]
    assert listed == [f"@{peer_handle.name}"]
    assert refused.is_error
    assert "SECRET_MARKER" not in str(refused)
    assert "SECRET_MARKER peer" in json.dumps(allowed)
