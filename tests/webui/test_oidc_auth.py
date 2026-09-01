from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Callable
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from nanobot.channels.websocket.runtime import (
    OidcAuthConfig,
    TrustedProxyAuthConfig,
    WebSocketConfig,
)
from nanobot.collaboration import AsyncLocalCollaborationRepository, CollaborationStore
from nanobot.webui.gateway_endpoint import WebUIGatewayEndpoint
from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.ingress_policy import WebUIIngressPolicy
from nanobot.webui.oidc_auth import OidcAuthenticator, OidcError, safe_return_to
from nanobot.webui.ws_http import GatewayHTTPHandler

_ISSUER = "https://issuer.example"
_CLIENT_ID = "webui-client"
_REDIRECT_URI = "https://agent.example/auth/oidc/callback"


def _config(**overrides: object) -> OidcAuthConfig:
    values: dict[str, object] = {
        "enabled": True,
        "issuer": _ISSUER,
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "scopes": ["openid", "profile"],
        "token_endpoint_auth_method": "none",
        "session_ttl_s": 600,
        "flow_ttl_s": 60,
        "session_capacity": 3,
        "flow_capacity": 3,
    }
    values.update(overrides)
    return OidcAuthConfig(**values)


def _cookie_value(headers: list[tuple[str, str]], name: str) -> str:
    prefix = f"{name}="
    for header, value in headers:
        if header.casefold() == "set-cookie" and value.startswith(prefix):
            return value[len(prefix):].split(";", 1)[0]
    raise AssertionError(f"missing {name} cookie")


def _set_cookie_values(headers: list[tuple[str, str]]) -> list[str]:
    return [value for header, value in headers if header.casefold() == "set-cookie"]


class _FakeOidcProvider:
    """Deterministic in-memory authorization server for browser-flow tests."""

    def __init__(
        self,
        *,
        claim_override: Callable[[dict[str, object]], None] | None = None,
        discovery_algorithms: list[str] | None = None,
        oversized: str | None = None,
    ) -> None:
        self.key = RSAKey.generate_key(2048)
        self.key.ensure_kid()
        self.claim_override = claim_override
        self.discovery_algorithms = discovery_algorithms or ["RS256"]
        self.oversized = oversized
        self.authorization: dict[str, str] = {}
        self.token_form: dict[str, list[str]] | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original_client = httpx.AsyncClient

        def client(*args: object, **kwargs: object) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(self.handle)
            return original_client(*args, **kwargs)

        monkeypatch.setattr("nanobot.webui.oidc_auth.httpx.AsyncClient", client)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return self._response(
                request,
                "discovery",
                {
                    "issuer": _ISSUER,
                    "authorization_endpoint": f"{_ISSUER}/authorize",
                    "token_endpoint": f"{_ISSUER}/token",
                    "jwks_uri": f"{_ISSUER}/jwks",
                    "id_token_signing_alg_values_supported": self.discovery_algorithms,
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if path == "/token":
            self.token_form = parse_qs(request.content.decode("utf-8"))
            verifier = self.token_form["code_verifier"][0]
            expected_challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            ).rstrip(b"=").decode("ascii")
            assert expected_challenge == self.authorization["code_challenge"]
            assert self.token_form == {
                "grant_type": ["authorization_code"],
                "code": ["provider-code"],
                "redirect_uri": [_REDIRECT_URI],
                "client_id": [_CLIENT_ID],
                "code_verifier": [verifier],
            }
            claims: dict[str, object] = {
                "iss": _ISSUER,
                "sub": "provider-subject-42",
                "aud": _CLIENT_ID,
                "nonce": self.authorization["nonce"],
                "exp": time.time() + 300,
                "iat": time.time() - 5,
                "name": "Ada OIDC",
                "email": "ada@example.test",
            }
            if self.claim_override is not None:
                self.claim_override(claims)
            value = jwt.encode(
                {"alg": "RS256", "kid": self.key.kid}, claims, self.key
            )
            return self._response(request, "token", {"id_token": value})
        if path == "/jwks":
            return self._response(request, "jwks", {"keys": [self.key.as_dict(private=False)]})
        return httpx.Response(404, request=request)

    def _response(
        self,
        request: httpx.Request,
        resource: str,
        payload: dict[str, object],
    ) -> httpx.Response:
        if self.oversized == resource:
            return httpx.Response(
                200,
                headers={"Content-Length": "131073"},
                content=b"{}",
                request=request,
            )
        return httpx.Response(200, json=payload, request=request)


class _EndpointConnection:
    def __init__(self) -> None:
        self.responses: list[tuple[int, str]] = []
        self.closed: list[tuple[int, str]] = []

    def respond(self, status: int, body: str) -> tuple[int, str]:
        self.responses.append((status, body))
        return status, body

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))


