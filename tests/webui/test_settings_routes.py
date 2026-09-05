from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.datastructures import Headers

from nanobot.config.loader import get_config_path
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.mcp_presets_api import custom_mcp_action
from nanobot.webui.settings_routes import WebUISettingsRouter
from nanobot.webui.settings_services import WebUISettingsServices


def _router(
    *,
    authorized: bool = True,
    config_path: Path | None = None,
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    mcp_reload: Callable[[], Awaitable[dict[str, object]]] | None = None,
    rename_model_preset: Callable[[str, str], int] | None = None,
    refresh_runtime_config: Callable[[], None] | None = None,
) -> WebUISettingsRouter:
    return WebUISettingsRouter(
        settings=WebUISettingsServices.create(
            config_path or get_config_path(),
            rename_model_preset=rename_model_preset,
            refresh_runtime_config=refresh_runtime_config,
        ),
        bus=SimpleNamespace(),
        logger=SimpleNamespace(exception=lambda *_args: None),
        check_api_token=lambda _request: authorized,
        parse_query=lambda path: parse_qs(urlsplit(path).query),
        json_response=http_json_response,
        error_response=lambda status, message: http_json_response(
            {"error": message},
            status=status,
        ),
        runtime_surface="browser",
        runtime_capabilities={},
        mcp_runtime_status=mcp_runtime_status,
        mcp_reload=mcp_reload,
        mcp_oauth_redirect_uri=lambda _request: "https://gateway.example/auth/mcp/callback",
    )


def _mutation_request(
    path: str,
    payload: dict[str, object],
    *,
    actor_user_id: str | None = None,
    system_admin: bool = False,
    host_admin: bool | None = None,
) -> SimpleNamespace:
    request = SimpleNamespace(path=path, headers=Headers())
    request._nanobot_webui_mutation_request = True
    request._nanobot_webui_mutation_payload = payload
    request._nanobot_trusted_proxy_authenticated = True
    request._nanobot_settings_actor_user_id = actor_user_id
    request._nanobot_settings_system_admin = system_admin
    request._nanobot_settings_host_admin = (
        system_admin if host_admin is None else host_admin
    )
    return request


def _read_request(
    path: str,
    *,
    actor_user_id: str | None = None,
    system_admin: bool = False,
    host_admin: bool | None = None,
) -> SimpleNamespace:
    request = SimpleNamespace(path=path, headers=Headers())
    request._nanobot_settings_actor_user_id = actor_user_id
    request._nanobot_settings_system_admin = system_admin
    request._nanobot_settings_host_admin = (
        system_admin if host_admin is None else host_admin
    )
    return request


