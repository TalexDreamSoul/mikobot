import asyncio
import json
from pathlib import Path
from typing import Callable

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.session_messages import (
    ListSessionsTool,
    SendSessionMessageTool,
    SessionMessageError,
)
from nanobot.bus.queue import MessageBus
from nanobot.session.manager import SessionManager
from nanobot.session.privacy import (
    SESSION_ACCESS_KIND_METADATA_KEY,
    SESSION_ACCESS_PROJECT_METADATA_KEY,
    SESSION_ACCESS_USER_METADATA_KEY,
)
from nanobot.session.session_handles import SessionHandle, SessionHandleResolver
from nanobot.session.session_messages import (
    SESSION_MESSAGE_METADATA_KEY,
    session_message_envelope,
)


def _persist(manager: SessionManager, *keys: str) -> None:
    for key in keys:
        manager.save(manager.get_or_create(key))


def _mark(manager: SessionManager, key: str, *, user_id: str, project_id: str) -> None:
    """Persist the access record the agent loop writes for one resolved member turn."""
    session = manager.get_or_create(key)
    session.metadata[SESSION_ACCESS_KIND_METADATA_KEY] = "direct"
    session.metadata[SESSION_ACCESS_USER_METADATA_KEY] = user_id
    session.metadata[SESSION_ACCESS_PROJECT_METADATA_KEY] = project_id
    manager.save(session)


def _handle(manager: SessionManager, key: str) -> SessionHandle:
    handle = SessionHandleResolver(manager).handle_for_session(key)
    assert handle is not None
    return handle


class _Timer:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.callback()


class _Scheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[float, _Timer]] = []

    def __call__(self, delay: float, callback: Callable[[], None]) -> _Timer:
        timer = _Timer(callback)
        self.calls.append((delay, timer))
        return timer



@pytest.mark.asyncio
async def test_list_sessions_includes_all_persisted_channels_except_current(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:current", "telegram:other", "slack:team")
    tool = ListSessionsTool(sessions)

    with request_context(RequestContext(
        channel="websocket",
        chat_id="current",
        session_key="websocket:current",
    )):
        result = json.loads(await tool.execute())

    assert set(result) == {
        f"@{_handle(sessions, 'telegram:other').name}",
        f"@{_handle(sessions, 'slack:team').name}",
    }

@pytest.mark.asyncio
async def test_member_session_listing_exposes_only_the_current_project(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    current = "user:member:project:alpha:chat-current"
    same_project = "user:member:project:alpha:chat-history"
    other_project = "user:member:project:beta:chat-history"
    other_member = "user:other-member:project:alpha:chat-history"
    host = "websocket:host-history"
    _persist(sessions, current, same_project, other_project, other_member, host)

    with request_context(RequestContext(
        channel="telegram",
        chat_id="member-chat",
        session_key=current,
    )):
        listed = json.loads(await ListSessionsTool(sessions).execute())

    assert listed == [f"@{_handle(sessions, same_project).name}"]


@pytest.mark.asyncio
async def test_member_can_send_only_to_a_session_in_the_same_project(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    source = "user:member:project:alpha:chat-source"
    target = "user:member:project:alpha:chat-target"
    _persist(sessions, source, target)
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)

    with request_context(RequestContext(
        channel="telegram",
        chat_id="member-chat",
        session_key=source,
    )):
        result = await tool.execute(
            to=f"@{_handle(sessions, target).name}",
            content="review the project artifact",
            expect_reply=False,
        )

    inbound = await bus.consume_inbound()
    assert result == f"Sent to @{_handle(sessions, target).name}."
    assert inbound.session_key_override == target
    assert inbound.content == "review the project artifact"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "user:member:project:beta:chat-history",
        "user:other-member:project:alpha:chat-history",
        "websocket:host-history",
    ],
)
async def test_member_session_message_rejects_other_privacy_scopes_without_delivery(
    tmp_path: Path,
    target: str,
) -> None:
    sessions = SessionManager(tmp_path)
    source = "user:member:project:alpha:chat-source"
    _persist(sessions, source, target)
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)

    with request_context(RequestContext(
        channel="telegram",
        chat_id="member-chat",
        session_key=source,
    )):
        rejected = await tool.execute(
            to=f"@{_handle(sessions, target).name}",
            content="do not deliver this",
            expect_reply=False,
        )

    assert rejected.is_error
    assert bus.inbound.empty()


