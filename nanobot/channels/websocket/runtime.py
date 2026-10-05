"""WebSocket server channel: nanobot acts as a WebSocket server and serves connected clients."""

from __future__ import annotations

import asyncio
import errno
import inspect
import ipaddress
import json
import socket
import ssl
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self, TypeGuard, cast
from urllib.parse import urlsplit, urlunsplit
from weakref import WeakSet

from pydantic import Field, PrivateAttr, field_validator, model_validator
from websockets.asyncio.server import Server, ServerConnection, serve, unix_serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request as WsRequest

from nanobot.bus.events import (
    OUTBOUND_META_AGENT_UI,
    OutboundMessage,
)
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.collaboration.models import COLLABORATION_USER_METADATA_KEY
from nanobot.config.schema import Base
from nanobot.session.webui_turns import (
    clear_websocket_turn_if_current,
    mark_websocket_turn_transcript_persistence_failed,
    websocket_turn_transcript_persistence_failed,
)
from nanobot.webui.gateway_services import GatewayServices
from nanobot.webui.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from nanobot.webui.http_utils import (
    parse_request_path as _parse_request_path,
)
from nanobot.webui.http_utils import (
    query_first as _query_first,
)
from nanobot.webui.inbound_commands import WebUICommandRouter
from nanobot.webui.metadata import (
    WEBSOCKET_TURN_OWNER_METADATA_KEY,
    WEBUI_TURN_METADATA_KEY,
)
from nanobot.webui.outbound_projection import WebUIOutboundProjector
from nanobot.webui.outbound_wire import (
    WebUIWirePayload,
    WebUIWirePersistence,
    project_tool_events,
)
from nanobot.webui.session_identity import is_valid_webui_chat_id, webui_session_key
from nanobot.webui.transcript import WEBUI_TRANSCRIPT_INCOMPLETE_KEY
from nanobot.webui.websocket_logging import websockets_server_logger

if TYPE_CHECKING:
    from nanobot.bus.outbound_events import ProgressEvent

# Plain HTTP WebUI routes also run through websockets.process_request.
_WEBUI_HTTP_OPEN_TIMEOUT_S = 360.0
_LISTENER_CHECK_INTERVAL_S = 0.5
_LISTENER_STABLE_AFTER_S = 30.0
_LISTENER_RESTART_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)

# Outbound delivery is isolated per connection.  A bounded queue keeps a slow
# or suspended terminal from retaining an unbounded stream in server memory.
_OUTBOUND_QUEUE_MAX_FRAMES = 256
_OUTBOUND_QUEUE_MAX_BYTES = 8 * 1024 * 1024
_OUTBOUND_SEND_TIMEOUT_S = 10.0
_OUTBOUND_CLOSE_TIMEOUT_S = 1.0

# A bind conflict or invalid address needs operator action and must not be
# retried forever. These errors can be caused by a transient local network
# interruption and are safe to retry at the channel boundary.
_RECOVERABLE_LISTENER_ERRNOS = {
    getattr(socket, name)
    for name in (
        "ECONNABORTED",
        "ECONNRESET",
        "EHOSTDOWN",
        "EHOSTUNREACH",
        "ENETDOWN",
        "ENETRESET",
        "ENETUNREACH",
        "ETIMEDOUT",
    )
    if hasattr(socket, name)
}
_RECOVERABLE_LISTENER_WINERRORS = {
    64,  # ERROR_NETNAME_DELETED / "The specified network name is no longer available."
    995,  # ERROR_OPERATION_ABORTED
    10050,  # WSAENETDOWN
    10052,  # WSAENETRESET
    10053,  # WSAECONNABORTED
    10054,  # WSAECONNRESET
    10060,  # WSAETIMEDOUT
    10065,  # WSAEHOSTUNREACH
}


_ROUTING_ASSERTION_HEADERS = frozenset(
    {
        "host",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "cf-connecting-ip",
    }
)

async def _await_if_needed(value: object) -> None:
    """Await lifecycle hooks while tolerating legacy synchronous gateway adapters."""
    if inspect.isawaitable(value):
        await value


def _is_routing_assertion_header(value: str) -> bool:
    normalized = value.casefold()
    return normalized in _ROUTING_ASSERTION_HEADERS or normalized.startswith("x-forwarded-")


