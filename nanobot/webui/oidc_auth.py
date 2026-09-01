"""Bounded, provider-neutral OpenID Connect Authorization Code + PKCE support."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode, urlsplit

import httpx
from joserfc import jwt
from joserfc.jwk import DictKey, KeySet, KeySetSerialization

from nanobot.webui.http_utils import case_insensitive_header

if TYPE_CHECKING:
    from nanobot.channels.websocket.runtime import OidcAuthConfig


_MAX_DISCOVERY_BYTES = 64 * 1024
_MAX_JWKS_BYTES = 128 * 1024
_MAX_TOKEN_RESPONSE_BYTES = 64 * 1024
_MAX_ID_TOKEN_CHARS = 16 * 1024
_MAX_CODE_CHARS = 8 * 1024
_MAX_STATE_CHARS = 256
_MAX_NAME_CHARS = 256
_MAX_EMAIL_CHARS = 320
_ALLOWED_ALGORITHMS = frozenset({
    "RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
    "ES256", "ES384", "ES512", "EdDSA",
})


@dataclass(frozen=True)
class OidcSession:
    """Only the display-safe projection of a verified OIDC identity."""

    principal: str
    name: str
    email: str | None
    csrf: str
    expires_at: float


@dataclass(frozen=True)
class _Flow:
    browser: str
    nonce: str
    verifier: str
    return_to: str
    expires_at: float


class OidcError(Exception):
    """A deliberately non-provider-specific OIDC failure."""


class OidcCapacityError(OidcError):
    """A bounded in-memory OIDC store has no safe remaining capacity."""


def safe_return_to(value: str | None) -> str:
    """Keep navigation on this origin and away from auth routes."""
    value = (value or "/").strip()
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in value
        or any(ord(char) < 0x20 for char in value)
        or len(value) > 2048
        or parsed.path.startswith("/auth/")
    ):
        return "/"
    return value


def _cookie_values(headers: Any) -> dict[str, str]:
    raw = case_insensitive_header(headers, "Cookie")
    result: dict[str, str] = {}
    for part in raw.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key and len(key) <= 64 and len(value) <= 512:
            result[key] = value
    return result


def _cookie(name: str, value: str, *, max_age: int, secure: bool, clear: bool = False) -> tuple[str, str]:
    if clear:
        value, max_age = "", 0
    attributes = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
    if secure:
        attributes.append("Secure")
    return "Set-Cookie", "; ".join(attributes)


def _required_text(value: object, name: str, *, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise OidcError(f"invalid {name}")
    return value


def _endpoint(value: object, name: str) -> str:
    text = _required_text(value, name)
    parsed = urlsplit(text)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        raise OidcError(f"invalid {name}")
    host = parsed.hostname or ""
    if parsed.scheme != "https" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise OidcError(f"insecure {name}")
    if parsed.fragment:
        raise OidcError(f"invalid {name}")
    return text


def _bounded_claim(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 0x20 for char in value):
        return None
    return value


@dataclass(frozen=True)
class _ProviderMetadata:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    algorithms: tuple[str, ...]


class OidcAuthenticator:
    """Own process-local OIDC browser flows and sessions; provider tokens never persist."""

    def __init__(self, config: OidcAuthConfig) -> None:
        self._config = config
        self._flows: OrderedDict[str, _Flow] = OrderedDict()
        self._sessions: OrderedDict[str, OidcSession] = OrderedDict()
        self._metadata: _ProviderMetadata | None = None
        self._metadata_expires_at = 0.0

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def session(self, headers: Any) -> OidcSession | None:
        self._purge()
        value = _cookie_values(headers).get("nanobot_oidc_session")
        if not value:
            return None
        session = self._sessions.get(value)
        if session is None or session.expires_at <= time.monotonic():
            self._sessions.pop(value, None)
            return None
        self._sessions.move_to_end(value)
        return session

    async def begin(self, headers: Any, return_to: str | None) -> tuple[str, list[tuple[str, str]]]:
        self._purge()
        metadata = await self._discovery()
        browser = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        expires_at = time.monotonic() + self._config.flow_ttl_s
        if len(self._flows) >= self._config.flow_capacity:
            raise OidcCapacityError("too many active login flows")
        self._flows[state] = _Flow(
            browser, nonce, verifier, safe_return_to(return_to), expires_at
        )
        params = {
            "response_type": "code", "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri, "scope": " ".join(self._config.scopes),
            "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        separator = "&" if "?" in metadata.authorization_endpoint else "?"
        return f"{metadata.authorization_endpoint}{separator}{urlencode(params)}", [
            _cookie("nanobot_oidc_flow", browser, max_age=self._config.flow_ttl_s, secure=self._cookie_secure()),
        ]

    async def callback(self, headers: Any, query: Mapping[str, list[str]]) -> tuple[OidcSession, str, list[tuple[str, str]]]:
        self._purge()
        state = _query_value(query, "state", _MAX_STATE_CHARS)
        code = _query_value(query, "code", _MAX_CODE_CHARS)
        browser = _cookie_values(headers).get("nanobot_oidc_flow", "")
        flow = self._flows.get(state) if state else None
        clear_flow = _cookie("nanobot_oidc_flow", "", max_age=0, secure=self._cookie_secure(), clear=True)
        if not state or not flow or not code or not browser or not hmac.compare_digest(browser, flow.browser) or flow.expires_at <= time.monotonic():
            raise OidcError("invalid login flow")
        self._flows.pop(state, None)
        metadata = await self._discovery()
        tokens = await self._exchange_code(metadata, code, flow.verifier)
        try:
            id_token = tokens.get("id_token")
            claims = await self._validate_id_token(metadata, id_token, flow.nonce)
        finally:
            tokens.clear()
        subject = _required_text(claims.get("sub"), "subject", limit=255)
        material = f"nanobot/oidc-principal/v1\0{self._config.issuer}\0{subject}".encode("utf-8")
        principal = f"oidc:{hashlib.sha256(material).hexdigest()}"
        name = _bounded_claim(claims.get("name"), _MAX_NAME_CHARS) or _bounded_claim(claims.get("preferred_username"), _MAX_NAME_CHARS) or "OIDC user"
        email = _bounded_claim(claims.get("email"), _MAX_EMAIL_CHARS)
        raw_exp = claims.get("exp")
        if not isinstance(raw_exp, (int, float)) or isinstance(raw_exp, bool):
            raise OidcError("invalid ID token lifetime")
        remaining = min(float(self._config.session_ttl_s), raw_exp - time.time())
        if remaining <= 0:
            raise OidcError("expired ID token")
        session = OidcSession(
            principal, name, email, secrets.token_urlsafe(32), time.monotonic() + remaining
        )
        session_id = secrets.token_urlsafe(32)
        self._bounded_put(self._sessions, session_id, session, self._config.session_capacity)
        return session, flow.return_to, [
            clear_flow,
            _cookie(
                "nanobot_oidc_session",
                session_id,
                max_age=max(1, int(remaining)),
                secure=self._cookie_secure(),
            ),
        ]

    def csrf_valid(self, headers: Any, session: OidcSession) -> bool:
        supplied = case_insensitive_header(headers, "X-Nanobot-OIDC-CSRF")
        return bool(supplied) and hmac.compare_digest(supplied, session.csrf)

    def logout(self, headers: Any) -> list[tuple[str, str]]:
        session_id = _cookie_values(headers).get("nanobot_oidc_session")
        if session_id:
            self._sessions.pop(session_id, None)
        return [
            _cookie("nanobot_oidc_session", "", max_age=0, secure=self._cookie_secure(), clear=True),
            _cookie("nanobot_oidc_flow", "", max_age=0, secure=self._cookie_secure(), clear=True),
        ]

    async def _discovery(self) -> _ProviderMetadata:
        if self._metadata is not None and self._metadata_expires_at > time.monotonic():
            return self._metadata
        url = f"{self._config.issuer.rstrip('/')}/.well-known/openid-configuration"
        payload = await self._fetch_json(url, _MAX_DISCOVERY_BYTES)
        if payload.get("issuer") != self._config.issuer:
            raise OidcError("issuer mismatch")
        authorization_endpoint = _endpoint(payload.get("authorization_endpoint"), "authorization endpoint")
        token_endpoint = _endpoint(payload.get("token_endpoint"), "token endpoint")
        jwks_uri = _endpoint(payload.get("jwks_uri"), "JWKS URI")
        algorithms = tuple(
            algorithm for algorithm in _required_string_list(
                payload.get("id_token_signing_alg_values_supported"), "signing algorithms"
            ) if algorithm in _ALLOWED_ALGORITHMS
        )
        if not algorithms:
            raise OidcError("no supported signing algorithms")
        if "code" not in _required_string_list(payload.get("response_types_supported"), "response types"):
            raise OidcError("authorization code flow is not supported")
        grant_types = payload.get("grant_types_supported")
        if grant_types is not None and "authorization_code" not in _required_string_list(grant_types, "grant types"):
            raise OidcError("authorization code grant is not supported")
        methods = payload.get("token_endpoint_auth_methods_supported")
        if methods is not None and self._config.token_endpoint_auth_method not in _required_string_list(methods, "token endpoint auth methods"):
            raise OidcError("unsupported token endpoint auth method")
        pkce_methods = payload.get("code_challenge_methods_supported")
        if pkce_methods is not None and "S256" not in _required_string_list(pkce_methods, "PKCE methods"):
            raise OidcError("provider does not support PKCE S256")
        self._metadata = _ProviderMetadata(authorization_endpoint, token_endpoint, jwks_uri, algorithms)
        self._metadata_expires_at = time.monotonic() + min(self._config.flow_ttl_s, 300)
        return self._metadata

    async def _exchange_code(self, metadata: _ProviderMetadata, code: str, verifier: str) -> dict[str, object]:
        data = {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self._config.redirect_uri, "client_id": self._config.client_id,
            "code_verifier": verifier,
        }
        auth: tuple[str, str] | None = None
        method = self._config.token_endpoint_auth_method
        if method == "client_secret_basic":
            auth = (self._config.client_id, self._config.client_secret)
            data.pop("client_id")
        elif method == "client_secret_post":
            data["client_secret"] = self._config.client_secret
        elif method != "none":
            raise OidcError("unsupported token endpoint auth method")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
                async with client.stream(
                    "POST", metadata.token_endpoint, data=data, auth=auth,
                    headers={"Accept": "application/json"},
                ) as response:
                    if response.status_code != 200:
                        raise OidcError("token exchange failed")
                    body = await _bounded_response_body(response, _MAX_TOKEN_RESPONSE_BYTES)
        except httpx.HTTPError as exc:
            raise OidcError("token exchange failed") from exc
        token_response = _json_object(body, "invalid token response")
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str) or not id_token or len(id_token) > _MAX_ID_TOKEN_CHARS:
            raise OidcError("missing ID token")
        return token_response

    async def _validate_id_token(self, metadata: _ProviderMetadata, value: object, nonce: str) -> dict[str, object]:
        if not isinstance(value, str) or not value or len(value) > _MAX_ID_TOKEN_CHARS:
            raise OidcError("invalid ID token")
        jwks = _key_set_serialization(await self._fetch_json(metadata.jwks_uri, _MAX_JWKS_BYTES))
        try:
            token = jwt.decode(value, KeySet.import_key_set(jwks), algorithms=metadata.algorithms)
        except Exception as exc:
            raise OidcError("invalid ID token") from exc
        claims = _object_mapping(token.claims, "invalid ID token")
        if claims.get("iss") != self._config.issuer or claims.get("nonce") != nonce:
            raise OidcError("invalid ID token claims")
        audience = claims.get("aud")
        if isinstance(audience, str):
            audiences = (audience,)
        elif isinstance(audience, list):
            audiences = tuple(
                _required_string_list(cast(object, audience), "ID token audience")
            )
        else:
            raise OidcError("invalid ID token audience")
        if self._config.client_id not in audiences:
            raise OidcError("invalid ID token audience")
        azp = claims.get("azp")
        if azp is not None and (not isinstance(azp, str) or azp != self._config.client_id):
            raise OidcError("invalid ID token azp")
        if len(audiences) > 1 and azp != self._config.client_id:
            raise OidcError("invalid ID token azp")
        now = time.time()
        exp, issued_at = claims.get("exp"), claims.get("iat")
        if (
            not isinstance(exp, (int, float)) or isinstance(exp, bool)
            or not isinstance(issued_at, (int, float)) or isinstance(issued_at, bool)
            or not math.isfinite(exp) or not math.isfinite(issued_at)
            or exp <= now or issued_at > now + 60 or issued_at > exp
        ):
            raise OidcError("invalid ID token lifetime")
        not_before = claims.get("nbf")
        if not_before is not None and (
            not isinstance(not_before, (int, float))
            or isinstance(not_before, bool)
            or not math.isfinite(not_before)
            or not_before > now + 60
        ):
            raise OidcError("invalid ID token lifetime")
        return claims

    async def _fetch_json(self, url: str, maximum: int) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
                async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                    if response.status_code != 200:
                        raise OidcError("invalid OIDC provider response")
                    body = await _bounded_response_body(response, maximum)
        except httpx.HTTPError as exc:
            raise OidcError("OIDC provider unavailable") from exc
        return _json_object(body, "invalid OIDC provider response")

    def _purge(self) -> None:
        now = time.monotonic()
        for store in (self._flows, self._sessions):
            for key, value in list(store.items()):
                if value.expires_at <= now:
                    store.pop(key, None)

    @staticmethod
    def _bounded_put(store: OrderedDict[str, Any], key: str, value: Any, capacity: int) -> None:
        while len(store) >= capacity:
            store.popitem(last=False)
        store[key] = value

    def _cookie_secure(self) -> bool:
        return urlsplit(self._config.redirect_uri).scheme == "https"



async def _bounded_response_body(response: httpx.Response, maximum: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > maximum:
                raise OidcError("OIDC response is too large")
        except ValueError:
            raise OidcError("invalid OIDC response length") from None
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > maximum:
            raise OidcError("OIDC response is too large")
    return bytes(body)


def _json_object(body: bytes, message: str) -> dict[str, object]:
    try:
        value = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise OidcError(message) from exc
    return _object_mapping(value, message)


def _object_mapping(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise OidcError(message)
    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise OidcError(message)
        result[key] = item
    return result


def _required_string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list):
        raise OidcError(f"invalid {name}")
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str):
            raise OidcError(f"invalid {name}")
        result.append(item)
    return result


def _key_set_serialization(value: Mapping[str, object]) -> KeySetSerialization:
    raw_keys = value.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise OidcError("invalid JWKS")
    keys: list[DictKey] = []
    for raw_key in cast(list[object], raw_keys):
        key = _object_mapping(raw_key, "invalid JWKS")
        serialized: DictKey = {}
        for name, item in key.items():
            if isinstance(item, str):
                serialized[name] = item
                continue
            if not isinstance(item, list):
                raise OidcError("invalid JWKS")
            entries: list[str] = []
            for entry in cast(list[object], item):
                if not isinstance(entry, str):
                    raise OidcError("invalid JWKS")
                entries.append(entry)
            serialized[name] = entries
        keys.append(serialized)
    return {"keys": keys}

def _query_value(query: Mapping[str, list[str]], key: str, limit: int) -> str | None:
    values = query.get(key)
    if not values or len(values) != 1:
        return None
    value = values[0]
    return value if value and len(value) <= limit else None
