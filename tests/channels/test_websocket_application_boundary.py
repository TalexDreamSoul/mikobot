"""Executable architecture constraints for the WebSocket transport adapter."""

from __future__ import annotations

from nanobot.channels.websocket import runtime


def test_runtime_exports_compatibility_protocol_helpers() -> None:
    assert runtime._is_valid_chat_id("unified:default")  # pyright: ignore[reportPrivateUsage]
    assert not runtime._is_valid_chat_id("../escape")  # pyright: ignore[reportPrivateUsage]
