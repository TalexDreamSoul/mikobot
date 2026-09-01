from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from nanobot.channels.websocket.runtime import WebSocketChannel, WebSocketConfig
from nanobot.collaboration.models import COLLABORATION_USER_METADATA_KEY
from nanobot.session.manager import SessionManager
from nanobot.webui.gateway_endpoint import WebUIGatewayEndpoint
from nanobot.webui.gateway_services import build_gateway_services
from nanobot.webui.http_utils import trusted_proxy_principal_key


class _Connection:
    def __init__(self, peer: str, path: str, frames: list[str]) -> None:
        self.remote_address = (peer, 443)
        self.request = SimpleNamespace(path=path)
        self._frames = iter(frames)
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    def __aiter__(self) -> _Connection:
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._frames)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    def respond(self, status: int, message: str) -> tuple[int, str]:
        return status, message


def _proxy_config() -> WebSocketConfig:
    return WebSocketConfig.model_validate({
        "websocketRequiresToken": False,
        "allowFrom": ["browser-tab"],
        "trustedProxyAuth": {
            "trustedPeerCidrs": ["10.10.0.0/16"],
            "assertionHeader": "X-Proxy-Assertion",
            "subjectHeader": "X-Verified-Subject",
        },
    })


def _connection_loop_channel(endpoint: WebUIGatewayEndpoint) -> WebSocketChannel:
    """Construct only the ingress dependencies exercised by the connection loop."""
    channel = object.__new__(WebSocketChannel)
    channel.gateway = SimpleNamespace(endpoint=endpoint)
    channel._subs = {}
    channel._conn_chats = {}
    channel._conn_default = {}
    channel._hydrate_after_subscribe = AsyncMock()
    channel._handle_message = AsyncMock()
    channel._cleanup_connection = AsyncMock()
    channel.logger = MagicMock()
    return channel


def _websocket_upgrade(path: str, headers: dict[str, str]) -> WsRequest:
    return WsRequest(path, Headers({"Connection": "Upgrade", "Upgrade": "websocket", **headers}))


@asynccontextmanager
async def _proxy_routing_channel(
    config: WebSocketConfig, sessions: SessionManager, workspace_path: Path
) -> AsyncIterator[WebSocketChannel]:
    gateway = build_gateway_services(
        config=config,
        bus=MagicMock(),
        session_manager=sessions,
        static_dist_path=None,
        workspace_path=workspace_path,
        default_restrict_to_workspace=False,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
    )
    await gateway.initialize()
    try:
        yield WebSocketChannel(config, MagicMock(), gateway=gateway)
    finally:
        await gateway.aclose()


def test_verified_trusted_proxy_subject_is_stable_opaque_and_untrusted_spoofs_are_ignored() -> None:
    """Only a CIDR-verified peer with its separate assertion can turn a proxy subject into identity."""
    config = _proxy_config()
    headers = {"X-Proxy-Assertion": "assertion", "X-Verified-Subject": "person-42"}

    first = trusted_proxy_principal_key(_Connection("10.10.1.5", "/", []), headers, config)
    repeated = trusted_proxy_principal_key(_Connection("10.10.9.9", "/", []), headers, config)
    changed_subject = trusted_proxy_principal_key(
        _Connection("10.10.1.5", "/", []),
        {**headers, "X-Verified-Subject": "person-43"},
        config,
    )
    no_assertion = trusted_proxy_principal_key(
        _Connection("10.10.1.5", "/", []),
        {"X-Verified-Subject": "person-42"},
        config,
    )
    untrusted_spoof = trusted_proxy_principal_key(_Connection("203.0.113.8", "/", []), headers, config)

    assert first is not None and first.startswith("proxy:")
    assert first == repeated
    assert changed_subject is not None and changed_subject != first
    assert "person-42" not in first
    assert no_assertion is None
    assert untrusted_spoof is None


@pytest.mark.asyncio
async def test_trusted_proxy_handshake_authorizes_client_id_but_inbound_uses_verified_principal() -> None:
    """A listed browser client is admitted while its proxy principal remains the inbound identity."""
    config = _proxy_config()
    endpoint = WebUIGatewayEndpoint(config=config, http=MagicMock(), tokens=MagicMock())
    headers = {"X-Proxy-Assertion": "assertion", "X-Verified-Subject": "person-42"}
    request = _websocket_upgrade("/?client_id=browser-tab", headers)
    connection = _Connection("10.10.1.5", request.path, ["hello"])
    connection.request = request
    channel = _connection_loop_channel(endpoint)
    channel.config = config

    assert await endpoint.process_request(
        connection, request, is_allowed=channel.is_allowed
    ) is None
    await channel._connection_loop(connection)

    ready = json.loads(connection.sent[0])
    principal = endpoint.trusted_proxy_principal(connection)
    assert ready["client_id"] == "browser-tab"
    assert ready["principal"] is True
    assert principal is not None
    channel._handle_message.assert_awaited_once()
    assert channel._handle_message.await_args.kwargs["sender_id"] == principal
    assert channel._handle_message.await_args.kwargs["authorization_id"] == "browser-tab"