@pytest.mark.asyncio
async def test_mcp_list_serializes_local_runtime_failure_snapshot(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    custom_mcp_action(
        "custom",
        {
            "name": ["team-docs"],
            "transport": ["streamableHttp"],
            "url": ["https://mcp.example.com/mcp"],
        },
        config_path=config_path,
    )
    snapshot_calls = 0

    def runtime_snapshot() -> Mapping[str, str]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return {"team-docs": "failed"}

    router = _router(
        config_path=config_path,
        mcp_runtime_status=runtime_snapshot,
    )
    request = SimpleNamespace(
        path="/api/settings/mcp-presets",
        headers=Headers(),
    )

    response = await router.dispatch(None, request, "/api/settings/mcp-presets")

    assert response is not None
    assert response.status_code == 200
    payload = json.loads(response.body)
    row = next(item for item in payload["presets"] if item["name"] == "team-docs")
    assert row["status"] == "configured"
    assert row["runtime_status"] == "failed"
    assert b'"runtime_status": "failed"' in response.body
    assert snapshot_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin"),
    [("member", False), (None, True)],
)
async def test_agent_plugin_mutations_require_server_derived_admin_and_actor(
    tmp_path: Path,
    actor_user_id: str | None,
    system_admin: bool,
) -> None:
    config_path = tmp_path / "config.json"
    reload_calls = 0

    async def reload_mcp() -> dict[str, object]:
        nonlocal reload_calls
        reload_calls += 1
        return {"ok": True, "requires_restart": False}

    request = _mutation_request(
        "/api/settings/mcp-presets/enable",
        {
            "extension_id": "ext:agent_plugin:desktop",
            "expected_revision": "client-revision",
            "risk_acknowledged": True,
            "actor_user_id": "client-controlled-actor",
            "system_admin": True,
        },
        actor_user_id=actor_user_id,
        system_admin=system_admin,
    )

    response = await _router(config_path=config_path, mcp_reload=reload_mcp).dispatch(
        None,
        request,
        request.path,
    )

    assert response is not None
    assert response.status_code == 403
    assert reload_calls == 0
    assert not config_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/settings/mcp-presets/enable",
        "/api/settings/mcp-presets/disable",
        "/api/settings/mcp-presets/remove",
        "/api/settings/mcp-presets/test",
        "/api/settings/mcp-presets/reconnect",
        "/api/settings/mcp-presets/custom",
        "/api/settings/mcp-presets/import",
        "/api/settings/mcp-presets/import-cursor",
        "/api/settings/mcp-presets/tools",
    ],
)
async def test_every_mcp_mutation_rejects_non_admin_before_side_effects(
    tmp_path: Path,
    path: str,
) -> None:
    config_path = tmp_path / "config.json"
    reload_calls = 0

    async def reload_mcp() -> dict[str, object]:
        nonlocal reload_calls
        reload_calls += 1
        return {"ok": True, "requires_restart": False}

    request = _mutation_request(
        path,
        {"name": "blocked", "transport": "stdio", "command": "echo"},
        actor_user_id="ordinary-user",
        system_admin=False,
    )

    response = await _router(config_path=config_path, mcp_reload=reload_mcp).dispatch(
        None,
        request,
        path,
    )

    assert response is not None
    assert response.status_code == 403
    assert reload_calls == 0
    assert not config_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin"),
    [("ordinary-member", False), (None, True), ("   ", True)],
)
@pytest.mark.parametrize(
    "path",
    [
        "/api/settings/update",
        "/api/settings/model-configurations/create",
        "/api/settings/model-configurations/update",
        "/api/settings/model-configurations/delete",
        "/api/settings/model-configurations/migrate",
        "/api/settings/model-call-order/update",
        "/api/settings/provider/create",
        "/api/settings/provider/update",
        "/api/settings/provider/oauth-login",
        "/api/settings/provider/oauth-login/complete",
        "/api/settings/provider/oauth-logout",
    ],
)
async def test_model_routes_reject_non_admins_before_writing_provider_credentials(
    tmp_path: Path,
    path: str,
    actor_user_id: str | None,
    system_admin: bool,
) -> None:
    """Provider settings carry API keys, so no member may reach the host config file."""
    config_path = tmp_path / "config.json"
    refresh_calls = 0

    def refresh_runtime_config() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    request = _mutation_request(
        path,
        {"provider": "openai", "api_key": "attacker-supplied-key", "name": "openai"},
        actor_user_id=actor_user_id,
        system_admin=system_admin,
    )

    response = await _router(
        config_path=config_path,
        refresh_runtime_config=refresh_runtime_config,
    ).dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 403
    assert refresh_calls == 0
    assert not config_path.exists()


# Every settings route needs a decided authorization posture. A route that is neither
# refused for a member here nor listed below is undecided — which is exactly how the
# pairing and capability gaps shipped. Adding a route means adding it to one of these.
_MEMBER_REACHABLE_ROUTES = {
    "/api/settings/provider-models": "read-only model list that the model picker needs",
    "/api/settings/api-service": "read-only API service status",
    "/api/settings/cli-apps": "read-only installed CLI app list",
    "/api/settings/version-check": "read-only update check",
    "/api/settings/mcp-presets": "read-only MCP preset list",
    "/api/settings/nanobot-features": (
        "read that projects to a restricted, non-enumerating payload for members; "
        "proven by test_nanobot_features_projects_away_the_host_inventory_for_members"
    ),
}

# Empty on purpose. Every settings route now refuses a member inside its own domain, so
# nothing depends on ws_http.py alone. A route added here is one that a second caller of
# dispatch would reach unprotected, which is the shape this file exists to catch.
_ENFORCED_BEFORE_DISPATCH: dict[str, str] = {}