async def _begin(
    authenticator: OidcAuthenticator,
    provider: _FakeOidcProvider,
    *,
    return_to: str = "/projects?view=mine",
) -> tuple[dict[str, str], Headers]:
    location, cookies = await authenticator.begin(Headers(), return_to)
    authorization = {
        key: values[0] for key, values in parse_qs(urlsplit(location).query).items()
    }
    provider.authorization = authorization
    return authorization, Headers({"Cookie": f"nanobot_oidc_flow={_cookie_value(cookies, 'nanobot_oidc_flow')}"})


def _callback_request(authorization: dict[str, str], headers: Headers) -> WsRequest:
    return WsRequest(
        f"/auth/oidc/callback?code=provider-code&state={authorization['state']}",
        headers,
    )


async def _http_handler(tmp_path, authenticator: OidcAuthenticator) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = WebSocketConfig(
        host="127.0.0.1",
        path="/ws",
        token="",
        websocket_requires_token=True,
        oidc_auth=authenticator._config,
        token_ttl_s=120,
    )
    handler.oidc = authenticator
    handler.collaboration = AsyncLocalCollaborationRepository(
        CollaborationStore(tmp_path / "collaboration")
    )
    handler._collaboration_init_lock = asyncio.Lock()
    handler._collaboration_initialized = False
    handler._collaboration_closed = False
    handler.skills_workspace_path = tmp_path / "workspace"
    handler.tokens = GatewayTokenStore()
    handler.ingress = WebUIIngressPolicy()
    handler.runtime_model_name = None
    handler.settings = SimpleNamespace(config=SimpleNamespace(path=None))
    handler._runtime_surface = "native"
    handler._capabilities = {}
    handler._oidc_connection_revoker = None
    await handler.initialize_collaboration()
    return handler


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": ""},
        {"client_id": ""},
        {"redirect_uri": ""},
        {"issuer": "http://issuer.example"},
        {"issuer": "https://issuer.example?tenant=wrong"},
        {"client_secret": "not-for-public"},
        {"token_endpoint_auth_method": "client_secret_basic", "client_secret": ""},
    ],
    ids=[
        "enabled-requires-issuer",
        "enabled-requires-client-id",
        "enabled-requires-redirect-uri",
        "rejects-public-http-issuer",
        "rejects-queried-issuer",
        "public-client-rejects-secret",
        "confidential-client-requires-secret",
    ],
)
def test_oidc_config_rejects_insecure_or_incomplete_enabled_clients(overrides: dict[str, object]) -> None:
    """Enabled OIDC only accepts a complete loopback-safe public or confidential client."""
    with pytest.raises(ValueError):
        _config(**overrides)


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("/projects?view=mine", "/projects?view=mine"),
        ("https://attacker.example/", "/"),
        ("//attacker.example/", "/"),
        ("/auth/oidc/callback", "/"),
        ("/projects\\login", "/"),
        ("/projects\x00", "/"),
    ],
)
def test_safe_return_to_keeps_post_login_navigation_on_the_gateway(candidate: str, expected: str) -> None:
    """OIDC redirects may only resume a safe, relative non-auth route."""
    assert safe_return_to(candidate) == expected


