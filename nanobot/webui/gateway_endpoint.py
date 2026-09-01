"""HTTP and handshake composition for the WebUI gateway listener."""

from __future__ import annotations

import asyncio
import hmac
import time
from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from websockets.asyncio.server import ServerConnection
from websockets.http11 import Request as WsRequest

from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.http_utils import (
    is_trusted_proxy_authenticated_request,
    normalize_config_path,
    parse_request_path,
    query_first,
    trusted_proxy_principal_key,
)
from nanobot.webui.ws_http import GatewayHTTPHandler

if TYPE_CHECKING:
    from nanobot.channels.websocket.runtime import WebSocketConfig


def is_websocket_upgrade(request: WsRequest) -> bool:
    """Return whether a request contains a complete WebSocket upgrade handshake."""
    upgrade = request.headers.get("Upgrade") or request.headers.get("upgrade")
    connection = request.headers.get("Connection") or request.headers.get("connection")
    return bool(
        upgrade
        and "websocket" in upgrade.lower()
        and connection
        and "upgrade" in connection.lower()
    )


@dataclass(frozen=True)
class _OidcConnection:
    principal: str
    deadline: float


class WebUIGatewayEndpoint:
    """Compose HTTP routing and WebSocket authentication on one listener."""

    def __init__(
        self,
        *,
        config: WebSocketConfig,
        http: GatewayHTTPHandler,
        tokens: GatewayTokenStore,
        webui_connections: set[ServerConnection] | None = None,
    ) -> None:
        self._config = config
        self._http = http
        self._tokens = tokens
        self.webui_connections: set[ServerConnection] = (
            webui_connections if webui_connections is not None else set()
        )
        self._trusted_proxy_principals: dict[ServerConnection, str] = {}
        self._oidc_connections: dict[ServerConnection, _OidcConnection] = {}
        self._oidc_connection_index: dict[str, set[ServerConnection]] = defaultdict[str, set[ServerConnection]](set)

    async def process_request(
        self,
        connection: ServerConnection,
        request: WsRequest,
        *,
        is_allowed: Callable[[str], bool],
    ) -> Any:
        """Route one listener request to a WS handshake or the HTTP application."""
        got, query = parse_request_path(request.path)
        expected_ws = normalize_config_path(self._config.path)
        if got == expected_ws and is_websocket_upgrade(request):
            client_id = query_first(query, "client_id") or ""
            if len(client_id) > 128:
                client_id = client_id[:128]
            if not is_allowed(client_id):
                return connection.respond(403, "Forbidden")
            return self.authorize_websocket_handshake(connection, query, request.headers)
        return await self._http.dispatch(connection, request)

    def authorize_websocket_handshake(
        self,
        connection: ServerConnection,
        query: dict[str, list[str]],
        headers: Any = None,
    ) -> Any:
        """Authorize a WebSocket upgrade and remember trusted WebUI connections."""
        if is_trusted_proxy_authenticated_request(connection, headers or {}, self._config):
            principal_key = trusted_proxy_principal_key(connection, headers or {}, self._config)
            if principal_key is not None:
                self._trusted_proxy_principals[connection] = principal_key
            self.webui_connections.add(connection)
            return None

        supplied = query_first(query, "token")
        static_token = self._config.token.strip()
        if static_token:
            if supplied and hmac.compare_digest(supplied, static_token):
                self.webui_connections.add(connection)
                setattr(connection, "_nanobot_local_webui_authenticated", True)
                return None
            if supplied and self.consume_issued_token(connection, supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if self._config.websocket_requires_token or self._config.oidc_auth.enabled:
            if supplied and self.consume_issued_token(connection, supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if supplied:
            self.consume_issued_token(connection, supplied)
        return None

    def consume_issued_token(self, connection: ServerConnection, token: str) -> bool:
        """Consume one issued token and retain an OIDC principal deadline."""
        taken = self._tokens.take_issued_token(token)
        if taken is None:
            return False
        audience, principal, deadline = taken
        if principal:
            state = _OidcConnection(principal, deadline)
            self._oidc_connections[connection] = state
            self._oidc_connection_index[principal].add(connection)
            setattr(connection, "_nanobot_oidc_principal", principal)
        if audience != "webui":
            return True
        self.webui_connections.add(connection)
        if principal is None:
            setattr(connection, "_nanobot_local_webui_authenticated", True)
        return True

    def is_webui_connection(self, connection: ServerConnection) -> bool:
        return connection in self.webui_connections

    def trusted_proxy_principal(self, connection: ServerConnection) -> str | None:
        return self._trusted_proxy_principals.get(connection)

    def oidc_principal(self, connection: ServerConnection) -> str | None:
        state = self._oidc_connections.get(connection)
        if state is None or state.deadline <= time.monotonic():
            return None
        return state.principal

    def oidc_connection_active(self, connection: ServerConnection) -> bool:
        state = self._oidc_connections.get(connection)
        return state is not None and state.deadline > time.monotonic()

    async def close_oidc_at_deadline(self, connection: ServerConnection) -> None:
        state = self._oidc_connections.get(connection)
        if state is None:
            return
        await asyncio.sleep(max(0.0, state.deadline - time.monotonic()))
        if self._oidc_connections.get(connection) == state:
            await self.close_oidc_connection(connection, "OIDC credential expired")

    async def close_oidc_principal(self, principal: str) -> None:
        connections = tuple(self._oidc_connection_index.get(principal, ()))
        for connection in connections:
            await self.close_oidc_connection(connection, "OIDC logout")

    async def close_oidc_connection(self, connection: ServerConnection, reason: str) -> None:
        state = self._forget_oidc_connection(connection)
        if state is None:
            return
        with suppress(Exception):
            await connection.close(code=1008, reason=reason)

    def discard_connection(self, connection: ServerConnection) -> None:
        self.webui_connections.discard(connection)
        self._trusted_proxy_principals.pop(connection, None)
        self._forget_oidc_connection(connection)
        with suppress(Exception):
            delattr(connection, "_nanobot_local_webui_authenticated")

    def clear(self) -> None:
        self.webui_connections.clear()
        self._trusted_proxy_principals.clear()
        self._oidc_connections.clear()
        self._oidc_connection_index.clear()

    def _forget_oidc_connection(self, connection: ServerConnection) -> _OidcConnection | None:
        state = self._oidc_connections.pop(connection, None)
        if state is not None:
            connections = self._oidc_connection_index.get(state.principal)
            if connections is not None:
                connections.discard(connection)
                if not connections:
                    self._oidc_connection_index.pop(state.principal, None)
        with suppress(Exception):
            delattr(connection, "_nanobot_oidc_principal")
        return state