@pytest.mark.asyncio
async def test_no_settings_route_reaches_a_member_without_a_recorded_decision(
    tmp_path: Path,
) -> None:
    """A newly added privileged route cannot ship undecided the way pairing did."""
    from nanobot.webui.settings_routes import (
        _CAPABILITY_ROUTES,
        _EXTENSION_ROUTES,
        _MODEL_ROUTES,
        _SYSTEM_ROUTES,
    )

    undecided: list[tuple[str, int]] = []
    stale: list[str] = []
    for path in (
        *_MODEL_ROUTES,
        *_CAPABILITY_ROUTES,
        *_EXTENSION_ROUTES,
        *_SYSTEM_ROUTES,
    ):
        request = _mutation_request(
            path,
            {"name": "probe", "provider": "openai", "code": "PROBE"},
            actor_user_id="ordinary-member",
            system_admin=False,
        )
        response = await _router(config_path=tmp_path / "config.json").dispatch(
            None, request, path
        )
        status = 404 if response is None else response.status_code
        declared = path in _MEMBER_REACHABLE_ROUTES or path in _ENFORCED_BEFORE_DISPATCH
        if status == 403:
            if declared:
                stale.append(path)
            continue
        if declared:
            continue
        undecided.append((path, status))

    assert stale == [], (
        "these settings routes now refuse a non-administrator on their own, so their "
        f"member-reachable / enforced-earlier declaration is out of date: {stale}"
    )
    assert undecided == [], (
        "these settings routes ran for a non-administrator without being declared "
        f"member-reachable or enforced earlier in the stack: {undecided}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin"),
    [("ordinary-member", False), (None, True), ("   ", True)],
)
async def test_nanobot_features_projects_away_the_host_inventory_for_members(
    monkeypatch: pytest.MonkeyPatch,
    actor_user_id: str | None,
    system_admin: bool,
) -> None:
    """The one member-reachable system read must not enumerate host packages."""
    router = _router()

    def unreachable() -> dict[str, object]:
        raise AssertionError("the host inventory must not be built for a member")

    monkeypatch.setattr(router._system, "_features_payload", unreachable)
    request = _read_request(
        "/api/settings/nanobot-features",
        actor_user_id=actor_user_id,
        system_admin=system_admin,
    )

    response = await router.dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "features": [],
        "enabled_count": 0,
        "restricted": True,
    }


@pytest.mark.asyncio
async def test_nanobot_features_returns_the_host_inventory_to_an_administrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blocked read is real: an administrator still receives the full projection."""
    router = _router()
    inventory = {
        "features": [
            {
                "name": "weixin",
                "extension_id": "ext:channel_package:weixin",
                "extension_revision": "weixin-r3",
                "extension_lifecycle": "failed",
                "runtime_error": "Channel runtime failed. Check gateway logs.",
            }
        ],
        "enabled_count": 1,
    }
    monkeypatch.setattr(router._system, "_features_payload", lambda *_args: inventory)
    request = _read_request(
        "/api/settings/nanobot-features",
        actor_user_id="operator",
        system_admin=True,
    )

    response = await router.dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["features"][0]["extension_revision"] == "weixin-r3"
    assert "restricted" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/settings/channels/validate",
        "/api/settings/channels/configure",
        "/api/settings/nanobot-features/enable",
        "/api/settings/nanobot-features/disable",
    ],
)
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin"),
    [("ordinary-member", False), (None, True), ("   ", True)],
)
async def test_channel_control_routes_refuse_members_without_relying_on_ws_http(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    actor_user_id: str | None,
    system_admin: bool,
) -> None:
    """ws_http gates these first; a second caller of dispatch must not be able to skip it."""
    validated: list[object] = []
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.validate_channel_config",
        lambda name, values, *, instance_id=None: validated.append(name) or {},
    )
    request = _mutation_request(
        path,
        {
            "name": "weixin",
            "instance_id": "tenant-b",
            "extension_id": "ext:channel_package:weixin/channel:tenant-b",
            "expected_revision": "weixin-r3",
            "values": {"token": "attacker-supplied"},
        },
        actor_user_id=actor_user_id,
        system_admin=system_admin,
    )

    response = await _router().dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 403
    assert json.loads(response.body) == {
        "error": "System administrator access is required"
    }
    assert validated == []


def _pending_pairing() -> list[dict[str, object]]:
    return [
        {
            "code": "ABCD-EFGH",
            "channel": "weixin.tenant-b",
            "sender_id": "wxid_attacker",
            "created_at": 1_000.0,
            "expires_at": 1_600.0,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin", "status"),
    [("ordinary-member", False, 403), (None, True, 403), ("operator", True, 200)],
)
async def test_pairing_list_discloses_codes_only_to_server_derived_admins(
    monkeypatch: pytest.MonkeyPatch,
    actor_user_id: str | None,
    system_admin: bool,
    status: int,
) -> None:
    """A member who cannot administer the host never learns another tenant's code."""
    reads: list[object] = []

    def list_pending() -> list[dict[str, object]]:
        reads.append(object())
        return _pending_pairing()

    monkeypatch.setattr("nanobot.webui.settings_routes.list_pending", list_pending)
    request = _read_request(
        "/api/settings/pairing",
        actor_user_id=actor_user_id,
        system_admin=system_admin,
    )

    response = await _router().dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == status
    if status == 403:
        assert reads == []
        assert b"ABCD-EFGH" not in response.body
        assert b"wxid_attacker" not in response.body
    else:
        listed = json.loads(response.body)["requests"][0]
        assert listed["code"] == "ABCD-EFGH"
        assert listed["sender_id"] == "wxid_attacker"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "deny"])
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin"),
    [("ordinary-member", False), (None, True)],
)
async def test_pairing_decisions_reject_non_admins_before_granting_bot_access(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    actor_user_id: str | None,
    system_admin: bool,
) -> None:
    """A stolen code cannot be redeemed without server-derived administrator identity."""
    approved: list[str] = []
    denied: list[str] = []
    monkeypatch.setattr("nanobot.webui.settings_routes.list_pending", _pending_pairing)
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.approve_code",
        lambda code: approved.append(code) or ("weixin.tenant-b", "wxid_attacker"),
    )
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.deny_code",
        lambda code: denied.append(code) or True,
    )
    path = f"/api/settings/pairing/{action}"
    request = _mutation_request(
        path,
        {
            "code": "ABCD-EFGH",
            "actor_user_id": "client-controlled-actor",
            "system_admin": True,
        },
        actor_user_id=actor_user_id,
        system_admin=system_admin,
    )

    response = await _router().dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 403
    assert approved == []
    assert denied == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "deny"])
