"""Short-lived WebUI channel connection sessions."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot.channels.connect import ChannelConnectError, QueryParams, query_first
from nanobot.channels.feishu import runtime as feishu
from nanobot.channels.feishu.instances import DEFAULT_INSTANCE_ID, validate_instance_id

_MAX_CONNECT_SESSIONS = 32
_MAX_CONNECT_SESSIONS_PER_ACTOR = 4


@dataclass(slots=True)
class FeishuConnectSession:
    id: str
    instance_id: str
    mode: str
    instance_name: str
    device_code: str
    qr_url: str
    domain: str
    interval: int
    expire_in: int
    created_wall: float
    deadline: float
    actor_user_id: str | None = None


class FeishuConnectStore:
    """In-memory Feishu/Lark QR connection state.

    Sessions intentionally live only in the gateway process and expire quickly.
    The app secret is never returned to the browser; it is saved directly to
    config when Feishu/Lark completes authorization.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, FeishuConnectSession] = {}
        self._completion_lock = threading.Lock()

    async def handle(self, action: str, query: QueryParams) -> dict[str, Any]:
        """Handle one generic settings connection action."""
        actor_user_id = (query_first(query, "_actor_user_id") or "").strip() or None
        if action == "start":
            mode = query_first(query, "mode")
            return await asyncio.to_thread(
                self.start,
                domain=(query_first(query, "domain") or "feishu").strip(),
                instance_id=(query_first(query, "instance_id") or "default").strip(),
                mode=mode if mode is not None else "replace",
                actor_user_id=actor_user_id,
            )

        session_id = (query_first(query, "session_id") or "").strip()
        if not session_id:
            raise ChannelConnectError("missing Feishu connect session")
        if action == "poll":
            return await asyncio.to_thread(
                self.poll, session_id, actor_user_id=actor_user_id
            )
        if action == "cancel":
            return await asyncio.to_thread(
                self.cancel, session_id, actor_user_id=actor_user_id
            )
        raise ChannelConnectError("unsupported Feishu connect action", status=404)

    def start(
        self,
        *,
        domain: str = "feishu",
        instance_id: str = DEFAULT_INSTANCE_ID,
        mode: str = "replace",
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        domain = _normalize_domain(domain)
        instance_id = _resolve_instance_id(instance_id, mode)
        with self._completion_lock:
            self._cleanup()
            for session_id, session in tuple(self._sessions.items()):
                if session.instance_id == instance_id:
                    self._sessions.pop(session_id, None)
            if actor_user_id is not None and sum(
                session.actor_user_id == actor_user_id
                for session in self._sessions.values()
            ) >= _MAX_CONNECT_SESSIONS_PER_ACTOR:
                raise ChannelConnectError(
                    "Too many active Feishu connection sessions for user",
                    status=429,
                )
            if len(self._sessions) >= _MAX_CONNECT_SESSIONS:
                raise ChannelConnectError(
                    "Too many active Feishu connection sessions",
                    status=429,
                )
            try:
                feishu._init_registration(domain)
                begin = feishu._begin_registration(domain)
                expire_in = int(begin["expire_in"])
                interval = max(2, int(begin["interval"]))
                device_code = str(begin["device_code"])
                qr_url = str(begin["qr_url"])
            except Exception:
                logger.exception("Failed to start Feishu/Lark connection")
                raise ChannelConnectError(
                    "Unable to start Feishu/Lark connection.", status=502
                ) from None

            session_id = secrets.token_urlsafe(18)
            now_wall = time.time()
            now = time.monotonic()
            session = FeishuConnectSession(
                id=session_id,
                instance_id=instance_id,
                mode=mode,
                instance_name=_default_instance_name(instance_id),
                device_code=device_code,
                qr_url=qr_url,
                domain=domain,
                interval=interval,
                expire_in=expire_in,
                created_wall=now_wall,
                deadline=now + expire_in,
                actor_user_id=actor_user_id,
            )
            self._sessions[session_id] = session
            return _start_payload(session)

    def poll(
        self, session_id: str, *, actor_user_id: str | None = None
    ) -> dict[str, Any]:
        self._cleanup()
        session = self._sessions.get(session_id)
        if session is None:
            return {
                "session_id": session_id,
                "status": "expired",
                "message": "This Feishu connection has expired. Start again.",
            }
        _require_session_actor(session, actor_user_id)

        if time.monotonic() >= session.deadline:
            self._sessions.pop(session_id, None)
            return {
                "session_id": session_id,
                "status": "expired",
                "message": "This Feishu connection has expired. Start again.",
            }

        try:
            result = feishu.poll_registration_once(
                device_code=session.device_code,
                domain=session.domain,
            )
        except Exception:
            logger.exception("Failed to poll Feishu/Lark connection")
            return _pending_payload(session)

        status = result.get("status")
        if status == "succeeded":
            with self._completion_lock:
                if self._sessions.get(session_id) is not session:
                    return {
                        "session_id": session_id,
                        "instance_id": session.instance_id,
                        "status": "cancelled",
                        "message": "Feishu connection cancelled.",
                    }
                _require_session_actor(session, actor_user_id)
                session.domain = _normalize_domain(str(result.get("domain") or session.domain))
                try:
                    feishu.save_registration_result(
                        result,
                        instance_id=session.instance_id,
                        name=session.instance_name,
                        mode=session.mode,
                    )
                except ChannelConnectError:
                    self._sessions.pop(session_id, None)
                    raise
                except Exception:
                    logger.exception("Failed to save Feishu/Lark connection")
                    self._sessions.pop(session_id, None)
                    return {
                        "session_id": session_id,
                        "instance_id": session.instance_id,
                        "status": "failed",
                        "message": "Feishu connection could not be completed.",
                    }
                self._sessions.pop(session_id, None)
                return {
                    "session_id": session_id,
                    "instance_id": session.instance_id,
                    "status": "succeeded",
                    "pairing_required": True,
                    "message": "Feishu is connected.",
                }

        session.domain = _normalize_domain(str(result.get("domain") or session.domain))
        if status == "failed":
            self._sessions.pop(session_id, None)
            return {
                "session_id": session_id,
                "instance_id": session.instance_id,
                "status": "failed",
                "message": "Authorization was cancelled or expired.",
                "domain": session.domain,
            }

        return _pending_payload(session)

    def cancel(
        self, session_id: str, *, actor_user_id: str | None = None
    ) -> dict[str, Any]:
        with self._completion_lock:
            session = self._sessions.get(session_id)
            if session is not None:
                _require_session_actor(session, actor_user_id)
            self._sessions.pop(session_id, None)
        return {
            "session_id": session_id,
            "instance_id": session.instance_id if session else DEFAULT_INSTANCE_ID,
            "status": "cancelled",
            "message": "Feishu connection cancelled.",
        }

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now >= session.deadline
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)


