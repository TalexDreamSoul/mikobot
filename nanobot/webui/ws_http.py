"""HTTP API handler extracted from WebSocketChannel.

Handles all non-WebSocket HTTP routes: bootstrap, sessions, settings,
media, commands, sidebar state, static file serving, and token management.

Also houses shared HTTP utility functions used by both this module and
``websocket.py`` to avoid circular imports.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import mimetypes
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, TypeGuard, cast
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from loguru import logger
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.agent.plugins import agent_plugin_mcp_servers
from nanobot.agent.skills import SkillsLoader
from nanobot.collaboration import (
    COLLABORATION_USER_METADATA_KEY,
    CollaborationConflictError,
    CollaborationRepository,
    CollaborationStoreError,
)
from nanobot.collaboration.models import (
    BotState,
    ContextSource,
    MembershipRole,
    OrganizationRole,
    PairingPurpose,
    PersonalTask,
    SharePermission,
    Task,
    TaskReviewState,
    TaskStatus,
    User,
    VaultKind,
)
from nanobot.command.builtin import builtin_command_palette
from nanobot.config.paths import get_runtime_subdir
from nanobot.cron.session_turns import is_bound_cron_job
from nanobot.cron.types import CronJob, CronSchedule
from nanobot.personal.ics import export_tasks_ics
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.session.manager import SessionManager
from nanobot.session.recovery import RecoveryActionError
from nanobot.session.session_handles import (
    SessionHandleResolver,
)
from nanobot.triggers.local_types import LocalTrigger
from nanobot.webui.collaboration_api import (
    bot_capability_payload,
    bot_channel_payload,
    bot_payload,
    bot_project_channel_payload,
    bot_project_payload,
    context_source_payload,
    create_assignee,
    extension_profile_payload,
    optional_config,
    optional_enabled,
    optional_nonnegative_int,
    optional_position,
    optional_source_kind,
    optional_status,
    optional_string,
    organization_member_payload,
    organization_payload,
    pairing_challenge_payload,
    personal_task_payload,
    profile_settings,
    project_member_payload,
    project_payload,
    required_source_kind,
    required_string,
    task_list_payload,
    task_payload,
    update_assignee,
    user_payload,
)
from nanobot.webui.file_preview import (
    WebUIFilePreviewError,
    file_preview_availability_payload,
    file_preview_payload,
)
from nanobot.webui.gateway_tokens import GatewayTokenStore, token_response_payload
from nanobot.webui.http_utils import (
    accepts_gzip as _accepts_gzip,
)
from nanobot.webui.http_utils import (
    case_insensitive_header as _case_insensitive_header,
)
from nanobot.webui.http_utils import (
    combined_list_header as _combined_list_header,
)
from nanobot.webui.http_utils import (
    host_for_url as _host_for_url,
)
from nanobot.webui.http_utils import (
    http_error as _http_error,
)
from nanobot.webui.http_utils import (
    http_json_response as _http_json_response,
)
from nanobot.webui.http_utils import (
    http_response as _http_response,
)
from nanobot.webui.http_utils import (
    is_local_browser_request as _is_local_browser_request,
)
from nanobot.webui.http_utils import (
    is_localhost as _is_localhost,
)
from nanobot.webui.http_utils import is_loopback_host as _is_loopback_host
from nanobot.webui.http_utils import (
    is_trusted_proxy_authenticated_request as _is_trusted_proxy_authenticated_request,
)
from nanobot.webui.http_utils import (
    issue_route_secret_matches as _issue_route_secret_matches,
)
from nanobot.webui.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from nanobot.webui.http_utils import (
    parse_query as _parse_query,
)
from nanobot.webui.http_utils import (
    parse_request_path as _parse_request_path,
)
from nanobot.webui.http_utils import (
    query_first as _query_first,
)
from nanobot.webui.http_utils import (
    safe_host_header as _safe_host_header,
)
from nanobot.webui.http_utils import (
    trusted_proxy_principal_key as _trusted_proxy_principal_key,
)
from nanobot.webui.ingress_policy import WebUIIngressPolicy
from nanobot.webui.login_security import (
    LoginSecuritySettingsError,
    apply_oidc_update,
    build_oidc_update,
    oidc_settings_payload,
)
from nanobot.webui.media_gateway import WebUIMediaGateway
from nanobot.webui.native_folder_picker import (
    NativeFolderPickerError,
    native_folder_picker_available,
    pick_native_folder,
)
from nanobot.webui.oidc_auth import OidcAuthenticator, OidcCapacityError, OidcError
from nanobot.webui.session_automations import (
    serialize_automation_jobs,
    session_automation_jobs,
    session_automations_payload,
)
from nanobot.webui.session_context import session_context_payload
from nanobot.webui.session_identity import is_webui_session_key
from nanobot.webui.session_list_index import (
    WEBUI_SESSION_INDEX_INTERNAL_FIELDS,
    indexed_workspace_scope,
    list_webui_sessions,
)
from nanobot.webui.sidebar_state import (
    read_webui_sidebar_state,
    write_webui_sidebar_state,
)
from nanobot.webui.skills_api import (
    SkillManagementError,
    delete_webui_skill,
    set_webui_skill_enabled,
    webui_skill_detail_payload,
    webui_skills_payload,
)
from nanobot.webui.skills_marketplace import (
    SkillsMarketplaceError,
    install_marketplace_skill,
    marketplace_skill_trends,
    search_marketplace_skills,
    trending_marketplace_skills,
)
from nanobot.webui.thread_disk import delete_webui_thread
from nanobot.webui.transcript import build_webui_thread_response
from nanobot.webui.workspaces import WebUIWorkspaceController

_SLOW_WEBUI_HTTP_LOG_MS = 1_000
_WEBUI_MUTATION_PAYLOAD_ATTR = "_nanobot_webui_mutation_payload"
_WEBUI_MUTATION_REQUEST_ATTR = "_nanobot_webui_mutation_request"
_SETTINGS_ACTOR_USER_ATTR = "_nanobot_settings_actor_user_id"
_SETTINGS_ACTOR_ORG_ATTR = "_nanobot_settings_actor_organization_id"
_SETTINGS_ADMIN_ATTR = "_nanobot_settings_system_admin"
_NO_STORE_HEADERS = [("Cache-Control", "no-store")]

_WEBUI_MUTATION_PATHS = {
    "automation.enable": "/api/webui/automations/enable",
    "automation.disable": "/api/webui/automations/disable",
    "automation.delete": "/api/webui/automations/delete",
    "automation.run": "/api/webui/automations/run",
    "automation.update": "/api/webui/automations/update",
    "skill.install": "/api/webui/skills/install",
    "skill.update": "/api/webui/skills/update",
    "skill.delete": "/api/webui/skills/delete",
    "sidebar.update": "/api/webui/sidebar-state/update",
    "workspace.pick_folder": "/api/workspaces/pick-folder",
    "recovery.continue": "/api/webui/recovery/continue",
    "recovery.dismiss": "/api/webui/recovery/dismiss",
    "settings.agent.update": "/api/settings/update",
    "settings.model_configuration.create": "/api/settings/model-configurations/create",
    "settings.model_configuration.update": "/api/settings/model-configurations/update",
    "settings.model_configuration.delete": "/api/settings/model-configurations/delete",
    "settings.model_configuration.migrate": "/api/settings/model-configurations/migrate",
    "settings.model_call_order.update": "/api/settings/model-call-order/update",
    "settings.provider.update": "/api/settings/provider/update",
    "settings.provider.create": "/api/settings/provider/create",
    "settings.provider.oauth_login": "/api/settings/provider/oauth-login",
    "settings.provider.oauth_complete": "/api/settings/provider/oauth-login/complete",
    "settings.provider.oauth_logout": "/api/settings/provider/oauth-logout",
    "settings.web_search.update": "/api/settings/web-search/update",
    "settings.api_service.start": "/api/settings/api-service/start",
    "settings.api_service.stop": "/api/settings/api-service/stop",
    "settings.image_generation.update": "/api/settings/image-generation/update",
    "settings.transcription.update": "/api/settings/transcription/update",
    "settings.network_safety.update": "/api/settings/network-safety/update",
    "settings.login_security.update": "/api/settings/login-security/update",
    "settings.cli_app.install": "/api/settings/cli-apps/install",
    "settings.cli_app.update": "/api/settings/cli-apps/update",
    "settings.cli_app.uninstall": "/api/settings/cli-apps/uninstall",
    "settings.cli_app.test": "/api/settings/cli-apps/test",
    "settings.feature.enable": "/api/settings/nanobot-features/enable",
    "settings.feature.disable": "/api/settings/nanobot-features/disable",
    "settings.channel.validate": "/api/settings/channels/validate",
    "settings.channel.configure": "/api/settings/channels/configure",
    "settings.pairing.approve": "/api/settings/pairing/approve",
    "settings.pairing.deny": "/api/settings/pairing/deny",
    "settings.mcp.enable": "/api/settings/mcp-presets/enable",
    "settings.mcp.disable": "/api/settings/mcp-presets/disable",
    "settings.mcp.remove": "/api/settings/mcp-presets/remove",
    "settings.mcp.test": "/api/settings/mcp-presets/test",
    "settings.mcp.reconnect": "/api/settings/mcp-presets/reconnect",
    "settings.mcp.custom": "/api/settings/mcp-presets/custom",
    "settings.mcp.import": "/api/settings/mcp-presets/import",
    "settings.mcp.import_cursor": "/api/settings/mcp-presets/import-cursor",
    "settings.mcp.tools": "/api/settings/mcp-presets/tools",
    "settings.mcp.oauth_start": "/api/settings/mcp-oauth/start",
    "settings.mcp.oauth_complete": "/api/settings/mcp-oauth/complete",
    "settings.mcp.oauth_cancel": "/api/settings/mcp-oauth/cancel",
    "collaboration.organization.create": "/api/collaboration/mutations/organization/create",
    "collaboration.organization.update": "/api/collaboration/mutations/organization/update",
    "collaboration.organization.delete": "/api/collaboration/mutations/organization/delete",
    "collaboration.organization.member.add": "/api/collaboration/mutations/organization/member/add",
    "collaboration.organization.member.remove": "/api/collaboration/mutations/organization/member/remove",
    "collaboration.user.defaults": "/api/collaboration/mutations/user/defaults",
    "collaboration.bot.create": "/api/collaboration/mutations/bot/create",
    "collaboration.bot.update": "/api/collaboration/mutations/bot/update",
    "collaboration.bot.delete": "/api/collaboration/mutations/bot/delete",
    "collaboration.bot.capabilities.update": "/api/collaboration/mutations/bot/capabilities/update",
    "collaboration.pairing.create": "/api/collaboration/mutations/pairing/create",
    "collaboration.pairing.consume": "/api/collaboration/mutations/pairing/consume",
    "collaboration.project.create": "/api/collaboration/mutations/project/create",
    "collaboration.project.member.add": "/api/collaboration/mutations/project/member/add",
    "collaboration.project.member.remove": "/api/collaboration/mutations/project/member/remove",
    "collaboration.task_list.create": "/api/collaboration/mutations/task-list/create",
    "collaboration.task.create": "/api/collaboration/mutations/task/create",
    "collaboration.task.update": "/api/collaboration/mutations/task/update",
    "collaboration.task.delete": "/api/collaboration/mutations/task/delete",
    "collaboration.extensions.update": "/api/collaboration/mutations/extensions/update",
    "collaboration.context_source.create": "/api/collaboration/mutations/context-source/create",
    "collaboration.context_source.update": "/api/collaboration/mutations/context-source/update",
    "collaboration.context_source.delete": "/api/collaboration/mutations/context-source/delete",
    "personal.vault.create": "/api/personal/mutations/vault/create",
    "personal.vault.default": "/api/personal/mutations/vault/default",
    "personal.persona.create": "/api/personal/mutations/persona/create",
    "personal.persona.default": "/api/personal/mutations/persona/default",
    "personal.task.create": "/api/personal/mutations/task/create",
    "personal.task.update": "/api/personal/mutations/task/update",
    "personal.task.delete": "/api/personal/mutations/task/delete",
    "personal.share.create": "/api/personal/mutations/share/create",
    "personal.share.revoke": "/api/personal/mutations/share/revoke",
}

_WEBUI_CHANNEL_CONNECT_ACTIONS = {
    "settings.channel.connect.start": "start",
    "settings.channel.connect.poll": "poll",
    "settings.channel.connect.cancel": "cancel",
}


class _AutomationUpdate(TypedDict, total=False):
    name: str
    message: str
    schedule: CronSchedule
    delete_after_run: bool


class _LocalTriggerUpdate(TypedDict, total=False):
    name: str

# Fix for #5190: On Windows, mimetypes.guess_type() reads the registry key
# HKEY_CLASSES_ROOT\.js\Content Type, which is commonly set to 'text/plain'
# because .js is associated with Windows Script Host rather than web JavaScript.
# That registry value overrides Python's built-in mapping and causes browsers to
# reject ES module scripts with:
#   Failed to load module script: Expected a JavaScript-or-Wasm module script
#   but the server responded with a MIME type of "text/plain".
# We explicitly register correct MIME types for common web static assets here
# (module-import time) so all callers of mimetypes.guess_type() in this process
# benefit, regardless of host registry configuration.
_MIME_FIXES: dict[str, str] = {
    ".js":    "application/javascript",
    ".mjs":   "application/javascript",
    ".css":   "text/css",
    ".html":  "text/html",
    ".json":  "application/json",
    ".svg":   "image/svg+xml",
    ".wasm":  "application/wasm",
}

for _ext, _ctype in _MIME_FIXES.items():
    mimetypes.add_type(_ctype, _ext, strict=True)


if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

    from nanobot.bus.queue import MessageBus
    from nanobot.channels.websocket.runtime import WebSocketConfig
    from nanobot.cron.service import CronService
    from nanobot.triggers.local_store import LocalTriggerStore
    from nanobot.webui.settings_services import WebUISettingsServices

def _decode_api_key(raw_key: str) -> str | None:
    key = unquote(raw_key)
    _api_key_re = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
    if _api_key_re.match(key) is None:
        return None
    return key


def _is_string_dict(value: object) -> TypeGuard[dict[str, object]]:
    if not isinstance(value, dict):
        return False
    mapping = cast(dict[object, object], value)
    return all(isinstance(key, str) for key in mapping)


def _mutation_payload(request: WsRequest) -> dict[str, object] | None:
    payload: object = getattr(request, _WEBUI_MUTATION_PAYLOAD_ATTR, None)
    return payload if _is_string_dict(payload) else None


def _request_query(request: WsRequest) -> dict[str, list[str]]:
    payload = _mutation_payload(request)
    if payload is None:
        return _parse_query(request.path)
    query: dict[str, list[str]] = {}
    for key, value in payload.items():
        if not key:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif value is None:
            text = ""
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            text = str(value)
        query[key] = [text]
    return query


def _default_model_name_from_config(config_path: Path | None = None) -> str | None:
    try:
        from nanobot.config.loader import load_config
        model = load_config(config_path).resolve_preset().model.strip()
        return model or None
    except Exception as e:
        logger.debug("bootstrap model_name could not load from config: {}", e)
        return None


def _resolve_bootstrap_model_name(
    runtime_name: Callable[[], str | None] | None,
    config_path: Path | None = None,
) -> str:
    if runtime_name is not None:
        try:
            raw = runtime_name()
        except Exception as e:
            logger.debug("bootstrap runtime model resolver failed: {}", e)
        else:
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped:
                    return stripped
    return _default_model_name_from_config(config_path) or ""


# ---------------------------------------------------------------------------
# GatewayHTTPHandler
# ---------------------------------------------------------------------------


class GatewayHTTPHandler:
    """Handles all HTTP routes served alongside the WebSocket endpoint.

    Routes HTTP requests and delegates stateful work to explicit gateway
    services owned by the composition layer.
    """

    def __init__(
        self,
        collaboration: CollaborationRepository,
        *,
        config: WebSocketConfig,
        webui_connections: set[ServerConnection],
        session_manager: SessionManager | None,
        static_dist_path: Path | None,
        runtime_model_name: Callable[[], str | None] | None,
        runtime_surface: str,
        runtime_capabilities_overrides: dict[str, Any] | None,
        bus: MessageBus,
        tokens: GatewayTokenStore,
        media: WebUIMediaGateway,
        ingress: WebUIIngressPolicy,
        workspaces: WebUIWorkspaceController,
        settings: WebUISettingsServices,
        skills_workspace_path: Path,
        disabled_skills: set[str] | None = None,
        cron_service: CronService | None = None,
        local_trigger_store: LocalTriggerStore | None = None,
        cron_pending_job_ids: Callable[[str], set[str]] | None = None,
        local_trigger_pending_ids: Callable[[str], set[str]] | None = None,
        channel_feature_action: Callable[..., Any] | None = None,
        channel_runtime_status: Callable[[], dict[str, Any]] | None = None,
        mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
        mcp_reload: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        skill_state_action: Callable[[set[str]], None] | None = None,
        recovery_action: (
            Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
        oidc: OidcAuthenticator | None = None,
        log: Any = logger,
    ) -> None:
        self.config = config
        self.session_manager = session_manager
        self.collaboration = collaboration
        self._collaboration_init_lock = asyncio.Lock()
        self._collaboration_initialized = False
        self._collaboration_closed = False
        self._webui_connections = webui_connections
        self.static_dist_path = static_dist_path
        self.runtime_model_name = runtime_model_name
        self.bus = bus
        self.tokens = tokens
        self.oidc = oidc or OidcAuthenticator(config.oidc_auth)
        self._oidc_connection_revoker: Callable[[str], Awaitable[None]] | None = None
        self.media = media
        self.ingress = ingress
        self.workspaces = workspaces
        self.settings = settings
        self.skills_workspace_path = skills_workspace_path
        self.disabled_skills: set[str] = (
            disabled_skills if disabled_skills is not None else set()
        )
        self.skill_state_action = skill_state_action
        self.recovery_action = recovery_action
        self._skill_install_lock = asyncio.Lock()
        self._folder_picker_lock = asyncio.Lock()
        self.cron_service = cron_service
        self.local_trigger_store = local_trigger_store
        self.cron_pending_job_ids = cron_pending_job_ids
        self.local_trigger_pending_ids = local_trigger_pending_ids
        self._log = log
        self._runtime_surface = runtime_surface

        from nanobot.webui.settings_api import runtime_capabilities as _rc
        from nanobot.webui.settings_routes import WebUISettingsRouter

        self._capabilities = _rc(runtime_surface, runtime_capabilities_overrides or {})
        self._channel_feature_action = channel_feature_action
        self.settings_routes = WebUISettingsRouter(
            settings=settings,
            bus=bus,
            logger=self._log,
            check_api_token=self.check_api_token,
            parse_query=_parse_query,
            json_response=_http_json_response,
            error_response=_http_error,
            runtime_surface=runtime_surface,
            runtime_capabilities=self._capabilities,
            channel_feature_action=channel_feature_action,
            channel_runtime_status=channel_runtime_status,
            mcp_runtime_status=mcp_runtime_status,
            mcp_reload=mcp_reload,
            mcp_oauth_redirect_uri=self._mcp_oauth_redirect_uri,
        )

    async def initialize_collaboration(self) -> None:
        """Initialize the configured collaboration backend under one gateway lock."""
        async with self._collaboration_init_lock:
            if self._collaboration_initialized:
                return
            if self._collaboration_closed:
                raise RuntimeError("collaboration repository is closed")
            await self.collaboration.initialize()
            await self.collaboration.ensure_local_owner(self.skills_workspace_path)
            self._collaboration_initialized = True

    async def aclose_collaboration(self) -> None:
        """Close the configured collaboration backend exactly once."""
        async with self._collaboration_init_lock:
            if self._collaboration_closed:
                return
            await self.collaboration.aclose()
            self._collaboration_closed = True

    def workspace_controls_available(self, connection: Any) -> bool:
        return self._runtime_surface == "native" or _is_localhost(connection)

    def workspace_folder_picker_available(
        self,
        connection: Any,
        request: WsRequest,
    ) -> bool:
        return (
            _is_loopback_host(self.config.host)
            and _is_local_browser_request(connection, request.headers)
            and native_folder_picker_available()
        )

    # -- Token management ---------------------------------------------------

    def set_oidc_connection_revoker(
        self,
        revoker: Callable[[str], Awaitable[None]],
    ) -> None:
        """Install the listener-owned closer for consumed OIDC WebSockets."""
        self._oidc_connection_revoker = revoker

    def check_api_token(self, request: WsRequest) -> bool:
        if getattr(request, "_nanobot_trusted_proxy_authenticated", False):
            return True
        if self.tokens.check_api_token(request):
            return True
        oidc = getattr(self, "oidc", None)
        if oidc is not None and oidc.enabled and oidc.session(request.headers) is not None:
            return True
        if not getattr(request, _WEBUI_MUTATION_REQUEST_ATTR, False):
            return False
        try:
            return getattr(request, "_nanobot_connection", None) in self._webui_connections
        except TypeError:
            return False
    async def _collaboration_identity(
        self, request: WsRequest
    ) -> tuple[User, bool] | None:
        """Resolve the authenticated collaboration user, never a payload identity."""
        await self.initialize_collaboration()
        connection = getattr(request, "_nanobot_connection", None)
        headers = request.headers
        trusted = bool(
            getattr(request, "_nanobot_trusted_proxy_authenticated", False)
        )
        if trusted:
            principal = _trusted_proxy_principal_key(connection, headers, self.config)
            if principal is None:
                return None
            user, _project = await self.collaboration.ensure_identity_user(
                "websocket", principal, self.skills_workspace_path, local_owner=False
            )
            return user, False

        oidc = getattr(self, "oidc", None)
        oidc_principal: str | None = None
        if oidc is not None and oidc.enabled:
            oidc_principal = (
                self.tokens.api_token_principal(request)
                or getattr(connection, "_nanobot_oidc_principal", None)
            )
            session = oidc.session(request.headers)
            oidc_principal = oidc_principal or (
                session.principal if session is not None else None
            )
        if oidc is not None and oidc.enabled:
            local_gateway_auth = self.tokens.check_api_token(request) or bool(
                getattr(connection, "_nanobot_local_webui_authenticated", False)
                and getattr(request, _WEBUI_MUTATION_REQUEST_ATTR, False)
            )
            if not oidc_principal and not local_gateway_auth:
                return None
            if not oidc_principal:
                source_request = getattr(connection, "request", None)
                raw_source_path = getattr(source_request, "path", "")
                source_path = raw_source_path if isinstance(raw_source_path, str) else ""
                _, query = _parse_request_path(source_path)
                client_id = (_query_first(query, "client_id") or "webui-http").strip()[:128]
                user, _project = await self.collaboration.ensure_identity_user(
                    "websocket", client_id or "webui-http",
                    self.skills_workspace_path, local_owner=True,
                )
                return user, True
            user, _project = await self.collaboration.ensure_identity_user(
                "oidc", oidc_principal, self.skills_workspace_path, local_owner=False
            )
            await self.collaboration.bind_identity("websocket", oidc_principal, user.id)
            return user, False

        source_request = getattr(connection, "request", None)
        raw_source_path = getattr(source_request, "path", "")
        source_path = raw_source_path if isinstance(raw_source_path, str) else ""
        _, query = _parse_request_path(source_path)
        client_id = (_query_first(query, "client_id") or "webui-http").strip()[:128]
        user, _project = await self.collaboration.ensure_identity_user(
            "websocket", client_id or "webui-http",
            self.skills_workspace_path, local_owner=True,
        )
        return user, True


    async def _is_system_admin(self, request: WsRequest) -> bool:
        try:
            identity = await self._collaboration_identity(request)
        except (CollaborationStoreError, ValueError):
            identity = None
        if identity is not None and identity[1]:
            return True
        session = self.oidc.session(request.headers) if self.oidc.enabled else None
        return bool(
            session is not None
            and session.principal in self.config.oidc_auth.admin_subjects
        )
    async def _can_manage_channel_instance(
        self, request: WsRequest, channel_type: str, instance_id: str
    ) -> bool:
        """Return whether the authenticated user owns or administers one claimed instance."""
        actor_user_id = getattr(request, _SETTINGS_ACTOR_USER_ATTR, None)
        if not isinstance(actor_user_id, str) or not actor_user_id:
            return False
        channel_type = channel_type.strip()
        instance_id = instance_id.strip() or "default"
        try:
            bots = await self.collaboration.list_bots(actor_user_id)
            checked_organizations: set[str] = set()
            admin_organizations: set[str] = set()
            for bot in bots:
                assignments = await self.collaboration.list_bot_channels(
                    actor_user_id, bot.id
                )
                for assignment in assignments:
                    if (
                        assignment.channel_type != channel_type
                        or assignment.instance_id != instance_id
                    ):
                        continue
                    if assignment.claimed_by_user_id == actor_user_id:
                        return True
                    if bot.organization_id in checked_organizations:
                        continue
                    checked_organizations.add(bot.organization_id)
                    members = await self.collaboration.list_organization_members(
                        bot.organization_id, actor_user_id
                    )
                    if any(
                        member.user_id == actor_user_id
                        and member.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}
                        for member in members
                    ):
                        admin_organizations.add(bot.organization_id)
                    if bot.organization_id in admin_organizations:
                        return True
        except (CollaborationStoreError, ValueError):
            return False
        return False

    @staticmethod
    def _channel_control_target(
        path: str, payload: Mapping[str, object]
    ) -> tuple[str, str] | None:
        connect_match = re.fullmatch(
            r"/api/settings/channels/([^/]+)/connect/(?:start|poll|cancel)", path
        )
        if connect_match is not None:
            channel_name = unquote(connect_match.group(1)).strip()
        elif path in {
            "/api/settings/channels/configure",
            "/api/settings/channels/validate",
            "/api/settings/nanobot-features/enable",
            "/api/settings/nanobot-features/disable",
        }:
            raw_name = payload.get("name")
            channel_name = raw_name.strip() if isinstance(raw_name, str) else ""
        else:
            return None
        if not channel_name:
            return None
        raw_instance_id = payload.get("instance_id")
        instance_id = raw_instance_id.strip() if isinstance(raw_instance_id, str) else "default"
        return channel_name, instance_id or "default"


    async def _handle_login_security(
        self, request: WsRequest, *, update: bool
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not await self._is_system_admin(request):
            return _http_error(403, "System administrator access is required")
        if not update:
            config = await asyncio.to_thread(self.settings.config.load)
            return _http_json_response(
                {"login_security": oidc_settings_payload(config)},
                extra_headers=_NO_STORE_HEADERS,
            )
        if not getattr(request, _WEBUI_MUTATION_REQUEST_ATTR, False):
            return _http_error(405, "WebUI mutations require an authenticated WebSocket")
        values = _mutation_payload(request)
        if values is None:
            return _http_error(400, "invalid login security payload")
        try:
            current = await asyncio.to_thread(self.settings.config.load)
            expected_oidc = build_oidc_update(current, {})
            next_oidc = build_oidc_update(current, values)
            await OidcAuthenticator(next_oidc).validate_configuration()
            await asyncio.to_thread(
                self.settings.config.update,
                lambda config: apply_oidc_update(
                    config, next_oidc, expected=expected_oidc
                ),
            )
            updated = await asyncio.to_thread(self.settings.config.load)
        except LoginSecuritySettingsError as exc:
            return _http_json_response(
                {"error": str(exc), "fields": exc.fields},
                status=exc.status,
                extra_headers=_NO_STORE_HEADERS,
            )
        except OidcError as exc:
            return _http_json_response(
                {
                    "error": "OIDC provider validation failed",
                    "fields": [{"field": "issuer", "message": str(exc)}],
                },
                status=400,
                extra_headers=_NO_STORE_HEADERS,
            )
        return _http_json_response(
            {
                "login_security": oidc_settings_payload(updated),
                "requires_restart": True,
            },
            extra_headers=_NO_STORE_HEADERS,
        )

    def _session_owned_by_user_sync(
        self, session_key: str, user: User, local_owner: bool
    ) -> bool:
        if not _is_websocket_channel_session_key(session_key):
            return False
        if self.session_manager is None:
            return local_owner
        snapshot = self.session_manager.read_session_metadata(session_key)
        if snapshot is None:
            return local_owner
        raw_metadata: object = snapshot.get("metadata")
        metadata = raw_metadata if _is_string_dict(raw_metadata) else {}
        owner = metadata.get(COLLABORATION_USER_METADATA_KEY)
        return owner == user.id or (local_owner and owner is None)

    async def _session_owned_by_user(
        self, session_key: str, user: User, local_owner: bool
    ) -> bool:
        return await asyncio.to_thread(
            self._session_owned_by_user_sync, session_key, user, local_owner
        )

    async def can_access_webui_session(self, connection: Any, session_key: str) -> bool:
        """Enforce per-user ownership for persisted WebUI session operations."""
        if not _is_websocket_channel_session_key(session_key):
            return False
        request = getattr(connection, "request", None)
        if request is None:
            return False
        try:
            setattr(request, "_nanobot_connection", connection)
            setattr(
                request,
                "_nanobot_trusted_proxy_authenticated",
                _is_trusted_proxy_authenticated_request(
                    connection, request.headers, self.config
                ),
            )
            identity = await self._collaboration_identity(request)
            if identity is None:
                return False
            user, local_owner = identity
            return await self._session_owned_by_user(session_key, user, local_owner)
        except (CollaborationStoreError, ValueError):
            return False

    async def _request_can_access_session(
        self, request: WsRequest, session_key: str
    ) -> bool:
        try:
            identity = await self._collaboration_identity(request)
            if identity is None:
                return False
            user, local_owner = identity
            return await self._session_owned_by_user(session_key, user, local_owner)
        except (CollaborationStoreError, ValueError):
            return False

    # -- Main dispatch ------------------------------------------------------

    # -- Main dispatch ------------------------------------------------------

    async def dispatch(self, connection: Any, request: WsRequest) -> Any | None:
        """Route an HTTP request. Returns Response or None."""
        got, _ = _parse_request_path(request.path)
        started = time.perf_counter()
        response: Any | None = None
        setattr(
            request,
            "_nanobot_trusted_proxy_authenticated",
            _is_trusted_proxy_authenticated_request(connection, request.headers, self.config),
        )
        setattr(request, "_nanobot_connection", connection)
        await self.initialize_collaboration()

        try:
            if self._is_webui_mutation_path(got):
                return _http_error(
                    405,
                    "WebUI mutations require an authenticated WebSocket",
                )
            response = await self._dispatch_resolved(connection, request, got)
            return response
        finally:
            self._log_slow_http(got, response, started)

    async def dispatch_webui_mutation(
        self,
        connection: Any,
        action: str,
        payload: Mapping[str, object],
    ) -> Response:
        """Run one explicitly allowlisted mutation for an authenticated WebUI socket."""
        path = self._webui_mutation_path(action, payload)
        if isinstance(path, Response):
            return path

        source_request = getattr(connection, "request", None)
        source_headers = getattr(source_request, "headers", None)
        if source_headers is None:
            headers = Headers()
        else:
            try:
                headers = Headers(source_headers.raw_items())
            except (AttributeError, TypeError):
                try:
                    headers = Headers(source_headers)
                except TypeError:
                    headers = Headers()
        request = WsRequest(path, headers)
        setattr(
            request,
            "_nanobot_trusted_proxy_authenticated",
            _is_trusted_proxy_authenticated_request(connection, headers, self.config),
        )
        setattr(request, "_nanobot_connection", connection)
        setattr(request, _WEBUI_MUTATION_REQUEST_ATTR, True)
        setattr(request, _WEBUI_MUTATION_PAYLOAD_ATTR, dict(payload))
        response = await self._dispatch_resolved(connection, request, path)
        if isinstance(response, Response):
            return response
        return _http_error(404, "WebUI mutation action not found")

    def _is_webui_mutation_path(self, path: str) -> bool:
        settings_routes = getattr(self, "settings_routes", None)
        if settings_routes is not None and settings_routes.is_mutation_path(path):
            return True
        if re.match(r"^/api/sessions/[^/]+/delete$", path):
            return True
        if re.match(r"^/api/webui/automations/(enable|disable|delete|run|update)$", path):
            return True
        if path.startswith("/api/collaboration/mutations/") or path.startswith("/api/personal/mutations/"):
            return True
        if path in {"/api/webui/recovery/continue", "/api/webui/recovery/dismiss"}:
            return True
        return path in {
            "/api/webui/skills/install",
            "/api/webui/skills/update",
            "/api/webui/skills/delete",
            "/api/webui/sidebar-state/update",
            "/api/workspaces/pick-folder",
        }

    @staticmethod
    def _webui_mutation_path(
        action: str,
        payload: Mapping[str, object],
    ) -> str | Response:
        path = _WEBUI_MUTATION_PATHS.get(action)
        if path is not None:
            return path
        if action == "session.delete":
            key = payload.get("key")
            if not isinstance(key, str) or not key.strip():
                return _http_error(400, "missing session key")
            return f"/api/sessions/{quote(key, safe='')}/delete"
        connect_action = _WEBUI_CHANNEL_CONNECT_ACTIONS.get(action)
        if connect_action is not None:
            channel = payload.get("channel")
            if not isinstance(channel, str) or re.fullmatch(
                r"[A-Za-z0-9_-]{1,64}",
                channel,
            ) is None:
                return _http_error(400, "invalid channel name")
            return f"/api/settings/channels/{channel}/connect/{connect_action}"
        return _http_error(404, "unknown WebUI mutation action")

    async def _dispatch_resolved(
        self,
        connection: Any,
        request: WsRequest,
        got: str,
    ) -> Any | None:
        if got == "/auth/oidc/login":
            return await self._handle_oidc_login(request)
        if got == "/auth/oidc/callback":
            return await self._handle_oidc_callback(request)
        if got == "/auth/logout":
            return await self._handle_oidc_logout(request)

        # Token issue endpoint
        if self.config.token_issue_path:
            issue_expected = _normalize_config_path(self.config.token_issue_path)
            if got == issue_expected:
                return self._handle_token_issue(connection, request)

        # Bootstrap
        if got == "/webui/bootstrap":
            return self._handle_bootstrap(connection, request)

        if got == "/api/settings/login-security":
            return await self._handle_login_security(request, update=False)
        if got.startswith("/api/settings/"):
            try:
                identity = await self._collaboration_identity(request)
            except (CollaborationStoreError, ValueError):
                identity = None
            if identity is not None:
                user, _local_owner = identity
                setattr(request, _SETTINGS_ACTOR_USER_ATTR, user.id)
                setattr(request, _SETTINGS_ACTOR_ORG_ATTR, user.default_organization_id)
                setattr(request, _SETTINGS_ADMIN_ATTR, await self._is_system_admin(request))

        channel_control_path = (
            got in {
                "/api/settings/channels/configure",
                "/api/settings/channels/validate",
                "/api/settings/nanobot-features/enable",
                "/api/settings/nanobot-features/disable",
            }
            or re.fullmatch(r"/api/settings/channels/[^/]+/connect/[^/]+", got) is not None
        )
        connect_match = re.fullmatch(
            r"/api/settings/channels/[^/]+/connect/(start|poll|cancel)", got
        )
        mutation_payload = _mutation_payload(request) or {}
        is_user_connect_session_action = (
            bool(getattr(request, _SETTINGS_ACTOR_USER_ATTR, None))
            and connect_match is not None
            and (
                connect_match.group(1) in {"poll", "cancel"}
                or (
                    connect_match.group(1) == "start"
                    and str(mutation_payload.get("mode", "")).strip().lower() == "create"
                )
            )
        )
        if (
            channel_control_path
            and self.check_api_token(request)
            and not bool(getattr(request, _SETTINGS_ADMIN_ATTR, False))
            and not is_user_connect_session_action
        ):
            target = self._channel_control_target(got, mutation_payload)
            if target is None or not await self._can_manage_channel_instance(
                request, *target
            ):
                return _http_error(403, "System administrator access is required")
        if got == "/api/settings/login-security/update":
            return await self._handle_login_security(request, update=True)

        # Settings routes (delegated)
        response = await self.settings_routes.dispatch(connection, request, got)
        if response is not None:
            return response

        # Collaboration routes
        response = await self._dispatch_collaboration_routes(request, got)
        if response is not None:
            return response

        # Recovery routes
        response = await self._dispatch_recovery_route(request, got)
        if response is not None:
            return response

        # Session routes
        response = await self._dispatch_session_routes(request, got)
        if response is not None:
            return response

        # Media routes
        response = self._dispatch_media_routes(request, got)
        if response is not None:
            return response

        # Automation routes
        response = await self._dispatch_automation_routes(request, got)
        if response is not None:
            return response

        # Misc routes
        response = await self._dispatch_misc_routes(connection, request, got)
        if response is not None:
            return response

        # API 404 (never serve SPA for /api/ routes)
        if got.startswith("/api/"):
            return _http_error(404, "API route not found")

        # Static SPA serving
        if self.static_dist_path is not None:
            response = self._serve_static(
                got,
                accept_encoding=_combined_list_header(request.headers, "Accept-Encoding"),
            )
            if response is not None:
                return response

        return connection.respond(404, "Not Found")

    async def _dispatch_collaboration_routes(
        self, request: WsRequest, path: str
    ) -> Response | None:
        if path == "/api/collaboration":
            return await self._handle_collaboration_index(request)
        if path == "/api/personal":
            return await self._handle_personal_index(request)
        if path == "/api/personal/ics":
            return await self._handle_personal_ics(request)
        if path == "/api/collaboration/organizations":
            return await self._handle_collaboration_organizations(request)
        match = re.fullmatch(r"/api/collaboration/organizations/([^/]+)", path)
        if match is not None:
            return await self._handle_collaboration_organization(request, unquote(match.group(1)))
        match = re.fullmatch(r"/api/collaboration/bots/([^/]+)", path)
        if match is not None:
            return await self._handle_collaboration_bot(request, unquote(match.group(1)))
        match = re.fullmatch(r"/api/collaboration/pairing/([^/]+)", path)
        if match is not None:
            return await self._handle_collaboration_pairing(request, unquote(match.group(1)))
        match = re.fullmatch(r"/api/collaboration/projects/([^/]+)", path)
        if match is not None:
            return await self._handle_collaboration_project(request, unquote(match.group(1)))
        match = re.fullmatch(r"/api/collaboration/mutations/(.+)", path)
        if match is not None:
            return await self._handle_collaboration_mutation(request, match.group(1))
        match = re.fullmatch(r"/api/personal/mutations/(.+)", path)
        if match is not None:
            return await self._handle_personal_mutation(request, match.group(1))
        return None

    async def _collaboration_user_or_error(
        self, request: WsRequest
    ) -> tuple[User, bool] | Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        try:
            identity = await self._collaboration_identity(request)
        except (CollaborationStoreError, ValueError):
            return _http_error(503, "collaboration service unavailable")
        if identity is None:
            return _http_error(401, "Unauthorized")
        return identity

    async def _handle_collaboration_index(self, request: WsRequest) -> Response:
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            projects = (await self.collaboration.list_projects(user.id))[:256]
            organizations = (await self.collaboration.list_organizations(user.id))[:256]
            bots = (await self.collaboration.list_bots(user.id))[:256]
        except (CollaborationStoreError, ValueError):
            return _http_error(503, "collaboration service unavailable")
        active_organization_id = user.default_organization_id
        if active_organization_id not in {item.id for item in organizations}:
            active_organization_id = organizations[0].id if organizations else None
        active_bot_id = user.default_bot_id
        if active_bot_id not in {bot.id for bot in bots}:
            active_bot_id = next(
                (bot.id for bot in bots if bot.organization_id == active_organization_id),
                bots[0].id if bots else None,
            )
        active_project_id = user.default_project_id
        if active_project_id not in {project.id for project in projects}:
            active_project_id = next(
                (
                    project.id
                    for project in projects
                    if project.organization_id == active_organization_id
                ),
                projects[0].id if projects else None,
            )
        return _http_json_response(
            {
                "user": user_payload(user),
                "projects": [project_payload(project) for project in projects],
                "organizations": [organization_payload(item) for item in organizations],
                "bots": [bot_payload(bot) for bot in bots],
                "active_organization_id": active_organization_id,
                "active_bot_id": active_bot_id,
                "active_project_id": active_project_id,
            },
            extra_headers=_NO_STORE_HEADERS,
        )

    async def _handle_collaboration_organizations(
        self, request: WsRequest
    ) -> Response:
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            organizations = (await self.collaboration.list_organizations(user.id))[:256]
        except (CollaborationStoreError, ValueError):
            return _http_error(503, "collaboration service unavailable")
        return _http_json_response(
            {"organizations": [organization_payload(item) for item in organizations]},
            extra_headers=_NO_STORE_HEADERS,
        )

    async def _handle_collaboration_organization(
        self, request: WsRequest, organization_id: str
    ) -> Response:
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            organization = await self.collaboration.get_organization(user.id, organization_id)
            if organization is None:
                return _http_error(404, "organization not found")
            members = await self.collaboration.list_organization_members(
                organization.id, user.id
            )
        except (CollaborationStoreError, ValueError):
            return _http_error(404, "organization not found")
        return _http_json_response(
            {
                "organization": organization_payload(organization),
                "members": [organization_member_payload(member) for member in members],
            },
            extra_headers=_NO_STORE_HEADERS,
        )

    async def _handle_personal_index(self, request: WsRequest) -> Response:
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            vaults = await self.collaboration.list_vaults(user.id)
            tasks: list[PersonalTask] = []
            for vault in vaults:
                tasks.extend(await self.collaboration.list_personal_tasks(user.id, vault.id))
            personas = (await self.collaboration.list_personas(user.id))[:128]
        except (CollaborationStoreError, ValueError):
            return _http_error(503, "personal assistant service unavailable")
        return _http_json_response(
            {
                "user": user_payload(user),
                "default_vault_id": user.default_vault_id,
                "default_persona_id": user.default_persona_id,
                "vaults": [
                    {
                        "id": vault.id, "name": vault.name, "kind": vault.kind.value,
                        "created_at_ms": vault.created_at_ms, "updated_at_ms": vault.updated_at_ms,
                    }
                    for vault in vaults
                ],
                "personas": [
                    {
                        "id": persona.id, "name": persona.name,
                        "default_vault_id": persona.default_vault_id,
                        "instructions": persona.instructions,
                    }
                    for persona in personas
                ],
                "tasks": [personal_task_payload(task) for task in tasks[:1_000]],
            },
            extra_headers=_NO_STORE_HEADERS,
        )

    async def _handle_personal_ics(self, request: WsRequest) -> Response:
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            tasks: list[PersonalTask] = []
            for vault in await self.collaboration.list_vaults(user.id):
                tasks.extend(await self.collaboration.list_personal_tasks(user.id, vault.id))
        except (CollaborationStoreError, ValueError):
            return _http_error(503, "personal assistant service unavailable")
        return _http_json_response(
            {"ics": export_tasks_ics(tasks, calendar_name=f"{user.display_name} — Nanobot")},
            extra_headers=_NO_STORE_HEADERS,
        )

    async def _handle_personal_mutation(
        self, request: WsRequest, operation: str
    ) -> Response:
        if not getattr(request, _WEBUI_MUTATION_REQUEST_ATTR, False):
            return _http_error(405, "personal assistant mutations require an authenticated WebSocket")
        payload = _mutation_payload(request)
        if payload is None:
            return _http_error(400, "invalid personal assistant payload")
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            if operation == "vault/create":
                raw_kind = payload.get("kind", VaultKind.PRIVATE.value)
                vault = await self.collaboration.create_vault(
                    user.id, required_string(payload, "name"), kind=VaultKind(str(raw_kind))
                )
                return _http_json_response({"vault": {"id": vault.id, "name": vault.name, "kind": vault.kind.value}})
            if operation == "vault/default":
                updated = await self.collaboration.update_user_default_vault(
                    user.id, required_string(payload, "vault_id")
                )
                return _http_json_response({"default_vault_id": updated.default_vault_id})
            if operation == "persona/create":
                persona = await self.collaboration.create_persona(
                    user.id, required_string(payload, "name"), required_string(payload, "vault_id"),
                    instructions=optional_string(payload, "instructions") or "",
                )
                return _http_json_response({"persona": {"id": persona.id, "name": persona.name, "default_vault_id": persona.default_vault_id}})
            if operation == "persona/default":
                updated = await self.collaboration.update_user_default_persona(
                    user.id, required_string(payload, "persona_id")
                )
                return _http_json_response({"default_persona_id": updated.default_persona_id})
            if operation == "task/create":
                priority = payload.get("priority", 0)
                due_at_ms = payload.get("due_at_ms")
                if isinstance(priority, bool) or not isinstance(priority, int):
                    raise ValueError("priority must be an integer")
                if due_at_ms is not None and (isinstance(due_at_ms, bool) or not isinstance(due_at_ms, int)):
                    raise ValueError("due_at_ms must be an integer")
                task = await self.collaboration.create_personal_task(
                    user.id, required_string(payload, "vault_id"), required_string(payload, "title"),
                    note=optional_string(payload, "note") or "", priority=priority, due_at_ms=due_at_ms,
                    timezone=optional_string(payload, "timezone"),
                    recurrence_rule=optional_string(payload, "recurrence_rule"),
                    source_type=optional_string(payload, "source_type") or "user",
                    review_state=TaskReviewState(str(payload.get("review_state", TaskReviewState.CONFIRMED.value))),
                )
                return _http_json_response({"task": personal_task_payload(task)})
            if operation == "task/update":
                raw_status = payload.get("status")
                raw_review = payload.get("review_state")
                raw_priority = payload.get("priority")
                priority = raw_priority if isinstance(raw_priority, int) and not isinstance(raw_priority, bool) else None
                raw_due_at_ms = payload.get("due_at_ms")
                due_at_ms = raw_due_at_ms if isinstance(raw_due_at_ms, int) and not isinstance(raw_due_at_ms, bool) else None
                task = await self.collaboration.update_personal_task(
                    user.id, required_string(payload, "task_id"),
                    title=optional_string(payload, "title"), note=optional_string(payload, "note"),
                    status=TaskStatus(str(raw_status)) if raw_status is not None else None,
                    priority=priority, due_at_ms=due_at_ms,
                    review_state=TaskReviewState(str(raw_review)) if raw_review is not None else None,
                )
                return _http_json_response({"task": personal_task_payload(task)})
            if operation == "task/delete":
                deleted = await self.collaboration.delete_personal_task(user.id, required_string(payload, "task_id"))
                return _http_json_response({"deleted": deleted})
            if operation == "share/create":
                raw_permission = payload.get("permission", SharePermission.READ.value)
                grant = await self.collaboration.create_share_grant(
                    user.id, required_string(payload, "vault_id"), required_string(payload, "grantee_user_id"),
                    resource_type=required_string(payload, "resource_type"),
                    resource_id=optional_string(payload, "resource_id"),
                    permission=SharePermission(str(raw_permission)),
                )
                return _http_json_response({"share_grant": {"id": grant.id, "permission": grant.permission.value}})
            if operation == "share/revoke":
                grant = await self.collaboration.revoke_share_grant(user.id, required_string(payload, "grant_id"))
                return _http_json_response({"share_grant": {"id": grant.id, "revoked_at_ms": grant.revoked_at_ms}})
        except (ValueError, CollaborationStoreError):
            return _http_error(400, "invalid or unauthorized personal assistant request")
        return _http_error(404, "unknown personal assistant mutation")

    async def _handle_collaboration_project(
        self, request: WsRequest, project_id: str
    ) -> Response:
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            project = await self.collaboration.get_project(user.id, project_id)
            if project is None:
                return _http_error(404, "project not found")
            profile = await self.collaboration.get_extension_profile(user.id, project.id)
            task_lists = await self.collaboration.list_task_lists(user.id, project.id)
            tasks = await self.collaboration.list_tasks(user.id, project.id)
            context_sources = await self.collaboration.list_context_sources(user.id, project.id)
            members = await self.collaboration.list_members(project.id, user.id)
            project_bots: list[dict[str, object]] = []
            if project.organization_id is not None:
                for bot in (await self.collaboration.list_bots(
                    user.id, organization_id=project.organization_id
                ))[:256]:
                    assignments = await self.collaboration.list_bot_projects(user.id, bot.id)
                    assignment = next(
                        (item for item in assignments if item.project_id == project.id), None
                    )
                    if assignment is None:
                        continue
                    project_bots.append(
                        {
                            "bot": bot_payload(bot),
                            "assignment": bot_project_payload(assignment),
                            "channels": [
                                bot_project_channel_payload(route)
                                for route in await self.collaboration.list_bot_project_channels(
                                    user.id, bot.id, project.id
                                )
                            ],
                        }
                    )
            return _http_json_response(
                {
                    "project": project_payload(project),
                    "members": [project_member_payload(member) for member in members[:256]],
                    "bots": project_bots,
                    "task_lists": [task_list_payload(item) for item in task_lists[:256]],
                    "tasks": [task_payload(item) for item in tasks[:1_000]],
                    "extension_profile": extension_profile_payload(profile),
                    "available": self._collaboration_available(),
                    "context_sources": [context_source_payload(item) for item in context_sources[:256]],
                },
                extra_headers=_NO_STORE_HEADERS,
            )
        except (CollaborationStoreError, ValueError):
            return _http_error(404, "project not found")


    async def _handle_collaboration_bot(
        self, request: WsRequest, bot_id: str
    ) -> Response:
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            bot = await self.collaboration.get_bot(user.id, bot_id)
            if bot is None:
                return _http_error(404, "bot not found")
            assignments = await self.collaboration.list_bot_projects(user.id, bot.id)
            channels = await self.collaboration.list_bot_channels(user.id, bot.id)
            visible_projects = {
                project.id: project
                for project in await self.collaboration.list_projects(user.id)
            }
            project_payloads: list[dict[str, object]] = []
            routes: list[dict[str, object]] = []
            capabilities: list[dict[str, object]] = [
                bot_capability_payload(
                    await self.collaboration.get_bot_capability_profile(user.id, bot.id)
                )
            ]
            for assignment in assignments[:256]:
                project = visible_projects.get(assignment.project_id)
                if project is None:
                    continue
                project_payloads.append(
                    {
                        "assignment": bot_project_payload(assignment),
                        "project": project_payload(project),
                    }
                )
                routes.extend(
                    bot_project_channel_payload(route)
                    for route in await self.collaboration.list_bot_project_channels(
                        user.id, bot.id, project.id
                    )
                )
                capabilities.append(
                    bot_capability_payload(
                        await self.collaboration.get_bot_capability_profile(
                            user.id, bot.id, project_id=project.id
                        )
                    )
                )
            return _http_json_response(
                {
                    "bot": bot_payload(bot),
                    "projects": project_payloads,
                    "channels": [bot_channel_payload(item) for item in channels[:256]],
                    "project_channels": routes[:512],
                    "capability_profiles": capabilities[:257],
                },
                extra_headers=_NO_STORE_HEADERS,
            )
        except (CollaborationStoreError, ValueError):
            return _http_error(404, "bot not found")

    async def _handle_collaboration_pairing(
        self, request: WsRequest, challenge_id: str
    ) -> Response:
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            challenge = await self.collaboration.get_pairing_challenge(
                user.id, challenge_id
            )
        except (CollaborationStoreError, ValueError):
            challenge = None
        if challenge is None:
            return _http_error(404, "pairing challenge not found")
        return _http_json_response(
            {"pairing": pairing_challenge_payload(challenge)},
            extra_headers=_NO_STORE_HEADERS,
        )

    def _collaboration_available(self) -> dict[str, list[dict[str, str]]]:
        skills: list[dict[str, str]] = []
        try:
            loader = SkillsLoader(self.skills_workspace_path)
            for entry in loader.list_skills(filter_unavailable=False)[:256]:
                name = entry.get("name")
                if not isinstance(name, str) or not name:
                    continue
                metadata = loader.get_skill_metadata(name) or {}
                description = metadata.get("description")
                skills.append({
                    "id": name, "name": name,
                    "description": description.strip() if isinstance(description, str) and description.strip() else name,
                })
        except Exception:
            self._log.warning("unable to load collaboration skill metadata")
        mcp_servers: list[dict[str, str]] = []
        try:
            config = self.settings.config.load()
            mcp_servers = [
                {"id": name, "name": name}
                for name in sorted(agent_plugin_mcp_servers(self.skills_workspace_path, config.tools.mcp_servers))[:256]
            ]
        except Exception:
            self._log.warning("unable to load collaboration MCP descriptors")
        return {"skills": skills, "mcp_servers": mcp_servers}

    async def _handle_collaboration_mutation(
        self, request: WsRequest, operation: str
    ) -> Response:
        if not getattr(request, _WEBUI_MUTATION_REQUEST_ATTR, False):
            return _http_error(405, "WebUI mutations require an authenticated WebSocket")
        payload = _mutation_payload(request)
        if payload is None:
            return _http_error(400, "invalid collaboration payload")
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, _local_owner = identity
        try:
            if operation == "organization/create":
                organization = await self.collaboration.create_organization(user.id, required_string(payload, "name"))
                return _http_json_response({"organization": organization_payload(organization)})
            if operation == "organization/update":
                organization = await self.collaboration.update_organization(
                    required_string(payload, "organization_id"), user.id, name=required_string(payload, "name")
                )
                return _http_json_response({"organization": organization_payload(organization)})
            if operation == "organization/delete":
                deleted = await self.collaboration.delete_organization(
                    required_string(payload, "organization_id"), user.id
                )
                return _http_json_response({"deleted": deleted})
            if operation == "organization/member/add":
                raw_role = payload.get("role", OrganizationRole.MEMBER.value)
                member = await self.collaboration.add_organization_member(
                    required_string(payload, "organization_id"), user.id,
                    required_string(payload, "member_user_id"), OrganizationRole(str(raw_role)),
                )
                return _http_json_response({"member": organization_member_payload(member)})
            if operation == "organization/member/remove":
                deleted = await self.collaboration.remove_organization_member(
                    required_string(payload, "organization_id"), user.id,
                    required_string(payload, "member_user_id"),
                )
                return _http_json_response({"deleted": deleted})
            if operation == "user/defaults":
                updated = await self.collaboration.update_user_defaults(
                    user.id,
                    organization_id=required_string(payload, "organization_id"),
                    bot_id=required_string(payload, "bot_id"),
                    project_id=optional_string(payload, "project_id"),
                )
                return _http_json_response({"user": user_payload(updated)})
            if operation == "bot/create":
                bot = await self.collaboration.create_bot(
                    user.id,
                    required_string(payload, "organization_id"),
                    required_string(payload, "name"),
                    avatar_url=optional_string(payload, "avatar_url"),
                    persona_id=optional_string(payload, "persona_id"),
                )
                return _http_json_response({"bot": bot_payload(bot)})
            if operation == "bot/update":
                raw_state = optional_string(payload, "state")
                bot = await self.collaboration.update_bot(
                    required_string(payload, "bot_id"),
                    user.id,
                    name=optional_string(payload, "name"),
                    avatar_url=optional_string(payload, "avatar_url"),
                    persona_id=optional_string(payload, "persona_id"),
                    state_value=BotState(raw_state) if raw_state is not None else None,
                )
                return _http_json_response({"bot": bot_payload(bot)})
            if operation == "bot/delete":
                deleted = await self.collaboration.delete_bot(
                    required_string(payload, "bot_id"), user.id
                )
                return _http_json_response({"deleted": deleted})
            if operation == "bot/capabilities/update":
                settings = payload.get("settings")
                if not isinstance(settings, Mapping):
                    raise ValueError("settings must be an object")
                profile = await self.collaboration.update_bot_capability_profile(
                    user.id,
                    required_string(payload, "bot_id"),
                    cast(Mapping[str, object], settings),
                    project_id=optional_string(payload, "project_id"),
                    expected_revision=optional_nonnegative_int(payload, "revision"),
                )
                return _http_json_response(
                    {"capability_profile": bot_capability_payload(profile)}
                )
            if operation == "pairing/create":
                challenge, code = await self.collaboration.create_pairing_challenge(
                    user.id,
                    purpose=PairingPurpose(required_string(payload, "purpose")),
                    organization_id=required_string(payload, "organization_id"),
                    bot_id=required_string(payload, "bot_id"),
                    channel_type=required_string(payload, "channel_type"),
                    instance_id=required_string(payload, "instance_id"),
                    project_id=optional_string(payload, "project_id"),
                )
                return _http_json_response(
                    {"pairing": pairing_challenge_payload(challenge, code=code)}
                )
            if operation == "pairing/consume":
                challenge = await self.collaboration.consume_pairing_challenge(
                    user.id, required_string(payload, "challenge_id")
                )
                activation: object | None = None
                if self._channel_feature_action is not None:
                    activation = self._channel_feature_action(
                        "activate", challenge.channel_type, challenge.instance_id
                    )
                    if inspect.isawaitable(activation):
                        activation = await activation
                return _http_json_response(
                    {
                        "pairing": pairing_challenge_payload(challenge),
                        **({"channel_activation": activation} if activation is not None else {}),
                    }
                )
            if operation == "project/create":
                return await self._create_collaboration_project(user, payload)
            if operation == "project/member/add":
                raw_role = payload.get("role", MembershipRole.MEMBER.value)
                member = await self.collaboration.add_member(
                    required_string(payload, "project_id"),
                    user.id,
                    required_string(payload, "member_user_id"),
                    MembershipRole(str(raw_role)),
                )
                return _http_json_response({"member": project_member_payload(member)})
            if operation == "project/member/remove":
                deleted = await self.collaboration.remove_member(
                    required_string(payload, "project_id"),
                    user.id,
                    required_string(payload, "member_user_id"),
                )
                return _http_json_response({"deleted": deleted})
            if operation == "task-list/create":
                project_id = required_string(payload, "project_id")
                item = await self.collaboration.create_task_list(
                    project_id, user.id, required_string(payload, "name"), position=optional_position(payload)
                )
                return _http_json_response({"task_list": task_list_payload(item)})
            if operation == "task/create":
                task = await self.collaboration.create_task(
                    required_string(payload, "project_id"), required_string(payload, "task_list_id"),
                    user.id, required_string(payload, "title"),
                    description=optional_string(payload, "description") or "",
                    assignee_user_id=create_assignee(payload),
                    status=optional_status(payload) or TaskStatus.TODO,
                    position=optional_position(payload),
                )
                return _http_json_response({"task": task_payload(task)})
            if operation == "task/update":
                return await self._update_collaboration_task(user, payload)
            if operation == "task/delete":
                return await self._delete_collaboration_task(user, payload)
            if operation == "extensions/update":
                return await self._update_collaboration_extensions(user, payload)
            if operation == "context-source/create":
                enabled = optional_enabled(payload)
                source = await self.collaboration.create_context_source(
                    required_string(payload, "project_id"), user.id, required_string(payload, "name"),
                    required_source_kind(payload), config=optional_config(payload) or {},
                    enabled=True if enabled is None else enabled,
                )
                return _http_json_response({"context_source": context_source_payload(source)})
            if operation == "context-source/update":
                return await self._update_collaboration_context_source(user, payload)
            if operation == "context-source/delete":
                return await self._delete_collaboration_context_source(user, payload)
        except CollaborationConflictError:
            return _http_error(409, "collaboration revision conflict")
        except ValueError:
            return _http_error(400, "invalid collaboration payload")
        except CollaborationStoreError:
            return _http_error(404, "collaboration resource not found")
        return _http_error(404, "unknown collaboration mutation")

    async def _create_collaboration_project(
        self, user: User, payload: Mapping[str, object]
    ) -> Response:
        name = required_string(payload, "name")
        workspace_root = await asyncio.to_thread(get_runtime_subdir, "collaboration")
        workspace = workspace_root / "workspaces" / user.id / f"project-{uuid.uuid4().hex}"

        def create_workspace() -> None:
            workspace.mkdir(parents=True, exist_ok=False)
            try:
                workspace.chmod(0o700)
            except OSError:
                pass

        await asyncio.to_thread(create_workspace)
        try:
            project = await self.collaboration.create_project(
                user.id, name, workspace,
                organization_id=optional_string(payload, "organization_id"),
            )
        except Exception:
            await asyncio.to_thread(workspace.rmdir)
            raise
        return _http_json_response({"project": project_payload(project)})

    async def _project_task(self, user: User, payload: Mapping[str, object]) -> Task:
        task = await self.collaboration.get_task(user.id, required_string(payload, "task_id"))
        if task is None or task.project_id != required_string(payload, "project_id"):
            raise CollaborationStoreError("task is unavailable")
        return task

    async def _update_collaboration_task(
        self, user: User, payload: Mapping[str, object]
    ) -> Response:
        task = await self._project_task(user, payload)
        values = payload.get("values")
        if not isinstance(values, Mapping):
            raise ValueError("values must be an object")
        task_values: dict[str, object] = {}
        for key, value in cast(Mapping[object, object], values).items():
            if not isinstance(key, str):
                raise ValueError("values must be an object")
            task_values[key] = value
        updated = await self.collaboration.update_task(
            task.id, user.id, title=optional_string(task_values, "title"),
            description=optional_string(task_values, "description"), status=optional_status(task_values),
            assignee_user_id=update_assignee(task_values), position=optional_position(task_values),
        )
        return _http_json_response({"task": task_payload(updated)})

    async def _delete_collaboration_task(
        self, user: User, payload: Mapping[str, object]
    ) -> Response:
        task = await self._project_task(user, payload)
        await self.collaboration.delete_task(task.id, user.id)
        return _http_json_response({"deleted": True})

    async def _update_collaboration_extensions(
        self, user: User, payload: Mapping[str, object]
    ) -> Response:
        project_id = required_string(payload, "project_id")
        available = self._collaboration_available()
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("revision must be a non-negative integer")
        profile = await self.collaboration.update_extension_profile(
            user.id, project_id,
            profile_settings(
                payload.get("settings"),
                available_skill_ids={item["id"] for item in available["skills"]},
                available_mcp_ids={item["id"] for item in available["mcp_servers"]},
            ),
            expected_revision=revision,
        )
        return _http_json_response({"extension_profile": extension_profile_payload(profile)})

    async def _project_context_source(
        self, user: User, payload: Mapping[str, object]
    ) -> ContextSource:
        source = await self.collaboration.get_context_source(
            user.id, required_string(payload, "source_id")
        )
        if source is None or source.project_id != required_string(payload, "project_id"):
            raise CollaborationStoreError("context source is unavailable")
        return source

    async def _update_collaboration_context_source(
        self, user: User, payload: Mapping[str, object]
    ) -> Response:
        source = await self._project_context_source(user, payload)
        values = payload.get("values")
        if not isinstance(values, Mapping):
            raise ValueError("values must be an object")
        source_values: dict[str, object] = {}
        for key, value in cast(Mapping[object, object], values).items():
            if not isinstance(key, str):
                raise ValueError("values must be an object")
            source_values[key] = value
        updated = await self.collaboration.update_context_source(
            source.id, user.id, name=optional_string(source_values, "name"),
            kind=optional_source_kind(source_values), config=optional_config(source_values),
            enabled=optional_enabled(source_values),
        )
        return _http_json_response({"context_source": context_source_payload(updated)})

    async def _delete_collaboration_context_source(
        self, user: User, payload: Mapping[str, object]
    ) -> Response:
        source = await self._project_context_source(user, payload)
        await self.collaboration.delete_context_source(source.id, user.id)
        return _http_json_response({"deleted": True})

    def _log_slow_http(self, path: str, response: Any | None, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms < _SLOW_WEBUI_HTTP_LOG_MS:
            return
        if not (path.startswith("/api/") or path == "/webui/bootstrap"):
            return
        status = getattr(response, "status_code", None)
        self._log.warning(
            "slow webui http route path={} status={} duration_ms={}",
            path,
            status if status is not None else "none",
            elapsed_ms,
        )

    # -- Token issue --------------------------------------------------------

    def _handle_token_issue(self, connection: Any, request: Any) -> Any:
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        secret_authenticated = bool(secret) and _issue_route_secret_matches(request.headers, secret)
        proxy_authenticated = _is_trusted_proxy_authenticated_request(
            connection, request.headers, self.config
        )
        oidc_session = self.oidc.session(request.headers) if self.oidc.enabled else None
        if self.oidc.enabled and not (oidc_session or proxy_authenticated or secret_authenticated):
            return connection.respond(401, "Unauthorized")
        if not self.oidc.enabled and secret and not secret_authenticated:
            return connection.respond(401, "Unauthorized")
        if not self.oidc.enabled and not secret:
            self._log.warning(
                "token_issue_path is set but token_issue_secret is empty; "
                "any client can obtain connection tokens — set token_issue_secret for production."
            )
        ttl_s = self.config.token_ttl_s
        principal = None
        if oidc_session is not None:
            remaining = oidc_session.expires_at - time.monotonic()
            ttl_s = min(ttl_s, int(remaining))
            if ttl_s <= 0:
                return connection.respond(401, "Unauthorized")
            principal = oidc_session.principal
        elif proxy_authenticated:
            principal = _trusted_proxy_principal_key(
                connection, request.headers, self.config
            )
            if principal is None:
                return connection.respond(401, "Unauthorized")
        if not self.tokens.can_issue():
            return _http_json_response(
                {"error": "too many outstanding tokens"}, status=429,
                extra_headers=_NO_STORE_HEADERS,
            )
        token_value = self.tokens.issue_token(
            ttl_s, audience="client", principal=principal
        )
        return _http_json_response(
            token_response_payload(token_value, ttl_s), extra_headers=_NO_STORE_HEADERS,
        )

    # -- Bootstrap ----------------------------------------------------------

    async def _handle_oidc_login(self, request: WsRequest) -> Response:
        if not self.oidc.enabled or getattr(request, "method", "GET") != "GET":
            return _http_error(404, "Not Found")
        try:
            _path, query = _parse_request_path(request.path)
            location, cookies = await self.oidc.begin(request.headers, _query_first(query, "return_to"))
        except OidcCapacityError:
            return _http_error(429, "too many active OIDC login flows")
        except OidcError:
            return _http_error(503, "OIDC login unavailable")
        return _http_response(
            b"", status=303,
            extra_headers=[("Location", location), *_NO_STORE_HEADERS, *cookies],
        )

    async def _handle_oidc_callback(self, request: WsRequest) -> Response:
        if not self.oidc.enabled or getattr(request, "method", "GET") != "GET":
            return _http_error(404, "Not Found")
        try:
            _path, query = _parse_request_path(request.path)
            session, return_to, cookies = await self.oidc.callback(request.headers, query)
            await self.initialize_collaboration()
            user, _project = await self.collaboration.ensure_identity_user(
                "oidc", session.principal, self.skills_workspace_path, local_owner=False
            )
            user = await self.collaboration.update_user_display_name(user.id, session.name)
            await self.collaboration.bind_identity("websocket", session.principal, user.id)
        except (CollaborationStoreError, OidcError, ValueError):
            return _http_response(
                b"OIDC login failed", status=400,
                extra_headers=[*_NO_STORE_HEADERS, *self.oidc.logout(request.headers)],
            )
        return _http_response(
            b"", status=303,
            extra_headers=[("Location", return_to), *_NO_STORE_HEADERS, *cookies],
        )

    async def _handle_oidc_logout(self, request: WsRequest) -> Response:
        if not self.oidc.enabled:
            return _http_error(404, "Not Found")
        if getattr(request, "method", "GET") != "GET":
            return _http_response(
                b"Method Not Allowed", status=405,
                extra_headers=[("Allow", "GET"), *_NO_STORE_HEADERS],
            )
        session = self.oidc.session(request.headers)
        if session is None or not self.oidc.csrf_valid(request.headers, session):
            return _http_error(401, "Unauthorized")
        self.tokens.revoke_principal(session.principal)
        if self._oidc_connection_revoker is not None:
            await self._oidc_connection_revoker(session.principal)
        return _http_response(
            b"", status=204,
            extra_headers=[*_NO_STORE_HEADERS, *self.oidc.logout(request.headers)],
        )

    def _handle_bootstrap(self, connection: Any, request: Any) -> Response:
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        is_local_browser = _is_local_browser_request(connection, request.headers)
        is_proxy_authenticated = _is_trusted_proxy_authenticated_request(
            connection, request.headers, self.config
        )
        secret_authenticated = bool(secret) and _issue_route_secret_matches(request.headers, secret)
        oidc_session = self.oidc.session(request.headers) if self.oidc.enabled else None

        if is_proxy_authenticated:
            payload = {
                "ws_path": _normalize_config_path(self.config.path),
                "ws_url": self._bootstrap_ws_url(request),
                "limits": self.ingress.bootstrap_limits(max_frame_bytes=self.config.max_message_bytes),
                "model_name": _resolve_bootstrap_model_name(self.runtime_model_name, self.settings.config.path),
                "runtime_surface": self._runtime_surface,
                "runtime_capabilities": self._capabilities,
            }
            return _http_json_response(payload, extra_headers=_NO_STORE_HEADERS)

        if self.oidc.enabled and oidc_session is None and not secret_authenticated:
            return _http_json_response(
                {
                    "error": "authentication_required",
                    "auth": {
                        "mode": "oidc",
                        "login_url": "/auth/oidc/login",
                        "password_enabled": bool(secret),
                    },
                },
                status=401,
                extra_headers=_NO_STORE_HEADERS,
            )

        if oidc_session is None:
            if secret:
                if not secret_authenticated:
                    return _http_error(401, "Unauthorized")
            elif not is_local_browser:
                return _http_error(403, "bootstrap is localhost-only")

        principal = oidc_session.principal if oidc_session is not None else None
        credential_ttl_s = self.config.token_ttl_s
        if oidc_session is not None:
            credential_ttl_s = min(
                credential_ttl_s, int(oidc_session.expires_at - time.monotonic())
            )
            if credential_ttl_s <= 0:
                return _http_json_response(
                    {
                        "error": "authentication_required",
                        "auth": {
                            "mode": "oidc", "login_url": "/auth/oidc/login",
                            "password_enabled": bool(secret),
                        },
                    },
                    status=401,
                    extra_headers=_NO_STORE_HEADERS,
                )
        api_token_allowed = bool(secret) or is_local_browser or principal is not None
        if not self.tokens.can_issue(include_api_token=api_token_allowed):
            return _http_response(
                json.dumps({"error": "too many outstanding tokens"}).encode("utf-8"),
                status=429,
                content_type="application/json; charset=utf-8",
                extra_headers=_NO_STORE_HEADERS,
            )
        token = self.tokens.issue_token(
            credential_ttl_s, audience="webui", principal=principal
        )
        api_token = (
            self.tokens.issue_api_token(credential_ttl_s, principal=principal)
            if api_token_allowed else None
        )
        payload: dict[str, Any] = {
            "token": token,
            "ws_path": _normalize_config_path(self.config.path),
            "ws_url": self._bootstrap_ws_url(request),
            "expires_in": credential_ttl_s,
            "limits": self.ingress.bootstrap_limits(max_frame_bytes=self.config.max_message_bytes),
            "model_name": _resolve_bootstrap_model_name(self.runtime_model_name, self.settings.config.path),
            "runtime_surface": self._runtime_surface,
            "runtime_capabilities": self._capabilities,
        }
        if api_token is not None:
            payload["api_token"] = api_token
        if oidc_session is not None:
            user: dict[str, str] = {"name": oidc_session.name}
            if oidc_session.email:
                user["email"] = oidc_session.email
            payload["auth"] = {
                "mode": "oidc", "logout_url": "/auth/logout",
                "logout_csrf_token": oidc_session.csrf, "user": user,
            }
        return _http_json_response(payload, extra_headers=_NO_STORE_HEADERS)

    def _bootstrap_ws_url(self, request: Any) -> str:
        headers = getattr(request, "headers", {}) or {}
        if self.config.public_ws_url:
            return self.config.public_ws_url
        host = _safe_host_header(_case_insensitive_header(headers, "Host"))
        if not host:
            host = _host_for_url(self.config.host, self.config.port)
        proto = _case_insensitive_header(headers, "X-Forwarded-Proto")
        proto = proto.split(",", 1)[0].strip().lower()
        secure = proto in {"https", "wss"} or bool(self.config.ssl_certfile.strip())
        scheme = "wss" if secure else "ws"
        expected_path = _normalize_config_path(self.config.path)
        return f"{scheme}://{host}{expected_path}"

    def _mcp_oauth_redirect_uri(self, request: WsRequest) -> str:
        """Derive the browser callback from the same public origin as WebSocket bootstrap."""
        from nanobot.agent.tools.mcp_oauth import MCP_OAUTH_CALLBACK_PATH

        public_ws_url = urlsplit(self._bootstrap_ws_url(request))
        scheme = "https" if public_ws_url.scheme == "wss" else "http"
        return urlunsplit((scheme, public_ws_url.netloc, MCP_OAUTH_CALLBACK_PATH, "", ""))

    # -- Session routes -----------------------------------------------------

    async def _dispatch_session_routes(self, request: WsRequest, got: str) -> Response | None:
        m = re.match(r"^/api/sessions/([^/]+)/webui-thread$", got)
        if m:
            return await self._handle_webui_thread_get(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/context$", got)
        if m:
            return await self._handle_session_context_get(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-preview$", got)
        if m:
            return await self._handle_file_preview(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/automations$", got)
        if m:
            return await self._handle_session_automations(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/delete$", got)
        if m:
            return await self._handle_session_delete(request, m.group(1))

        return None

    async def _dispatch_recovery_route(
        self,
        request: WsRequest,
        path: str,
    ) -> Response | None:
        match = re.fullmatch(r"/api/webui/recovery/(continue|dismiss)", path)
        if match is None:
            return None
        if not getattr(request, _WEBUI_MUTATION_REQUEST_ATTR, False):
            return _http_error(405, "WebUI recovery actions require an authenticated WebSocket")
        if self.recovery_action is None:
            return _http_error(503, "WebUI recovery is unavailable")
        payload = _mutation_payload(request)
        if payload is None:
            return _http_error(400, "invalid recovery payload")
        try:
            result = await self.recovery_action(match.group(1), payload)
        except RecoveryActionError as exc:
            return _http_error(exc.status, str(exc))
        return _http_json_response(result)

    async def _handle_session_context_get(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        if not await self._request_can_access_session(request, decoded_key):
            return _http_error(404, "session not found")
        session = await asyncio.to_thread(
            self.session_manager.read_session_snapshot,
            decoded_key,
        )
        if session is None:
            return _http_error(404, "session not found")
        return _http_json_response(session_context_payload(session))

    async def _handle_sessions_list(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, local_owner = identity
        payload = await asyncio.to_thread(self._sessions_list_payload, user, local_owner)
        return _http_json_response(
            payload,
            accept_encoding=_combined_list_header(request.headers, "Accept-Encoding"),
        )

    def _sessions_list_payload(self, user: Any, local_owner: bool) -> dict[str, Any]:
        assert self.session_manager is not None
        from nanobot.session.webui_turns import websocket_turn_wall_started_at

        sessions = list_webui_sessions(self.session_manager)
        handles = SessionHandleResolver(self.session_manager).list_all_by_key()
        cleaned: list[dict[str, Any]] = []
        default_scope: WorkspaceScope | None = None
        for s in sessions:
            key = s.get("key")
            if not (isinstance(key, str) and is_webui_session_key(key)):
                continue
            if not self._session_owned_by_user_sync(key, user, local_owner):
                continue
            row = {
                k: v
                for k, v in s.items()
                if k != "path" and k not in WEBUI_SESSION_INDEX_INTERNAL_FIELDS
            }
            # Keep the additive recovery field absent for ordinary sessions so
            # older clients and compact list responses stay unchanged.
            if row.get("recovery_state") is None:
                row.pop("recovery_state", None)
            chat_id = key.split(":", 1)[1]
            started_at = websocket_turn_wall_started_at(chat_id)
            if started_at is not None:
                row["run_started_at"] = started_at
            if default_scope is None:
                default_scope = self.workspaces.default_scope()
            scope_present, raw_scope = indexed_workspace_scope(s)
            scope = self.workspaces.scope_for_indexed_metadata(
                raw_scope,
                scope_present=scope_present,
                default_scope=default_scope,
            )
            row["workspace_scope"] = scope.payload()
            handle = handles.get(key)
            if handle is not None:
                row["handle"] = handle.public_payload()
            cleaned.append(row)
        return {"sessions": cleaned}

    async def _handle_webui_thread_get(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        if not await self._request_can_access_session(request, decoded_key):
            return _http_error(404, "session not found")
        scope = self.workspaces.scope_for_session_key(decoded_key)

        def load_session_messages() -> list[dict[str, Any]] | None:
            if self.session_manager is None:
                return None
            session_data = self.session_manager.read_session_file(decoded_key)
            raw_messages = session_data.get("messages") if isinstance(session_data, dict) else None
            if not isinstance(raw_messages, list):
                return None
            raw_session_messages = cast(list[Any], raw_messages)
            return [
                cast(dict[str, Any], raw_message)
                for raw_message in raw_session_messages
                if isinstance(raw_message, dict)
            ]

        query = _parse_query(request.path)
        raw_limit = _query_first(query, "limit")
        limit: int | None = None
        if raw_limit is not None and raw_limit.strip():
            try:
                limit = int(raw_limit)
            except ValueError:
                return _http_error(400, "invalid limit")
        direction = _query_first(query, "direction")
        if direction is not None and direction not in {"latest"}:
            return _http_error(400, "invalid direction")
        before = _query_first(query, "before")
        from nanobot.session.webui_turns import (
            websocket_turn_id,
            websocket_turn_transcript_persistence_failed,
            websocket_turn_wall_started_at,
        )

        chat_id = decoded_key.split(":", 1)[1]
        active_turn_started_at = websocket_turn_wall_started_at(chat_id)
        active_turn_id = websocket_turn_id(chat_id)
        active_turn_transcript_persistence_failed = (
            websocket_turn_transcript_persistence_failed(chat_id)
        )
        data = build_webui_thread_response(
            decoded_key,
            augment_user_media=self.media.augment_transcript_media,
            augment_assistant_media=self.media.augment_transcript_media,
            augment_assistant_text=lambda text: self.media.rewrite_local_markdown_images(
                text,
                workspace_path=scope.project_path,
            ),
            session_messages_loader=load_session_messages,
            active_turn_started_at=active_turn_started_at,
            active_turn_id=active_turn_id,
            active_turn_transcript_persistence_failed=(
                active_turn_transcript_persistence_failed
            ),
            limit=limit,
            direction=direction,
            before=before,
        )
        if data is None:
            return _http_error(404, "webui thread not found")
        data["workspace_scope"] = scope.payload()
        return _http_json_response(
            data,
            accept_encoding=_combined_list_header(request.headers, "Accept-Encoding"),
        )

    async def _handle_file_preview(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        if not await self._request_can_access_session(request, decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        is_probe = _query_first(query, "probe") == "1"
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            if is_probe:
                payload = file_preview_availability_payload(path, scope=scope)
            else:
                payload = file_preview_payload(path, scope=scope)
        except WebUIFilePreviewError as e:
            if is_probe and e.status in {400, 403, 404, 415}:
                return _http_json_response({"available": False})
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_session_automations(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        if not await self._request_can_access_session(request, decoded_key):
            return _http_error(404, "session not found")
        pending_job_ids = self._pending_automation_ids_for_session(decoded_key)
        return _http_json_response(
            session_automations_payload(
                self.cron_service,
                decoded_key,
                local_trigger_store=self.local_trigger_store,
                pending_job_ids=pending_job_ids,
            )
        )

    async def _handle_session_delete(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        if not await self._request_can_access_session(request, decoded_key):
            return _http_error(404, "session not found")
        query = _request_query(request)
        delete_automations = (_query_first(query, "delete_automations") or "").lower()
        automation_jobs = session_automation_jobs(
            self.cron_service,
            decoded_key,
            local_trigger_store=self.local_trigger_store,
        )
        if automation_jobs and delete_automations not in {"1", "true", "yes"}:
            return _http_json_response(
                {
                    "deleted": False,
                    "blocked_by_automations": True,
                    "automations": serialize_automation_jobs(automation_jobs),
                }
            )
        if automation_jobs:
            for job in automation_jobs:
                if isinstance(job, LocalTrigger):
                    if self.local_trigger_store is not None:
                        self.local_trigger_store.delete(job.id)
                elif self.cron_service is not None:
                    self.cron_service.remove_job(job.id)
        session_deleted = self.session_manager.delete_session(decoded_key)
        transcript_deleted = delete_webui_thread(decoded_key)
        return _http_json_response({"deleted": bool(session_deleted or transcript_deleted)})

    # -- Automation routes --------------------------------------------------

    async def _dispatch_automation_routes(
        self,
        request: WsRequest,
        got: str,
    ) -> Response | None:
        if got == "/api/webui/automations":
            return await self._handle_webui_automations(request)
        m = re.match(r"^/api/webui/automations/(enable|disable|delete|run|update)$", got)
        if m:
            return await self._handle_webui_automation_action(request, m.group(1))
        return None

    def _pending_cron_job_ids_for_all(self) -> set[str]:
        if self.cron_service is None or self.cron_pending_job_ids is None:
            return set()
        pending: set[str] = set()
        for job in self.cron_service.list_jobs(include_disabled=True):
            session_key = job.payload.session_key
            if not session_key and job.payload.origin_channel and job.payload.origin_chat_id:
                session_key = f"{job.payload.origin_channel}:{job.payload.origin_chat_id}"
            if session_key:
                pending.update(self.cron_pending_job_ids(session_key))
        return pending

    def _pending_local_trigger_ids_for_all(self) -> set[str]:
        if self.local_trigger_store is None or self.local_trigger_pending_ids is None:
            return set()
        pending: set[str] = set()
        for trigger in self.local_trigger_store.list_triggers(include_disabled=True):
            session_key = trigger.session_key
            if not session_key and trigger.channel and trigger.chat_id:
                session_key = f"{trigger.channel}:{trigger.chat_id}"
            if session_key:
                pending.update(self.local_trigger_pending_ids(session_key))
        return pending

    def _pending_automation_ids_for_session(self, session_key: str) -> set[str]:
        pending: set[str] = set()
        if self.cron_pending_job_ids is not None:
            pending.update(self.cron_pending_job_ids(session_key))
        if self.local_trigger_pending_ids is not None:
            pending.update(self.local_trigger_pending_ids(session_key))
        return pending
    @staticmethod
    def _automation_session_key(job: CronJob | LocalTrigger) -> str | None:
        if isinstance(job, LocalTrigger):
            if job.session_key:
                return job.session_key
            return f"{job.channel}:{job.chat_id}" if job.channel and job.chat_id else None
        if job.payload.session_key:
            return job.payload.session_key
        if job.payload.origin_channel and job.payload.origin_chat_id:
            return f"{job.payload.origin_channel}:{job.payload.origin_chat_id}"
        return None

    async def _automation_owned_by_user(
        self, job: CronJob | LocalTrigger, user: Any, local_owner: bool
    ) -> bool:
        if local_owner:
            return True
        session_key = self._automation_session_key(job)
        if session_key is None:
            return False
        return await self._session_owned_by_user(session_key, user, local_owner)


    async def _handle_webui_automations(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, local_owner = identity
        pending_job_ids = self._pending_cron_job_ids_for_all()
        pending_job_ids.update(self._pending_local_trigger_ids_for_all())
        jobs: list[CronJob | LocalTrigger] = []
        if self.cron_service is not None:
            jobs.extend(self.cron_service.list_jobs(include_disabled=True))
        if self.local_trigger_store is not None:
            jobs.extend(self.local_trigger_store.list_triggers(include_disabled=True))
        authorized_jobs = [
            job for job in jobs
            if await self._automation_owned_by_user(job, user, local_owner)
        ]
        return _http_json_response(
            {
                "jobs": serialize_automation_jobs(
                    authorized_jobs,
                    pending_job_ids=pending_job_ids,
                    include_details=True,
                    session_manager=self.session_manager,
                )
            }
        )

    async def _handle_webui_automation_action(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.cron_service is None and self.local_trigger_store is None:
            return _http_error(503, "automation service unavailable")

        query = _request_query(request)
        job_id = (_query_first(query, "id") or _query_first(query, "job_id") or "").strip()
        if not job_id:
            return _http_error(400, "missing automation id")
        trigger = self.local_trigger_store.get(job_id) if self.local_trigger_store else None
        if trigger is not None:
            identity = await self._collaboration_user_or_error(request)
            if isinstance(identity, Response):
                return identity
            user, local_owner = identity
            if not await self._automation_owned_by_user(trigger, user, local_owner):
                return _http_error(404, "automation not found")
            return await self._handle_local_trigger_action(request, action, trigger)

        if self.cron_service is None:
            return _http_error(404, "automation not found")
        job = self.cron_service.get_job(job_id)
        if job is None:
            return _http_error(404, "automation not found")
        identity = await self._collaboration_user_or_error(request)
        if isinstance(identity, Response):
            return identity
        user, local_owner = identity
        if not await self._automation_owned_by_user(job, user, local_owner):
            return _http_error(404, "automation not found")
        if job.payload.kind == "system_event":
            return _http_error(403, "system automation is protected")
        if action in {"enable", "run"} and not is_bound_cron_job(job):
            return _http_error(409, "automation has no linked chat")

        if action == "enable":
            if self.cron_service.enable_job(job_id, enabled=True) is None:
                return _http_error(404, "automation not found")
        elif action == "disable":
            if self.cron_service.enable_job(job_id, enabled=False) is None:
                return _http_error(404, "automation not found")
        elif action == "delete":
            result = self.cron_service.remove_job(job_id)
            if result == "not_found":
                return _http_error(404, "automation not found")
            if result == "protected":
                return _http_error(403, "system automation is protected")
        elif action == "run":
            if not job.enabled:
                return _http_error(409, "automation is disabled")
            task = asyncio.create_task(self.cron_service.run_job(job_id, force=False))
            task.add_done_callback(self._log_automation_run_result)
        elif action == "update":
            values = _automation_values_from_request(request)
            if values is None:
                return _http_error(400, "invalid automation update payload")
            parsed = _parse_automation_update(values, current_job=job)
            if isinstance(parsed, str):
                return _http_error(400, parsed)
            try:
                result = self.cron_service.update_job(job_id, **parsed)
            except ValueError as exc:
                return _http_error(400, str(exc))
            if result == "not_found":
                return _http_error(404, "automation not found")
            if result == "protected":
                return _http_error(403, "system automation is protected")
        else:
            return _http_error(404, "unknown automation action")

        return await self._handle_webui_automations(request)

    async def _handle_local_trigger_action(
        self,
        request: WsRequest,
        action: str,
        trigger: LocalTrigger,
    ) -> Response:
        if self.local_trigger_store is None:
            return _http_error(503, "trigger service unavailable")
        if action == "enable":
            if self.local_trigger_store.enable(trigger.id, enabled=True) is None:
                return _http_error(404, "automation not found")
        elif action == "disable":
            if self.local_trigger_store.enable(trigger.id, enabled=False) is None:
                return _http_error(404, "automation not found")
        elif action == "delete":
            if not self.local_trigger_store.delete(trigger.id):
                return _http_error(404, "automation not found")
        elif action == "run":
            return _http_error(409, "local trigger requires a CLI message")
        elif action == "update":
            values = _automation_values_from_request(request)
            if values is None:
                return _http_error(400, "invalid automation update payload")
            parsed = _parse_local_trigger_update(values)
            if isinstance(parsed, str):
                return _http_error(400, parsed)
            if parsed:
                if self.local_trigger_store.update(trigger.id, **parsed) is None:
                    return _http_error(404, "automation not found")
        else:
            return _http_error(404, "unknown automation action")

        return await self._handle_webui_automations(request)

    @staticmethod
    def _log_automation_run_result(task: asyncio.Task[bool]) -> None:
        try:
            ran = task.result()
        except Exception:
            logger.exception("WebUI automation run-now task failed")
            return
        if not ran:
            logger.warning("WebUI automation run-now task did not execute")

    # -- Media routes -------------------------------------------------------

    def _dispatch_media_routes(self, request: WsRequest, got: str) -> Response | None:
        m = re.match(r"^/api/media/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$", got)
        if m:
            return self._handle_media_fetch(m.group(1), m.group(2), request)
        return None

    def _handle_media_fetch(
        self, sig: str, payload: str, request: WsRequest | None = None
    ) -> Response:
        return self.media.serve_signed_media(
            sig,
            payload,
            request=request,
        )

    # -- Misc routes --------------------------------------------------------

    async def _dispatch_misc_routes(
        self, connection: Any, request: WsRequest, got: str
    ) -> Response | None:
        if got == "/api/sessions":
            return await self._handle_sessions_list(request)
        if got == "/api/commands":
            return self._handle_commands(request)
        if got == "/api/workspaces/pick-folder":
            return await self._handle_workspace_folder_picker(connection, request)
        if got == "/api/workspaces":
            return self._handle_workspaces(connection, request)
        if got == "/api/webui/skills/search":
            return await self._handle_webui_skills_search(request)
        if got == "/api/webui/skills/trending":
            return await self._handle_webui_skills_trending(request)
        if got == "/api/webui/skills/trends":
            return await self._handle_webui_skill_trends(request)
        if got == "/api/webui/skills/install":
            return await self._handle_webui_skill_install(connection, request)
        if got == "/api/webui/skills/update":
            return self._handle_webui_skill_update(request)
        if got == "/api/webui/skills/delete":
            return self._handle_webui_skill_delete(connection, request)
        if got == "/api/webui/skills":
            return self._handle_webui_skills(request)
        m = re.match(r"^/api/webui/skills/([^/]+)$", got)
        if m:
            return self._handle_webui_skill_detail(request, m.group(1))
        if got == "/api/webui/sidebar-state":
            return self._handle_webui_sidebar_state(request)
        if got == "/api/webui/sidebar-state/update":
            return self._handle_webui_sidebar_state_update(request)
        return None

    def _handle_commands(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response({"commands": builtin_command_palette()})

    def _handle_workspaces(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            self.workspaces.payload(
                controls_available=self.workspace_controls_available(connection),
                folder_picker_available=self.workspace_folder_picker_available(
                    connection,
                    request,
                ),
            )
        )

    async def _handle_workspace_folder_picker(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_folder_picker_available(connection, request):
            return _http_error(403, "native folder picker is unavailable for this connection")
        if self._folder_picker_lock.locked():
            return _http_error(409, "native folder picker is already open")
        try:
            async with self._folder_picker_lock:
                path = await pick_native_folder()
        except NativeFolderPickerError as exc:
            return _http_error(503, str(exc))
        return _http_json_response({"path": path})

    def _handle_webui_skills(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            webui_skills_payload(
                self.skills_workspace_path,
                disabled_skills=self.disabled_skills,
            )
        )

    async def _handle_webui_skills_search(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        params = _parse_query(request.path)
        query = _query_first(params, "q") or ""
        provider = _query_first(params, "provider") or "all"
        try:
            payload = await search_marketplace_skills(
                query,
                self.skills_workspace_path,
                provider=provider,
            )
        except SkillsMarketplaceError as exc:
            return _http_error(exc.status, exc.message)
        except Exception:
            self._log.exception("skills marketplace search failed")
            return _http_error(500, "skills marketplace search failed")
        return _http_json_response(payload)

    async def _handle_webui_skills_trending(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        provider = _query_first(_parse_query(request.path), "provider") or "all"
        try:
            payload = await trending_marketplace_skills(
                self.skills_workspace_path,
                provider=provider,
            )
        except SkillsMarketplaceError as exc:
            return _http_error(exc.status, exc.message)
        except Exception:
            self._log.exception("skills marketplace trending lookup failed")
            return _http_error(500, "skills marketplace trending lookup failed")
        return _http_json_response(payload)

    async def _handle_webui_skill_trends(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        skill_ids = _parse_query(request.path).get("id", [])
        try:
            payload = await marketplace_skill_trends(skill_ids)
        except Exception:
            self._log.exception("skills.sh trend history lookup failed")
            return _http_error(500, "skills.sh trend history lookup failed")
        return _http_json_response(payload)

    async def _handle_webui_skill_install(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self._allow_webui_package_install(connection, request):
            return _http_error(403, "remote skill installation is disabled")
        if self._skill_install_lock.locked():
            return _http_error(409, "another skill installation is already in progress")

        query = _request_query(request)
        provider = _query_first(query, "provider") or "skills_sh"
        source = _query_first(query, "source") or ""
        skill_id = _query_first(query, "skill") or ""
        version = _query_first(query, "version") or ""
        async with self._skill_install_lock:
            try:
                action = await install_marketplace_skill(
                    source,
                    skill_id,
                    self.skills_workspace_path,
                    provider=provider,
                    version=version,
                )
            except SkillsMarketplaceError as exc:
                return _http_error(exc.status, exc.message)
            except Exception:
                self._log.exception("skill installation failed")
                return _http_error(500, "skill installation failed")
        return _http_json_response({
            **webui_skills_payload(
                self.skills_workspace_path,
                disabled_skills=self.disabled_skills,
            ),
            "last_action": action,
        })

    def _allow_webui_package_install(self, connection: Any, request: WsRequest) -> bool:
        if _is_local_browser_request(connection, request.headers):
            return True
        try:
            return bool(
                self.settings.config.load().tools.webui_allow_remote_package_install
            )
        except Exception:
            self._log.exception("failed to load remote package install policy")
            return False

    def _handle_webui_skill_update(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _request_query(request)
        name = _query_first(query, "name") or ""
        raw_enabled = (_query_first(query, "enabled") or "").lower()
        if raw_enabled not in {"true", "false"}:
            return _http_error(400, "enabled must be true or false")
        try:
            action = self.settings.config.run_serialized(
                lambda config_path: set_webui_skill_enabled(
                    self.skills_workspace_path,
                    name,
                    enabled=raw_enabled == "true",
                    disabled_skills=self.disabled_skills,
                    config_path=config_path,
                )
            )
        except SkillManagementError as exc:
            return _http_error(exc.status, exc.message)
        self._apply_skill_state()
        return _http_json_response({
            **webui_skills_payload(
                self.skills_workspace_path,
                disabled_skills=self.disabled_skills,
            ),
            "last_action": action,
        })

    def _handle_webui_skill_delete(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not _is_local_browser_request(connection, request.headers):
            return _http_error(403, "remote skill deletion is disabled")
        name = _query_first(_request_query(request), "name") or ""
        try:
            action = self.settings.config.run_serialized(
                lambda config_path: delete_webui_skill(
                    self.skills_workspace_path,
                    name,
                    disabled_skills=self.disabled_skills,
                    config_path=config_path,
                )
            )
        except SkillManagementError as exc:
            return _http_error(exc.status, exc.message)
        self._apply_skill_state()
        return _http_json_response({
            **webui_skills_payload(
                self.skills_workspace_path,
                disabled_skills=self.disabled_skills,
            ),
            "last_action": action,
        })

    def _apply_skill_state(self) -> None:
        if self.skill_state_action is not None:
            self.skill_state_action(set(self.disabled_skills))

    def _handle_webui_skill_detail(self, request: WsRequest, raw_name: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from urllib.parse import unquote

        name = unquote(raw_name)
        if not name or "/" in name or "\\" in name:
            return _http_error(400, "invalid skill name")
        payload = webui_skill_detail_payload(
            self.skills_workspace_path,
            name,
            disabled_skills=self.disabled_skills,
        )
        if payload is None:
            return _http_error(404, "skill not found")
        return _http_json_response(payload)

    def _handle_webui_sidebar_state(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(read_webui_sidebar_state())

    def _handle_webui_sidebar_state_update(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = _mutation_payload(request)
        state_value = payload.get("state") if payload is not None else None
        if state_value is None:
            return _http_error(400, "missing state")
        if not _is_string_dict(state_value):
            return _http_error(400, "state must be an object")
        try:
            state = write_webui_sidebar_state(state_value)
        except ValueError as e:
            return _http_error(400, str(e))
        except OSError:
            self._log.exception("failed to write webui sidebar state")
            return _http_error(500, "failed to write sidebar state")
        return _http_json_response(state)

    # -- Static file serving ------------------------------------------------

    def _serve_static(
        self,
        request_path: str,
        *,
        accept_encoding: str = "",
    ) -> Response | None:
        assert self.static_dist_path is not None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        if ".." in rel.split("/") or rel.startswith("/"):
            return _http_error(403, "Forbidden")
        candidate = (self.static_dist_path / rel).resolve()
        try:
            candidate.relative_to(self.static_dist_path)
        except ValueError:
            return _http_error(403, "Forbidden")
        if not candidate.is_file():
            index = self.static_dist_path / "index.html"
            if index.is_file():
                candidate = index
            else:
                return None
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        utf8_text = ctype.startswith("text/") or ctype in {
            "application/javascript",
            "application/json",
        }
        compressible = utf8_text or ctype == "image/svg+xml"
        response_path = candidate
        extra_headers: list[tuple[str, str]] = []
        if compressible:
            extra_headers.append(("Vary", "Accept-Encoding"))
            gzip_candidate = candidate.with_name(f"{candidate.name}.gz")
            if _accepts_gzip(accept_encoding) and gzip_candidate.is_file():
                response_path = gzip_candidate
                extra_headers.append(("Content-Encoding", "gzip"))
        try:
            body = response_path.read_bytes()
        except OSError as e:
            self._log.warning("static: failed to read {}: {}", response_path, e)
            return _http_error(500, "Internal Server Error")
        if utf8_text:
            ctype = f"{ctype}; charset=utf-8"
        if candidate.name == "index.html":
            cache = "no-cache"
        else:
            cache = "public, max-age=31536000, immutable"
        return _http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", cache), *extra_headers],
        )


def _automation_values_from_request(request: WsRequest) -> dict[str, object] | None:
    payload = _mutation_payload(request)
    if payload is None or "values" not in payload:
        return {}
    values = payload["values"]
    return values if _is_string_dict(values) else None


def _parse_automation_update(
    values: Mapping[str, object],
    *,
    current_job: CronJob | None = None,
) -> _AutomationUpdate | str:
    update: _AutomationUpdate = {}
    if "name" in values:
        raw_name = values.get("name")
        if not isinstance(raw_name, str):
            return "name must be a string"
        name = raw_name.strip()
        if not name:
            return "name cannot be empty"
        update["name"] = name
    if "message" in values:
        raw_message = values.get("message")
        if not isinstance(raw_message, str):
            return "message must be a string"
        message = raw_message.strip()
        if not message:
            return "message cannot be empty"
        update["message"] = message
    if "schedule" in values:
        raw_schedule = values.get("schedule")
        if not _is_string_dict(raw_schedule):
            return "schedule must be an object"
        parsed_schedule = _parse_automation_schedule(raw_schedule)
        if isinstance(parsed_schedule, str):
            return parsed_schedule
        if current_job is not None and _schedule_matches_job(parsed_schedule, current_job):
            return update
        schedule_error = _validate_automation_schedule(parsed_schedule)
        if schedule_error:
            return schedule_error
        update["schedule"] = parsed_schedule
        update["delete_after_run"] = parsed_schedule.kind == "at"
    return update


def _parse_local_trigger_update(values: Mapping[str, object]) -> _LocalTriggerUpdate | str:
    update: _LocalTriggerUpdate = {}
    if "name" in values:
        raw_name = values.get("name")
        if not isinstance(raw_name, str):
            return "name must be a string"
        name = raw_name.strip()
        if not name:
            return "name cannot be empty"
        update["name"] = name
    forbidden = [key for key in ("message", "schedule") if key in values]
    if forbidden:
        return "local trigger updates only support name"
    return update


def _parse_automation_schedule(values: Mapping[str, object]) -> CronSchedule | str:
    raw_kind = values.get("kind")
    if not isinstance(raw_kind, str):
        return "schedule kind must be a string"
    kind = raw_kind.strip()
    if kind == "every":
        every_ms = _positive_int(values.get("every_ms"))
        if every_ms is None:
            return "every schedule requires positive every_ms"
        return CronSchedule(kind="every", every_ms=every_ms)
    if kind == "cron":
        raw_expr = values.get("expr")
        if not isinstance(raw_expr, str):
            return "cron schedule requires expr"
        expr = raw_expr.strip()
        if not expr:
            return "cron schedule requires expr"
        raw_tz = values.get("tz")
        if raw_tz is not None and not isinstance(raw_tz, str):
            return "cron schedule timezone must be a string"
        tz = raw_tz.strip() if isinstance(raw_tz, str) else ""
        return CronSchedule(kind="cron", expr=expr, tz=tz or None)
    if kind == "at":
        at_ms = _positive_int(values.get("at_ms"))
        if at_ms is None:
            return "one-time schedule requires positive at_ms"
        return CronSchedule(kind="at", at_ms=at_ms)
    return "unknown schedule kind"


def _schedule_matches_job(schedule: CronSchedule, job: CronJob) -> bool:
    current = job.schedule
    if schedule.kind != current.kind:
        return False
    if schedule.kind == "at":
        return schedule.at_ms == current.at_ms
    if schedule.kind == "every":
        return schedule.every_ms == current.every_ms
    if schedule.kind == "cron":
        return (schedule.expr or "") == (current.expr or "") and (
            schedule.tz or None
        ) == (current.tz or None)
    return False


def _validate_automation_schedule(schedule: CronSchedule) -> str | None:
    if schedule.kind == "at":
        if not schedule.at_ms or schedule.at_ms <= int(time.time() * 1000):
            return "one-time schedule must be in the future"
        return None
    if schedule.kind != "cron":
        return None

    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from croniter import croniter

        tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
        base = datetime.now(tz=tz)
        croniter(cast(str, schedule.expr), base).get_next(datetime)
    except Exception:
        return "cron schedule is invalid"
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _is_websocket_channel_session_key(key: str) -> bool:
    return is_webui_session_key(key)
