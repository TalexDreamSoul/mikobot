"""Base channel interface for chat platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.collaboration.pairing import BOT_PROJECT_ROUTE_REQUIRED_METADATA_KEY
from nanobot.pairing import (
    PAIRING_CODE_META_KEY,
    format_pairing_reply,
    generate_code,
    is_approved,
)


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the nanobot message bus.
    """

    name: str = "base"
    display_name: str = "Base"
    send_progress: bool = True
    send_tool_hints: bool = True
    show_reasoning: bool = True

    def __init__(self, config: Any, bus: MessageBus):
        """Initialize channel configuration, transport, and optional assignment pairing."""
        self.config = config
        self.logger = logger.bind(channel=self.name)
        self.bus = bus
        self.assignment_pairing_handler: (
            Callable[[str, str], Awaitable[bool]] | None
        ) = None
        self.assignment_authorization_handler: (
            Callable[[str], Awaitable[bool]] | None
        ) = None
        self.assignment_conversation_authorization_handler: (
            Callable[[str, str], Awaitable[bool]] | None
        ) = None
        self.require_assignment_authorization = False
        self._running = False

    async def transcribe_audio(self, file_path: str | Path) -> str:
        """Transcribe an audio file via Whisper (OpenAI or Groq). Returns empty string on failure."""
        try:
            from nanobot.audio.transcription import (
                resolve_transcription_config,
                transcribe_audio_file,
            )
            from nanobot.config.loader import load_config

            return await transcribe_audio_file(file_path, resolve_transcription_config(load_config()))
        except Exception:
            self.logger.exception("Audio transcription failed")
            return ""

    async def login(self, force: bool = False) -> bool:
        """
        Perform channel-specific interactive login (e.g. QR code scan).

        Args:
            force: If True, ignore existing credentials and force re-authentication.

        Returns True if already authenticated or login succeeds.
        Override in subclasses that support interactive login.
        """
        return True

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Args:
            msg: The message to send.

        Implementations should raise on delivery failure so the channel manager
        can apply any retry policy in one place.
        """
        pass

    def progress_transport_defaults(self) -> tuple[bool, bool] | None:
        """Return channel-owned defaults for progress and tool-hint messages.

        ``None`` keeps the global channel policy. Channels should override this
        only when their transport requires different defaults.
        """
        return None

    def should_retry_send_error(self, error: Exception) -> bool:
        """Return whether the channel manager may retry a failed delivery.

        Channels with protocol-level business errors can override this hook to
        prevent retries that cannot succeed until external state changes.
        Transport and unexpected errors remain retryable by default.
        """
        return True

    def start_error_message(self, error: Exception) -> str | None:
        """Return an actionable public message for a channel startup failure.

        Channel-specific exception handling stays in the owning channel. Returning
        ``None`` keeps the manager's generic fallback.
        """
        return None

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
        stream_end: bool = False,
        resuming: bool = False,
        merge_next: bool = False,
    ) -> None:
        """Deliver a streaming text chunk.

        Override in subclasses to enable streaming. Implementations should
        raise on delivery failure so the channel manager can retry.

        Stateful implementations should key buffers by ``stream_id`` rather
        than only by ``chat_id`` when it is provided.

        ``merge_next`` marks a resumable provider boundary whose next text
        segment belongs to the same user-visible message.
        """
        pass

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
    ) -> None:
        """Stream a chunk of model reasoning/thinking content.

        Default is no-op. Channels with a native low-emphasis primitive
        (Slack context block, Telegram expandable blockquote, Discord
        subtext, WebUI italic bubble, ...) override to render reasoning
        as a subordinate trace that updates in place as the model thinks.

        Streaming contract mirrors :meth:`send_delta`: stateful implementations
        should key buffers by ``stream_id`` rather than only by ``chat_id``.
        """
        return

    async def send_reasoning_end(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
    ) -> None:
        """Mark the end of a reasoning stream segment.

        Default is no-op. Channels that buffer ``send_reasoning_delta``
        chunks for in-place updates use this signal to flush and freeze
        the rendered group; one-shot channels can ignore it entirely.
        """
        return

    async def send_file_edit_events(
        self,
        chat_id: str,
        edits: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Deliver structured live file-edit events.

        Default is no-op. Channels with a rich activity surface can override
        this to render editing progress without receiving empty text messages.
        """
        return

    async def send_reasoning(self, msg: OutboundMessage) -> None:
        """Deliver a complete reasoning block.

        Default implementation reuses the streaming pair so plugins only
        need to override the delta/end methods. Equivalent to one delta
        with the full content followed immediately by an end marker —
        keeps a single rendering path for both streamed and one-shot
        reasoning (e.g. DeepSeek-R1's final-response ``reasoning_content``).
        """
        if not msg.content:
            return
        stream_id = getattr(msg.event, "stream_id", None)
        await self.send_reasoning_delta(
            msg.chat_id,
            msg.content,
            msg.metadata,
            stream_id=stream_id,
        )
        await self.send_reasoning_end(
            msg.chat_id,
            msg.metadata,
            stream_id=stream_id,
        )

    @property
    def supports_streaming(self) -> bool:
        """True when config enables streaming AND this subclass implements send_delta."""
        cfg = self.config
        config_mapping = cast(dict[str, Any], cfg) if isinstance(cfg, dict) else None
        streaming: Any = (
            config_mapping.get("streaming", False)
            if config_mapping is not None
            else getattr(cast(Any, cfg), "streaming", False)
        )
        return bool(streaming) and type(self).send_delta is not BaseChannel.send_delta

    def is_allowed(self, sender_id: str) -> bool:
        """Check sender permission: star > allowlist > pairing store > deny."""
        if isinstance(self.config, dict):
            config_mapping = cast(dict[str, Any], self.config)
            allow_list: Any = (
                config_mapping.get("allow_from") or config_mapping.get("allowFrom") or []
            )
        else:
            allow_list = getattr(self.config, "allow_from", None) or []
        if "*" in allow_list:
            return True
        # allowFrom entries are opaque tokens — must match exactly.
        if str(sender_id) in allow_list:
            return True
        if is_approved(self.name, str(sender_id)):
            return True
        return False

    async def _consume_assignment_pairing(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        *,
        is_dm: bool,
    ) -> bool:
        pairing_handler = self.assignment_pairing_handler
        if pairing_handler is None or not is_dm:
            return False
        try:
            assignment_verified = await pairing_handler(content, str(sender_id))
        except Exception:
            self.logger.exception("Assignment Pair Code verification failed")
            return True
        if not assignment_verified:
            return False
        await self.send(
            OutboundMessage(
                channel=self.name,
                chat_id=str(chat_id),
                content="Pair Code verified. Return to the WebUI to finish the assignment.",
            )
        )
        return True

    async def _assignment_sender_is_authorized(self, sender_id: str) -> bool:
        authorization_handler = self.assignment_authorization_handler
        if authorization_handler is None:
            return False
        try:
            return await authorization_handler(str(sender_id))
        except Exception:
            self.logger.exception("Channel assignment authorization failed")
            return False


    async def _assignment_conversation_is_authorized(
        self, sender_id: str, conversation_id: str
    ) -> bool:
        authorization_handler = self.assignment_conversation_authorization_handler
        if authorization_handler is None:
            return False
        try:
            return await authorization_handler(str(sender_id), str(conversation_id))
        except Exception:
            self.logger.exception("Channel conversation assignment authorization failed")
            return False
    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
        is_dm: bool = False,
        authorization_id: str | None = None,
        require_existing_session: bool = False,
    ) -> None:
        """Handle a message after checking its authorization subject.

        ``sender_id`` is the identity recorded on the inbound message.  Channels
        where access is scoped to another entity (for example, a group or room)
        can pass that entity as ``authorization_id`` without changing the
        sender's identity.  When omitted, authorization remains sender-based.
        """
        if await self._consume_assignment_pairing(
            sender_id, chat_id, content, is_dm=is_dm
        ):
            return
        permission_id = authorization_id if authorization_id is not None else sender_id
        assignment_allowed = await self._assignment_sender_is_authorized(str(sender_id))
        conversation_allowed = True
        if self.require_assignment_authorization and authorization_id is not None:
            conversation_allowed = await self._assignment_conversation_is_authorized(
                str(sender_id), str(authorization_id)
            )
        if self.require_assignment_authorization:
            permission_granted = (
                conversation_allowed if authorization_id is not None else assignment_allowed
            )
        else:
            permission_granted = assignment_allowed or self.is_allowed(permission_id)
        if not permission_granted:
            if is_dm:
                try:
                    code = generate_code(self.name, str(sender_id))
                except OSError:
                    # Transient pairing-store I/O failure: skip the pairing
                    # reply for this message rather than crash the handler.
                    self.logger.warning(
                        "Pairing store unavailable; dropping DM from {}", sender_id
                    )
                    return
                await self.send(
                    OutboundMessage(
                        channel=self.name,
                        chat_id=str(chat_id),
                        content=format_pairing_reply(code),
                        metadata={PAIRING_CODE_META_KEY: code},
                    )
                )
                self.logger.info(
                    "Sent pairing code {} to sender {} in chat {}",
                    code, sender_id, chat_id,
                )
            else:
                self.logger.warning(
                    "Access denied for sender {}. "
                    "Add them to allowFrom list in config to grant access.",
                    sender_id,
                )
            return

        meta = dict(metadata or {})
        if is_dm:
            meta["is_direct"] = True
        if self.require_assignment_authorization:
            meta[BOT_PROJECT_ROUTE_REQUIRED_METADATA_KEY] = True
        if self.supports_streaming:
            meta["_wants_stream"] = True

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=meta,
            session_key_override=session_key,
            require_existing_session=require_existing_session,
        )

        await self.bus.publish_inbound(msg)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """Return default config for onboard. Override in plugins to auto-populate config.json."""
        return {"enabled": False}

    @classmethod
    def refresh_feature_metadata(
        cls,
        config_path: Path,
        *,
        instance_id: str = "default",
    ) -> bool:
        """Refresh persisted display metadata after an explicit settings action."""
        return False

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