@pytest.mark.asyncio
async def test_oidc_browser_flow_uses_one_time_browser_bound_state_nonce_and_s256_pkce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A callback succeeds only for its browser-bound state and signed nonce-bound ID token."""
    provider = _FakeOidcProvider()
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config())

    authorization, flow_headers = await _begin(authenticator, provider)
    session, return_to, cookies = await authenticator.callback(
        flow_headers,
        {"code": ["provider-code"], "state": [authorization["state"]]},
    )

    assert authorization["response_type"] == "code"
    assert authorization["code_challenge_method"] == "S256"
    assert return_to == "/projects?view=mine"
    assert session.principal.startswith("oidc:")
    assert session.name == "Ada OIDC"
    assert session.email == "ada@example.test"
    assert provider.token_form is not None
    set_cookies = _set_cookie_values(cookies)
    assert any(cookie.startswith("nanobot_oidc_flow=") and "Max-Age=0" in cookie for cookie in set_cookies)
    session_cookie = _cookie_value(cookies, "nanobot_oidc_session")
    assert all("HttpOnly" in cookie and "SameSite=Lax" in cookie for cookie in set_cookies)
    assert authenticator.session(Headers({"Cookie": f"nanobot_oidc_session={session_cookie}"})) == session

    with pytest.raises(OidcError):
        await authenticator.callback(
            flow_headers,
            {"code": ["provider-code"], "state": [authorization["state"]]},
        )


@pytest.mark.asyncio
async def test_oidc_callback_rejects_flow_presented_by_a_different_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stealing a callback URL is insufficient without the initiating browser cookie."""
    provider = _FakeOidcProvider()
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config())
    authorization, flow_headers = await _begin(authenticator, provider)

    with pytest.raises(OidcError):
        await authenticator.callback(
            Headers({"Cookie": "nanobot_oidc_flow=another-browser"}),
            {"code": ["provider-code"], "state": [authorization["state"]]},
        )

    session, _return_to, _cookies = await authenticator.callback(
        flow_headers,
        {"code": ["provider-code"], "state": [authorization["state"]]},
    )
    assert session.principal.startswith("oidc:")


@pytest.mark.asyncio
async def test_oidc_callback_route_clears_cookies_when_flow_validation_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The browser receives a non-reusable failed login response for a mismatched flow cookie."""
    provider = _FakeOidcProvider()
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config())
    handler = await _http_handler(tmp_path, authenticator)
    authorization, _flow_headers = await _begin(authenticator, provider)

    response = await handler._handle_oidc_callback(
        _callback_request(authorization, Headers({"Cookie": "nanobot_oidc_flow=other-browser"}))
    )

    assert response.status_code == 400
    assert response.body == b"OIDC login failed"
    cleared = _set_cookie_values(list(response.headers.raw_items()))
    assert any(cookie.startswith("nanobot_oidc_flow=") and "Max-Age=0" in cookie for cookie in cleared)
    assert any(cookie.startswith("nanobot_oidc_session=") and "Max-Age=0" in cookie for cookie in cleared)


@pytest.mark.parametrize(
    "case",
    [
        "issuer",
        "audience",
        "azp",
        "nonce",
        "expired",
        "future-issued-at",
        "future-not-before",
        "disallowed-algorithm",
    ],
)
@pytest.mark.asyncio
async def test_oidc_callback_rejects_invalid_signed_id_token_claims(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """A provider signature alone cannot bypass issuer, audience, nonce, or lifetime validation."""
    def modify(claims: dict[str, object]) -> None:
        if case == "issuer":
            claims["iss"] = "https://other-issuer.example"
        elif case == "audience":
            claims["aud"] = "another-client"
        elif case == "azp":
            claims["aud"] = [_CLIENT_ID, "another-client"]
            claims["azp"] = "another-client"
        elif case == "nonce":
            claims["nonce"] = "wrong-nonce"
        elif case == "expired":
            claims["exp"] = time.time() - 1
        elif case == "future-issued-at":
            claims["iat"] = time.time() + 61
            claims["exp"] = time.time() + 120
        elif case == "future-not-before":
            claims["nbf"] = time.time() + 61

    provider = _FakeOidcProvider(
        claim_override=modify,
        discovery_algorithms=["RS384"] if case == "disallowed-algorithm" else None,
    )
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config())
    authorization, flow_headers = await _begin(authenticator, provider)

    with pytest.raises(OidcError):
        await authenticator.callback(
            flow_headers,
            {"code": ["provider-code"], "state": [authorization["state"]]},
        )


@pytest.mark.parametrize("resource", ["discovery", "token", "jwks"])
@pytest.mark.asyncio
async def test_oidc_provider_response_bodies_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
) -> None:
    """An OIDC provider cannot make login retain or parse an unbounded HTTP response."""
    provider = _FakeOidcProvider(oversized=resource)
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config())

    if resource == "discovery":
        with pytest.raises(OidcError):
            await authenticator.begin(Headers(), "/")
        return

    authorization, flow_headers = await _begin(authenticator, provider)
    with pytest.raises(OidcError):
        await authenticator.callback(
            flow_headers,
            {"code": ["provider-code"], "state": [authorization["state"]]},
        )


@pytest.mark.asyncio
async def test_oidc_flow_store_rejects_new_states_at_its_explicit_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full in-memory login-flow store preserves its active state rather than evicting it."""
    provider = _FakeOidcProvider()
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config(flow_capacity=1))
    first, first_headers = await _begin(authenticator, provider)

    with pytest.raises(OidcError):
        await _begin(authenticator, provider)

    session, _return_to, _cookies = await authenticator.callback(
        first_headers,
        {"code": ["provider-code"], "state": [first["state"]]},
    )
    assert session.principal.startswith("oidc:")


