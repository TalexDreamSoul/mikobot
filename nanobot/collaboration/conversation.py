"""Canonical collaboration conversation identifiers."""

from __future__ import annotations

from collections.abc import Mapping


def canonical_conversation_id(
    chat_id: str,
    metadata: Mapping[str, object] | None = None,
) -> str:
    """Return the stable conversation id shared by bindings and scope lookup.

    Feishu P2P events provide ``source_chat_id`` as the durable conversation
    identifier while their transport chat id may vary across deliveries.
    """
    source_chat_id = (metadata or {}).get("source_chat_id")
    if isinstance(source_chat_id, str) and source_chat_id.strip():
        return source_chat_id.strip()
    return chat_id
