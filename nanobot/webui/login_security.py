"""Safe WebUI management for embedded OIDC login configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from nanobot.config.schema import Config

if TYPE_CHECKING:
    from nanobot.channels.websocket.runtime import OidcAuthConfig, WebSocketConfig


class LoginSecuritySettingsError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        fields: list[dict[str, str]] | None = None,
        status: int = 400,
    ) -> None:
        super().__init__(message)
        self.fields = fields or []
        self.status = status


def oidc_settings_payload(config: Config) -> dict[str, object]:
    oidc = _websocket_config(config).oidc_auth
    return {
        "enabled": oidc.enabled,
        "issuer": oidc.issuer,
        "client_id": oidc.client_id,
        "client_secret_configured": bool(oidc.client_secret),
        "redirect_uri": oidc.redirect_uri,
        "scopes": list(oidc.scopes),
        "admin_subjects": list(oidc.admin_subjects),
        "token_endpoint_auth_method": oidc.token_endpoint_auth_method,
        "session_ttl_s": oidc.session_ttl_s,
        "flow_ttl_s": oidc.flow_ttl_s,
        "session_capacity": oidc.session_capacity,
        "flow_capacity": oidc.flow_capacity,
        "restart_required_after_save": True,
    }


def build_oidc_update(config: Config, values: Mapping[str, object]) -> OidcAuthConfig:
    from nanobot.channels.websocket.runtime import OidcAuthConfig

    current = _websocket_config(config).oidc_auth
    payload = current.model_dump(mode="json", by_alias=True)
    field_map = {
        "enabled": "enabled",
        "issuer": "issuer",
        "client_id": "clientId",
        "redirect_uri": "redirectUri",
        "scopes": "scopes",
        "admin_subjects": "adminSubjects",
        "token_endpoint_auth_method": "tokenEndpointAuthMethod",
        "session_ttl_s": "sessionTtlS",
        "flow_ttl_s": "flowTtlS",
        "session_capacity": "sessionCapacity",
        "flow_capacity": "flowCapacity",
    }
    for source, target in field_map.items():
        if source in values:
            payload[target] = values[source]
    if values.get("clear_client_secret") is True:
        payload["clientSecret"] = ""
    elif "client_secret" in values:
        payload["clientSecret"] = values["client_secret"]
    try:
        return OidcAuthConfig.model_validate(payload)
    except ValidationError as exc:
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": str(error["msg"]),
            }
            for error in exc.errors(include_url=False, include_input=False)
        ]
        raise LoginSecuritySettingsError("invalid OIDC configuration", fields=fields) from exc


def apply_oidc_update(
    config: Config,
    oidc: OidcAuthConfig,
    *,
    expected: OidcAuthConfig | None = None,
) -> None:
    if expected is not None and _websocket_config(config).oidc_auth != expected:
        raise LoginSecuritySettingsError(
            "OIDC configuration changed; reload and retry",
            status=409,
        )
    current_section = getattr(config.channels, "websocket", None)
    if current_section is not None and hasattr(current_section, "model_dump"):
        section = current_section.model_dump(mode="json", by_alias=True)
    elif isinstance(current_section, dict):
        section = dict(cast(dict[str, Any], current_section))
    else:
        section = {}
    section["oidcAuth"] = oidc.model_dump(mode="json", by_alias=True)
    setattr(config.channels, "websocket", section)


def _websocket_config(config: Config) -> WebSocketConfig:
    from nanobot.channels.websocket.runtime import WebSocketConfig

    section = getattr(config.channels, "websocket", None)
    if section is not None and hasattr(section, "model_dump"):
        raw = section.model_dump(mode="json", by_alias=True)
    elif isinstance(section, dict):
        raw = dict(cast(dict[str, Any], section))
    else:
        raw = {}
    return WebSocketConfig.model_validate(raw)