@pytest.mark.asyncio
async def test_oidc_session_and_cookie_lifetimes_do_not_outlive_the_id_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short-lived ID token caps both the server session and its browser cookie."""
    def shorten_token(claims: dict[str, object]) -> None:
        claims["exp"] = time.time() + 30

    provider = _FakeOidcProvider(claim_override=shorten_token)
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config(session_ttl_s=600))
    authorization, flow_headers = await _begin(authenticator, provider)

    session, _return_to, cookies = await authenticator.callback(
        flow_headers,
        {"code": ["provider-code"], "state": [authorization["state"]]},
    )

    session_cookie = next(
        cookie for cookie in _set_cookie_values(cookies) if cookie.startswith("nanobot_oidc_session=")
    )
    max_age = int(next(item for item in session_cookie.split("; ") if item.startswith("Max-Age=")).split("=", 1)[1])
    assert 1 <= max_age <= 30
    assert 0 < session.expires_at - time.monotonic() <= 30


@pytest.mark.asyncio
async def test_oidc_token_issue_credential_is_principal_bound_and_expiry_capped(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OIDC-authenticated token issue route cannot mint a credential past its session expiry."""
    def shorten_token(claims: dict[str, object]) -> None:
        claims["exp"] = time.time() + 30

    provider = _FakeOidcProvider(claim_override=shorten_token)
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config(session_ttl_s=600))
    handler = await _http_handler(tmp_path, authenticator)
    authorization, flow_headers = await _begin(authenticator, provider)
    session, _return_to, cookies = await authenticator.callback(
        flow_headers,
        {"code": ["provider-code"], "state": [authorization["state"]]},
    )
    browser_headers = Headers({
        "Cookie": f"nanobot_oidc_session={_cookie_value(cookies, 'nanobot_oidc_session')}"
    })

    response = handler._handle_token_issue(
        SimpleNamespace(respond=lambda status, body: (status, body)),
        SimpleNamespace(headers=browser_headers),
    )

    payload = json.loads(response.body)
    assert 1 <= payload["expires_in"] <= 30
    taken = handler.tokens.take_issued_token(payload["token"])
    assert taken is not None
    audience, principal, deadline = taken
    assert (audience, principal) == ("client", session.principal)
    assert deadline <= session.expires_at


@pytest.mark.asyncio
async def test_oidc_token_issue_rejects_anonymous_requests(
    tmp_path,
) -> None:
    """OIDC mode never exposes the token issue route to an unauthenticated browser."""
    handler = await _http_handler(tmp_path, OidcAuthenticator(_config()))
    connection = _EndpointConnection()

    response = handler._handle_token_issue(connection, SimpleNamespace(headers=Headers()))

    assert response == (401, "Unauthorized")
    assert connection.responses == [(401, "Unauthorized")]


@pytest.mark.asyncio
async def test_trusted_proxy_token_issue_keeps_an_opaque_non_local_principal(
    tmp_path,
) -> None:
    """A trusted proxy grant survives the headerless handshake without becoming local ownership."""
    handler = await _http_handler(tmp_path, OidcAuthenticator(_config()))
    handler.config.trusted_proxy_auth = TrustedProxyAuthConfig(
        trusted_peer_cidrs=["10.0.0.0/8"],
        assertion_header="X-Proxy-Assertion",
        subject_header="X-Verified-Subject",
    )
    issue_connection = SimpleNamespace(remote_address=("10.1.2.3", 8765))
    issued = handler._handle_token_issue(
        issue_connection,
        SimpleNamespace(headers=Headers({
            "X-Proxy-Assertion": "verified",
            "X-Verified-Subject": "person-42",
        })),
    )
    payload = json.loads(issued.body)
    endpoint = WebUIGatewayEndpoint(
        config=handler.config, http=SimpleNamespace(), tokens=handler.tokens
    )
    connection = _EndpointConnection()

    assert endpoint.authorize_websocket_handshake(
        connection, {"token": [payload["token"]]}, Headers()
    ) is None
    principal = endpoint.oidc_principal(connection)
    assert principal is not None
    assert principal.startswith("proxy:")
    assert "person-42" not in principal
    assert endpoint.is_webui_connection(connection) is False
    request = WsRequest("/api/sessions", Headers())
    setattr(request, "_nanobot_connection", connection)
    identity = await handler._collaboration_identity(request)
    assert identity is not None
    _user, local_owner = identity
    assert local_owner is False


