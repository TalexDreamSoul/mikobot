"""Pair Code helpers shared by the collaboration store and channel runtimes."""

from __future__ import annotations

import hashlib
import secrets

_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# Channels that only serve assigned instances (Feishu, Weixin) set this inbound
# metadata flag so an unassigned instance is denied instead of falling back to
# the sender's personal default project.
CHANNEL_ASSIGNMENT_REQUIRED_METADATA_KEY = "_channel_assignment_required"


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