@pytest.mark.asyncio
async def test_send_follows_the_recorded_access_scope(tmp_path: Path) -> None:
    """Sending obeys the recorded project of both sessions, not just their channel keys."""
    sessions = SessionManager(tmp_path)
    source = "telegram:alpha-chat"
    same_project = "telegram:alpha-peer"
    other_project = "telegram:beta-peer"
    host_history = "websocket:host-history"
    _mark(sessions, source, user_id="member", project_id="alpha")
    _mark(sessions, same_project, user_id="member", project_id="alpha")
    _mark(sessions, other_project, user_id="member", project_id="beta")
    _persist(sessions, host_history)
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)

    with request_context(RequestContext(
        channel="telegram",
        chat_id="alpha-chat",
        session_key=source,
    )):
        delivered = await tool.execute(
            to=f"@{_handle(sessions, same_project).name}",
            content="review the project artifact",
            expect_reply=False,
        )
        other_project_refusal = await tool.execute(
            to=f"@{_handle(sessions, other_project).name}",
            content="do not deliver this",
            expect_reply=False,
        )
        host_refusal = await tool.execute(
            to=f"@{_handle(sessions, host_history).name}",
            content="do not deliver this either",
            expect_reply=False,
        )

    assert delivered == f"Sent to @{_handle(sessions, same_project).name}."
    assert other_project_refusal.is_error
    assert host_refusal.is_error
    inbound = await bus.consume_inbound()
    assert inbound.session_key_override == same_project
    assert inbound.content == "review the project artifact"
    assert bus.inbound.empty()


@pytest.mark.asyncio
async def test_send_publishes_user_input_to_the_existing_target(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "telegram:target")
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)
    target = _handle(sessions, "telegram:target")

    sent_to = await tool.enqueue(
        source_session_key="websocket:source",
        target_handle=f"@{target.name}",
        content="Please review this.",
        expect_reply=False,
    )
    inbound = await bus.consume_inbound()
    envelope = session_message_envelope(inbound.metadata)

    assert sent_to == f"@{target.name}"
    assert inbound.channel == "system"
    assert inbound.chat_id == "telegram:target"
    assert inbound.session_key_override == "telegram:target"
    assert inbound.is_user_input
    assert inbound.content == "Please review this."
    assert envelope is not None
    assert envelope["source_session_key"] == "websocket:source"
    assert envelope["target_session_key"] == "telegram:target"
    assert inbound.metadata == {SESSION_MESSAGE_METADATA_KEY: envelope}


@pytest.mark.asyncio
async def test_send_fails_when_target_does_not_exist(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source")
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)

    with pytest.raises(SessionMessageError, match="was not found"):
        await tool.enqueue(
            source_session_key="websocket:source",
            target_handle="@zzzz",
            content="Hello",
            expect_reply=False,
        )

    assert bus.inbound.empty()


@pytest.mark.asyncio
async def test_rate_limit_is_per_source_session_and_uses_a_rolling_minute(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:a", "websocket:b", "websocket:target")
    now = 0.0
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=MessageBus(),
        max_messages_per_minute=1,
        clock=lambda: now,
    )
    target = _handle(sessions, "websocket:target").name

    await tool.enqueue(
        source_session_key="websocket:a",
        target_handle=target,
        content="A1",
        expect_reply=False,
    )
    await tool.enqueue(
        source_session_key="websocket:b",
        target_handle=target,
        content="B1",
        expect_reply=False,
    )
    with pytest.raises(SessionMessageError, match="rate limit"):
        await tool.enqueue(
            source_session_key="websocket:a",
            target_handle=target,
            content="A2",
            expect_reply=False,
        )

    now = 61.0
    await tool.enqueue(
        source_session_key="websocket:a",
        target_handle=target,
        content="A3",
        expect_reply=False,
    )