async def test_pairing_decisions_still_run_for_server_derived_admins(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    redeemed: list[str] = []
    monkeypatch.setattr("nanobot.webui.settings_routes.list_pending", list)
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.approve_code",
        lambda code: redeemed.append(code) or ("weixin.tenant-b", "wxid_attacker"),
    )
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.deny_code",
        lambda code: redeemed.append(code) or True,
    )
    path = f"/api/settings/pairing/{action}"
    request = _mutation_request(
        path,
        {"code": "ABCD-EFGH"},
        actor_user_id="operator",
        system_admin=True,
    )

    response = await _router().dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["last_action"]["action"] == action
    assert redeemed == ["ABCD-EFGH"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "function_name", "payload"),
    [
        (
            "/api/settings/web-search/update",
            "update_web_search_settings",
            {"provider": "tavily", "api_key": "attacker-supplied-key"},
        ),
        (
            "/api/settings/network-safety/update",
            "update_network_safety_settings",
            {"webui_default_access_mode": "full"},
        ),
        (
            "/api/settings/image-generation/update",
            "update_image_generation_settings",
            {"enabled": True, "provider": "openrouter"},
        ),
        (
            "/api/settings/transcription/update",
            "update_transcription_settings",
            {"provider": "openrouter", "model": "openai/whisper-large-v3"},
        ),
    ],
)
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin"),
    [("ordinary-member", False), (None, True)],
)
async def test_capability_updates_reject_non_admins_before_writing_host_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
    function_name: str,
    payload: dict[str, object],
    actor_user_id: str | None,
    system_admin: bool,
) -> None:
    """Capability settings are host-wide, so a member cannot persist them."""
    config_path = tmp_path / "config.json"
    applied: list[object] = []

    def mutate(query, *, config_path=None):
        applied.append(query)
        return {}

    monkeypatch.setattr(f"nanobot.webui.settings_routes.{function_name}", mutate)
    request = _mutation_request(
        path,
        payload | {"actor_user_id": "client-controlled-actor", "system_admin": True},
        actor_user_id=actor_user_id,
        system_admin=system_admin,
    )

    response = await _router(config_path=config_path).dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 403
    assert applied == []
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_web_search_update_persists_the_credential_only_for_admins(
    tmp_path: Path,
) -> None:
    """The blocked chain is real: the same payload does persist for an administrator."""
    config_path = tmp_path / "config.json"
    path = "/api/settings/web-search/update"
    payload = {"provider": "tavily", "api_key": "operator-supplied-key"}

    blocked = await _router(config_path=config_path).dispatch(
        None,
        _mutation_request(path, payload, actor_user_id="member", system_admin=False),
        path,
    )
    assert blocked is not None
    assert blocked.status_code == 403
    assert not config_path.exists()

    allowed = await _router(config_path=config_path).dispatch(
        None,
        _mutation_request(path, payload, actor_user_id="operator", system_admin=True),
        path,
    )

    assert allowed is not None
    assert allowed.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tools"]["web"]["search"]["provider"] == "tavily"
    assert saved["tools"]["web"]["search"]["apiKey"] == "operator-supplied-key"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/settings/cli-apps/install",
        "/api/settings/cli-apps/update",
        "/api/settings/cli-apps/uninstall",
        "/api/settings/cli-apps/test",
    ],
)
async def test_cli_app_actions_reject_non_admins_before_touching_the_host(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """CLI App actions install host packages, so members must not reach the manager."""
    invoked: list[str] = []
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.cli_apps_action",
        lambda action, query, *, config_path=None: invoked.append(action) or {},
    )
    request = _mutation_request(
        path,
        {"name": "gimp"},
        actor_user_id="ordinary-member",
        system_admin=False,
    )

    response = await _router().dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 403
    assert invoked == []