class TrustedProxyAuthConfig(Base):
    """Authentication assertions accepted from explicitly trusted proxy peers."""

    trusted_peer_cidrs: list[str] = Field(min_length=1)
    assertion_header: str = Field(min_length=1)
    # When configured, this proxy-generated header supplies a stable upstream
    # subject. Its value is only used to derive an opaque sender identity.
    subject_header: str = ""
    _trusted_peer_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = PrivateAttr(
        default=()
    )

    @field_validator("trusted_peer_cidrs")
    @classmethod
    def validate_trusted_peer_cidrs(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            value = value.strip()
            try:
                network = ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid trusted proxy CIDR: {value!r}") from exc
            if network.prefixlen == 0:
                raise ValueError("universal trusted proxy CIDRs are not allowed")
            if isinstance(network, ipaddress.IPv6Network):
                mapped_start = ipaddress.IPv6Address("::ffff:0:0")
                mapped_end = ipaddress.IPv6Address("::ffff:ffff:ffff")
                if mapped_start in network and mapped_end in network:
                    raise ValueError("trusted proxy CIDRs must not cover all IPv4-mapped addresses")
            normalized.append(network.with_prefixlen)
        return normalized

    @field_validator("assertion_header")
    @classmethod
    def validate_assertion_header(cls, value: str) -> str:
        value = value.strip()
        if not value or any(char.isspace() or ord(char) < 0x21 for char in value):
            raise ValueError("assertion_header must be a valid HTTP header name")
        if _is_routing_assertion_header(value):
            raise ValueError(
                "assertion_header must identify a proxy-generated authentication assertion, "
                "not a routing or client metadata header"
            )
        return value

    @field_validator("subject_header")
    @classmethod
    def validate_subject_header(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if any(char.isspace() or ord(char) < 0x21 for char in value):
            raise ValueError("subject_header must be a valid HTTP header name")
        if _is_routing_assertion_header(value):
            raise ValueError(
                "subject_header must identify a proxy-generated authentication subject, "
                "not a routing or client metadata header"
            )
        return value

    @model_validator(mode="after")
    def subject_header_is_distinct_from_credentials(self) -> Self:
        if not self.subject_header:
            return self
        subject_header = self.subject_header.casefold()
        if subject_header in {"authorization", "cookie", self.assertion_header.casefold()}:
            raise ValueError(
                "subject_header must differ from Authorization, Cookie, and assertion_header"
            )
        return self

    @model_validator(mode="after")
    def compile_trusted_peer_networks(self) -> Self:
        self._trusted_peer_networks = tuple(
            ipaddress.ip_network(value, strict=False) for value in self.trusted_peer_cidrs
        )
        return self





class OidcAuthConfig(Base):
    """OIDC Authorization Code + PKCE settings for the embedded WebUI."""

    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    admin_subjects: list[str] = Field(default_factory=list)
    token_endpoint_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "none"
    session_ttl_s: int = Field(default=3600, ge=60, le=86_400)
    flow_ttl_s: int = Field(default=300, ge=30, le=600)
    session_capacity: int = Field(default=1_000, ge=1, le=10_000)
    flow_capacity: int = Field(default=1_000, ge=1, le=10_000)

    @field_validator("issuer", "redirect_uri")
    @classmethod
    def secure_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme not in {"https", "http"} or not parsed.netloc
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment):
            raise ValueError("OIDC URLs must be absolute HTTP(S) URLs without credentials or fragments")
        if parsed.scheme == "http":
            try:
                loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise ValueError("OIDC HTTP URLs are allowed only for loopback hosts")
        return value

    @field_validator("issuer")
    @classmethod
    def issuer_has_no_query(cls, value: str) -> str:
        if value and urlsplit(value).query:
            raise ValueError("OIDC issuer URL must not contain a query")
        return value

    @field_validator("client_id", "client_secret")
    @classmethod
    def strip_client_credentials(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 4_096 or any(ord(char) < 0x21 for char in value):
            raise ValueError("OIDC client credentials must be bounded visible text")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            scope = value.strip()
            if not scope or len(scope) > 128 or any(char.isspace() or ord(char) < 0x21 for char in scope):
                raise ValueError("OIDC scopes must be bounded tokens")
            if scope not in normalized:
                normalized.append(scope)
        if "openid" not in normalized:
            raise ValueError("OIDC scopes must include openid")
        return normalized

    @field_validator("admin_subjects")
    @classmethod
    def validate_admin_subjects(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            subject = value.strip()
            if (
                not subject
                or len(subject) > 512
                or any(ord(character) < 0x21 for character in subject)
            ):
                raise ValueError("OIDC admin subjects must be bounded visible text")
            if subject not in normalized:
                normalized.append(subject)
        return normalized

    @model_validator(mode="after")
    def enabled_fields_are_complete(self) -> Self:
        if not self.enabled:
            return self
        if not self.issuer or not self.client_id or not self.redirect_uri:
            raise ValueError("enabled OIDC requires issuer, client_id, and redirect_uri")
        if self.token_endpoint_auth_method == "none":
            if self.client_secret:
                raise ValueError("OIDC public clients must not configure client_secret")
        elif not self.client_secret:
            raise ValueError("confidential OIDC clients require client_secret")
        return self


class WebSocketConfig(Base):
    """WebSocket server configuration and embedded WebUI authentication settings."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    unix_socket_path: str = ""
    path: str = "/"
    public_ws_url: str = ""
    token: str = ""
    token_issue_path: str = ""
    token_issue_secret: str = ""
    trusted_proxy_auth: TrustedProxyAuthConfig | None = None
    oidc_auth: OidcAuthConfig = Field(default_factory=OidcAuthConfig)
    token_ttl_s: int = Field(default=300, ge=30, le=86_400)
    websocket_requires_token: bool = True
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    streaming: bool = True
    max_message_bytes: int = Field(default=37_748_736, ge=1024, le=41_943_040)
    ping_interval_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ping_timeout_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    @field_validator("unix_socket_path")
    @classmethod
    def unix_socket_path_format(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if "\x00" in value:
            raise ValueError("unix_socket_path must not contain NUL bytes")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("unix_socket_path must be an absolute path")
        return str(path)

    @field_validator("path")
    @classmethod
    def path_must_start_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError('path must start with "/"')
        return _normalize_config_path(value)

    @field_validator("token_issue_path")
    @classmethod
    def token_issue_path_format(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if not value.startswith("/"):
            raise ValueError('token_issue_path must start with "/"')
        return _normalize_config_path(value)

    @field_validator("public_ws_url")
    @classmethod
    def public_ws_url_format(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        parsed = urlsplit(value)
        if (parsed.scheme not in {"ws", "wss"} or not parsed.netloc
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError("public_ws_url must be an absolute ws:// or wss:// URL without credentials")
        return urlunsplit((parsed.scheme, parsed.netloc, _normalize_config_path(parsed.path or "/"), "", ""))

    @model_validator(mode="after")
    def public_ws_url_matches_path(self) -> Self:
        if self.public_ws_url and urlsplit(self.public_ws_url).path != _normalize_config_path(self.path):
            raise ValueError("public_ws_url path must match path")
        return self

    @model_validator(mode="after")
    def token_issue_path_differs_from_ws_path(self) -> Self:
        if self.token_issue_path and _normalize_config_path(self.token_issue_path) == _normalize_config_path(self.path):
            raise ValueError("token_issue_path must differ from path (the WebSocket upgrade path)")
        return self

    @model_validator(mode="after")
    def wildcard_host_requires_auth(self) -> Self:
        if self.host not in ("0.0.0.0", "::"):
            return self
        if self.token.strip() or self.token_issue_secret.strip() or self.trusted_proxy_auth is not None or self.oidc_auth.enabled:
            return self
        raise ValueError(
            "host is 0.0.0.0 (all interfaces) but neither token, token_issue_secret, "
            "trusted_proxy_auth, nor enabled oidc_auth is set — set one to prevent unauthenticated access"
        )


def _parse_inbound_payload(raw: str) -> str | None:
    """Parse a client frame into text; return None for empty or unrecognized content."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = cast(object, json.loads(text))
        except json.JSONDecodeError:
            return text
        if isinstance(data, dict):
            payload = cast(dict[str, Any], data)
            for key in ("content", "text", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return None
        return None
    return text


def _is_valid_chat_id(value: Any) -> TypeGuard[str]:  # pyright: ignore[reportUnusedFunction]
    return is_valid_webui_chat_id(value)


def _parse_envelope(raw: str) -> dict[str, Any] | None:
    """Return a typed envelope dict if the frame is a new-style JSON envelope, else None.

    A frame qualifies when it parses as a JSON object with a string ``type`` field.
    Legacy frames (plain text, or ``{"content": ...}`` without ``type``) return None;
    callers should fall back to :func:`_parse_inbound_payload` for those.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        data = cast(object, json.loads(text))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    envelope = cast(dict[str, Any], data)
    t = envelope.get("type")
    if not isinstance(t, str):
        return None
    return envelope


class _ListenerUnavailableError(OSError):
    """Raised when a previously bound listener loses its serving socket."""


@dataclass(slots=True)
class _OutboundFrame:
    raw: str
    utf8_bytes: int
    label: str


@dataclass(slots=True)
class _ConnectionOutbound:
    queue: asyncio.Queue[_OutboundFrame]
    buffered_bytes: int = 0
    writer: asyncio.Task[None] | None = None
    closing: bool = False


class WebSocketChannel(BaseChannel):
    """Run a local WebSocket server; forward text/JSON messages to the message bus."""

    name = "websocket"
    display_name = "WebSocket"

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        gateway: GatewayServices,
    ):
        if isinstance(config, dict):
            config = WebSocketConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: WebSocketConfig = config
        # chat_id -> connections subscribed to it (fan-out target).
        self._subs: dict[str, set[ServerConnection]] = {}
        # connection -> chat_ids it is subscribed to (O(1) cleanup on disconnect).
        self._conn_chats: dict[ServerConnection, set[str]] = {}
        # connection -> default chat_id for legacy frames that omit routing.
        self._conn_default: dict[ServerConnection, str] = {}
        # Connections authenticated with a one-time token from /webui/bootstrap.
        self._webui_connections = gateway.endpoint.webui_connections
        self._stop_event: asyncio.Event | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._server: Server | None = None
        self._connection_outbound: dict[ServerConnection, _ConnectionOutbound] = {}
        self._outbound_retire_tasks: set[asyncio.Task[None]] = set()
        self._retired_connections: WeakSet[ServerConnection] = WeakSet()

        self.gateway = gateway
        self._media = gateway.media
        self._transcripts = gateway.transcripts
        self._temporary_chats = gateway.temporary_chats
        self._session_projection = gateway.session_projection
        self._commands = WebUICommandRouter(self, gateway)
        self._webui_request_tasks = self._commands.request_tasks
        self._webui_request_operations = self._commands.request_operations
        self._webui_request_locks = self._commands.request_locks
        self._outbound = WebUIOutboundProjector(self, self._session_projection)

        self._stream_text_buffers: dict[tuple[str, str], list[str]] = {}
        self._reasoning_text_buffers: dict[tuple[str, str], list[str]] = {}

    # -- Subscription bookkeeping -------------------------------------------

    def webui_subscribers(self, chat_id: str) -> tuple[ServerConnection, ...]:
        """Return a stable snapshot of one chat's transport subscribers."""
        return tuple(self._subs.get(chat_id, ()))

    def webui_connection_chats(self, connection: ServerConnection) -> tuple[str, ...]:
        return tuple(self._conn_chats.get(connection, ()))

    def webui_attach(self, connection: ServerConnection, chat_id: str) -> None:
        self._attach(connection, chat_id)

    def webui_detach(self, connection: ServerConnection, chat_id: str) -> None:
        self._detach(connection, chat_id)

    def webui_clear_connection_default(self, connection: ServerConnection) -> None:
        self._conn_default.pop(connection, None)

    def webui_clear_stream_buffers(self, chat_id: str) -> None:
        self._clear_stream_buffers(chat_id)

    async def webui_hydrate(self, chat_id: str) -> None:
        await self._hydrate_after_subscribe(chat_id)

    async def webui_send_event(
        self,
        connection: ServerConnection,
        event: str,
        **fields: Any,
    ) -> None:
        await self._send_event(connection, event, **fields)

    async def webui_send_raw(
        self,
        connection: ServerConnection,
        raw: str,
        *,
        label: str = "",
    ) -> None:
        await self._safe_send_to(connection, raw, label=label)

    async def webui_dispatch_message(
        self,
        *,
        sender_id: str,
        authorization_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None,
        metadata: dict[str, Any],
        is_dm: bool,
        session_key: str | None,
        require_existing_session: bool,
    ) -> None:
        await self._handle_message(
            sender_id=sender_id,
            authorization_id=authorization_id,
            chat_id=chat_id,
            content=content,
            media=media,
            metadata=metadata,
            is_dm=is_dm,
            session_key=session_key,
            require_existing_session=require_existing_session,
        )

    def _attach(self, connection: ServerConnection, chat_id: str) -> None:
        """Idempotently subscribe *connection* to *chat_id*."""
        if not self._register_connection_outbound(connection):
            return
        self._subs.setdefault(chat_id, set()).add(connection)
        self._conn_chats.setdefault(connection, set()).add(chat_id)

    def _register_connection_outbound(self, connection: ServerConnection) -> bool:
        if connection in self._retired_connections:
            return False
        self._connection_outbound.setdefault(
            connection,
            _ConnectionOutbound(asyncio.Queue(maxsize=_OUTBOUND_QUEUE_MAX_FRAMES)),
        )
        return True

    def _detach(self, connection: ServerConnection, chat_id: str) -> None:
        chats = self._conn_chats.get(connection)
        if chats is not None:
            chats.discard(chat_id)
            if not chats:
                self._conn_chats.pop(connection, None)
        subscribers = self._subs.get(chat_id)
        if subscribers is not None:
            subscribers.discard(connection)
            if not subscribers:
                self._subs.pop(chat_id, None)

    def _clear_stream_buffers(self, chat_id: str) -> None:
        for key in tuple(self._stream_text_buffers):
            if key[0] == chat_id:
                self._stream_text_buffers.pop(key, None)
        for key in tuple(self._reasoning_text_buffers):
            if key[0] == chat_id:
                self._reasoning_text_buffers.pop(key, None)

    async def _cleanup_connection(self, connection: ServerConnection) -> None:
        """Remove *connection* from every subscription set; safe to call multiple times."""
        self._retired_connections.add(connection)
        state = self._connection_outbound.get(connection)
        if state is not None:
            state.closing = True
            await self._stop_connection_writer(state)
        try:
            await self._commands.cleanup_connection(connection)
        finally:
            for chat_id in tuple(self._conn_chats.get(connection, ())):
                self._detach(connection, chat_id)
            self._conn_default.pop(connection, None)
            self.gateway.endpoint.discard_connection(connection)
            if self._connection_outbound.get(connection) is state:
                self._connection_outbound.pop(connection, None)

    async def _hydrate_after_subscribe(self, chat_id: str) -> None:
        """Replay persisted or actively running per-chat state after subscribe."""
        await self._outbound.hydrate(chat_id)

    async def _send_event(
        self,
        connection: ServerConnection,
        event: str,
        **fields: Any,
    ) -> None:
        """Send a control event (attached, error, ...) to a single connection."""
        payload: dict[str, Any] = {"event": event}
        payload.update(fields)
        raw = json.dumps(payload, ensure_ascii=False)
        await self._safe_send_to(connection, raw, label=f" {event} ")

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WebSocketConfig().model_dump(by_alias=True)

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        cert = self.config.ssl_certfile.strip()
        key = self.config.ssl_keyfile.strip()
        if not cert and not key:
            return None
        if not cert or not key:
            raise ValueError(
                "ssl_certfile and ssl_keyfile must both be set for WSS, or both left empty"
            )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        return ctx

    # -- HTTP dispatch ------------------------------------------------------

    async def _dispatch_http(self, connection: ServerConnection, request: WsRequest) -> Any:
        """Compatibility proxy for the externally composed gateway endpoint."""
        return await self.gateway.endpoint.process_request(
            connection,
            request,
            is_allowed=self.is_allowed,
        )

    def _authorize_websocket_handshake(
        self,
        connection: ServerConnection,
        query: dict[str, list[str]],
        headers: Any = None,
    ) -> Any:
        """Compatibility proxy for handshake tests and integrations."""
        return self.gateway.endpoint.authorize_websocket_handshake(
            connection,
            query,
            headers,
        )

    # -- Server lifecycle and connection ingress ---------------------------

    @staticmethod
    def _socket_is_accepting(sock: socket.socket) -> bool:
        """Return whether a bound socket still advertises a listen capability.

        ``SO_ACCEPTCONN`` is not portable: macOS/BSD raise ``OSError`` with
        ``ENOPROTOOPT`` ("Protocol not available") for this option even on a
        perfectly healthy listening socket. Treating that as "not serving"
        makes the listener look permanently degraded, so the caller retries
        forever and the channel never reaches a ready state. When the option
        is unavailable we fall back to the file-descriptor liveness check.
        """
        if sock.fileno() < 0:
            return False
        try:
            return bool(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN))
        except OSError as exc:
            if exc.errno in (errno.ENOPROTOOPT, errno.EOPNOTSUPP, errno.ENOTSUP):
                return True
            raise

    @classmethod
    def _listener_is_serving(cls, server: Server) -> bool:
        """Return whether every bound socket still has a live listen capability."""
        try:
            sockets = server.sockets
            return bool(sockets) and server.is_serving() and all(
                cls._socket_is_accepting(sock) for sock in sockets
            )
        except OSError:
            return False

    @staticmethod
    def _is_recoverable_listener_error(error: Exception, *, was_serving: bool) -> bool:
        if isinstance(error, _ListenerUnavailableError):
            return True
        if not isinstance(error, OSError):
            return False
        if was_serving:
            return True
        winerror = getattr(error, "winerror", None)
        return (
            error.errno in _RECOVERABLE_LISTENER_ERRNOS
            or winerror in _RECOVERABLE_LISTENER_WINERRORS
        )

    async def _wait_for_listener_loss(self, server: Server) -> None:
        """Wait for shutdown or raise when the serving socket disappears."""
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=_LISTENER_CHECK_INTERVAL_S,
                )
            except TimeoutError:
                if not self._listener_is_serving(server):
                    raise _ListenerUnavailableError(
                        "WebSocket listener is no longer accepting connections"
                    )

    async def _close_server(self, server: Server, socket_path: str) -> None:
        server.close()
        try:
            await server.wait_closed()
        except OSError as exc:
            self.logger.warning("WebSocket server close failed: {}", exc)
        if socket_path:
            with suppress(FileNotFoundError):
                Path(socket_path).unlink()

    def _log_listener_ready(self, scheme: str) -> None:
        self.logger.info(
            "WebSocket server listening on {}",
            (
                f"unix:{self.config.unix_socket_path}{self.config.path}"
                if self.config.unix_socket_path
                else f"{scheme}://{self.config.host}:{self.config.port}{self.config.path}"
            ),
        )
        if self.config.token_issue_path:
            self.logger.info(
                "WebSocket token issue route: {}",
                (
                    f"unix:{self.config.unix_socket_path}"
                    f"{_normalize_config_path(self.config.token_issue_path)}"
                    if self.config.unix_socket_path
                    else (
                        f"{scheme}://{self.config.host}:{self.config.port}"
                        f"{_normalize_config_path(self.config.token_issue_path)}"
                    )
                ),
            )

    async def start(self) -> None:
        await _await_if_needed(self.gateway.initialize())
        self.gateway.http.remote_instances.resume()
        from nanobot.utils.logging_bridge import redirect_lib_logging

        redirect_lib_logging("websockets", level="WARNING")
        ws_logger = websockets_server_logger()

        stop_event = asyncio.Event()
        self._stop_event = stop_event

        ssl_context = self._build_ssl_context()
        scheme = "wss" if ssl_context else "ws"

        async def process_request(
            connection: ServerConnection,
            request: WsRequest,
        ) -> Any:
            return await self._dispatch_http(connection, request)

        async def handler(connection: ServerConnection) -> None:
            await self._connection_loop(connection)

        async def runner() -> None:
            socket_path = self.config.unix_socket_path
            failures = 0
            while not stop_event.is_set():
                server: Server | None = None
                was_serving = False
                started_at = 0.0
                try:
                    if socket_path:
                        path_obj = Path(socket_path)
                        path_obj.parent.mkdir(parents=True, exist_ok=True)
                        with suppress(FileNotFoundError):
                            path_obj.unlink()
                        server = await unix_serve(
                            handler,
                            socket_path,
                            process_request=process_request,
                            open_timeout=_WEBUI_HTTP_OPEN_TIMEOUT_S,
                            max_size=self.config.max_message_bytes,
                            ping_interval=self.config.ping_interval_s,
                            ping_timeout=self.config.ping_timeout_s,
                            logger=ws_logger,
                        )
                        with suppress(OSError):
                            path_obj.chmod(0o600)
                    else:
                        server = await serve(
                            handler,
                            self.config.host,
                            self.config.port,
                            process_request=process_request,
                            open_timeout=_WEBUI_HTTP_OPEN_TIMEOUT_S,
                            max_size=self.config.max_message_bytes,
                            ping_interval=self.config.ping_interval_s,
                            ping_timeout=self.config.ping_timeout_s,
                            ssl=ssl_context,
                            logger=ws_logger,
                        )

                    self._server = server
                    was_serving = True
                    if not self._listener_is_serving(server):
                        raise _ListenerUnavailableError(
                            "WebSocket listener did not enter a serving state"
                        )
                    self._running = True
                    started_at = asyncio.get_running_loop().time()
                    self._log_listener_ready(scheme)
                    await self._wait_for_listener_loss(server)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._running = False
                    if not self._is_recoverable_listener_error(
                        exc,
                        was_serving=was_serving,
                    ):
                        raise
                    uptime = (
                        asyncio.get_running_loop().time() - started_at
                        if started_at
                        else 0.0
                    )
                    if uptime >= _LISTENER_STABLE_AFTER_S:
                        failures = 0
                    delay = _LISTENER_RESTART_BACKOFF_S[
                        min(failures, len(_LISTENER_RESTART_BACKOFF_S) - 1)
                    ]
                    failures += 1
                    self.logger.warning(
                        "WebSocket listener failed ({}: {}); retrying in {:.1f}s",
                        type(exc).__name__,
                        exc,
                        delay,
                    )
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                finally:
                    self._running = False
                    if server is not None:
                        await self._close_server(server, socket_path)
                    if self._server is server:
                        self._server = None

        task = asyncio.create_task(runner())
        self._server_task = task
        try:
            await task
        finally:
            self._running = False
            if self._server_task is task:
                self._server_task = None

    async def _connection_loop(self, connection: ServerConnection) -> None:
        self._retired_connections.discard(connection)
        request = connection.request
        path_part = request.path if request else "/"
        _, query = _parse_request_path(path_part)
        client_id_raw = _query_first(query, "client_id")
        client_id = client_id_raw.strip() if client_id_raw else ""
        if not client_id:
            client_id = f"anon-{uuid.uuid4().hex[:12]}"
        elif len(client_id) > 128:
            self.logger.warning("client_id too long ({} chars), truncating", len(client_id))
            client_id = client_id[:128]
        trusted_principal = self.gateway.endpoint.trusted_proxy_principal(connection)
        oidc_principal = self.gateway.endpoint.oidc_principal(connection)
        # The browser-controlled client_id remains the allowFrom subject. Trusted
        # proxy and OIDC verification supply durable opaque sender identities.
        principal_key = trusted_principal or oidc_principal
        sender_id = principal_key or client_id
        oidc_expiry_task = (
            asyncio.create_task(self.gateway.endpoint.close_oidc_at_deadline(connection))
            if oidc_principal is not None
            else None
        )

        default_chat_id = str(uuid.uuid4())
        from nanobot.webui.client_contract import gateway_identity

        try:
            if principal_key is not None and not await self.gateway.prepare_webui_session(
                connection, webui_session_key(default_chat_id)
            ):
                await connection.close(code=1008, reason="Project authorization is unavailable")
                return
            ready: dict[str, Any] = {
                "event": "ready",
                "chat_id": default_chat_id,
                "client_id": client_id,
            }
            if principal_key is not None:
                ready["principal"] = True
            if _query_first(query, "terminal_protocol") == "1":
                ready["terminal"] = gateway_identity(self.gateway.tokens.instance_id)
            await connection.send(json.dumps(ready, ensure_ascii=False))
            # Register only after ready is successfully sent to avoid out-of-order sends
            self._conn_default[connection] = default_chat_id
            self._attach(connection, default_chat_id)
            await self._hydrate_after_subscribe(default_chat_id)

            async for raw in connection:
                if oidc_principal is not None and not self.gateway.endpoint.oidc_connection_active(connection):
                    await self.gateway.endpoint.close_oidc_connection(connection, "OIDC credential expired")
                    break
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        self.logger.warning("ignoring non-utf8 binary frame")
                        continue

                envelope = _parse_envelope(raw)
                if envelope is not None:
                    await self._dispatch_envelope(
                        connection,
                        client_id,
                        envelope,
                        trusted_principal=principal_key,
                    )
                    continue

                content = _parse_inbound_payload(raw)
                if content is None:
                    continue
                if principal_key is not None and not await self.gateway.can_access_webui_session(
                    connection, webui_session_key(default_chat_id)
                ):
                    await self._send_event(
                        connection, "error", detail="session_not_found", chat_id=default_chat_id
                    )
                    continue
                # WebSocket already authenticates at handshake time (token),
                # so pairing is not applicable. Treat as non-DM to avoid
                # sending pairing codes to an already-authenticated client.
                await self._handle_message(
                    sender_id=sender_id,
                    authorization_id=client_id,
                    chat_id=default_chat_id,
                    content=content,
                    metadata={"remote": getattr(connection, "remote_address", None)},
                    is_dm=False,
                )
        except Exception as e:
            self.logger.debug("connection ended: {}", e)
        finally:
            if oidc_expiry_task is not None:
                oidc_expiry_task.cancel()
                with suppress(asyncio.CancelledError):
                    await oidc_expiry_task
            await self._cleanup_connection(connection)

    # -- Inbound WebSocket envelopes ---------------------------------------

    async def _dispatch_envelope(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
        *,
        trusted_principal: str | None = None,
    ) -> None:
        """Delegate one typed envelope to the WebUI application router."""
        if not self._register_connection_outbound(connection):
            return
        await self._commands.dispatch(
            connection,
            client_id,
            envelope,
            trusted_principal=trusted_principal,
        )

    # -- Outbound WebSocket events -----------------------------------------

    async def stop(self) -> None:
        await self.gateway.http.remote_instances.close()
        server_task = self._server_task
        if (
            not self._running
            and server_task is None
            and not self._connection_outbound
            and not self._outbound_retire_tasks
        ):
            await _await_if_needed(self.gateway.aclose())
            return
        self._running = False
        if self._stop_event:
            self._stop_event.set()
        for connection in tuple(self._connection_outbound):
            await self._cleanup_connection(connection)
        if server_task:
            try:
                await server_task
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                self.logger.debug("server task was already cancelled during shutdown")
            except Exception as e:
                self.logger.warning("server task error during shutdown: {}", e)
            if self._server_task is server_task:
                self._server_task = None
        retire_tasks = tuple(self._outbound_retire_tasks)
        if retire_tasks:
            await asyncio.gather(*retire_tasks, return_exceptions=True)
        await self._commands.close()
        self._subs.clear()
        self._conn_chats.clear()
        self._conn_default.clear()
        await _await_if_needed(self.gateway.aclose())

    @staticmethod
    async def _stop_connection_writer(state: _ConnectionOutbound) -> None:
        task = state.writer
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        while True:
            try:
                state.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        state.buffered_bytes = 0

    def _start_connection_writer(
        self,
        connection: ServerConnection,
        state: _ConnectionOutbound,
    ) -> None:
        if state.closing or (state.writer is not None and not state.writer.done()):
            return
        state.writer = asyncio.create_task(
            self._drain_connection_outbound(connection, state),
            name=f"websocket-outbound-{id(connection):x}",
        )

    async def _drain_connection_outbound(
        self,
        connection: ServerConnection,
        state: _ConnectionOutbound,
    ) -> None:
        current = asyncio.current_task()
        try:
            while not state.closing:
                try:
                    frame = state.queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    if not await self._authorize_outbound_frame(connection, frame.raw):
                        continue
                    async with asyncio.timeout(_OUTBOUND_SEND_TIMEOUT_S):
                        await connection.send(frame.raw)
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    self.logger.warning("connection send timed out{}", frame.label)
                    self._schedule_connection_retirement(
                        connection,
                        state,
                        close_connection=True,
                        close_code=1013,
                        close_reason="outbound send timeout",
                    )
                    return
                except ConnectionClosed:
                    self.logger.warning("connection gone{}", frame.label)
                    self._schedule_connection_retirement(
                        connection,
                        state,
                        close_connection=False,
                    )
                    return
                except Exception:
                    self.logger.exception("send failed{}", frame.label)
                    self._schedule_connection_retirement(
                        connection,
                        state,
                        close_connection=True,
                        close_code=1011,
                        close_reason="outbound send failed",
                    )
                    return
                finally:
                    state.buffered_bytes = max(0, state.buffered_bytes - frame.utf8_bytes)
        finally:
            if state.writer is current:
                state.writer = None
            if not state.closing and not state.queue.empty():
                self._start_connection_writer(connection, state)

    def _schedule_connection_retirement(
        self,
        connection: ServerConnection,
        state: _ConnectionOutbound,
        *,
        close_connection: bool,
        close_code: int = 1000,
        close_reason: str = "",
    ) -> None:
        if state.closing:
            return
        state.closing = True
        task = asyncio.create_task(
            self._retire_connection(
                connection,
                close_connection=close_connection,
                close_code=close_code,
                close_reason=close_reason,
            ),
            name=f"websocket-retire-{id(connection):x}",
        )
        self._outbound_retire_tasks.add(task)
        task.add_done_callback(self._outbound_retire_tasks.discard)

    async def _retire_connection(
        self,
        connection: ServerConnection,
        *,
        close_connection: bool,
        close_code: int,
        close_reason: str,
    ) -> None:
        try:
            if close_connection:
                try:
                    async with asyncio.timeout(_OUTBOUND_CLOSE_TIMEOUT_S):
                        await connection.close(code=close_code, reason=close_reason)
                except TimeoutError:
                    self.logger.warning("timed out closing slow WebSocket connection")
                    with suppress(Exception):
                        connection.transport.abort()
                except ConnectionClosed:
                    pass
                except Exception:
                    self.logger.exception("failed to close WebSocket connection")
                    with suppress(Exception):
                        connection.transport.abort()
        finally:
            try:
                await self._cleanup_connection(connection)
            except Exception:
                self.logger.exception("failed to clean up WebSocket connection")

    async def _authorize_outbound_frame(
        self, connection: ServerConnection, raw: str,
    ) -> bool:
        """Revalidate session frames at admission and physical delivery."""
        payload = _parse_envelope(raw)
        chat_id = payload.get("chat_id") if payload is not None else None
        if not isinstance(chat_id, str):
            return True
        principal = (
            self.gateway.endpoint.trusted_proxy_principal(connection)
            or self.gateway.endpoint.oidc_principal(connection)
            or getattr(connection, "_nanobot_oidc_principal", None)
        )
        session_key = webui_session_key(chat_id)
        sessions = self.gateway.session_manager
        metadata = sessions.cached_metadata(session_key) if sessions is not None else None
        scoped = metadata is not None and COLLABORATION_USER_METADATA_KEY in metadata
        if principal is not None or scoped:
            if not await self.gateway.can_access_webui_session(connection, session_key):
                self._detach(connection, chat_id)
                return False
        return True

    async def _safe_send_to(
        self,
        connection: ServerConnection,
        raw: str,
        *,
        label: str = "",
    ) -> None:
        """Authorize and queue one frame without blocking other clients."""
        state = self._connection_outbound.get(connection)
        if state is None or state.closing:
            return
        if not await self._authorize_outbound_frame(connection, raw):
            return
        utf8_bytes = len(raw.encode("utf-8"))
        if state.queue.full() or state.buffered_bytes + utf8_bytes > _OUTBOUND_QUEUE_MAX_BYTES:
            self.logger.warning(
                "disconnecting slow WebSocket connection: outbound queue full "
                "({} frames, {} bytes)",
                state.queue.qsize(),
                state.buffered_bytes,
            )
            self._schedule_connection_retirement(
                connection, state, close_connection=True,
                close_code=1013, close_reason="outbound queue full",
            )
            await asyncio.sleep(0)
            return
        try:
            state.queue.put_nowait(_OutboundFrame(raw, utf8_bytes, label))
        except asyncio.QueueFull:
            self._schedule_connection_retirement(
                connection,
                state,
                close_connection=True,
                close_code=1013,
                close_reason="outbound queue full",
            )
            await asyncio.sleep(0)
            return
        state.buffered_bytes += utf8_bytes
        self._start_connection_writer(connection, state)
        # Give an idle writer a chance to start without waiting for physical I/O.
        await asyncio.sleep(0)

    def _persist_turn_transcript_event(
        self,
        chat_id: str,
        event: dict[str, Any],
        *,
        metadata: dict[str, Any] | None,
        phase: str,
        include_source: bool = False,
        transcript_overrides: dict[str, Any] | None = None,
    ) -> bool:
        """Persist one canonical turn event and retain unsafe owners on failure."""
        if not self._temporary_chats.should_persist_transcript(chat_id):
            self._transcripts.prepare_event(
                chat_id, event, metadata=metadata, phase=phase, include_source=include_source,
            )
            return True
        persisted = self._transcripts.prepare_and_append(
            chat_id,
            event,
            metadata=metadata,
            phase=phase,
            include_source=include_source,
            transcript_overrides=transcript_overrides,
        )
        return self._retain_turn_on_transcript_failure(
            chat_id,
            persisted=persisted,
            metadata=metadata,
            phase=phase,
        )

    @staticmethod
    def _retain_turn_on_transcript_failure(
        chat_id: str,
        *,
        persisted: bool,
        metadata: dict[str, Any] | None,
        phase: str,
    ) -> bool:
        if not persisted and phase in {"answer", "complete"} and (metadata or {}).get("webui") is True:
            owner = (metadata or {}).get(WEBSOCKET_TURN_OWNER_METADATA_KEY)
            mark_websocket_turn_transcript_persistence_failed(
                chat_id,
                owner if isinstance(owner, str) else None,
            )
        return persisted

    def _persist_turn_stream_event(
        self,
        chat_id: str,
        event: dict[str, Any],
        *,
        completed_text: str | None,
        metadata: dict[str, Any] | None,
        phase: str,
        include_source: bool = False,
    ) -> bool:
        """Persist the canonical end of a live stream, never its wire chunks."""
        if not self._temporary_chats.should_persist_transcript(chat_id):
            self._transcripts.prepare_event(
                chat_id, event, metadata=metadata, phase=phase, include_source=include_source,
            )
            return True
        persisted = self._transcripts.prepare_and_append_stream_event(
            chat_id,
            event,
            completed_text=completed_text,
            metadata=metadata,
            phase=phase,
            include_source=include_source,
        )
        return self._retain_turn_on_transcript_failure(
            chat_id,
            persisted=persisted,
            metadata=metadata,
            phase=phase,
        )

    async def send(self, msg: OutboundMessage) -> None:
        await self._outbound.send(msg)

    async def send_projected_message(
        self,
        msg: OutboundMessage,
        progress_event: ProgressEvent | None,
    ) -> None:
        """Serialize one ordinary outbound message selected by the projector."""
        conns = list(self._subs.get(msg.chat_id, ()))
        text = msg.content
        wire_text = self._media.rewrite_local_markdown_images(
            text, session_key=webui_session_key(msg.chat_id)
        )
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": msg.chat_id,
            "text": wire_text,
        }
        turn_id = msg.metadata.get(WEBUI_TURN_METADATA_KEY)
        if isinstance(turn_id, str) and turn_id:
            payload["turn_id"] = turn_id
        if msg.media:
            payload["media"] = msg.media
            urls: list[dict[str, str]] = []
            for entry in msg.media:
                signed = self._media.sign_or_stage_media_path(
                    Path(entry), session_key=webui_session_key(msg.chat_id)
                )
                if signed is not None:
                    urls.append(signed)
            if urls:
                payload["media_urls"] = urls
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        lat = msg.metadata.get("latency_ms")
        if isinstance(lat, (int, float)):
            payload["latency_ms"] = int(lat)
        if progress_event and progress_event.tool_events:
            payload["tool_events"] = project_tool_events(progress_event.tool_events)
        agent_ui = msg.metadata.get(OUTBOUND_META_AGENT_UI)
        if agent_ui is not None:
            payload["agent_ui"] = agent_ui
        # Mark intermediate agent breadcrumbs (tool-call hints, generic
        # progress strings) so WS clients can render them as subordinate
        # trace rows rather than conversational replies.
        if progress_event and progress_event.tool_hint:
            payload["kind"] = "tool_hint"
        elif progress_event:
            payload["kind"] = "progress"
        phase = "activity" if payload.get("kind") in ("tool_hint", "progress") else "answer"
        self._persist_turn_transcript_event(
            msg.chat_id,
            payload,
            metadata=msg.metadata,
            phase=phase,
            include_source=True,
            transcript_overrides={"text": text},
        )
        raw = json.dumps(payload, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" ")

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
    ) -> None:
        """Push one chunk of model reasoning. Mirrors ``send_delta`` shape so
        clients receive a stream that opens, updates in place, and closes —
        rendered above the active assistant bubble with a shimmer header
        until the matching ``reasoning_end`` arrives.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not delta:
            return
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_delta",
            "chat_id": chat_id,
            "text": delta,
        }
        if stream_id is not None:
            body["stream_id"] = stream_id
        stream_key = (chat_id, str(stream_id or ""))
        self._reasoning_text_buffers.setdefault(stream_key, []).append(delta)
        self._persist_turn_stream_event(
            chat_id,
            body,
            completed_text=None,
            metadata=meta,
            phase="reasoning",
        )
        raw = json.dumps(body, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" reasoning ")

    async def send_reasoning_end(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
    ) -> None:
        """Close the current reasoning stream segment for in-place renderers."""
        conns = list(self._subs.get(chat_id, ()))
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_end",
            "chat_id": chat_id,
        }
        if stream_id is not None:
            body["stream_id"] = stream_id
        stream_key = (chat_id, str(stream_id or ""))
        reasoning_text = "".join(self._reasoning_text_buffers.pop(stream_key, []))
        self._persist_turn_stream_event(
            chat_id,
            body,
            completed_text=reasoning_text or None,
            metadata=meta,
            phase="reasoning",
        )
        raw = json.dumps(body, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" reasoning_end ")

    async def send_file_edit_events(
        self,
        chat_id: str,
        edits: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        payload: dict[str, Any] = {
            "event": "file_edit",
            "chat_id": chat_id,
            "edits": edits,
        }
        self._persist_turn_transcript_event(
            chat_id,
            payload,
            metadata=metadata,
            phase="activity",
        )
        raw = json.dumps(payload, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" file_edit ")

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
        conns = list(self._subs.get(chat_id, ()))
        meta = metadata or {}
        stream_key = (chat_id, str(stream_id or ""))
        completed_text: str | None = None
        if stream_end:
            body: dict[str, Any] = {"event": "stream_end", "chat_id": chat_id}
            buffered = (
                self._stream_text_buffers.setdefault(stream_key, [])
                if merge_next
                else self._stream_text_buffers.pop(stream_key, [])
            )
            if delta:
                buffered.append(delta)
            full_text = "".join(buffered)
            rewritten = self._media.rewrite_local_markdown_images(
                full_text, session_key=webui_session_key(chat_id)
            )
            completed_text = rewritten
            if delta or rewritten != full_text:
                body["text"] = rewritten
        else:
            body = {
                "event": "delta",
                "chat_id": chat_id,
                "text": delta,
            }
            self._stream_text_buffers.setdefault(stream_key, []).append(delta)
        if stream_id is not None:
            body["stream_id"] = stream_id
        if stream_end and resuming:
            body["resuming"] = True
        if stream_end and merge_next:
            body["merge_next"] = True
        self._persist_turn_stream_event(
            chat_id,
            body,
            completed_text=completed_text,
            metadata=meta,
            phase="answer",
            include_source=True,
        )
        raw = json.dumps(body, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" stream ")

    async def send_payload(
        self,
        chat_id: str,
        payload: WebUIWirePayload,
        *,
        persistence: WebUIWirePersistence,
        metadata: dict[str, Any] | None = None,
        turn_owner: str | None = None,
    ) -> None:
        """Persist as requested, frame, and fan out one encoded WebUI payload."""
        conns = list(self._subs.get(chat_id, ()))
        body: dict[str, Any] = dict(payload)
        if persistence == "turn_activity":
            self._persist_turn_transcript_event(
                chat_id,
                body,
                metadata=metadata,
                phase="activity",
            )
        elif persistence == "turn_complete":
            canonical_webui_turn = (metadata or {}).get("webui") is True
            prior_persistence_failure = (
                canonical_webui_turn
                and websocket_turn_transcript_persistence_failed(chat_id, turn_owner)
            )
            persisted = self._persist_turn_transcript_event(
                chat_id,
                body,
                metadata=metadata,
                phase="complete",
                transcript_overrides=(
                    {WEBUI_TRANSCRIPT_INCOMPLETE_KEY: True}
                    if prior_persistence_failure
                    else None
                ),
            )
            if persisted:
                # A successful completion either has a complete transcript or now
                # carries a durable incomplete marker. The HTTP replay path can
                # recover the latter from session history after a gateway restart.
                clear_websocket_turn_if_current(chat_id, turn_owner)
            self._clear_stream_buffers(chat_id)
        raw = json.dumps(body, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=f" {body['event']} ")

    async def send_goal_state(self, chat_id: str, blob: dict[str, Any]) -> None:
        """Push persisted goal-state snapshot for *chat_id* (multi-chat isolation)."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body = {"event": "goal_state", "chat_id": chat_id, "goal_state": blob}
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" goal_state ")

    async def send_goal_status(
        self,
        chat_id: str,
        status: str,
        *,
        started_at: float | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Notify subscribed clients that a turn started or finished (wall-clock hint)."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "goal_status",
            "chat_id": chat_id,
            "status": status,
        }
        if status == "running" and started_at is not None:
            body["started_at"] = started_at
        if turn_id:
            body["turn_id"] = turn_id
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" goal_status ")

    async def send_session_updated(self, chat_id: str, *, scope: str | None = None) -> None:
        """Notify WebUI clients that a session row should refresh."""
        conns = list(self._conn_chats)
        if not conns:
            return
        body: dict[str, Any] = {"event": "session_updated", "chat_id": chat_id}
        if scope:
            body["scope"] = scope
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" session_updated ")

    async def send_user_input(
        self,
        chat_id: str,
        *,
        content: str,
        created_at_ms: int,
        provenance: dict[str, Any],
    ) -> None:
        """Project user input produced outside a WebSocket connection."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "user_message",
            "chat_id": chat_id,
            "text": content,
            "created_at_ms": created_at_ms,
            "starts_turn": False,
        }
        if provenance:
            body["provenance"] = provenance
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" user_message ")

    async def send_runtime_model_updated(
        self,
        *,
        model_name: Any,
        model_preset: Any = None,
    ) -> None:
        """Broadcast runtime model changes to every open websocket connection."""
        conns = list(self._conn_chats)
        if not conns or not isinstance(model_name, str) or not model_name.strip():
            return
        body: dict[str, Any] = {
            "event": "runtime_model_updated",
            "model_name": model_name.strip(),
        }
        if isinstance(model_preset, str) and model_preset.strip():
            body["model_preset"] = model_preset.strip()
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" runtime_model_updated ")

    async def send_turn_model_updated(
        self,
        chat_id: str,
        *,
        model_name: Any,
        model_preset: Any = None,
        context_window_tokens: Any = None,
        fallback: bool = False,
        reauth_provider: str | None = None,
    ) -> None:
        """Notify one chat's subscribers which model is handling its current request."""
        conns = list(self._subs.get(chat_id, ()))
        if (
            not conns
            or not isinstance(model_name, str)
            or not model_name.strip()
        ):
            return
        body: dict[str, Any] = {
            "event": "turn_model_updated",
            "chat_id": chat_id,
            "model_name": model_name.strip(),
        }
        if isinstance(model_preset, str) and model_preset.strip():
            body["model_preset"] = model_preset.strip()
        if isinstance(context_window_tokens, int) and context_window_tokens > 0:
            body["context_window_tokens"] = context_window_tokens
        if fallback:
            body["fallback"] = True
            if reauth_provider:
                body["reauth_provider"] = reauth_provider
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" turn_model_updated ")
