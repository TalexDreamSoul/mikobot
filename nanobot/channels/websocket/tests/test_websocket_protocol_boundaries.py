"""Boundary tests for pure WebSocket protocol helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nanobot.channels.websocket.runtime import (
    _is_valid_chat_id,
    _parse_envelope,
)
from nanobot.webui.http_utils import http_json_response

from .test_websocket_channel import _ch


def test_chat_id_validator_accepts_only_compact_capability_keys() -> None:
    valid = [
        "a",
        "A-Z_09:chat-id",
        "x" * 64,
    ]
    invalid = [
        "",
        "x" * 65,
        "../escape",
        "chat/id",
        "chat id",
        "chat\nid",
        None,
        123,
    ]

    for value in valid:
        assert _is_valid_chat_id(value), value
    for value in invalid:
        assert not _is_valid_chat_id(value), repr(value)


@pytest.mark.parametrize(
    ("raw", "expected_type"),
    [
        ("plain text", None),
        ("{not json", None),
        ("[]", None),
        ("{}", None),
        ('{"type": 42}', None),
        ('{"type": "message", "content": "hi"}', "message"),
        ('  {"type": "new_chat"}  ', "new_chat"),
    ],
)
def test_parse_envelope_only_accepts_typed_json_objects(
    raw: str,
    expected_type: str | None,
) -> None:
    parsed = _parse_envelope(raw)
    if expected_type is None:
        assert parsed is None
    else:
        assert parsed is not None
        assert parsed["type"] == expected_type


@pytest.mark.asyncio
async def test_qr_cancel_completes_before_same_connection_poll_returns() -> None:
    """A blocked provider poll must not prevent revoking its QR session."""
    channel = _ch(MagicMock())
    connection = MagicMock()
    channel._webui_connections.add(connection)
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()
    revoked = False

    async def dispatch(_connection, action, payload):
        nonlocal revoked
        if action == "settings.channel.connect.poll":
            poll_started.set()
            await release_poll.wait()
            return http_json_response({"status": "cancelled" if revoked else "confirmed"})
        assert action == "settings.channel.connect.cancel"
        revoked = True
        return http_json_response({"status": "cancelled"})

    channel.gateway.http.dispatch_webui_mutation = dispatch
    poll = asyncio.create_task(channel._commands.execute_webui_request(
        connection, "settings.channel.connect.poll", {"sessionId": "qr-session"},
    ))
    try:
        await asyncio.wait_for(poll_started.wait(), timeout=2)
        cancelled = await asyncio.wait_for(channel._commands.execute_webui_request(
            connection, "settings.channel.connect.cancel", {"sessionId": "qr-session"},
        ), timeout=2)
        assert cancelled.result == {"status": "cancelled"}
        assert not poll.done()
        release_poll.set()
        assert (await poll).result == {"status": "cancelled"}
    finally:
        release_poll.set()
        await asyncio.gather(poll, return_exceptions=True)


@pytest.mark.asyncio
async def test_ordinary_mutation_cannot_overtake_blocked_same_connection_write() -> None:
    """Only QR polls bypass FIFO; two configuration writes retain their order."""
    channel = _ch(MagicMock())
    connection = MagicMock()
    first_started = asyncio.Event()
    second_submitted = asyncio.Event()
    release_first = asyncio.Event()
    committed = []

    async def dispatch(_connection, action, payload):
        if action == "settings.provider.update":
            first_started.set()
            await release_first.wait()
        committed.append(action)
        return http_json_response({"committed": list(committed)})

    async def second_write():
        second_submitted.set()
        return await channel._commands.execute_webui_request(
            connection, "settings.agent.update", {},
        )

    channel.gateway.http.dispatch_webui_mutation = dispatch
    first = asyncio.create_task(channel._commands.execute_webui_request(
        connection, "settings.provider.update", {},
    ))
    second = None
    try:
        await asyncio.wait_for(first_started.wait(), timeout=2)
        second = asyncio.create_task(second_write())
        await asyncio.wait_for(second_submitted.wait(), timeout=2)
        assert committed == []
        assert not second.done()
        release_first.set()
        assert (await first).result == {"committed": ["settings.provider.update"]}
        assert (await second).result == {
            "committed": ["settings.provider.update", "settings.agent.update"],
        }
    finally:
        release_first.set()
        await asyncio.gather(first, *([second] if second is not None else []),
                             return_exceptions=True)