@pytest.mark.asyncio
async def test_usage_query_runs_off_the_event_loop(monkeypatch) -> None:
    calling_thread = threading.get_ident()
    worker_threads: list[int] = []

    def usage_payload(**_kwargs):
        worker_threads.append(threading.get_ident())
        return {"days": []}

    monkeypatch.setattr("nanobot.webui.settings_routes.settings_usage_payload", usage_payload)
    request = SimpleNamespace(path="/api/settings/usage", headers=Headers())

    response = await _router().dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 200
    assert worker_threads and worker_threads[0] != calling_thread


@pytest.mark.asyncio
async def test_full_settings_query_runs_off_the_event_loop(monkeypatch) -> None:
    calling_thread = threading.get_ident()
    worker_threads: list[int] = []
    router = _router()

    def settings_response():
        worker_threads.append(threading.get_ident())
        return http_json_response({"ok": True})

    monkeypatch.setattr(router, "_handle_settings", settings_response)
    request = SimpleNamespace(path="/api/settings", headers=Headers())

    response = await router.dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 200
    assert worker_threads and worker_threads[0] != calling_thread


@pytest.mark.asyncio
async def test_mcp_reload_callback_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def reload_mcp() -> dict[str, object]:
        started.set()
        await asyncio.Event().wait()
        return {"ok": True}

    monkeypatch.setattr(
        "nanobot.webui.settings_routes._MCP_RELOAD_TIMEOUT_SECONDS",
        0.01,
    )
    router = _router(mcp_reload=reload_mcp)

    result = await router._reload_mcp_runtime()

    assert started.is_set()
    assert result == {
        "ok": False,
        "message": "MCP hot reload timed out. Restart nanobot to pick up changes.",
        "requires_restart": True,
    }


@pytest.mark.asyncio
async def test_mcp_reload_exception_response_does_not_expose_runtime_details() -> None:
    async def reload_mcp() -> dict[str, object]:
        raise RuntimeError("/private/runtime/reload-secret")

    result = await _router(mcp_reload=reload_mcp)._reload_mcp_runtime()

    assert result["ok"] is False
    assert result["requires_restart"] is True
    assert "/private/runtime/reload-secret" not in str(result)
    assert "reload-secret" not in str(result)


