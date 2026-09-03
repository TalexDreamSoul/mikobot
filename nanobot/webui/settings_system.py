"""System and channel settings domain logic."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast
from zoneinfo import ZoneInfo

from nanobot.channels.connect import ChannelConnectError
from nanobot.config.schema import Config
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionLifecycle,
    ExtensionSource,
    safe_extension_message,
)
from nanobot.extensions.targets import resolve_extension_package_target
from nanobot.llm_usage import llm_usage_payload
from nanobot.optional_features import OptionalFeatureError
from nanobot.security.workspace_access import workspace_sandbox_status
from nanobot.utils.redaction import redact_credentials
from nanobot.webui.nanobot_features_api import (
    execute_nanobot_extension_action,
    nanobot_features_payload,
    resolve_nanobot_feature_target,
    restricted_nanobot_features_payload,
)
from nanobot.webui.settings_capabilities import network_safety_payload
from nanobot.webui.settings_contracts import (
    QueryParams,
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
    query_first,
    query_first_alias,
)

if TYPE_CHECKING:
    from nanobot.webui.settings_services import WebUISettingsServices

LoadChannelPlugin = Callable[[str], Any]
ListPendingPairings = Callable[[], Iterable[dict[str, Any]]]
SettingsOperation = Callable[..., Any]
def _sanitize_connector_payload(payload: Mapping[str, object]) -> dict[str, Any]:
    """Copy connector output while keeping public diagnostic fields bounded."""
    sanitized = dict(payload)
    for field in ("message", "error"):
        if field in sanitized:
            sanitized[field] = safe_extension_message(sanitized[field])
    return sanitized


_MAX_ACTION_ERROR = 400


def _safe_action_error(exc: Exception) -> str:
    """Bound and redact a domain error before it reaches an HTTP response body.

    Host paths survive here on purpose, unlike in `_sanitize_connector_payload`: the
    connect flow is reachable by a member, while both callers of this helper are
    administrator-only, the reader can already open the config file, and
    ``npx not found at /usr/local/bin`` is the useful half of the message.

    What must not survive is a credential — package managers echo index URLs such as
    ``https://user:token@pypi.internal/simple`` — or an unbounded tail:
    `nanobot/apps/cli/service.py` caps its captured stderr at 12_000 characters, which
    is thirty times what an error field should carry.
    """
    message = getattr(exc, "message", None)
    text = message if isinstance(message, str) else str(exc)
    return redact_credentials(text.strip())[:_MAX_ACTION_ERROR] or "Action failed."




@dataclass(frozen=True)
class SystemSettingsOperations:
    cli_apps_payload: SettingsOperation
    cli_apps_action: SettingsOperation
    validate_channel_config: SettingsOperation
    load_channel_plugin: LoadChannelPlugin
    list_pending: ListPendingPairings
    approve_code: SettingsOperation
    deny_code: SettingsOperation
    mcp_presets_action: SettingsOperation
    reload_mcp: SettingsOperation
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None
    check_for_update: SettingsOperation
    channel_pairing_action: SettingsOperation | None = None
_MAX_PAIRING_ACKNOWLEDGEMENTS = 256


class SystemSettingsPayload(TypedDict):
    runtime: dict[str, Any]
    usage: dict[str, Any]
    advanced: dict[str, Any]
    version: dict[str, Any]
    docs: dict[str, Any]


_DOCS_STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:\.post\d+)?$")
_DOCS_LATEST_URL = "https://nanobot.wiki/docs/latest"


def docs_version(version: str) -> str:
    """Map package versions to the matching public docs path."""
    normalized = version.strip()
    if _DOCS_STABLE_VERSION_RE.fullmatch(normalized):
        return normalized
    return "latest"


def docs_payload(version: str) -> dict[str, Any]:
    selected_version = docs_version(version)
    base_url = f"https://nanobot.wiki/docs/{selected_version}"
    return {
        "version": selected_version,
        "base_url": base_url,
        "chat_apps_url": f"{base_url}/getting-started/chat-apps",
        "latest_url": _DOCS_LATEST_URL,
    }


def system_settings_payload(
    config: Config,
    *,
    config_path: Path,
    version: str,
) -> SystemSettingsPayload:
    defaults = config.agents.defaults
    exec_config = config.tools.exec
    sandbox_status = workspace_sandbox_status(
        restrict_to_workspace=config.tools.restrict_to_workspace,
        workspace=config.workspace_path,
    )
    return {
        "runtime": {
            "config_path": str(config_path.expanduser()),
            "workspace_path": str(config.workspace_path),
            "gateway_host": config.gateway.host,
            "gateway_port": config.gateway.port,
            "heartbeat": {
                "enabled": config.gateway.heartbeat.enabled,
                "interval_s": config.gateway.heartbeat.interval_s,
                "keep_recent_messages": config.gateway.heartbeat.keep_recent_messages,
            },
            "dream": {
                "schedule": defaults.dream.describe_schedule(),
            },
            "unified_session": defaults.unified_session,
        },
        "usage": llm_usage_payload(timezone_name=defaults.timezone),
        "advanced": {
            "restrict_to_workspace": config.tools.restrict_to_workspace,
            "workspace_sandbox": sandbox_status.as_dict(),
            **network_safety_payload(config),
            "mcp_server_count": len(config.tools.mcp_servers),
            "exec_enabled": exec_config.enable,
            "exec_sandbox": exec_config.sandbox or None,
            "exec_path_prepend_set": bool(exec_config.path_prepend),
            "exec_path_append_set": bool(exec_config.path_append),
        },
        "version": {"current": version},
        "docs": docs_payload(version),
    }


def settings_usage_payload(config: Config) -> dict[str, Any]:
    """Return the lightweight token usage slice for Overview refreshes."""
    return llm_usage_payload(timezone_name=config.agents.defaults.timezone)


def update_agent_system_settings(config: Config, query: QueryParams) -> tuple[bool, bool]:
    defaults = config.agents.defaults
    changed = False
    restart_required = False

    timezone = query_first(query, "timezone")
    if timezone is not None:
        timezone = timezone.strip()
        if not timezone:
            raise WebUISettingsError("timezone is required")
        try:
            ZoneInfo(timezone)
        except Exception:
            raise WebUISettingsError("invalid timezone") from None
        timezone_changed = defaults.timezone != timezone
        if timezone_changed or defaults.timezone_mode != "manual":
            defaults.timezone = timezone
            defaults.timezone_mode = "manual"
            changed = True
            restart_required = timezone_changed

    tool_hint_max_length = query_first_alias(
        query,
        "tool_hint_max_length",
        "toolHintMaxLength",
    )
    if tool_hint_max_length is not None:
        try:
            parsed = int(tool_hint_max_length)
        except ValueError:
            raise WebUISettingsError(
                "tool_hint_max_length must be an integer"
            ) from None
        if parsed < 20 or parsed > 500:
            raise WebUISettingsError(
                "tool_hint_max_length must be between 20 and 500"
            )
        if defaults.tool_hint_max_length != parsed:
            defaults.tool_hint_max_length = parsed
            changed = True
            restart_required = True
    return changed, restart_required


def pairing_payload(
    list_pending: ListPendingPairings,
    last_action: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    current_time = time.time() if now is None else now
    requests: list[dict[str, Any]] = []
    for item in list_pending():
        expires_at = float(item.get("expires_at", 0) or 0)
        created_at = float(item.get("created_at", 0) or 0)
        requests.append(
            {
                "code": str(item.get("code", "")),
                "channel": str(item.get("channel", "")),
                "sender_id": str(item.get("sender_id", "")),
                "created_at_ms": int(created_at * 1000) if created_at else None,
                "expires_at_ms": int(expires_at * 1000) if expires_at else None,
                "expires_in_seconds": (
                    max(0, int(expires_at - current_time)) if expires_at else None
                ),
            }
        )
    payload: dict[str, Any] = {"requests": requests}
    if last_action is not None:
        payload["last_action"] = last_action
    return payload


def _requires_system_admin(action: str) -> bool:
    """Report whether a system action reads or mutates host-wide privileged state.

    Pairing hands out the codes that grant an external DM sender access to a bot, and
    the CLI App actions install and remove host packages, so both families are closed
    to anyone but a server-derived administrator.
    """
    if action.startswith("pairing-"):
        return True
    return action.startswith("cli-") and action != "cli-list"


def _is_server_derived_admin(request: SettingsRequest) -> bool:
    """Report whether the transport proved an administrator, not whether one was claimed.

    Both halves matter: `system_admin` is only ever set from a resolved collaboration
    identity, and an empty actor means that identity was never derived, so a request
    carrying one without the other is refused rather than trusted.
    """
    return request.system_admin is True and bool((request.actor_user_id or "").strip())


class SystemSettingsHandler:
    """Handle channel and system commands behind a transport-neutral request DTO."""

    def __init__(self, settings: WebUISettingsServices, logger: Any) -> None:
        self.settings = settings
        self.logger = logger
        self._channel_connectors: dict[str, Any] = {}
        self._channel_pairing_acknowledgements: set[tuple[str, str, str]] = set()

    def _remember_pairing_acknowledgement(
        self,
        request: SettingsRequest,
        channel_name: str,
        action: str,
        payload: Mapping[str, object],
        acknowledged: bool,
    ) -> None:
        session_id = payload.get("session_id")
        actor_id = request.actor_user_id
        if not isinstance(session_id, str) or not session_id or not actor_id:
            return
        key = (channel_name, session_id, actor_id)
        if action == "start":
            if acknowledged:
                while len(self._channel_pairing_acknowledgements) >= _MAX_PAIRING_ACKNOWLEDGEMENTS:
                    self._channel_pairing_acknowledgements.pop()
                self._channel_pairing_acknowledgements.add(key)
            else:
                self._channel_pairing_acknowledgements.discard(key)
        elif action == "cancel" or payload.get("status") in {
            "cancelled",
            "expired",
            "failed",
        }:
            self._channel_pairing_acknowledgements.discard(key)

    def _consume_pairing_acknowledgement(
        self,
        request: SettingsRequest,
        channel_name: str,
        action: str,
        acknowledged: bool,
    ) -> bool:
        if action == "start":
            return acknowledged
        session_id = (query_first(request.query, "session_id") or "").strip()
        actor_id = request.actor_user_id
        if not session_id or not actor_id:
            return False
        key = (channel_name, session_id, actor_id)
        stored = key in self._channel_pairing_acknowledgements
        self._channel_pairing_acknowledgements.discard(key)
        return stored

    async def handle(
        self,
        action: str,
        request: SettingsRequest,
        operations: SystemSettingsOperations,
        *,
        channel_name: str | None = None,
        connect_action: str | None = None,
    ) -> SettingsRouteResult:
        if _requires_system_admin(action) and not _is_server_derived_admin(request):
            return SettingsRouteResult.failure(
                403, "System administrator access is required"
            )
        if action == "cli-list":
            return await self._cli_apps(request, operations)
        if action.startswith("cli-"):
            return await self._cli_apps_action(
                request,
                action.removeprefix("cli-"),
                operations,
            )
        if action == "features-list":
            return await self._features(request)
        if action in {"features-enable", "features-disable"}:
            return await self._features_action(
                request,
                action.removeprefix("features-"),
                operations,
            )
        if action == "channel-validate":
            return await self._channel_validate(request, operations)
        if action == "channel-configure":
            return await self._channel_configure(request, operations)
        if action == "channel-connect" and channel_name and connect_action:
            return await self._channel_connect(
                request,
                channel_name,
                connect_action,
                operations,
            )
        if action == "pairing-list":
            return SettingsRouteResult.success(pairing_payload(operations.list_pending))
        if action in {"pairing-approve", "pairing-deny"}:
            return self._pairing_action(
                request,
                action.removeprefix("pairing-"),
                operations,
            )
        if action == "mcp-list":
            return await self._mcp_presets(request, None, operations)
        if action.startswith("mcp-"):
            return await self._mcp_presets(
                request,
                action.removeprefix("mcp-"),
                operations,
            )
        if action == "version-check":
            return await self._version_check(operations)
        return SettingsRouteResult.failure(404, "unknown settings action")

    async def _cli_apps(
        self,
        request: SettingsRequest,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        installed_only = (query_first(request.query, "installed_only") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        try:
            payload = await operations.cli_apps_payload(
                installed_only=installed_only,
                config_path=self.settings.config.path,
            )
        except Exception:
            self.logger.exception("failed to load CLI Apps payload")
            return SettingsRouteResult.failure(500, "failed to load CLI Apps")
        return SettingsRouteResult.success(payload)

    async def _cli_apps_action(
        self,
        request: SettingsRequest,
        action: str,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        try:
            payload = await asyncio.to_thread(
                operations.cli_apps_action,
                action,
                request.query,
                config_path=self.settings.config.path,
            )
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception as exc:
            status = getattr(exc, "status", 500)
            message = _safe_action_error(exc)
            if status >= 500:
                self.logger.exception("CLI Apps action '{}' failed", action)
            return SettingsRouteResult.failure(status, message)
        return SettingsRouteResult.success(payload)

    def _registry(self):
        registry = self.settings.extensions
        if registry is None:
            raise OptionalFeatureError("extension registry is unavailable", status=503)
        return registry

    def _features_payload(
        self,
        request: SettingsRequest,
        last_action: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Build the host inventory only for a caller who administers the host.

        The gate is `host_admin`, not `system_admin`: the latter is raised for a member
        acting on a channel instance they own, which authorizes that action but must not
        widen what host state the response carries. Every response that ships a
        `nanobot_features` block goes through here so a new one cannot forget.
        """
        if not request.host_admin:
            return restricted_nanobot_features_payload()
        registry = self._registry()
        return nanobot_features_payload(
            extension_snapshot=registry.snapshot(),
            config_path=self.settings.config.path,
            last_action=last_action,
        )

    @staticmethod
    def _feature_fields(request: SettingsRequest) -> tuple[str, str | None, str, str, bool]:
        name = (query_first(request.query, "name") or "").strip()
        instance_id = (query_first(request.query, "instance_id") or "").strip() or None
        extension_id = (query_first(request.query, "extension_id") or "").strip()
        revision = (query_first(request.query, "expected_revision") or "").strip()
        acknowledged = (query_first(request.query, "risk_acknowledged") or "").strip().lower() in {"1", "true", "yes"}
        if not name or not extension_id or not revision:
            raise OptionalFeatureError("extension ID and current revision are required", status=400)
        return name, instance_id, extension_id, revision, acknowledged

    async def _execute_feature_action(
        self,
        request: SettingsRequest,
        *,
        action: ExtensionAction,
        values: Mapping[str, object] | None = None,
        channel_pairing_completed: bool = False,
    ):
        name, instance_id, extension_id, revision, acknowledged = self._feature_fields(request)
        return await execute_nanobot_extension_action(
            self._registry(),
            action=action,
            name=name,
            instance_id=instance_id,
            extension_id=extension_id,
            expected_revision=revision,
            risk_acknowledged=acknowledged,
            actor_id=request.actor_user_id or "webui",
            is_system_admin=request.system_admin,
            package_install_allowed=self.allow_feature_package_install(request),
            values=values,
            channel_pairing_completed=channel_pairing_completed,
        )

    async def _features(
        self,
        request: SettingsRequest,
    ) -> SettingsRouteResult:
        # The host inventory names every installed package and its revision, lifecycle,
        # trust, execution location, and failure diagnostics. A member has no tenant-scoped
        # slice of that to be shown, so the whole enumeration is withheld rather than
        # trimmed, and the caller is told why instead of being shown an empty host.
        if not _is_server_derived_admin(request):
            return SettingsRouteResult.success(restricted_nanobot_features_payload())
        try:
            return SettingsRouteResult.success(await asyncio.to_thread(self._features_payload, request))
        except OptionalFeatureError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception:
            self.logger.exception("failed to load nanobot features")
            return SettingsRouteResult.failure(500, "failed to load nanobot features")

    async def _features_action(
        self,
        request: SettingsRequest,
        action: str,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        # The registry refuses a non-administrator too, but only after the target has been
        # resolved and reported. Refusing here keeps authorization ahead of disclosure.
        if not _is_server_derived_admin(request):
            return SettingsRouteResult.failure(
                403, "System administrator access is required"
            )
        try:
            name, instance_id, extension_id, revision, _acknowledged = self._feature_fields(request)
            target = resolve_nanobot_feature_target(self._registry().snapshot(), name, instance_id)
            if target is None or target.target_id != extension_id:
                raise OptionalFeatureError("extension action target is unavailable", status=404)
            if target.revision != revision:
                raise OptionalFeatureError("extension action revision is stale", status=409)
            if action == "disable":
                if target.source is not ExtensionSource.CHANNEL_PACKAGE:
                    raise OptionalFeatureError("extension action is not supported", status=400)
                result = await self._execute_feature_action(request, action=ExtensionAction.DISABLE)
            elif target.source is ExtensionSource.OPTIONAL_FEATURE and target.lifecycle is ExtensionLifecycle.ENABLED:
                result = None
            else:
                operation = (
                    ExtensionAction.INSTALL
                    if target.source is ExtensionSource.OPTIONAL_FEATURE
                    and target.lifecycle is not ExtensionLifecycle.ENABLED
                    and ExtensionAction.INSTALL in target.actions
                    else ExtensionAction.ENABLE
                )
                result = await self._execute_feature_action(request, action=operation)
            last_action: dict[str, object] = {
                "ok": result.ok if result is not None else True,
                "action": action,
            }
            if result is not None:
                last_action.update({
                    "message": result.message,
                    "lifecycle": result.lifecycle.value if result.lifecycle else None,
                })
            payload = await asyncio.to_thread(self._features_payload, request, last_action)
            if result is not None and result.lifecycle is ExtensionLifecycle.RESTART_REQUIRED:
                payload["requires_restart"] = True
        except OptionalFeatureError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception:
            self.logger.exception("nanobot feature action '{}' failed", action)
            return SettingsRouteResult.failure(500, "extension action could not be completed")
        return SettingsRouteResult.success(payload, decorate_restart=True, restart_section="runtime")

    async def _channel_configure(
        self,
        request: SettingsRequest,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        # `ws_http` already elevates the owner of a claimed instance to administrator
        # before dispatch, so this repeats that decision next to the write rather than
        # trusting the one caller of `dispatch` to keep making it.
        if not _is_server_derived_admin(request):
            return SettingsRouteResult.failure(
                403, "System administrator access is required"
            )
        values = self.parse_channel_values(request)
        enable = (query_first(request.query, "enable") or "").strip().lower() in {"1", "true", "yes"}
        try:
            name, instance_id, _extension_id, _revision, acknowledged = self._feature_fields(request)
            result = await self._execute_feature_action(
                request, action=ExtensionAction.CONFIGURE, values=values
            )
            if not result.ok:
                raise OptionalFeatureError("channel configuration could not be saved", status=400)
            saved = [
                key
                for key, value in values.items()
                if not (isinstance(value, str) and not value.strip())
            ]
            payload: dict[str, Any] = {"name": name, "saved": result.ok, "saved_keys": saved}
            enabled = None
            if enable:
                target = resolve_nanobot_feature_target(
                    self._registry().snapshot(), name, instance_id
                )
                if target is None or target.revision is None:
                    raise OptionalFeatureError("extension action target is unavailable", status=404)
                enabled = await execute_nanobot_extension_action(
                    self._registry(), action=ExtensionAction.ENABLE, name=name,
                    instance_id=instance_id, extension_id=target.target_id,
                    expected_revision=target.revision, risk_acknowledged=acknowledged,
                    actor_id=request.actor_user_id or "webui", is_system_admin=request.system_admin,
                    package_install_allowed=self.allow_feature_package_install(request),
                )
            last_action = (
                {
                    "ok": enabled.ok,
                    "action": "enable",
                    "message": enabled.message,
                    "lifecycle": (
                        enabled.lifecycle.value
                        if enabled.lifecycle is not None
                        else None
                    ),
                }
                if enabled is not None
                else None
            )
            features = await asyncio.to_thread(self._features_payload, request, last_action)
            if (
                result.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
                or (
                    enabled is not None
                    and enabled.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
                )
            ):
                features["requires_restart"] = True
            payload["nanobot_features"] = features
        except OptionalFeatureError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception:
            self.logger.exception("failed to configure channel")
            return SettingsRouteResult.failure(500, "channel configuration could not be saved")
        return SettingsRouteResult.success(payload, decorate_restart=True, restart_section="runtime", restart_payload_key="nanobot_features")

    async def _channel_validate(
        self,
        request: SettingsRequest,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        # Validation probes a channel with caller-supplied credentials and reports what
        # the host saw, so it is gated on the same authority as configure and refuses
        # before the channel name is read.
        if not _is_server_derived_admin(request):
            return SettingsRouteResult.failure(
                403, "System administrator access is required"
            )
        name = (query_first(request.query, "name") or "").strip()
        instance_id = (
            query_first(request.query, "instance_id") or "default"
        ).strip()
        try:
            payload = await asyncio.to_thread(
                operations.validate_channel_config,
                name,
                self.parse_channel_values(request),
                instance_id=instance_id,
            )
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception:
            self.logger.exception("failed to validate channel '{}' settings", name)
            return SettingsRouteResult.failure(
                500,
                "failed to validate channel settings",
            )
        return SettingsRouteResult.success(payload)

    @staticmethod
    def parse_channel_values(request: SettingsRequest) -> dict[str, Any]:
        if request.payload is None or "values" not in request.payload:
            return {}
        values = request.payload.get("values")
        if not isinstance(values, dict):
            raise WebUISettingsError(
                "channel settings payload must be a JSON object"
            )
        return cast(dict[str, Any], values)

    async def _channel_connect(
        self,
        request: SettingsRequest,
        channel_name: str,
        action: str,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        if not _is_server_derived_admin(request):
            return SettingsRouteResult.failure(403, "System administrator access is required")
        actor_user_id = (request.actor_user_id or "").strip()
        try:
            requested_instance_id = (
                query_first(request.query, "instance_id") or "default"
            ).strip() or "default"
            extension_id = (query_first(request.query, "extension_id") or "").strip()
            revision = (query_first(request.query, "expected_revision") or "").strip()
            target = resolve_nanobot_feature_target(
                self._registry().snapshot(), channel_name, requested_instance_id
            )
            if target is None or target.target_id != extension_id:
                raise OptionalFeatureError("extension action target is unavailable", status=404)
            if action == "start" and (not revision or target.revision != revision):
                raise OptionalFeatureError("extension action revision is stale", status=409)
        except OptionalFeatureError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)

        try:
            connector = self._channel_connectors.get(channel_name)
            if connector is None:
                connector = operations.load_channel_plugin(channel_name).load_connector()
                self._channel_connectors[channel_name] = connector
        except ImportError:
            return SettingsRouteResult.failure(404, "channel does not support connect")
        except Exception:
            self.logger.exception("failed to load channel connector")
            return SettingsRouteResult.failure(500, "channel connection could not be completed")

        try:
            query = {key: list(values) for key, values in request.query.items()}
            query["_actor_user_id"] = [actor_user_id]
            payload_value = cast(object, await connector.handle(action, query))
            if not isinstance(payload_value, Mapping):
                self.logger.warning("channel connector returned an invalid payload")
                return SettingsRouteResult.failure(500, "channel connection could not be completed")
            payload = _sanitize_connector_payload(cast(Mapping[str, object], payload_value))
        except ChannelConnectError as exc:
            return SettingsRouteResult.failure(exc.status, safe_extension_message(exc.message))
        except Exception:
            self.logger.exception("failed to run channel connector")
            return SettingsRouteResult.failure(500, "channel connection could not be completed")
        requested_acknowledgement = (
            query_first(request.query, "risk_acknowledged") or ""
        ).strip().lower() in {"1", "true", "yes"}
        self._remember_pairing_acknowledgement(
            request,
            channel_name,
            action,
            payload,
            requested_acknowledgement,
        )
        if payload.get("status") != "succeeded":
            return SettingsRouteResult.success(_sanitize_connector_payload(payload))
        instance_id = str(payload.get("instance_id") or "default")
        if payload.get("pairing_required") is True:
            acknowledged = self._consume_pairing_acknowledgement(
                request,
                channel_name,
                action,
                requested_acknowledgement,
            )
            package_target = resolve_extension_package_target(
                self._registry().snapshot(),
                channel_name,
                source=ExtensionSource.CHANNEL_PACKAGE,
            )
            install_result = None
            if (
                package_target is None
                or package_target.revision is None
                or ExtensionAction.INSTALL in package_target.actions
            ):
                try:
                    if package_target is None or package_target.revision is None:
                        raise OptionalFeatureError(
                            "extension action target is unavailable", status=404
                        )
                    install_result = await execute_nanobot_extension_action(
                        self._registry(),
                        action=ExtensionAction.INSTALL,
                        name=channel_name,
                        instance_id=None,
                        extension_id=package_target.target_id,
                        expected_revision=package_target.revision,
                        risk_acknowledged=acknowledged,
                        actor_id=request.actor_user_id or "webui",
                        is_system_admin=request.system_admin,
                        package_install_allowed=self.allow_feature_package_install(request),
                        package_target=True,
                        package_source=ExtensionSource.CHANNEL_PACKAGE,
                    )
                except OptionalFeatureError:
                    install_result = None
                if install_result is None or not install_result.ok:
                    payload["pairing_listener_error"] = "pairing listener could not be started"
                    last_action: dict[str, object] = {
                        "ok": False,
                        "action": ExtensionAction.INSTALL.value,
                        "message": (
                            install_result.message
                            if install_result is not None
                            else "Channel dependencies could not be prepared."
                        ),
                        "lifecycle": (
                            install_result.lifecycle.value
                            if install_result is not None and install_result.lifecycle
                            else ExtensionLifecycle.FAILED.value
                        ),
                    }
                    features = await asyncio.to_thread(
                        self._features_payload, request, last_action
                    )
                    features["requires_restart"] = bool(
                        install_result is not None
                        and install_result.lifecycle
                        is ExtensionLifecycle.RESTART_REQUIRED
                    )
                    payload["nanobot_features"] = features
                    return SettingsRouteResult.success(payload)
            if operations.channel_pairing_action is not None:
                try:
                    listener = operations.channel_pairing_action(channel_name, instance_id)
                    if inspect.isawaitable(listener):
                        listener = await listener
                    listener_value = cast(object, listener)
                    listener_payload = (
                        cast(Mapping[str, object], listener_value)
                        if isinstance(listener_value, Mapping)
                        else None
                    )
                    if listener_payload is not None and not listener_payload.get("ok", True):
                        payload["pairing_listener_error"] = "pairing listener could not be started"
                except Exception:
                    self.logger.exception("failed to start pairing listener")
                    payload["pairing_listener_error"] = "pairing listener could not be started"
            payload["nanobot_features"] = await asyncio.to_thread(self._features_payload, request)
            payload["nanobot_features"]["requires_restart"] = False
            return SettingsRouteResult.success(payload)
        try:
            target = resolve_nanobot_feature_target(self._registry().snapshot(), channel_name, instance_id)
            if target is None or target.revision is None:
                raise OptionalFeatureError("extension action target is unavailable", status=404)
            acknowledged = (query_first(request.query, "risk_acknowledged") or "").strip().lower() in {"1", "true", "yes"}
            result = await execute_nanobot_extension_action(
                self._registry(), action=ExtensionAction.ENABLE, name=channel_name,
                instance_id=instance_id, extension_id=target.target_id,
                expected_revision=target.revision, risk_acknowledged=acknowledged,
                actor_id=request.actor_user_id or "webui", is_system_admin=request.system_admin,
                package_install_allowed=self.allow_feature_package_install(request),
            )
            features = await asyncio.to_thread(
                self._features_payload,
                request,
                {
                    "ok": result.ok,
                    "action": "enable",
                    "message": result.message,
                    "lifecycle": (
                        result.lifecycle.value if result.lifecycle is not None else None
                    ),
                },
            )
            if result.lifecycle is ExtensionLifecycle.RESTART_REQUIRED:
                features["requires_restart"] = True
            payload["nanobot_features"] = features
        except OptionalFeatureError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        return SettingsRouteResult.success(payload, decorate_restart=True, restart_section="runtime", restart_payload_key="nanobot_features")

    def allow_feature_package_install(self, request: SettingsRequest) -> bool:
        if request.local_browser:
            return True
        try:
            return bool(
                self.settings.config.load().tools.webui_allow_remote_package_install
            )
        except Exception:
            self.logger.exception("failed to load remote package install policy")
            return False

    def _pairing_action(
        self,
        request: SettingsRequest,
        action: str,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        code = (query_first(request.query, "code") or "").strip()
        if not code:
            return SettingsRouteResult.failure(400, "Missing pairing code")
        if action == "approve":
            result = operations.approve_code(code)
            if result is None:
                return SettingsRouteResult.failure(
                    404,
                    "Pairing code not found or expired",
                )
            channel, sender_id = result
            return SettingsRouteResult.success(
                pairing_payload(
                    operations.list_pending,
                    {
                        "ok": True,
                        "action": "approve",
                        "message": f"Approved {sender_id} for {channel}",
                        "channel": channel,
                        "sender_id": sender_id,
                        "code": code,
                    },
                )
            )

        if not operations.deny_code(code):
            return SettingsRouteResult.failure(
                404,
                "Pairing code not found or expired",
            )
        return SettingsRouteResult.success(
            pairing_payload(
                operations.list_pending,
                {
                    "ok": True,
                    "action": "deny",
                    "message": f"Denied pairing code {code}",
                    "code": code,
                },
            )
        )

    async def _mcp_presets(
        self,
        request: SettingsRequest,
        action: str | None,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        if action is not None and (
            request.system_admin is not True
            or not request.actor_user_id
            or not request.actor_user_id.strip()
        ):
            return SettingsRouteResult.failure(
                403,
                "system administrator access is required",
            )
        extension_registry = getattr(self.settings, "extensions", None)
        try:
            extension_snapshot = (
                extension_registry.snapshot() if extension_registry is not None else None
            )
            payload = await operations.mcp_presets_action(
                action,
                request.query,
                reload_mcp=operations.reload_mcp,
                mcp_runtime_status=operations.mcp_runtime_status,
                config=self.settings.config,
                extension_snapshot=extension_snapshot,
                extension_registry=extension_registry,
                actor_user_id=request.actor_user_id,
                system_admin=request.system_admin,
            )
        except Exception as exc:
            status = getattr(exc, "status", 500)
            message = _safe_action_error(exc)
            if status >= 500:
                self.logger.exception(
                    "MCP preset action '{}' failed",
                    action or "list",
                )
            return SettingsRouteResult.failure(status, message)
        return SettingsRouteResult.success(
            payload,
            decorate_restart=action is not None,
            restart_section="runtime" if action is not None else None,
        )

    async def _version_check(
        self,
        operations: SystemSettingsOperations,
    ) -> SettingsRouteResult:
        try:
            update_info = await asyncio.to_thread(operations.check_for_update)
        except Exception:
            self.logger.exception("version check failed")
            return SettingsRouteResult.failure(500, "version check failed")
        return SettingsRouteResult.success({"updateAvailable": update_info})
