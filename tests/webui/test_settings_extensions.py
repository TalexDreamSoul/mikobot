"""Extensions control-plane transport contracts.

Every test here holds one line: the host inventory and its lifecycle actions are
reachable only by a proven host administrator, the payload carries the trust facts the
registry computed rather than a restated assumption, and each refusal happens before
any adapter callback can run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.datastructures import Headers

from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionConfigurationTarget,
    ExtensionDiagnostic,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)
from nanobot.extensions.registry import ExtensionRegistry
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.settings_routes import WebUISettingsRouter
from nanobot.webui.settings_services import WebUISettingsServices

_SNAPSHOT_PATH = "/api/settings/extensions"
_ACTION_PATH = "/api/settings/extensions/action"

_PLUGIN_ID = extension_package_id(ExtensionSource.AGENT_PLUGIN, "desktop")
_PLUGIN_SKILL_ID = extension_component_id(
    _PLUGIN_ID, ExtensionComponentKind.SKILL, "desktop-notes"
)
_SKILL_ID = extension_package_id(ExtensionSource.WORKSPACE, "meeting-notes")
_SKILL_COMPONENT_ID = extension_component_id(
    _SKILL_ID, ExtensionComponentKind.SKILL, "meeting-notes"
)


def _executable_plugin(
    *,
    lifecycle: ExtensionLifecycle = ExtensionLifecycle.DISABLED,
    revision: str = "plugin-r1",
) -> ExtensionPackageDescriptor:
    """An external, unisolated package: the exact row AC2 and AC4 are about."""
    return ExtensionPackageDescriptor(
        id=_PLUGIN_ID,
        name="desktop",
        display_name="Desktop Automation",
        description="Controls the desktop session.",
        source=ExtensionSource.AGENT_PLUGIN,
        trust=ExtensionTrust.OPERATOR_TRUSTED,
        execution=ExtensionExecution.CHILD_PROCESS,
        lifecycle=lifecycle,
        version="1.4.0",
        revision=revision,
        isolated=False,
        permissions=("filesystem.write", "network.outbound"),
        permissions_enforced=False,
        actions=frozenset({ExtensionAction.ENABLE, ExtensionAction.DISABLE}),
        configuration=ExtensionConfigurationTarget(section="apps", item="mcp"),
        components=(
            ExtensionComponentDescriptor(
                id=_PLUGIN_SKILL_ID,
                package_id=_PLUGIN_ID,
                kind=ExtensionComponentKind.SKILL,
                name="desktop-notes",
                display_name="Desktop Notes",
                capabilities=("notes.read",),
                execution=ExtensionExecution.DATA,
                lifecycle=lifecycle,
                revision="component-r1",
                actions=frozenset({ExtensionAction.INSPECT}),
                configuration=ExtensionConfigurationTarget(section="skills"),
            ),
        ),
    )


def _data_only_skill() -> ExtensionPackageDescriptor:
    """A data-only package, which must never be labelled executable."""
    return ExtensionPackageDescriptor(
        id=_SKILL_ID,
        name="meeting-notes",
        display_name="Meeting Notes",
        source=ExtensionSource.WORKSPACE,
        trust=ExtensionTrust.WORKSPACE_CONTENT,
        execution=ExtensionExecution.DATA,
        lifecycle=ExtensionLifecycle.ENABLED,
        revision="skill-r1",
        components=(
            ExtensionComponentDescriptor(
                id=_SKILL_COMPONENT_ID,
                package_id=_SKILL_ID,
                kind=ExtensionComponentKind.SKILL,
                name="meeting-notes",
                display_name="Meeting Notes",
                execution=ExtensionExecution.DATA,
                lifecycle=ExtensionLifecycle.ENABLED,
            ),
        ),
    )


class _RecordingAdapter:
    """An adapter whose dispatch count is externally observable."""

    def __init__(
        self,
        name: str,
        *packages: ExtensionPackageDescriptor,
        diagnostics: tuple[ExtensionDiagnostic, ...] = (),
    ) -> None:
        self._name = name
        self.packages = packages
        self.diagnostics = diagnostics
        self.calls: list[ExtensionActionRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def snapshot(self) -> ExtensionAdapterSnapshot:
        return ExtensionAdapterSnapshot(
            adapter_name=self._name,
            packages=self.packages,
            diagnostics=self.diagnostics,
        )

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        self.calls.append(request)
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=request.target_id.split("/", maxsplit=1)[0],
            target_id=request.target_id,
            lifecycle=ExtensionLifecycle.ENABLED,
            message="Applied.",
        )


class _BrokenAdapter:
    """An adapter that cannot report, so unrelated rows must survive it."""

    name = "broken-family"

    def snapshot(self) -> ExtensionAdapterSnapshot:
        raise RuntimeError(
            "load failed for /Users/operator/.nanobot/secret-plugin.py "
            f"and token sk-{'z' * 4000}"
        )

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        raise AssertionError("a broken adapter must not be dispatched")


def _router(
    *,
    config_path: Path,
    registry: ExtensionRegistry | None,
) -> WebUISettingsRouter:
    return WebUISettingsRouter(
        settings=WebUISettingsServices.create(
            config_path,
            extension_registry=registry,
        ),
        bus=SimpleNamespace(),
        logger=SimpleNamespace(exception=lambda *_args: None),
        check_api_token=lambda _request: True,
        parse_query=lambda path: parse_qs(urlsplit(path).query),
        json_response=http_json_response,
        error_response=lambda status, message: http_json_response(
            {"error": message},
            status=status,
        ),
        runtime_surface="browser",
        runtime_capabilities={},
    )


def _registry(*adapters: object) -> ExtensionRegistry:
    registry = ExtensionRegistry()
    for adapter in adapters:
        registry.register(adapter)  # type: ignore[arg-type]
    return registry


def _request(
    path: str,
    payload: dict[str, object] | None = None,
    *,
    actor_user_id: str | None = "operator",
    system_admin: bool = True,
    host_admin: bool | None = None,
) -> SimpleNamespace:
    request = SimpleNamespace(path=path, headers=Headers())
    if payload is not None:
        request._nanobot_webui_mutation_request = True
        request._nanobot_webui_mutation_payload = payload
    request._nanobot_settings_actor_user_id = actor_user_id
    request._nanobot_settings_system_admin = system_admin
    request._nanobot_settings_host_admin = (
        system_admin if host_admin is None else host_admin
    )
    return request


async def _dispatch(
    router: WebUISettingsRouter,
    request: SimpleNamespace,
) -> tuple[int, dict[str, Any]]:
    response = await router.dispatch(None, request, request.path)
    assert response is not None
    return response.status_code, json.loads(response.body)


@pytest.fixture
def plugin_adapter() -> _RecordingAdapter:
    return _RecordingAdapter("agent-plugins", _executable_plugin())


@pytest.fixture
def skill_adapter() -> _RecordingAdapter:
    return _RecordingAdapter("effective-skills", _data_only_skill())


@pytest.fixture
def admin_router(
    tmp_path: Path,
    plugin_adapter: _RecordingAdapter,
    skill_adapter: _RecordingAdapter,
) -> WebUISettingsRouter:
    return _router(
        config_path=tmp_path / "config.json",
        registry=_registry(plugin_adapter, skill_adapter),
    )


@pytest.mark.asyncio
async def test_snapshot_returns_every_package_in_registry_order(
    admin_router: WebUISettingsRouter,
) -> None:
    """AC1: one administrator read enumerates the whole composed registry."""
    status, payload = await _dispatch(admin_router, _request(_SNAPSHOT_PATH))

    assert status == 200
    assert payload["available"] is True
    assert [package["id"] for package in payload["packages"]] == sorted(
        [_PLUGIN_ID, _SKILL_ID]
    )
    plugin = next(row for row in payload["packages"] if row["id"] == _PLUGIN_ID)
    assert [component["id"] for component in plugin["components"]] == [_PLUGIN_SKILL_ID]
    assert plugin["components"][0]["package_id"] == _PLUGIN_ID


@pytest.mark.asyncio
async def test_snapshot_carries_the_trust_facts_the_ui_has_to_render(
    admin_router: WebUISettingsRouter,
) -> None:
    """AC2: `isolated` and `permissions_enforced` reach the client as data."""
    _status, payload = await _dispatch(admin_router, _request(_SNAPSHOT_PATH))
    plugin = next(row for row in payload["packages"] if row["id"] == _PLUGIN_ID)

    assert plugin["isolated"] is False
    assert plugin["permissions_enforced"] is False
    assert plugin["permissions"] == ["filesystem.write", "network.outbound"]
    assert plugin["trust"] == "operator_trusted"
    assert plugin["execution"] == "child_process"
    assert plugin["lifecycle"] == "disabled"
    assert plugin["revision"] == "plugin-r1"
    assert plugin["version"] == "1.4.0"
    assert plugin["actions"] == ["disable", "enable"]
    assert plugin["risk_acknowledgement_required"] is True
    assert plugin["configuration"] == {"section": "apps", "item": "mcp"}


@pytest.mark.asyncio
async def test_snapshot_does_not_label_a_data_only_package_executable(
    admin_router: WebUISettingsRouter,
) -> None:
    """A workspace Skill is content, so it carries no executable-risk demand."""
    _status, payload = await _dispatch(admin_router, _request(_SNAPSHOT_PATH))
    skill = next(row for row in payload["packages"] if row["id"] == _SKILL_ID)

    assert skill["execution"] == "data"
    assert skill["isolated"] is None
    assert skill["risk_acknowledgement_required"] is False
    assert skill["actions"] == []


@pytest.mark.asyncio
async def test_snapshot_reports_an_absent_registry_instead_of_an_empty_host(
    tmp_path: Path,
) -> None:
    """R1: no composed registry is a distinguishable state, not zero packages."""
    router = _router(config_path=tmp_path / "config.json", registry=None)

    status, payload = await _dispatch(router, _request(_SNAPSHOT_PATH))

    assert status == 200
    assert payload == {"available": False, "packages": [], "diagnostics": []}


@pytest.mark.asyncio
async def test_a_broken_adapter_is_attributable_and_bounded_but_isolated(
    tmp_path: Path,
    plugin_adapter: _RecordingAdapter,
) -> None:
    """AC6/AC8: a failing family is named and redacted; unrelated rows survive."""
    router = _router(
        config_path=tmp_path / "config.json",
        registry=_registry(plugin_adapter, _BrokenAdapter()),
    )

    _status, payload = await _dispatch(router, _request(_SNAPSHOT_PATH))

    assert [package["id"] for package in payload["packages"]] == [_PLUGIN_ID]
    diagnostic = next(
        row for row in payload["diagnostics"] if row["owner_id"] == "broken-family"
    )
    assert diagnostic["code"] == "adapter_snapshot_failed"
    assert "/Users/operator" not in diagnostic["message"]
    assert "secret-plugin.py" not in diagnostic["message"]
    assert "z" * 200 not in diagnostic["message"]
    assert len(diagnostic["message"]) <= 1_000


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [_SNAPSHOT_PATH, _ACTION_PATH])
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin", "host_admin"),
    [
        ("ordinary-member", False, False),
        (None, True, True),
        ("   ", True, True),
        # `system_admin` is raised for a member acting on a channel instance they own.
        # That authorizes one channel action; it must not open the host inventory.
        ("channel-owning-member", True, False),
    ],
)
async def test_neither_endpoint_serves_a_caller_without_proven_host_admin(
    tmp_path: Path,
    plugin_adapter: _RecordingAdapter,
    path: str,
    actor_user_id: str | None,
    system_admin: bool,
    host_admin: bool,
) -> None:
    """AC7: no inventory, no diagnostics, and no mutation without host administration."""
    router = _router(
        config_path=tmp_path / "config.json",
        registry=_registry(plugin_adapter),
    )
    payload: dict[str, object] | None = (
        None
        if path == _SNAPSHOT_PATH
        else {
            "target_id": _PLUGIN_ID,
            "action": "enable",
            "expected_revision": "plugin-r1",
            "risk_acknowledged": True,
        }
    )

    status, body = await _dispatch(
        router,
        _request(
            path,
            payload,
            actor_user_id=actor_user_id,
            system_admin=system_admin,
            host_admin=host_admin,
        ),
    )

    assert status == 403
    assert "packages" not in body
    assert "diagnostics" not in body
    assert plugin_adapter.calls == []


@pytest.mark.asyncio
async def test_acknowledged_enable_dispatches_once_with_a_fresh_package_row(
    tmp_path: Path,
) -> None:
    """AC4 confirm path: exactly one dispatch, and lifecycle comes back from the server."""
    adapter = _RecordingAdapter("agent-plugins", _executable_plugin())
    router = _router(config_path=tmp_path / "config.json", registry=_registry(adapter))

    status, payload = await _dispatch(
        router,
        _request(
            _ACTION_PATH,
            {
                "target_id": _PLUGIN_ID,
                "action": "enable",
                "expected_revision": "plugin-r1",
                "risk_acknowledged": True,
            },
        ),
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["action"] == "enable"
    assert payload["target_id"] == _PLUGIN_ID
    assert payload["package_id"] == _PLUGIN_ID
    assert payload["lifecycle"] == "enabled"
    assert payload["actor_id"] == "operator"
    assert payload["package"]["id"] == _PLUGIN_ID
    assert payload["package"]["isolated"] is False
    assert len(adapter.calls) == 1
    dispatched = adapter.calls[0]
    assert dispatched.context.actor_id == "operator"
    assert dispatched.context.is_system_admin is True
    assert dispatched.risk_acknowledged is True


@pytest.mark.asyncio
async def test_the_client_cannot_name_its_own_actor_or_admin_authority(
    tmp_path: Path,
) -> None:
    """R2: actor and administrator authority are server-derived, never client-selected."""
    adapter = _RecordingAdapter("agent-plugins", _executable_plugin())
    router = _router(config_path=tmp_path / "config.json", registry=_registry(adapter))

    _status, payload = await _dispatch(
        router,
        _request(
            _ACTION_PATH,
            {
                "target_id": _PLUGIN_ID,
                "action": "enable",
                "expected_revision": "plugin-r1",
                "risk_acknowledged": True,
                "actor_id": "client-controlled-actor",
                "actor_user_id": "client-controlled-actor",
                "is_system_admin": True,
                "package_install_allowed": True,
            },
            actor_user_id="operator",
        ),
    )

    assert payload["actor_id"] == "operator"
    assert adapter.calls[0].context.actor_id == "operator"
    assert adapter.calls[0].context.package_install_allowed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        pytest.param(
            {"action": "enable", "expected_revision": "plugin-r1"},
            400,
            id="missing-target",
        ),
        pytest.param(
            {
                "target_id": _PLUGIN_ID,
                "action": "detonate",
                "expected_revision": "plugin-r1",
            },
            400,
            id="unknown-action",
        ),
        pytest.param(
            {
                "target_id": _PLUGIN_ID,
                "action": "enable",
                "expected_revision": "plugin-r1",
                "risk_acknowledged": True,
                "values": ["not", "a", "mapping"],
            },
            400,
            id="malformed-values",
        ),
        pytest.param(
            {
                "target_id": extension_package_id(ExtensionSource.BUILTIN, "absent"),
                "action": "enable",
                "expected_revision": "plugin-r1",
            },
            404,
            id="unknown-target",
        ),
        pytest.param(
            {
                "target_id": _PLUGIN_ID,
                "action": "reload",
                "expected_revision": "plugin-r1",
            },
            422,
            id="undeclared-action",
        ),
        pytest.param(
            {
                "target_id": _PLUGIN_ID,
                "action": "enable",
                "expected_revision": "plugin-r0",
                "risk_acknowledged": True,
            },
            409,
            id="stale-revision",
        ),
        pytest.param(
            {"target_id": _PLUGIN_ID, "action": "enable", "risk_acknowledged": True},
            409,
            id="missing-revision",
        ),
        pytest.param(
            {
                "target_id": _PLUGIN_ID,
                "action": "enable",
                "expected_revision": "plugin-r1",
            },
            428,
            id="missing-acknowledgement",
        ),
    ],
)
async def test_every_rejection_has_its_own_status_and_reaches_no_adapter(
    tmp_path: Path,
    payload: dict[str, object],
    expected_status: int,
) -> None:
    """AC3/AC5: each refusal is distinguishable and happens before any dispatch."""
    adapter = _RecordingAdapter("agent-plugins", _executable_plugin())
    router = _router(config_path=tmp_path / "config.json", registry=_registry(adapter))

    status, body = await _dispatch(router, _request(_ACTION_PATH, payload))

    assert status == expected_status
    assert body["error"]
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_a_component_action_binds_to_the_component_revision(
    tmp_path: Path,
) -> None:
    """A component is its own target, so the package revision is not interchangeable."""
    adapter = _RecordingAdapter("agent-plugins", _executable_plugin())
    router = _router(config_path=tmp_path / "config.json", registry=_registry(adapter))

    stale_status, _stale = await _dispatch(
        router,
        _request(
            _ACTION_PATH,
            {
                "target_id": _PLUGIN_SKILL_ID,
                "action": "inspect",
                "expected_revision": "plugin-r1",
            },
        ),
    )
    ok_status, payload = await _dispatch(
        router,
        _request(
            _ACTION_PATH,
            {
                "target_id": _PLUGIN_SKILL_ID,
                "action": "inspect",
                "expected_revision": "component-r1",
            },
        ),
    )

    assert stale_status == 409
    assert ok_status == 200
    assert payload["target_id"] == _PLUGIN_SKILL_ID
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_the_action_endpoint_requires_an_authenticated_websocket(
    tmp_path: Path,
) -> None:
    """A plain HTTP GET must never reach a host lifecycle action."""
    adapter = _RecordingAdapter("agent-plugins", _executable_plugin())
    router = _router(config_path=tmp_path / "config.json", registry=_registry(adapter))

    status, _body = await _dispatch(router, _request(_ACTION_PATH))

    assert status == 405
    assert adapter.calls == []


def test_the_settings_route_table_registers_both_extension_paths() -> None:
    """The routes exist in the table the member-authorization canary walks."""
    from nanobot.webui.settings_routes import _EXTENSION_ROUTES

    assert _EXTENSION_ROUTES == {
        _SNAPSHOT_PATH: "extensions-list",
        _ACTION_PATH: "extensions-action",
    }


def test_the_action_path_is_declared_a_websocket_mutation() -> None:
    from nanobot.webui.settings_routes import WebUISettingsRouter as Router
    from nanobot.webui.ws_http import _WEBUI_MUTATION_PATHS

    assert Router.is_mutation_path(_ACTION_PATH) is True
    assert Router.is_mutation_path(_SNAPSHOT_PATH) is False
    assert _WEBUI_MUTATION_PATHS["settings.extension.action"] == _ACTION_PATH


@pytest.mark.asyncio
async def test_no_snapshot_field_carries_a_host_path_or_unbounded_text(
    admin_router: WebUISettingsRouter,
) -> None:
    """AC8: the serialized inventory is the descriptor projection and nothing more."""
    response = await admin_router.dispatch(None, _request(_SNAPSHOT_PATH), _SNAPSHOT_PATH)
    assert response is not None
    body = response.body.decode("utf-8")

    assert "/Users/" not in body
    assert "C:\\" not in body
    assert len(body) < 100_000

    payload: Mapping[str, Any] = json.loads(body)
    package_keys = set(payload["packages"][0])
    assert package_keys == {
        "id",
        "name",
        "display_name",
        "description",
        "source",
        "trust",
        "execution",
        "isolated",
        "lifecycle",
        "version",
        "revision",
        "permissions",
        "permissions_enforced",
        "risk_acknowledgement_required",
        "actions",
        "configuration",
        "diagnostic",
        "components",
    }
    component_keys: set[str] = set()
    for package in payload["packages"]:
        for component in package["components"]:
            component_keys |= set(component)
    assert component_keys == {
        "id",
        "package_id",
        "kind",
        "name",
        "display_name",
        "description",
        "capabilities",
        "execution",
        "lifecycle",
        "revision",
        "actions",
        "configuration",
        "diagnostic",
    }


@pytest.mark.asyncio
async def test_a_snapshot_read_never_mutates_the_gateway_config(
    tmp_path: Path,
    plugin_adapter: _RecordingAdapter,
) -> None:
    """R1: reading the inventory performs no write and no lifecycle side effect."""
    config_path = tmp_path / "config.json"
    router = _router(config_path=config_path, registry=_registry(plugin_adapter))

    await _dispatch(router, _request(_SNAPSHOT_PATH))

    assert not config_path.exists()
    assert plugin_adapter.calls == []


@pytest.mark.asyncio
async def test_an_adapter_failure_during_dispatch_is_bounded_not_raw(
    tmp_path: Path,
) -> None:
    """A raising adapter reports a bounded reason, never a traceback or a path."""

    class _RaisingAdapter:
        name = "agent-plugins"

        def snapshot(self) -> ExtensionAdapterSnapshot:
            return ExtensionAdapterSnapshot(
                adapter_name=self.name,
                packages=(_executable_plugin(),),
            )

        async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
            del request
            raise RuntimeError("/Users/operator/.nanobot/markers/desktop is unwritable")

    router = _router(
        config_path=tmp_path / "config.json",
        registry=_registry(_RaisingAdapter()),
    )

    status, body = await _dispatch(
        router,
        _request(
            _ACTION_PATH,
            {
                "target_id": _PLUGIN_ID,
                "action": "enable",
                "expected_revision": "plugin-r1",
                "risk_acknowledged": True,
            },
        ),
    )

    assert status == 502
    assert "/Users/operator" not in body["error"]