@pytest.mark.asyncio
async def test_mcp_oauth_start_uses_gateway_callback_and_requires_api_auth(monkeypatch) -> None:
    config = SimpleNamespace(
        type="streamableHttp",
        auth="oauth",
        url="https://app.xmind.com/api/mcp",
    )
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.ensure_mcp_oauth_server",
        lambda _query, *, config_path=None: ("xmind", config),
    )
    router = _router()
    start = AsyncMock(return_value={
        "status": "authorization_required",
        "flow_id": "flow-123",
        "name": "xmind",
        "authorization_url": "https://xmind.example/authorize?state=state-123",
    })
    router._mcp_oauth = SimpleNamespace(start=start)
    request = _mutation_request(
        "/api/settings/mcp-oauth/start",
        {"name": "xmind"},
        actor_user_id="operator",
        system_admin=True,
    )

    response = await router.dispatch(None, request, "/api/settings/mcp-oauth/start")

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["flow_id"] == "flow-123"
    start.assert_awaited_once_with(
        "xmind",
        config,
        "https://gateway.example/auth/mcp/callback",
        reload_mcp=ANY,
        reset_credentials=False,
        actor_user_id="operator",
    )

    denied = _router(authorized=False)
    denied_response = await denied.dispatch(None, request, "/api/settings/mcp-oauth/start")
    assert denied_response is not None
    assert denied_response.status_code == 401

    failed = _router()
    failed._mcp_oauth = SimpleNamespace(
        start=AsyncMock(side_effect=RuntimeError("upstream secret response"))
    )
    failed_response = await failed.dispatch(None, request, "/api/settings/mcp-oauth/start")
    assert failed_response is not None
    assert failed_response.status_code == 500
    assert json.loads(failed_response.body) == {"error": "MCP OAuth start failed"}
    assert b"upstream secret response" not in failed_response.body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin"),
    [("member", False), (None, True)],
)
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/settings/mcp-oauth/start", {"name": "xmind"}),
        ("/api/settings/mcp-oauth/status", {"flow_id": "missing"}),
        (
            "/api/settings/mcp-oauth/complete",
            {
                "flow_id": "missing",
                "callback_url": "http://127.0.0.1:8765/auth/mcp/callback?code=secret&state=state",
            },
        ),
        ("/api/settings/mcp-oauth/cancel", {"flow_id": "missing"}),
    ],
)
async def test_mcp_oauth_actions_require_server_derived_admin_and_actor(
    tmp_path: Path,
    actor_user_id: str | None,
    system_admin: bool,
    path: str,
    payload: dict[str, object],
) -> None:
    config_path = tmp_path / "config.json"
    request = _mutation_request(
        path,
        payload | {"actor_user_id": "client-actor", "system_admin": True},
        actor_user_id=actor_user_id,
        system_admin=system_admin,
    )

    response = await _router(config_path=config_path).dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 403
    assert not config_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/settings/mcp-oauth/start", {"name": "xmind"}),
        ("/api/settings/mcp-oauth/status", {"flow_id": "missing"}),
        (
            "/api/settings/mcp-oauth/complete",
            {
                "flow_id": "missing",
                "callback_url": "http://127.0.0.1:8765/auth/mcp/callback?code=secret&state=state",
            },
        ),
        ("/api/settings/mcp-oauth/cancel", {"flow_id": "missing"}),
    ],
)
async def test_unauthorized_mcp_oauth_actions_have_no_config_or_flow_side_effects(
    tmp_path: Path,
    path: str,
    payload: dict[str, object],
) -> None:
    config_path = tmp_path / "config.json"
    request = _mutation_request(
        path,
        payload,
        actor_user_id="operator",
        system_admin=True,
    )

    response = await _router(authorized=False, config_path=config_path).dispatch(
        None,
        request,
        path,
    )

    assert response is not None
    assert response.status_code == 401
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_mcp_oauth_config_loader_failures_are_safe_for_administrators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(
        "nanobot.webui.mcp_presets_api.load_config",
        lambda _path: (_ for _ in ()).throw(
            RuntimeError("/private/config/oauth-secret")
        ),
    )
    request = _mutation_request(
        "/api/settings/mcp-oauth/start",
        {"name": "xmind"},
        actor_user_id="operator",
        system_admin=True,
    )

    response = await _router(config_path=config_path).dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 500
    assert b"/private/config/oauth-secret" not in response.body
    assert b"oauth-secret" not in response.body


