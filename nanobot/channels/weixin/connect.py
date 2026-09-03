"""WeChat-owned interactive connection flow."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.channels.connect import ChannelConnectError, QueryParams, query_first
from nanobot.config.loader import load_config

if TYPE_CHECKING:
    from nanobot.channels.weixin.runtime import WeixinChannel


_MAX_CONNECT_SESSIONS = 32
_MAX_CONNECT_SESSIONS_PER_ACTOR = 4

@dataclass(slots=True)
class WeixinConnectSession:
    id: str
    instance_id: str
    qrcode_id: str
    qr_url: str
    channel: WeixinChannel
    current_poll_base_url: str
    refresh_count: int
    force: bool
    created_wall: float
    deadline: float
    actor_user_id: str | None = None


class WeixinConnectStore:
    """In-memory WeChat QR login sessions for the WebUI."""

    def __init__(self) -> None:
        self._sessions: dict[str, WeixinConnectSession] = {}
        self._start_lock = asyncio.Lock()

    async def handle(self, action: str, query: QueryParams) -> dict[str, Any]:
        """Handle one instance-aware settings connection action."""
        actor_user_id = (query_first(query, "_actor_user_id") or "").strip() or None
        if action == "start":
            from nanobot.channels.weixin.instances import (
                DEFAULT_INSTANCE_ID,
                validate_instance_id,
            )

            force = (query_first(query, "force") or "").strip().lower() in {
                "1", "true", "yes",
            }
            mode = query_first(query, "mode")
            if mode is None:
                mode = "replace"
            if mode not in {"create", "replace"}:
                raise ChannelConnectError("invalid WeChat connect mode", status=400)
            raw_instance_id = (
                query_first(query, "instance_id") or DEFAULT_INSTANCE_ID
            ).strip()
            if mode == "create":
                raw_instance_id = f"wechat-{secrets.token_hex(3)}"
            try:
                instance_id = validate_instance_id(raw_instance_id)
            except ValueError:
                raise ChannelConnectError("invalid WeChat connect instance", status=400) from None
            return await self.start(
                instance_id=instance_id,
                force=force,
                actor_user_id=actor_user_id,
            )

        session_id = (query_first(query, "session_id") or "").strip()
        if not session_id:
            raise ChannelConnectError("missing WeChat connect session")
        if action == "poll":
            return await self.poll(
                session_id,
                verify_code=(query_first(query, "verify_code") or "").strip(),
                actor_user_id=actor_user_id,
            )
        if action == "cancel":
            return await self.cancel(session_id, actor_user_id=actor_user_id)
        raise ChannelConnectError("unsupported WeChat connect action", status=404)

    async def start(
        self,
        *,
        instance_id: str = "default",
        force: bool = False,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._start_lock:
            await self._cleanup()
            for session_id, session in tuple(self._sessions.items()):
                if session.instance_id == instance_id:
                    self._sessions.pop(session_id, None)
                    await self._close_channel(session.channel)
            if actor_user_id is not None and sum(
                session.actor_user_id == actor_user_id
                for session in self._sessions.values()
            ) >= _MAX_CONNECT_SESSIONS_PER_ACTOR:
                raise ChannelConnectError(
                    "Too many active WeChat connection sessions for user",
                    status=429,
                )
            if len(self._sessions) >= _MAX_CONNECT_SESSIONS:
                raise ChannelConnectError(
                    "Too many active WeChat connection sessions",
                    status=429,
                )

            channel: WeixinChannel | None = None
            try:
                channel = self._build_channel(instance_id)
                if force:
                    channel.connect_reset_pending_credentials()
                elif channel.connect_load_state():
                    return {
                        "session_id": "",
                        "instance_id": instance_id,
                        "status": "succeeded",
                        "pairing_required": True,
                        "message": "WeChat is already connected.",
                        "interval_ms": 2000,
                    }

                channel.connect_open_client()
                qrcode_id, qr_url = await channel.connect_fetch_qr_code(force=force)
            except Exception:
                logger.exception("Failed to start WeChat QR login")
                if channel is not None:
                    await self._close_channel(channel)
                raise ChannelConnectError(
                    "Unable to start WeChat QR login.", status=502
                ) from None

            session_id = secrets.token_urlsafe(18)
            now_wall = time.time()
            self._sessions[session_id] = WeixinConnectSession(
                id=session_id,
                instance_id=instance_id,
                qrcode_id=qrcode_id,
                qr_url=qr_url,
                channel=channel,
                current_poll_base_url=channel.connect_base_url,
                refresh_count=0,
                force=force,
                created_wall=now_wall,
                deadline=time.monotonic() + 600,
                actor_user_id=actor_user_id,
            )
            return self._start_payload(self._sessions[session_id])
    async def poll(
        self,
        session_id: str,
        *,
        verify_code: str = "",
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        await self._cleanup()
        session = self._sessions.get(session_id)
        if session is None:
            return {
                "session_id": session_id,
                "status": "expired",
                "message": "This WeChat login has expired. Start again.",
            }
        _require_session_actor(session, actor_user_id)
        try:
            status_data = await session.channel.connect_poll_qr_code(
                base_url=session.current_poll_base_url,
                qrcode_id=session.qrcode_id,
                verify_code=verify_code,
            )
        except Exception as exc:
            logger.exception("WeChat QR login poll failed")
            try:
                retryable = session.channel.connect_poll_error_is_retryable(exc)
            except Exception:
                logger.exception("Failed to classify WeChat QR login poll error")
                retryable = False
            if retryable:
                return self._pending_payload(session)
            self._sessions.pop(session_id, None)
            await self._close_channel(session.channel)
            return {
                "session_id": session_id,
                "status": "failed",
                "message": "WeChat QR login failed.",
            }

        status_payload = status_data
        status = status_payload.get("status", "")
        from nanobot.channels.weixin.runtime import MAX_QR_REFRESH_COUNT

        if status == "confirmed":
            if self._sessions.get(session_id) is not session:
                return {
                    "session_id": session_id,
                    "status": "cancelled",
                    "message": "WeChat login cancelled.",
                }
            token = str(status_payload.get("bot_token", "") or "")
            if not token:
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "status": "failed",
                    "message": "WeChat confirmed the scan but returned no token.",
                }
            base_url = str(status_payload.get("baseurl", "") or "")
            try:
                session.channel.connect_commit_account(token=token, base_url=base_url)
            except Exception:
                logger.exception("Failed to save WeChat QR login")
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "status": "failed",
                    "message": "WeChat QR login failed.",
                }
            self._sessions.pop(session_id, None)
            await self._close_channel(session.channel)
            return {
                "session_id": session_id,
                "instance_id": session.instance_id,
                "status": "succeeded",
                "pairing_required": True,
                "message": "WeChat is connected.",
                "account": str(status_payload.get("ilink_user_id", "") or ""),
            }

        if status == "scaned_but_redirect":
            redirect_host = str(status_payload.get("redirect_host", "") or "").strip()
            if redirect_host:
                session.current_poll_base_url = (
                    redirect_host
                    if redirect_host.startswith(("http://", "https://"))
                    else f"https://{redirect_host}"
                )
            return self._pending_payload(session)

        if status == "need_verifycode":
            return self._pending_payload(
                session,
                challenge="verify_code",
                message=(
                    "That verification code did not match. Enter the new number shown in WeChat."
                    if verify_code
                    else "Enter the number shown in WeChat to continue."
                ),
                verification_failed=bool(verify_code),
            )

        if status == "verify_code_blocked":
            session.refresh_count += 1
            if session.refresh_count > MAX_QR_REFRESH_COUNT:
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "status": "failed",
                    "message": "Too many incorrect verification attempts. Try again later.",
                }
            try:
                session.qrcode_id, session.qr_url = (
                    await session.channel.connect_fetch_qr_code(force=session.force)
                )
            except Exception:
                logger.exception("Failed to refresh WeChat QR code")
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "status": "failed",
                    "message": "Could not refresh WeChat QR code.",
                }
            session.current_poll_base_url = session.channel.connect_base_url
            return self._pending_payload(
                session,
                message="Verification was blocked. Scan the refreshed QR code to try again.",
            )

        if status == "binded_redirect":
            if session.force:
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "status": "failed",
                    "message": (
                        "Unable to complete a new WeChat login. "
                        "Start again and scan with the account you want to connect."
                    ),
                }
            try:
                local_state_present = session.channel.connect_load_state()
            except Exception:
                logger.exception("Failed to load WeChat QR login state")
                local_state_present = False
            if not local_state_present:
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "status": "failed",
                    "message": (
                        "WeChat reports an existing binding, but no local credentials were found."
                    ),
                }
            self._sessions.pop(session_id, None)
            await self._close_channel(session.channel)
            return {
                "session_id": session_id,
                "instance_id": session.instance_id,
                "status": "succeeded",
                "pairing_required": True,
                "message": "WeChat is already connected to this nanobot instance.",
            }

        if status == "expired":
            session.refresh_count += 1
            if session.refresh_count > MAX_QR_REFRESH_COUNT:
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "status": "expired",
                    "message": "This WeChat QR code expired. Start again.",
                }
            try:
                session.qrcode_id, session.qr_url = (
                    await session.channel.connect_fetch_qr_code(force=session.force)
                )
            except Exception:
                logger.exception("Failed to refresh WeChat QR code")
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "status": "failed",
                    "message": "Could not refresh WeChat QR code.",
                }
            session.current_poll_base_url = session.channel.connect_base_url
            return self._pending_payload(session)

        return self._pending_payload(session)

    async def cancel(
        self, session_id: str, *, actor_user_id: str | None = None
    ) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is not None:
            _require_session_actor(session, actor_user_id)
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await self._close_channel(session.channel)
        return {
            "session_id": session_id,
            **({"instance_id": session.instance_id} if session is not None else {}),
            "status": "cancelled",
            "message": "WeChat login cancelled.",
        }

    async def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now >= session.deadline
        ]
        for session_id in expired:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                await self._close_channel(session.channel)

    @staticmethod
    def _build_channel(instance_id: str) -> WeixinChannel:
        from nanobot.bus.queue import MessageBus
        from nanobot.channels.weixin.instances import (
            upsert_weixin_instance,
            weixin_default_config,
            weixin_instance_specs,
        )
        from nanobot.channels.weixin.runtime import WeixinChannel

        section = getattr(load_config().channels, "weixin", None)
        canonical = upsert_weixin_instance(
            section, weixin_default_config(), instance_id, {}
        )
        selected = next(
            spec
            for spec in weixin_instance_specs(
                canonical, weixin_default_config(), enabled_only=False
            )
            if spec.instance_id == instance_id
        )
        return WeixinChannel(selected.config, MessageBus())

    @staticmethod
    async def _close_channel(channel: WeixinChannel) -> None:
        try:
            await channel.connect_close_client()
        except Exception:
            logger.exception("Failed to close WeChat QR login client")

    @staticmethod
    def _start_payload(session: WeixinConnectSession) -> dict[str, Any]:
        return {
            "session_id": session.id,
            "instance_id": session.instance_id,
            "status": "pending",
            "qr_url": session.qr_url,
            "interval_ms": 2000,
            "expires_at_ms": int((session.created_wall + 600) * 1000),
            "message": "Scan with WeChat to connect.",
        }

    @staticmethod
    def _pending_payload(
        session: WeixinConnectSession,
        *,
        challenge: str = "",
        message: str = "Waiting for WeChat scan.",
        verification_failed: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": session.id,
            "instance_id": session.instance_id,
            "status": "pending",
            "qr_url": session.qr_url,
            "interval_ms": 2000,
            "expires_at_ms": int((session.created_wall + 600) * 1000),
            "message": message,
        }
        if challenge:
            payload["challenge"] = challenge
            payload["verification_failed"] = verification_failed
        return payload

def _require_session_actor(
    session: WeixinConnectSession, actor_user_id: str | None
) -> None:
    if session.actor_user_id != actor_user_id:
        raise ChannelConnectError("WeChat connection belongs to another user", status=403)


__all__ = ["WeixinConnectStore"]
