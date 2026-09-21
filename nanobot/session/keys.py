"""Shared session key constants and helpers."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

UNIFIED_SESSION_KEY = "unified:default"
LAST_CHANNEL_METADATA_KEY = "last_channel"
HOST_PRIVATE_SESSION_KEY = "heartbeat"
_HOST_PRIVATE_SESSION_PREFIXES = ("heartbeat:", "cron:", "dream:", "websocket:", "cli:")


def is_host_private_session_key(session_key: str | None) -> bool:
    """Return whether a session key belongs to the host's own private namespace.

    These sessions carry no external channel conversation: the owner drives them
    directly (WebUI, CLI) or the runtime drives them for the owner (heartbeat,
    cron, dream). They may read each other and are never reachable from a
    channel session.
    """
    if not session_key:
        return False
    return session_key == HOST_PRIVATE_SESSION_KEY or session_key.startswith(
        _HOST_PRIVATE_SESSION_PREFIXES
    )


def session_key_for_channel(channel: str, chat_id: str, *, unified_session: bool = False) -> str:
    """Return the session key for a channel/chat pair."""
    if unified_session:
        return UNIFIED_SESSION_KEY
    return f"{channel}:{chat_id}"


def remember_last_channel(
    metadata: MutableMapping[str, Any],
    channel: str,
    chat_id: str,
) -> None:
    """Persist the latest concrete delivery route in session metadata."""
    if not channel or not chat_id:
        return
    metadata[LAST_CHANNEL_METADATA_KEY] = f"{channel}:{chat_id}"


def last_channel_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """Return a concrete delivery route from persisted session metadata."""
    if not isinstance(metadata, Mapping):
        return None
    route = metadata.get(LAST_CHANNEL_METADATA_KEY)
    if not isinstance(route, str) or ":" not in route:
        return None
    channel, chat_id = route.split(":", 1)
    if not channel or not chat_id:
        return None
    return channel, chat_id