@pytest.mark.asyncio
async def test_mcp_oauth_callback_is_state_authenticated_and_returns_close_page() -> None:
    router = _router(authorized=False)
    submit = MagicMock(return_value="xmind")
    router._mcp_oauth = SimpleNamespace(submit_callback=submit)
    request = SimpleNamespace(
        path="/auth/mcp/callback?code=oauth-code&state=state-123",
        headers=Headers(),
    )

    response = await router.dispatch(None, request, "/auth/mcp/callback")

    assert response is not None
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert b"window.close" in response.body
    assert b"Authorization received" in response.body
    assert b"oauth-code" not in response.body
    submit.assert_called_once_with(state="state-123", code="oauth-code", error=None)


@pytest.mark.asyncio
async def test_mcp_oauth_manual_completion_reads_websocket_payload() -> None:
    callback_url = (
        "http://127.0.0.1:8765/auth/mcp/callback?code=oauth-code&state=state-123"
    )
    router = _router()
    submit = MagicMock(
        return_value={
            "flow_id": "flow-123",
            "name": "linear",
            "status": "connecting",
            "expires_in": 299,
            "completion_input": "callback_url",
        }
    )
    router._mcp_oauth = SimpleNamespace(submit_callback_url=submit)
    request = _mutation_request(
        "/api/settings/mcp-oauth/complete",
        {"flow_id": "flow-123", "callback_url": callback_url},
        actor_user_id="operator",
        system_admin=True,
    )

    response = await router.dispatch(None, request, "/api/settings/mcp-oauth/complete")

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "connecting"
    assert b"oauth-code" not in response.body
    submit.assert_called_once_with(
        flow_id="flow-123",
        callback_url=callback_url,
        actor_user_id="operator",
    )

    denied = _router(authorized=False)
    denied_response = await denied.dispatch(
        None,
        request,
        "/api/settings/mcp-oauth/complete",
    )
    assert denied_response is not None
    assert denied_response.status_code == 401


@pytest.mark.parametrize(
    ("provider", "authorization_response"),
    [
        ("xai_grok", "secret"),
        (
            "openai_codex",
            "http://localhost:1455/auth/callback?code=secret&state=test",
        ),
    ],
)
@pytest.mark.asyncio
async def test_oauth_completion_reads_websocket_payload(
    monkeypatch,
    provider: str,
    authorization_response: str,
) -> None:
    captured: dict[str, object] = {}

    def complete(
        query,
        authorization_response=None,
        *,
        oauth_flows=None,
        config_path=None,
    ):
        captured.update(query=query, authorization_response=authorization_response)
        return {
            "status": "pending",
            "provider": provider,
            "flow_id": "flow-123",
        }

    monkeypatch.setattr("nanobot.webui.settings_routes.complete_oauth_provider", complete)
    router = _router()
    request = _mutation_request(
        "/api/settings/provider/oauth-login/complete",
        {
            "provider": provider,
            "flow_id": "flow-123",
            "authorization_response": authorization_response,
        },
        actor_user_id="operator",
        system_admin=True,
    )

    response = await router.dispatch(
        None,
        request,
        "/api/settings/provider/oauth-login/complete",
    )

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "status": "pending",
        "provider": provider,
        "flow_id": "flow-123",
    }
    assert captured == {
        "query": {"provider": [provider], "flow_id": ["flow-123"]},
        "authorization_response": authorization_response,
    }
    assert request.path == "/api/settings/provider/oauth-login/complete"
    assert not request.headers


