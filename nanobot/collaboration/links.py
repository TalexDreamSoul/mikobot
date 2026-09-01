"""Short-lived proof codes for linking private-channel identities."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from collections.abc import Mapping
from contextlib import suppress
from typing import TypedDict, cast

from filelock import FileLock

from nanobot.collaboration.repository import CollaborationRepository
from nanobot.config.paths import get_runtime_subdir

_MAX_BYTES = 64 * 1024
_DEFAULT_TTL_SECONDS = 600


class IdentityLinkError(ValueError):
    """A link code is invalid, expired, or unavailable."""


class _LinkRecord(TypedDict):
    userId: str
    expiresAt: int


class IdentityLinkStore:
    """Issue short-lived link codes backed by a collaboration repository."""

    def __init__(self, collaboration: CollaborationRepository) -> None:
        self.collaboration = collaboration
        self.path = get_runtime_subdir("collaboration") / "identity-links.json"
        self._lock = FileLock(str(self.path.with_suffix(".json.lock")))

    @staticmethod
    def _digest(code: str) -> str:
        return hashlib.sha256(f"nanobot-link-v1\0{code}".encode()).hexdigest()

    async def create(self, user_id: str, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
        if await self.collaboration.get_user(user_id) is None:
            raise IdentityLinkError("user not found")
        ttl = max(60, min(int(ttl_seconds), 1800))
        return await asyncio.to_thread(self._create_record, user_id, ttl)

    async def consume(self, code: str, *, channel: str, sender_id: str) -> str:
        normalized = "".join(character for character in code.upper() if character.isalnum())
        if len(normalized) != 8:
            raise IdentityLinkError("link code must contain 8 letters or digits")
        now = int(time.time())
        record = await asyncio.to_thread(self._consume_record, normalized, now)
        if record is None or record["expiresAt"] < now:
            raise IdentityLinkError("link code is invalid or expired")
        user_id = record["userId"]
        if await self.collaboration.get_user(user_id) is None:
            raise IdentityLinkError("link target is unavailable")
        await self.collaboration.rebind_identity(channel, sender_id, user_id)
        return user_id

    def _create_record(self, user_id: str, ttl: int) -> str:
        code = secrets.token_hex(4).upper()
        now = int(time.time())
        with self._lock:
            state = self._load(now)
            state[self._digest(code)] = {"userId": user_id, "expiresAt": now + ttl}
            self._save(state)
        return code

    def _consume_record(self, code: str, now: int) -> _LinkRecord | None:
        with self._lock:
            state = self._load(now)
            record = state.pop(self._digest(code), None)
            self._save(state)
        return record

    def _load(self, now: int) -> dict[str, _LinkRecord]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        if len(raw) > _MAX_BYTES:
            raise IdentityLinkError("identity link state is too large")
        try:
            payload: object = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IdentityLinkError("identity link state is invalid") from exc
        if not isinstance(payload, dict):
            raise IdentityLinkError("identity link state is invalid")
        state: dict[str, _LinkRecord] = {}
        for key, value in cast(Mapping[object, object], payload).items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            record = cast(Mapping[object, object], value)
            user_id = record.get("userId")
            expires_at = record.get("expiresAt")
            if isinstance(user_id, str) and isinstance(expires_at, int) and expires_at >= now:
                state[key] = {"userId": user_id, "expiresAt": expires_at}
        return state

    def _save(self, state: dict[str, _LinkRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with open(temporary, "x", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                json.dump(state, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            with suppress(PermissionError, NotImplementedError, OSError):
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = ["IdentityLinkError", "IdentityLinkStore"]