@pytest.mark.asyncio
async def test_rate_limit_releases_expired_source_state_and_keeps_recent_sources(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(
        sessions,
        "websocket:a",
        "websocket:b",
        "websocket:c",
        "websocket:target",
    )
    now = 0.0
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=MessageBus(),
        max_messages_per_minute=2,
        clock=lambda: now,
    )
    target = _handle(sessions, "websocket:target").name

    for source in ("websocket:a", "websocket:b"):
        await tool.enqueue(
            source_session_key=source,
            target_handle=target,
            content="initial",
            expect_reply=False,
        )
    now = 30.0
    await tool.enqueue(
        source_session_key="websocket:a",
        target_handle=target,
        content="recent",
        expect_reply=False,
    )

    now = 61.0
    await tool.enqueue(
        source_session_key="websocket:c",
        target_handle=target,
        content="trigger cleanup",
        expect_reply=False,
    )

    assert set(tool._sent_at) == {"websocket:a", "websocket:c"}
    await tool.enqueue(
        source_session_key="websocket:a",
        target_handle=target,
        content="within rolling window",
        expect_reply=False,
    )
    with pytest.raises(SessionMessageError, match="rate limit"):
        await tool.enqueue(
            source_session_key="websocket:a",
            target_handle=target,
            content="over limit",
            expect_reply=False,
        )


@pytest.mark.asyncio
async def test_reply_timeout_injects_a_user_input_back_into_the_source(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "websocket:target")
    bus = MessageBus()
    scheduler = _Scheduler()
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=bus,
        schedule_later=scheduler,
    )
    target = _handle(sessions, "websocket:target")

    await tool.enqueue(
        source_session_key="websocket:source",
        target_handle=target.name,
        content="Question",
        expect_reply=True,
        reply_timeout_seconds=5,
    )
    await bus.consume_inbound()
    delay, timer = scheduler.calls[0]

    assert delay == 5
    timer.fire()
    await asyncio.sleep(0)
    timeout = await bus.consume_inbound()
    assert timeout.chat_id == "websocket:source"
    assert timeout.is_user_input
    assert timeout.content == f"No reply from @{target.name} after 5 seconds."


@pytest.mark.asyncio
async def test_reverse_message_cancels_the_pending_reply_timeout(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "websocket:target")
    bus = MessageBus()
    scheduler = _Scheduler()
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=bus,
        schedule_later=scheduler,
    )
    source = _handle(sessions, "websocket:source")
    target = _handle(sessions, "websocket:target")

    await tool.enqueue(
        source_session_key=source.session_key,
        target_handle=target.name,
        content="Question",
        expect_reply=True,
        reply_timeout_seconds=5,
    )
    await tool.enqueue(
        source_session_key=target.session_key,
        target_handle=source.name,
        content="Answer",
        expect_reply=False,
    )

    assert scheduler.calls[0][1].cancelled


@pytest.mark.asyncio
async def test_reply_follows_a_recycled_handle(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "websocket:target")
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)
    source = _handle(sessions, "websocket:source")
    target = _handle(sessions, "websocket:target")

    await tool.enqueue(
        source_session_key=source.session_key,
        target_handle=target.name,
        content="Question",
        expect_reply=False,
    )
    received = await bus.consume_inbound()
    assert sessions.delete_session(source.session_key)
    _persist(sessions, "websocket:replacement")
    replacement = _handle(sessions, "websocket:replacement")
    assert replacement.name == source.name

    with request_context(RequestContext(
        channel="system",
        chat_id=target.session_key,
        session_key=target.session_key,
        metadata=received.metadata,
    )):
        result = await tool.execute(
            to=f"@{source.name}",
            content="Answer",
            expect_reply=False,
        )

    assert result == f"Sent to @{source.name}."
    reply = await bus.consume_inbound()
    assert reply.chat_id == replacement.session_key
