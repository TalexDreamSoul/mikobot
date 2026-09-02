"""Purpose-bound Pair Code helpers shared by collaboration backends."""

from __future__ import annotations

import hashlib
import secrets

_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BOT_PROJECT_ROUTE_REQUIRED_METADATA_KEY = "_bot_project_route_required"


def runtime_channel_key(channel_type: str, instance_id: str) -> str:
    return channel_type if instance_id == "default" else f"{channel_type}.{instance_id}"


def normalize_assignment_code(code: str) -> str:
    normalized = "".join(character for character in code.upper() if character.isalnum())
    if len(normalized) != 8 or any(character not in _PAIRING_ALPHABET for character in normalized):
        raise ValueError("pairing code must contain 8 valid letters or digits")
    return normalized


def new_assignment_code() -> str:
    raw = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def assignment_code_digest(code: str) -> str:
    normalized = normalize_assignment_code(code)
    return hashlib.sha256(f"nanobot-bot-pair-v1\0{normalized}".encode()).hexdigest()
