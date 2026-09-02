"""Shared validation for bot capability profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from nanobot.collaboration.models import JsonObject, freeze_json

_ALLOWED_KEYS = frozenset({"skills", "mcpServers", "contextMaxTokens"})


def normalize_bot_capability_settings(value: Mapping[str, object]) -> JsonObject:
    if not set(value) <= _ALLOWED_KEYS:
        raise ValueError("unsupported bot capability setting")
    normalized: dict[str, object] = {}
    for key in ("skills", "mcpServers"):
        if key not in value:
            continue
        raw_items = value[key]
        if not isinstance(raw_items, (list, tuple)):
            raise ValueError(f"{key} must be a bounded list")
        raw_values = cast(list[object] | tuple[object, ...], raw_items)
        if len(raw_values) > 256:
            raise ValueError(f"{key} must be a bounded list")
        items: list[str] = []
        for raw_item in raw_values:
            if not isinstance(raw_item, str):
                raise ValueError(f"{key} values must be strings")
            item = raw_item.strip()
            if not item or len(item) > 512 or any(ord(character) < 32 for character in item):
                raise ValueError(f"invalid {key} value")
            if item not in items:
                items.append(item)
        normalized[key] = items
    if "contextMaxTokens" in value:
        tokens = value["contextMaxTokens"]
        if isinstance(tokens, bool) or not isinstance(tokens, int) or not 256 <= tokens <= 16_000:
            raise ValueError("contextMaxTokens must be between 256 and 16000")
        normalized["contextMaxTokens"] = tokens
    return freeze_json(normalized)
