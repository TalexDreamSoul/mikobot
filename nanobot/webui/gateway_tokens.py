"""Token state for the embedded WebUI gateway."""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from websockets.http11 import Request as WsRequest

from nanobot.webui.http_utils import bearer_token, parse_query, query_first

IssuedTokenAudience = Literal["client", "webui"]


@dataclass
class GatewayTokenStore:
    """Own short-lived WebSocket and WebUI API tokens for one gateway process."""

    max_tokens: int = 10_000
    issued_tokens: dict[str, float] = field(default_factory=dict)
    issued_token_audiences: dict[str, IssuedTokenAudience] = field(default_factory=dict)
    issued_token_principals: dict[str, str] = field(default_factory=dict)
    api_tokens: dict[str, float] = field(default_factory=dict)
    api_token_principals: dict[str, str] = field(default_factory=dict)

    def check_api_token(self, request: WsRequest) -> bool:
        return self.api_token_principal(request) is not None or self._api_token_valid(request)

    def api_token_principal(self, request: WsRequest) -> str | None:
        token = self._request_api_token(request)
        if not token:
            return None
        self._purge_expired_api_tokens()
        if token not in self.api_tokens:
            return None
        return self.api_token_principals.get(token)

    def _api_token_valid(self, request: WsRequest) -> bool:
        token = self._request_api_token(request)
        if not token:
            return False
        self._purge_expired_api_tokens()
        return token in self.api_tokens

    @staticmethod
    def _request_api_token(request: WsRequest) -> str | None:
        return bearer_token(request.headers) or query_first(parse_query(request.path), "token")

    def can_issue(self, *, include_api_token: bool = False) -> bool:
        self._purge_expired_issued_tokens()
        self._purge_expired_api_tokens()
        if len(self.issued_tokens) >= self.max_tokens:
            return False
        if include_api_token and len(self.api_tokens) >= self.max_tokens:
            return False
        return True

    def issue_token(
        self,
        ttl_s: int | float,
        *,
        audience: IssuedTokenAudience = "client",
        principal: str | None = None,
    ) -> str:
        token_value = f"nbwt_{secrets.token_urlsafe(32)}"
        self._purge_expired_issued_tokens()
        if len(self.issued_tokens) >= self.max_tokens:
            raise RuntimeError("too many outstanding gateway tokens")
        self.issued_tokens[token_value] = time.monotonic() + float(ttl_s)
        self.issued_token_audiences[token_value] = audience
        if principal:
            self.issued_token_principals[token_value] = principal
        return token_value

    def issue_api_token(self, ttl_s: int | float, *, principal: str | None = None) -> str:
        token_value = f"nbwt_{secrets.token_urlsafe(32)}"
        self._purge_expired_api_tokens()
        if len(self.api_tokens) >= self.max_tokens:
            raise RuntimeError("too many outstanding gateway tokens")
        self.api_tokens[token_value] = time.monotonic() + float(ttl_s)
        if principal:
            self.api_token_principals[token_value] = principal
        return token_value

    def take_issued_token(
        self,
        token_value: str | None,
    ) -> tuple[IssuedTokenAudience, str | None, float] | None:
        if not token_value:
            return None
        self._purge_expired_issued_tokens()
        expiry = self.issued_tokens.pop(token_value, None)
        if expiry is None:
            self.issued_token_audiences.pop(token_value, None)
            self.issued_token_principals.pop(token_value, None)
            return None
        audience = self.issued_token_audiences.pop(token_value, "client")
        principal = self.issued_token_principals.pop(token_value, None)
        if time.monotonic() > expiry:
            return None
        return audience, principal, expiry

    def take_issued_token_audience(self, token_value: str | None) -> IssuedTokenAudience | None:
        taken = self.take_issued_token(token_value)
        return taken[0] if taken is not None else None

    def clear(self) -> None:
        self.issued_tokens.clear()
        self.issued_token_audiences.clear()
        self.issued_token_principals.clear()
        self.api_tokens.clear()
        self.api_token_principals.clear()

    def revoke_principal(self, principal: str) -> None:
        """Invalidate all bounded WebUI credentials issued to one principal."""
        for token, token_principal in list(self.issued_token_principals.items()):
            if hmac.compare_digest(token_principal, principal):
                self.issued_tokens.pop(token, None)
                self.issued_token_audiences.pop(token, None)
                self.issued_token_principals.pop(token, None)
        for token, token_principal in list(self.api_token_principals.items()):
            if hmac.compare_digest(token_principal, principal):
                self.api_tokens.pop(token, None)
                self.api_token_principals.pop(token, None)

    def _purge_expired_api_tokens(self) -> None:
        now = time.monotonic()
        for token_key, expiry in list(self.api_tokens.items()):
            if now > expiry:
                self.api_tokens.pop(token_key, None)
                self.api_token_principals.pop(token_key, None)

    def _purge_expired_issued_tokens(self) -> None:
        now = time.monotonic()
        for token_key, expiry in list(self.issued_tokens.items()):
            if now > expiry:
                self.issued_tokens.pop(token_key, None)
                self.issued_token_audiences.pop(token_key, None)
                self.issued_token_principals.pop(token_key, None)


def token_response_payload(token: str, expires_in: Any) -> dict[str, Any]:
    return {"token": token, "expires_in": expires_in}