@pytest.mark.parametrize(
    ("route_path", "function_name", "payload", "expected_query"),
    [
        (
            "/api/settings/update",
            "update_agent_settings",
            {"model_preset": "Codex"},
            {"model_preset": ["Codex"]},
        ),
        (
            "/api/settings/model-configurations/create",
            "create_model_configuration",
            {"name": "Codex", "model": "openai-codex/gpt-5.6"},
            {"name": ["Codex"], "model": ["openai-codex/gpt-5.6"]},
        ),
        (
            "/api/settings/model-configurations/delete",
            "delete_model_configuration",
            {"name": "spare"},
            {"name": ["spare"]},
        ),
        (
            "/api/settings/model-configurations/migrate",
            "migrate_model_configurations",
            {},
            {},
        ),
        (
            "/api/settings/model-call-order/update",
            "update_model_call_order",
            {"order": ["backup"]},
            {"order": ['["backup"]']},
        ),
        (
            "/api/settings/provider/create",
            "create_provider_settings",
            {"name": "team", "api_base": "https://llm.example/v1"},
            {"name": ["team"], "api_base": ["https://llm.example/v1"]},
        ),
        (
            "/api/settings/provider/update",
            "update_provider_settings",
            {"provider": "team", "api_base": "https://llm.example/v2"},
            {"provider": ["team"], "api_base": ["https://llm.example/v2"]},
        ),
    ],
)
@pytest.mark.asyncio
async def test_runtime_config_mutation_routes_refresh_live_runtime(
    monkeypatch,
    route_path: str,
    function_name: str,
    payload: dict[str, object],
    expected_query: dict[str, list[str]],
) -> None:
    captured: dict[str, object] = {}
    refresh_runtime_config = MagicMock()

    def mutate(query, *, config_path=None):
        captured["query"] = query
        return {"routed": function_name}

    monkeypatch.setattr(f"nanobot.webui.settings_routes.{function_name}", mutate)
    request = _mutation_request(
        route_path, payload, actor_user_id="operator", system_admin=True
    )

    response = await _router(
        refresh_runtime_config=refresh_runtime_config,
    ).dispatch(None, request, route_path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["routed"] == function_name
    assert captured["query"] == expected_query
    refresh_runtime_config.assert_called_once_with()


@pytest.mark.asyncio
async def test_model_update_route_forwards_session_rename_dependency(monkeypatch) -> None:
    rename_model_preset = MagicMock(return_value=2)
    refresh_runtime_config = MagicMock()
    captured: dict[str, object] = {}

    def update(query, *, config_path=None, rename_model_preset=None):
        captured.update(query=query, rename_model_preset=rename_model_preset)
        return {"updated": True}

    monkeypatch.setattr("nanobot.webui.settings_routes.update_model_configuration", update)
    path = "/api/settings/model-configurations/update"
    request = _mutation_request(
        path,
        {"name": "openai", "new_name": "Codex"},
        actor_user_id="operator",
        system_admin=True,
    )

    response = await _router(
        rename_model_preset=rename_model_preset,
        refresh_runtime_config=refresh_runtime_config,
    ).dispatch(
        None,
        request,
        path,
    )

    assert response is not None
    assert response.status_code == 200
    assert captured == {
        "query": {"name": ["openai"], "new_name": ["Codex"]},
        "rename_model_preset": rename_model_preset,
    }
    refresh_runtime_config.assert_called_once_with()


@pytest.mark.asyncio
async def test_settings_get_mutation_route_is_method_not_allowed() -> None:
    path = "/api/settings/provider/update"
    request = SimpleNamespace(
        path=f"{path}?provider=openrouter&api_key=must-not-run",
        headers=Headers(),
    )

    response = await _router().dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 405
    assert json.loads(response.body) == {
        "error": "WebUI mutations require an authenticated WebSocket"
    }


@pytest.mark.parametrize(
    ("update_info", "expected"),
    [
        (None, {"updateAvailable": None}),
        (
            {
                "currentVersion": "1.2.0",
                "latestVersion": "1.3.0",
                "pypiUrl": "https://pypi.org/project/nanobot-ai/",
            },
            {
                "updateAvailable": {
                    "currentVersion": "1.2.0",
                    "latestVersion": "1.3.0",
                    "pypiUrl": "https://pypi.org/project/nanobot-ai/",
                }
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_version_check_route_returns_stable_payload(
    monkeypatch: pytest.MonkeyPatch,
    update_info: dict[str, str] | None,
    expected: dict[str, object],
) -> None:
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.check_for_update",
        lambda: update_info,
    )
    request = SimpleNamespace(path="/api/settings/version-check", headers=Headers())

    response = await _router().dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == expected


@pytest.mark.asyncio
async def test_version_check_route_enforces_auth_and_bounds_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = MagicMock(side_effect=RuntimeError("upstream secret body"))
    monkeypatch.setattr("nanobot.webui.settings_routes.check_for_update", check)
    request = SimpleNamespace(path="/api/settings/version-check", headers=Headers())

    unauthorized = await _router(authorized=False).dispatch(None, request, request.path)
    assert unauthorized is not None
    assert unauthorized.status_code == 401
    check.assert_not_called()

    failed = await _router().dispatch(None, request, request.path)
    assert failed is not None
    assert failed.status_code == 500
    assert json.loads(failed.body) == {"error": "version check failed"}
    assert "upstream secret body" not in failed.body.decode()