@pytest.mark.asyncio
async def test_oidc_callback_bootstrap_and_logout_bind_and_revoke_one_principal(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One verified OIDC user owns its callback, API/WS credentials, and logout revocation scope."""
    provider = _FakeOidcProvider()
    provider.install(monkeypatch)
    authenticator = OidcAuthenticator(_config())
    handler = await _http_handler(tmp_path, authenticator)
    authorization, flow_headers = await _begin(authenticator, provider, return_to="/projects")

    callback = await handler._handle_oidc_callback(_callback_request(authorization, flow_headers))
    assert callback.status_code == 303
    assert callback.headers["Location"] == "/projects"
    callback_cookies = list(callback.headers.raw_items())
    session_cookie = _cookie_value(callback_cookies, "nanobot_oidc_session")
    browser_headers = Headers({"Cookie": f"nanobot_oidc_session={session_cookie}"})
    session = authenticator.session(browser_headers)
    assert session is not None

    bootstrap = handler._handle_bootstrap(
        SimpleNamespace(remote_address=("203.0.113.42", 8765)),
        WsRequest("/webui/bootstrap", browser_headers),
    )
    payload = json.loads(bootstrap.body)
    assert payload["auth"] == {
        "mode": "oidc",
        "logout_url": "/auth/logout",
        "logout_csrf_token": session.csrf,
        "user": {"name": "Ada OIDC", "email": "ada@example.test"},
    }
    taken = handler.tokens.take_issued_token(payload["token"])
    assert taken is not None
    audience, principal, ws_deadline = taken
    assert (audience, principal) == ("webui", session.principal)
    assert ws_deadline <= session.expires_at
    api_request = WsRequest("/api/sessions", Headers({"Authorization": f"Bearer {payload['api_token']}"}))
    identity = await handler._collaboration_identity(api_request)
    assert identity is not None
    user, local_owner = identity
    assert local_owner is False
    assert user.display_name == "Ada OIDC"
    oidc_user, _project = await handler.collaboration.ensure_identity_user(
        "oidc", session.principal, handler.skills_workspace_path, local_owner=False
    )
    assert user.id == oidc_user.id
    websocket_user, _project = await handler.collaboration.ensure_identity_user(
        "websocket", session.principal, handler.skills_workspace_path, local_owner=False
    )
    assert websocket_user.id == user.id

    other_api_token = handler.tokens.issue_api_token(120, principal="oidc:other")
    missing_csrf = await handler._handle_oidc_logout(
        SimpleNamespace(method="GET", headers=browser_headers)
    )
    assert missing_csrf.status_code == 401
    assert authenticator.session(browser_headers) == session

    wrong_csrf = await handler._handle_oidc_logout(
        SimpleNamespace(
            method="GET",
            headers=Headers({
                "Cookie": f"nanobot_oidc_session={session_cookie}",
                "X-Nanobot-OIDC-CSRF": "wrong-token",
            }),
        )
    )
    assert wrong_csrf.status_code == 401
    assert authenticator.session(browser_headers) == session

    non_get = await handler._handle_oidc_logout(
        SimpleNamespace(
            method="POST",
            headers=Headers({
                "Cookie": f"nanobot_oidc_session={session_cookie}",
                "X-Nanobot-OIDC-CSRF": session.csrf,
            }),
        )
    )
    assert non_get.status_code == 405
    assert non_get.headers["Allow"] == "GET"
    assert authenticator.session(browser_headers) == session
    assert handler.tokens.check_api_token(api_request) is True

    closed_principals: list[str] = []

    async def close_principal(principal: str) -> None:
        closed_principals.append(principal)

    handler._oidc_connection_revoker = close_principal
    logout = await handler._handle_oidc_logout(
        SimpleNamespace(
            method="GET",
            headers=Headers({
                "Cookie": f"nanobot_oidc_session={session_cookie}",
                "X-Nanobot-OIDC-CSRF": session.csrf,
            }),
        )
    )
    assert logout.status_code == 204
    assert closed_principals == [session.principal]
    assert authenticator.session(browser_headers) is None
    assert handler.tokens.check_api_token(api_request) is False
    assert handler.tokens.check_api_token(
        WsRequest("/api/sessions", Headers({"Authorization": f"Bearer {other_api_token}"}))
    ) is True
    assert any(
        cookie.startswith("nanobot_oidc_session=") and "Max-Age=0" in cookie
        for cookie in _set_cookie_values(list(logout.headers.raw_items()))
    )


@pytest.mark.asyncio
async def test_endpoint_expires_principal_bound_client_grants_without_real_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consumed client grant keeps its OIDC principal only until its issued deadline."""
    clock = [100.0]
    monkeypatch.setattr("nanobot.webui.gateway_endpoint.time.monotonic", lambda: clock[0])

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("nanobot.webui.gateway_endpoint.asyncio.sleep", no_sleep)
    config = WebSocketConfig(
        host="127.0.0.1",
        path="/ws",
        token="",
        websocket_requires_token=False,
        oidc_auth=_config(),
    )
    tokens = GatewayTokenStore()
    endpoint = WebUIGatewayEndpoint(config=config, http=SimpleNamespace(), tokens=tokens)
    connection = _EndpointConnection()
    grant = tokens.issue_token(10, audience="client", principal="oidc:alice")

    assert endpoint.authorize_websocket_handshake(
        connection, {"token": [grant]}, Headers()
    ) is None
    assert endpoint.oidc_principal(connection) == "oidc:alice"
    assert endpoint.oidc_connection_active(connection) is True
    assert endpoint.is_webui_connection(connection) is False

    clock[0] = 110.0
    await endpoint.close_oidc_at_deadline(connection)

    assert connection.closed == [(1008, "OIDC credential expired")]
    assert endpoint.oidc_principal(connection) is None
    assert endpoint.oidc_connection_active(connection) is False


@pytest.mark.asyncio
async def test_endpoint_logout_closes_only_connections_for_the_revoked_principal() -> None:
    """Revoking one OIDC principal leaves other authenticated sockets alive."""
    config = WebSocketConfig(
        host="127.0.0.1",
        path="/ws",
        token="",
        websocket_requires_token=False,
        oidc_auth=_config(),
    )
    tokens = GatewayTokenStore()
    endpoint = WebUIGatewayEndpoint(config=config, http=SimpleNamespace(), tokens=tokens)
    alice_first, alice_second, bob = _EndpointConnection(), _EndpointConnection(), _EndpointConnection()

    for connection, principal in (
        (alice_first, "oidc:alice"),
        (alice_second, "oidc:alice"),
        (bob, "oidc:bob"),
    ):
        grant = tokens.issue_token(60, audience="client", principal=principal)
        assert endpoint.authorize_websocket_handshake(
            connection, {"token": [grant]}, Headers()
        ) is None

    await endpoint.close_oidc_principal("oidc:alice")

    assert alice_first.closed == [(1008, "OIDC logout")]
    assert alice_second.closed == [(1008, "OIDC logout")]
    assert endpoint.oidc_connection_active(alice_first) is False
    assert endpoint.oidc_connection_active(alice_second) is False
    assert bob.closed == []
    assert endpoint.oidc_principal(bob) == "oidc:bob"


def test_oidc_enabled_gateway_rejects_tokenless_websocket_handshakes() -> None:
    """OIDC mode never grants a WebSocket upgrade without a one-time gateway credential."""
    config = WebSocketConfig(
        host="127.0.0.1",
        path="/ws",
        token="",
        websocket_requires_token=False,
        oidc_auth=_config(),
    )
    tokens = GatewayTokenStore()
    endpoint = WebUIGatewayEndpoint(config=config, http=SimpleNamespace(), tokens=tokens)
    rejected: list[tuple[int, str]] = []
    connection = SimpleNamespace(respond=lambda status, body: rejected.append((status, body)))

    result = endpoint.authorize_websocket_handshake(connection, {}, Headers())

    assert result is None
    assert rejected == [(401, "Unauthorized")]