@pytest.mark.asyncio
async def test_proxy_principal_cannot_route_a_foreign_persisted_chat(
    tmp_path: Path,
) -> None:
    """A proxy-authenticated browser cannot hydrate or route a guessed chat owned by another principal."""
    config = _proxy_config()
    sessions = SessionManager(tmp_path / "sessions")

    async with _proxy_routing_channel(config, sessions, tmp_path / "workspace") as channel:
        endpoint = channel.gateway.endpoint
        headers = {"X-Proxy-Assertion": "assertion", "X-Verified-Subject": "person-42"}
        request = _websocket_upgrade("/?client_id=browser-tab", headers)
        connection = _Connection("10.10.1.5", request.path, [])
        connection.request = request

        assert await endpoint.process_request(
            connection, request, is_allowed=channel.is_allowed
        ) is None
        principal = endpoint.trusted_proxy_principal(connection)
        assert principal is not None

        foreign_principal = trusted_proxy_principal_key(
            _Connection("10.10.1.6", "/", []),
            {"X-Proxy-Assertion": "assertion", "X-Verified-Subject": "person-99"},
            config,
        )
        assert foreign_principal is not None
        foreign_user, _ = await channel.gateway.collaboration.ensure_identity_user(
            "websocket", foreign_principal, tmp_path / "workspace", local_owner=False
        )
        chat_id = "foreign-existing-chat"
        session = sessions.get_or_create(f"websocket:{chat_id}")
        session.metadata[COLLABORATION_USER_METADATA_KEY] = foreign_user.id
        session.add_message("user", "private history")
        sessions.save(session)
        channel._hydrate_after_subscribe = AsyncMock()
        channel._handle_message = AsyncMock()

        await channel._dispatch_envelope(
            connection,
            "browser-tab",
            {
                "type": "message",
                "chat_id": chat_id,
                "content": "read the private history",
                "webui": True,
            },
            trusted_principal=principal,
        )

        denied = json.loads(connection.sent[-1])
        assert denied == {
            "event": "error",
            "detail": "session_not_found",
            "chat_id": chat_id,
        }
        channel._hydrate_after_subscribe.assert_not_awaited()
        channel._handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_proxy_principal_routes_its_chat_after_authorized_attach(
    tmp_path: Path,
) -> None:
    """A verified principal may send to its persisted chat only after the ownership-checked attach."""
    config = _proxy_config()
    sessions = SessionManager(tmp_path / "sessions")

    async with _proxy_routing_channel(config, sessions, tmp_path / "workspace") as channel:
        endpoint = channel.gateway.endpoint
        headers = {"X-Proxy-Assertion": "assertion", "X-Verified-Subject": "person-42"}
        request = _websocket_upgrade("/?client_id=browser-tab", headers)
        connection = _Connection("10.10.1.5", request.path, [])
        connection.request = request

        assert await endpoint.process_request(
            connection, request, is_allowed=channel.is_allowed
        ) is None
        principal = endpoint.trusted_proxy_principal(connection)
        assert principal is not None
        owner, _ = await channel.gateway.collaboration.ensure_identity_user(
            "websocket", principal, tmp_path / "workspace", local_owner=False
        )
        chat_id = "principal-owned-chat"
        session = sessions.get_or_create(f"websocket:{chat_id}")
        session.metadata[COLLABORATION_USER_METADATA_KEY] = owner.id
        session.add_message("user", "owned history")
        sessions.save(session)
        channel._hydrate_after_subscribe = AsyncMock()
        channel._handle_message = AsyncMock()

        await channel._dispatch_envelope(
            connection, "browser-tab", {"type": "attach", "chat_id": chat_id}
        )
        channel._hydrate_after_subscribe.assert_awaited_once_with(chat_id)
        channel._hydrate_after_subscribe.reset_mock()

        await channel._dispatch_envelope(
            connection,
            "browser-tab",
            {"type": "message", "chat_id": chat_id, "content": "continue"},
            trusted_principal=principal,
        )

        channel._hydrate_after_subscribe.assert_awaited_once_with(chat_id)
        channel._handle_message.assert_awaited_once()
        assert channel._handle_message.await_args.kwargs["sender_id"] == principal
        assert channel._handle_message.await_args.kwargs["authorization_id"] == "browser-tab"


@pytest.mark.asyncio
async def test_static_token_authentication_still_uses_client_id_without_proxy_identity() -> None:
    """Token-only deployments retain their previous client-id sender behavior."""
    config = WebSocketConfig.model_validate({"token": "static-token", "websocketRequiresToken": True})
    endpoint = WebUIGatewayEndpoint(config=config, http=MagicMock(), tokens=MagicMock())
    connection = _Connection("203.0.113.8", "/?client_id=token-client", ["hello"])

    assert endpoint.authorize_websocket_handshake(connection, {"token": ["static-token"]}) is None
    assert endpoint.trusted_proxy_principal(connection) is None
    channel = _connection_loop_channel(endpoint)
    await channel._connection_loop(connection)

    ready = json.loads(connection.sent[0])
    assert ready["client_id"] == "token-client"
    assert "principal" not in ready
    channel._handle_message.assert_awaited_once()
    assert channel._handle_message.await_args.kwargs["sender_id"] == "token-client"