def _require_session_actor(
    session: FeishuConnectSession, actor_user_id: str | None
) -> None:
    if session.actor_user_id != actor_user_id:
        raise ChannelConnectError("Feishu connection belongs to another user", status=403)


def _normalize_domain(domain: str) -> str:
    normalized = domain.strip().lower()
    return normalized if normalized in {"feishu", "lark"} else "feishu"


def _resolve_instance_id(instance_id: str, mode: str) -> str:
    if mode not in {"create", "replace"}:
        raise ChannelConnectError("invalid Feishu connect mode", status=400)
    if mode == "create":
        return f"assistant-{secrets.token_hex(3)}"
    try:
        return validate_instance_id(instance_id or DEFAULT_INSTANCE_ID)
    except ValueError:
        raise ChannelConnectError("invalid Feishu connect instance", status=400) from None


def _default_instance_name(instance_id: str) -> str:
    return "nanobot" if instance_id == DEFAULT_INSTANCE_ID else f"nanobot {instance_id}"


def _start_payload(session: FeishuConnectSession) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "instance_id": session.instance_id,
        "status": "pending",
        "qr_url": session.qr_url,
        "domain": session.domain,
        "interval_ms": session.interval * 1000,
        "expires_at_ms": int((session.created_wall + session.expire_in) * 1000),
        "message": "Scan with Feishu or Lark to connect.",
    }


def _pending_payload(session: FeishuConnectSession) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "instance_id": session.instance_id,
        "status": "pending",
        "domain": session.domain,
        "interval_ms": session.interval * 1000,
        "expires_at_ms": int((session.created_wall + session.expire_in) * 1000),
        "message": "Waiting for authorization.",
    }
