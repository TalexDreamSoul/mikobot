"""Canonical Settings extension action contracts.

These tests exercise the registry boundary with recording adapters so authority,
opaque target selection, and side-effect cardinality remain observable.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

import pytest

from nanobot.config.loader import save_config
from nanobot.config.schema import Config
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)
from nanobot.extensions.registry import ExtensionRegistry
from nanobot.optional_features import OptionalFeatureError
from nanobot.webui.nanobot_features_api import execute_nanobot_extension_action
from nanobot.webui.settings_capabilities import (
    CapabilitySettingsHandler,
    CapabilitySettingsOperations,
)
from nanobot.webui.settings_contracts import SettingsRequest
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.settings_system import SystemSettingsHandler, SystemSettingsOperations


class _RecordingAdapter:
    """Canonical inventory whose action calls are externally observable."""

    def __init__(self, *packages: ExtensionPackageDescriptor) -> None:
        self.packages = packages
        self.requests: list[ExtensionActionRequest] = []
        self.effects = 0
        self.reject_package_install = False

    @property
    def name(self) -> str:
        return "settings-actions"

    def snapshot(self) -> ExtensionAdapterSnapshot:
        return ExtensionAdapterSnapshot(adapter_name=self.name, packages=self.packages)

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        self.requests.append(request)
        if self.reject_package_install and not request.context.package_install_allowed:
            return ExtensionActionResult(
                ok=False,
                action=request.action,
                package_id=request.target_id.split("/", maxsplit=1)[0],
                target_id=request.target_id,
                lifecycle=ExtensionLifecycle.UNAVAILABLE,
                message="Package installation is not allowed for this request.",
            )
        self.effects += 1
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=request.target_id.split("/", maxsplit=1)[0],
            target_id=request.target_id,
            lifecycle=ExtensionLifecycle.ENABLED,
            message="Applied.",
        )


class _FailingAdapter(_RecordingAdapter):
    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        self.requests.append(request)
        raise RuntimeError("adapter failure at /private/extension-secret")


class _RevisionAdapter(_RecordingAdapter):
    """A configure action changes the exact component revision for follow-up enable."""

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        result = await super().execute(request)
        if request.action is ExtensionAction.CONFIGURE and result.ok:
            package = self.packages[0]
            components = tuple(
                replace(component, revision="relay-office-r2")
                if component.id == request.target_id
                else component
                for component in package.components
            )
            self.packages = (replace(package, components=components),)
        return result


class _PostMutationFailureAdapter(_RecordingAdapter):
    """Records a persisted action whose live follow-up reports a safe failure."""

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        result = await super().execute(request)
        self.packages = (replace(self.packages[0], lifecycle=ExtensionLifecycle.ENABLED),)
        return replace(
            result,
            ok=False,
            lifecycle=ExtensionLifecycle.RESTART_REQUIRED,
            message="Channel action could not be completed.",
        )


class _FailingConnector:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def handle(self, _action: str, _query: dict[str, list[str]]) -> dict[str, object]:
        self.calls += 1
        raise self.error


class _Connector:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, list[str]]]] = []

    async def handle(self, action: str, query: dict[str, list[str]]) -> dict[str, object]:
        self.calls.append((action, query))
        return dict(self.payload)


class _SequencedConnector:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict[str, list[str]]]] = []

    async def handle(self, action: str, query: dict[str, list[str]]) -> dict[str, object]:
        self.calls.append((action, query))
        return dict(self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)])


def _system_operations(
    *,
    plugin: object | None = None,
    channel_pairing_action: object | None = None,
) -> SystemSettingsOperations:
    def unused(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("legacy Settings operation must not be used")

    return SystemSettingsOperations(
        cli_apps_payload=unused,
        cli_apps_action=unused,
        validate_channel_config=unused,
        load_channel_plugin=(lambda _name: plugin) if plugin is not None else unused,
        list_pending=lambda: (),
        approve_code=unused,
        deny_code=unused,
        mcp_presets_action=unused,
        reload_mcp=unused,
        mcp_runtime_status=None,
        check_for_update=unused,
        channel_pairing_action=channel_pairing_action,
    )


def _system_handler(
    tmp_path, registry: ExtensionRegistry,
) -> SystemSettingsHandler:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    settings = WebUISettingsServices.create(
        config_path, extension_registry=registry
    )
    return SystemSettingsHandler(
        settings,
        logger=type("Logger", (), {"exception": staticmethod(lambda *_args: None)})(),
    )


class _ApiRuntime:
    def __init__(
        self,
        adapter: _RecordingAdapter,
        log_path: object,
        events: list[str] | None = None,
    ) -> None:
        self._adapter = adapter
        self._log_path = log_path
        self._events = events
        self.start_calls = 0
        self.stop_calls = 0
        self.effects_when_started: list[int] = []
        self.started_options: list[Any] = []

    def status(self) -> object:
        return type("Status", (), {"running": False, "log_path": self._log_path})()

    def start_background(self, options: Any) -> object:
        self.start_calls += 1
        self.effects_when_started.append(self._adapter.effects)
        self.started_options.append(options)
        if self._events is not None:
            self._events.append("start")
        return type("StartResult", (), {"ok": True, "message": "started"})()

    def stop(self) -> object:
        self.stop_calls += 1
        if self._events is not None:
            self._events.append("stop")
        return type("StopResult", (), {"ok": True, "message": "stopped"})()


def _capability_operations(
    runtime: _ApiRuntime,
    *,
    update_api: Callable[..., dict[str, Any]] | None = None,
) -> CapabilitySettingsOperations:
    async def unused_reload() -> dict[str, object]:
        raise AssertionError("image reload must not be used")

    def unused_update(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {}

    return CapabilitySettingsOperations(
        update_web_search=unused_update,
        update_api=update_api if update_api is not None else unused_update,
        update_image=unused_update,
        update_transcription=unused_update,
        update_network=unused_update,
        api_runtime=lambda: runtime,
        reload_image=unused_reload,
    )


def _channel_package(*, revision: str = "relay-package-r1") -> ExtensionPackageDescriptor:
    package_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, "relay")
    actions = frozenset({
        ExtensionAction.CONFIGURE,
        ExtensionAction.ENABLE,
        ExtensionAction.DISABLE,
        ExtensionAction.INSTALL,
    })
    office = ExtensionComponentDescriptor(
        id=extension_component_id(package_id, ExtensionComponentKind.CHANNEL, "office"),
        package_id=package_id,
        kind=ExtensionComponentKind.CHANNEL,
        name="office",
        display_name="Office relay",
        lifecycle=ExtensionLifecycle.DISABLED,
        revision=revision,
        actions=actions,
    )
    default = replace(office, id=extension_component_id(
        package_id, ExtensionComponentKind.CHANNEL, "default"
    ), name="default", display_name="Default relay")
    return ExtensionPackageDescriptor(
        id=package_id,
        name="relay",
        display_name="Relay",
        source=ExtensionSource.CHANNEL_PACKAGE,
        trust=ExtensionTrust.FIRST_PARTY,
        execution=ExtensionExecution.IN_PROCESS,
        lifecycle=ExtensionLifecycle.DISABLED,
        revision=revision,
        actions=frozenset({ExtensionAction.INSPECT}),
        components=(default, office),
    )


def _optional_package(
    name: str = "api",
    *,
    revision: str = "api-r1",
    trust: ExtensionTrust = ExtensionTrust.FIRST_PARTY,
    actions: frozenset[ExtensionAction] = frozenset({ExtensionAction.INSTALL}),
) -> ExtensionPackageDescriptor:
    return ExtensionPackageDescriptor(
        id=extension_package_id(ExtensionSource.OPTIONAL_FEATURE, name),
        name=name,
        display_name=name.title(),
        source=ExtensionSource.OPTIONAL_FEATURE,
        trust=trust,
        execution=ExtensionExecution.IN_PROCESS,
        lifecycle=ExtensionLifecycle.UNAVAILABLE,
        revision=revision,
        actions=actions,
    )


def _registry(*packages: ExtensionPackageDescriptor) -> tuple[ExtensionRegistry, _RecordingAdapter]:
    registry = ExtensionRegistry()
    adapter = _RecordingAdapter(*packages)
    registry.register(adapter)
    return registry, adapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "name", "instance_id", "package", "expected_revision", "values"),
    [
        (
            ExtensionAction.ENABLE,
            "relay",
            "office",
            _channel_package(),
            "relay-package-r1",
            None,
        ),
        (
            ExtensionAction.DISABLE,
            "relay",
            "office",
            _channel_package(),
            "relay-package-r1",
            None,
        ),
        (
            ExtensionAction.CONFIGURE,
            "relay",
            "office",
            _channel_package(),
            "relay-package-r1",
            {"region": "eu"},
        ),
        (
            ExtensionAction.INSTALL,
            "api",
            None,
            _optional_package(),
            "api-r1",
            None,
        ),
    ],
)
async def test_execute_extension_action_resolves_one_exact_server_target(
    action: ExtensionAction,
    name: str,
    instance_id: str | None,
    package: ExtensionPackageDescriptor,
    expected_revision: str,
    values: Mapping[str, object] | None,
) -> None:
    """Each action dispatches exactly once to the canonical target, never a legacy name."""
    registry, adapter = _registry(package)
    target_id = (
        next(component.id for component in package.components if component.name == instance_id)
        if instance_id is not None
        else package.id
    )

    result = await execute_nanobot_extension_action(
        registry,
        action=action,
        name=name,
        instance_id=instance_id,
        extension_id=target_id,
        expected_revision=expected_revision,
        risk_acknowledged=True,
        actor_id="server-operator",
        is_system_admin=True,
        package_install_allowed=True,
        values=values,
    )

    assert result.ok is True
    assert result.target_id == target_id
    assert adapter.effects == 1
    assert len(adapter.requests) == 1
    [request] = adapter.requests
    assert (request.target_id, request.action, request.expected_revision, dict(request.values)) == (
        target_id,
        action,
        expected_revision,
        {} if values is None else values,
    )
    assert (
        request.context.actor_id,
        request.context.is_system_admin,
        request.context.package_install_allowed,
    ) == ("server-operator", True, True)


@pytest.mark.asyncio
async def test_execute_extension_action_keeps_pairing_completion_scoped_to_the_exact_target() -> None:
    """Only the server-selected paired instance receives the pairing-complete context."""
    package = _channel_package()
    registry, adapter = _registry(package)
    office_id = next(component.id for component in package.components if component.name == "office")

    await execute_nanobot_extension_action(
        registry,
        action=ExtensionAction.ENABLE,
        name="relay",
        instance_id="office",
        extension_id=office_id,
        expected_revision="relay-package-r1",
        risk_acknowledged=True,
        actor_id="paired-operator",
        is_system_admin=True,
        package_install_allowed=True,
        channel_pairing_completed=True,
    )

    assert adapter.effects == 1
    [request] = adapter.requests
    assert request.target_id == office_id
    assert request.context.channel_pairing_completed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "instance_id", "extension_id", "expected_revision", "is_system_admin", "risk_acknowledged", "status"),
    [
        ("relay", "office", "ext:channel_package:relay/channel:office", "relay-package-r1", False, True, 403),
        ("relay", "office", "ext:channel_package:relay/channel:default", "relay-package-r1", True, True, 404),
        ("relay", "missing", "ext:channel_package:relay/channel:missing", "relay-package-r1", True, True, 404),
        ("relay", "office", "ext:channel_package:relay/channel:office", "", True, True, 409),
        ("risk", None, "ext:optional_feature:risk", "stale-revision", True, True, 409),
        ("risk", None, "ext:optional_feature:risk", "risk-r1", True, False, 409),

        ("relay", "office", "ext:channel_package:relay/channel:office", "stale-revision", True, True, 409),

    ],
)
async def test_execute_extension_action_rejects_invalid_authority_before_adapter_effects(
    name: str,
    instance_id: str | None,
    extension_id: str,
    expected_revision: str,
    is_system_admin: bool,
    risk_acknowledged: bool,
    status: int,
) -> None:
    """Invalid authority, identity, revision, or acknowledgement cannot reach an adapter."""
    registry, adapter = _registry(
        _channel_package(),
        _optional_package(
            "risk",
            revision="risk-r1",
            trust=ExtensionTrust.OPERATOR_TRUSTED,
            actions=frozenset({ExtensionAction.ENABLE, ExtensionAction.INSTALL}),
        ),
    )

    with pytest.raises(OptionalFeatureError) as failure:
        await execute_nanobot_extension_action(
            registry,
            action=ExtensionAction.ENABLE,
            name=name,
            instance_id=instance_id,
            extension_id=extension_id,
            expected_revision=expected_revision,
            risk_acknowledged=risk_acknowledged,
            actor_id="operator",
            is_system_admin=is_system_admin,
            package_install_allowed=True,
        )

    assert failure.value.status == status
    assert adapter.effects == 0
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_install_policy_rejects_before_adapter_effects() -> None:
    """A remote-policy denial stops before the registry can dispatch installation."""
    package = _optional_package()
    registry, adapter = _registry(package)
    with pytest.raises(OptionalFeatureError) as failure:
        await execute_nanobot_extension_action(
            registry,
            action=ExtensionAction.INSTALL,
            name="api",
            instance_id=None,
            extension_id=package.id,
            expected_revision="api-r1",
            risk_acknowledged=True,
            actor_id="operator",
            is_system_admin=True,
            package_install_allowed=False,
        )

    assert failure.value.status == 403
    assert adapter.effects == 0
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_execute_extension_action_redacts_adapter_failures() -> None:
    """An adapter exception is reduced to a bounded public error rather than raw diagnostics."""
    package = _optional_package()
    registry = ExtensionRegistry()
    adapter = _FailingAdapter(package)
    registry.register(adapter)

    with pytest.raises(OptionalFeatureError) as failure:
        await execute_nanobot_extension_action(
            registry,
            action=ExtensionAction.INSTALL,
            name="api",
            instance_id=None,
            extension_id=package.id,
            expected_revision="api-r1",
            risk_acknowledged=True,
            actor_id="operator",
            is_system_admin=True,
            package_install_allowed=True,
        )

    assert failure.value.status == 502
    assert "/private/extension-secret" not in failure.value.message
    assert len(adapter.requests) == 1
@pytest.mark.asyncio
async def test_settings_handler_lists_only_the_registry_snapshot_without_legacy_overlay(tmp_path) -> None:
    """The Settings list reads canonical inventory and does not invoke legacy payload/status hooks."""
    package = _optional_package("api")
    registry, adapter = _registry(package)
    result = await _system_handler(tmp_path, registry).handle(
        "features-list", SettingsRequest(query={}), _system_operations()
    )

    assert result.status == 200
    assert result.payload is not None
    assert result.payload["features"] == [
        {
            "name": "api",
            "display_name": "Api",
            "type": "feature",
            "install_supported": True,
            "requires_restart": False,
            "extension_id": package.id,
            "extension_revision": "api-r1",
            "extension_actions": ["install"],
            "extension_lifecycle": "unavailable",
            "extension_trust": "first_party",
            "extension_execution": "in_process",
            "enabled": False,
            "configured": False,
            "ready": False,
            "running": False,
            "installed": False,
            "status": "stopped",
        }
    ]
    assert "last_action" not in result.payload
    assert adapter.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_action", "expected_action", "package", "name", "instance_id"),
    [
        ("features-enable", ExtensionAction.INSTALL, _optional_package(), "api", None),
        ("features-disable", ExtensionAction.DISABLE, _channel_package(), "relay", "office"),
    ],
)
async def test_settings_handler_maps_feature_routes_to_one_canonical_action(
    tmp_path,
    route_action: str,
    expected_action: ExtensionAction,
    package: ExtensionPackageDescriptor,
    name: str,
    instance_id: str | None,
) -> None:
    """Legacy wire routes select canonical install/disable actions without a second hot-action call."""
    registry, adapter = _registry(package)
    target_id = (
        package.id
        if instance_id is None
        else next(component.id for component in package.components if component.name == instance_id)
    )
    query = {
        "name": [name],
        "extension_id": [target_id],
        "expected_revision": ["api-r1" if instance_id is None else "relay-package-r1"],
        "risk_acknowledged": ["true"],
    }
    if instance_id is not None:
        query["instance_id"] = [instance_id]

    result = await _system_handler(tmp_path, registry).handle(
        route_action,
        SettingsRequest(
            query=query,
            actor_user_id="server-operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(),
    )

    assert result.status == 200
    assert result.payload is not None
    assert result.payload["last_action"]["action"] == route_action.removeprefix("features-")
    assert "hot_reload" not in result.payload["last_action"]
    assert adapter.effects == 1
    assert [(request.action, request.target_id) for request in adapter.requests] == [
        (expected_action, target_id)
    ]


@pytest.mark.asyncio
async def test_channel_configure_enables_once_against_the_fresh_post_config_revision(tmp_path) -> None:
    """Configure guards the displayed revision, then enables the newly resolved exact target once."""
    registry = ExtensionRegistry()
    adapter = _RevisionAdapter(_channel_package(revision="relay-office-r1"))
    registry.register(adapter)
    office_id = next(
        component.id for component in adapter.packages[0].components if component.name == "office"
    )

    result = await _system_handler(tmp_path, registry).handle(
        "channel-configure",
        SettingsRequest(
            query={
                "name": ["relay"],
                "instance_id": ["office"],
                "extension_id": [office_id],
                "expected_revision": ["relay-office-r1"],
                "risk_acknowledged": ["true"],
                "enable": ["true"],
            },
            payload={"values": {"region": "eu", "secret": " "}},
            actor_user_id="server-operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(),
    )

    assert result.status == 200
    assert result.payload is not None
    assert result.payload["saved_keys"] == ["region"]
    assert [(request.action, request.target_id, request.expected_revision) for request in adapter.requests] == [
        (ExtensionAction.CONFIGURE, office_id, "relay-office-r1"),
        (ExtensionAction.ENABLE, office_id, "relay-office-r2"),
    ]
    assert adapter.effects == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extension_id", "system_admin", "status"),
    [
        ("ext:channel_package:relay/channel:default", True, 404),
        ("ext:channel_package:relay/channel:office", False, 403),
    ],
)
async def test_channel_configure_rejects_invalid_authority_before_config_effects(
    tmp_path,
    extension_id: str,
    system_admin: bool,
    status: int,
) -> None:
    """Opaque target and administrator gates run before a configuration action reaches its owner."""
    package = _channel_package()
    registry, adapter = _registry(package)

    result = await _system_handler(tmp_path, registry).handle(
        "channel-configure",
        SettingsRequest(
            query={
                "name": ["relay"],
                "instance_id": ["office"],
                "extension_id": [extension_id],
                "expected_revision": ["relay-package-r1"],
            },
            payload={"values": {"region": "eu"}},
            actor_user_id="operator",
            system_admin=system_admin,
            local_browser=True,
        ),
        _system_operations(),
    )

    assert result.status == status
    assert adapter.effects == 0
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_channel_connector_completion_enables_the_resolved_instance_once(tmp_path) -> None:
    """A successful connector completion enables only its returned canonical instance."""
    package = _channel_package()
    registry, adapter = _registry(package)
    office_id = next(component.id for component in package.components if component.name == "office")
    connector = _Connector({"status": "succeeded", "instance_id": "office"})
    plugin = type("Plugin", (), {"load_connector": lambda _self: connector})()

    result = await _system_handler(tmp_path, registry).handle(
        "channel-connect",
        SettingsRequest(
            query={
                "name": ["relay"],
                "instance_id": ["office"],
                "extension_id": [office_id],
                "expected_revision": ["relay-package-r1"],
                "risk_acknowledged": ["true"],
            },
            actor_user_id="connector-operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(plugin=plugin),
        channel_name="relay",
        connect_action="start",
    )

    assert result.status == 200
    assert connector.calls == [("start", {
        "name": ["relay"],
        "instance_id": ["office"],
        "extension_id": [office_id],
        "expected_revision": ["relay-package-r1"],
        "risk_acknowledged": ["true"],
        "_actor_user_id": ["connector-operator"],
    })]
    assert [(request.action, request.target_id) for request in adapter.requests] == [
        (ExtensionAction.ENABLE, office_id)
    ]
    assert adapter.effects == 1


@pytest.mark.asyncio
async def test_pairing_connector_completion_starts_only_the_narrow_pairing_listener(tmp_path) -> None:
    """Pairing-required completion creates one scoped listener and never enables the channel early."""
    package = _channel_package()
    registry, adapter = _registry(package)
    office_id = next(component.id for component in package.components if component.name == "office")
    connector = _Connector({
        "status": "succeeded",
        "instance_id": "office",
        "pairing_required": True,
    })
    plugin = type("Plugin", (), {"load_connector": lambda _self: connector})()
    pairing_calls: list[tuple[str, str]] = []

    def pairing_action(channel_type: str, instance_id: str) -> dict[str, object]:
        pairing_calls.append((channel_type, instance_id))
        return {"ok": True}

    result = await _system_handler(tmp_path, registry).handle(
        "channel-connect",
        SettingsRequest(
            query={
                "name": ["relay"],
                "instance_id": ["office"],
                "extension_id": [office_id],
                "expected_revision": ["relay-package-r1"],
                "risk_acknowledged": ["true"],
            },
            actor_user_id="connector-operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(plugin=plugin, channel_pairing_action=pairing_action),
        channel_name="relay",
        connect_action="poll",
    )

    assert result.status == 200
    assert pairing_calls == [("relay", "office")]
    assert len(connector.calls) == 1
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_connector_rejects_wrong_opaque_target_before_starting_the_connector(tmp_path) -> None:
    """An opaque target mismatch is denied before connector or registry side effects."""
    package = _channel_package()
    registry, adapter = _registry(package)
    connector = _Connector({"status": "succeeded", "instance_id": "office"})
    plugin = type("Plugin", (), {"load_connector": lambda _self: connector})()

    result = await _system_handler(tmp_path, registry).handle(
        "channel-connect",
        SettingsRequest(
            query={
                "name": ["relay"],
                "instance_id": ["office"],
                "extension_id": ["ext:channel_package:relay/channel:default"],
                "expected_revision": ["relay-package-r1"],
            },
            actor_user_id="connector-operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(plugin=plugin),
        channel_name="relay",
        connect_action="start",
    )

    assert result.status == 404
    assert connector.calls == []
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_connector_rejects_non_admin_before_starting_the_connector(tmp_path) -> None:
    """Connector actions require server-derived administration before their external effect."""
    package = _channel_package()
    registry, adapter = _registry(package)
    office_id = next(component.id for component in package.components if component.name == "office")
    connector = _Connector({"status": "succeeded", "instance_id": "office"})
    plugin = type("Plugin", (), {"load_connector": lambda _self: connector})()

    result = await _system_handler(tmp_path, registry).handle(
        "channel-connect",
        SettingsRequest(
            query={
                "name": ["relay"],
                "instance_id": ["office"],
                "extension_id": [office_id],
                "expected_revision": ["relay-package-r1"],
            },
            actor_user_id="member",
            system_admin=False,
            local_browser=True,
        ),
        _system_operations(plugin=plugin),
        channel_name="relay",
        connect_action="start",
    )

    assert result.status == 403
    assert connector.calls == []
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_api_start_updates_config_then_installs_the_exact_api_extension(tmp_path) -> None:
    """API startup serializes supplied settings before its revision-bound install/runtime sequence."""
    from nanobot.webui.settings_api import update_api_settings

    package = _optional_package("api")
    registry, adapter = _registry(package)
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    settings = WebUISettingsServices.create(config_path, extension_registry=registry)
    events: list[str] = []
    runtime = _ApiRuntime(adapter, tmp_path / "api.log", events)

    def update_api(
        query: Mapping[str, list[str]], *, config_path: Any
    ) -> dict[str, Any]:
        events.append("update")
        return update_api_settings(query, config_path=config_path)

    handler = CapabilitySettingsHandler(
        settings,
        logger=type("Logger", (), {"exception": staticmethod(lambda *_args: None)})(),
    )

    result = await handler.handle(
        "api-start",
        SettingsRequest(
            query={
                "extension_id": [package.id],
                "expected_revision": ["api-r1"],
                "risk_acknowledged": ["true"],
                "port": ["9387"],
            },
            actor_user_id="api-operator",
            system_admin=True,
            local_browser=True,
        ),
        _capability_operations(runtime, update_api=update_api),
    )

    assert result.status == 200
    assert [(request.action, request.target_id, request.expected_revision) for request in adapter.requests] == [
        (ExtensionAction.INSTALL, package.id, "api-r1")
    ]
    assert events == ["update", "start"]
    assert runtime.effects_when_started == [1]
    [options] = runtime.started_options
    assert options.port == 9387


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin", "local_browser", "status"),
    [
        (None, True, True, 403),
        ("api-operator", False, True, 403),
        ("api-operator", True, False, 403),
    ],
)
async def test_api_start_failures_prevent_runtime_start(
    tmp_path,
    actor_user_id: str | None,
    system_admin: bool,
    local_browser: bool,
    status: int,
) -> None:
    """Actor, administrator, and install-policy gates all precede API config and runtime effects."""
    package = _optional_package("api")
    registry, adapter = _registry(package)
    settings = WebUISettingsServices.create(
        tmp_path / "config.json", extension_registry=registry
    )
    config = Config()
    config.tools.webui_allow_remote_package_install = False
    save_config(config, tmp_path / "config.json")
    runtime = _ApiRuntime(adapter, tmp_path / "api.log")
    handler = CapabilitySettingsHandler(
        settings,
        logger=type("Logger", (), {"exception": staticmethod(lambda *_args: None)})(),
    )

    result = await handler.handle(
        "api-start",
        SettingsRequest(
            query={
                "extension_id": [package.id],
                "expected_revision": ["api-r1"],
                "risk_acknowledged": ["true"],
            },
            actor_user_id=actor_user_id,
            system_admin=system_admin,
            local_browser=local_browser,
        ),
        _capability_operations(runtime),
    )

    assert result.status == status
    assert runtime.start_calls == 0
    assert adapter.effects == 0


@pytest.mark.asyncio
async def test_enabled_optional_noop_rechecks_the_supplied_revision_before_returning(tmp_path) -> None:
    """An already-enabled optional feature cannot turn a stale mutation request into a success."""
    package = replace(_optional_package(), lifecycle=ExtensionLifecycle.ENABLED)
    registry, adapter = _registry(package)

    result = await _system_handler(tmp_path, registry).handle(
        "features-enable",
        SettingsRequest(
            query={
                "name": ["api"],
                "extension_id": [package.id],
                "expected_revision": ["stale-api-r0"],
            },
            actor_user_id="operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(),
    )

    assert result.status == 409
    assert adapter.effects == 0
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_channel_feature_runtime_fields_use_aggregate_package_lifecycle(tmp_path) -> None:
    """A running named instance makes the channel row live without replacing its default action target."""
    package = _channel_package()
    default, office = package.components
    aggregate = replace(
        package,
        lifecycle=ExtensionLifecycle.ENABLED,
        components=(default, replace(office, lifecycle=ExtensionLifecycle.ENABLED)),
    )
    registry, _adapter = _registry(aggregate)

    result = await _system_handler(tmp_path, registry).handle(
        "features-list", SettingsRequest(query={}), _system_operations()
    )

    assert result.status == 200
    assert result.payload is not None
    [feature] = result.payload["features"]
    assert feature["action_target_id"] == default.id
    assert (
        feature["extension_lifecycle"],
        feature["running"],
        feature["ready"],
        feature["status"],
    ) == ("enabled", True, True, "enabled")


@pytest.mark.asyncio
async def test_false_feature_result_returns_fresh_payload_with_restart_truth(tmp_path) -> None:
    """A runtime failure after persistence remains observable as a safe fresh Settings payload."""
    package = _optional_package()
    registry = ExtensionRegistry()
    adapter = _PostMutationFailureAdapter(package)
    registry.register(adapter)

    result = await _system_handler(tmp_path, registry).handle(
        "features-enable",
        SettingsRequest(
            query={
                "name": ["api"],
                "extension_id": [package.id],
                "expected_revision": ["api-r1"],
                "risk_acknowledged": ["true"],
            },
            actor_user_id="operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(),
    )

    assert result.status == 200
    assert result.payload is not None
    assert result.payload["last_action"] == {
        "ok": False,
        "action": "enable",
        "message": "Channel action could not be completed.",
        "lifecycle": "restart_required",
    }
    assert result.payload["requires_restart"] is True
    [feature] = result.payload["features"]
    assert feature["extension_lifecycle"] == "enabled"
    assert adapter.effects == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("risk_acknowledged", "local_browser"),
    [(False, True), (True, False)],
)
async def test_pairing_install_acknowledgement_and_policy_gate_listener_effects(
    tmp_path,
    risk_acknowledged: bool,
    local_browser: bool,
) -> None:
    """Pairing cannot start its listener when its required package install is not authorized."""
    package = replace(_channel_package(), actions=frozenset({ExtensionAction.INSTALL}))
    registry, adapter = _registry(package)
    office_id = next(component.id for component in package.components if component.name == "office")
    connector = _Connector({
        "status": "succeeded",
        "instance_id": "office",
        "pairing_required": True,
    })
    plugin = type("Plugin", (), {"load_connector": lambda _self: connector})()
    pairing_calls: list[tuple[str, str]] = []
    handler = _system_handler(tmp_path, registry)
    config = Config()
    config.tools.webui_allow_remote_package_install = False
    save_config(config, tmp_path / "config.json")
    query = {
        "name": ["relay"],
        "instance_id": ["office"],
        "extension_id": [office_id],
        "expected_revision": ["relay-package-r1"],
    }
    if risk_acknowledged:
        query["risk_acknowledged"] = ["true"]

    result = await handler.handle(
        "channel-connect",
        SettingsRequest(
            query=query,
            actor_user_id="connector-operator",
            system_admin=True,
            local_browser=local_browser,
        ),
        _system_operations(
            plugin=plugin,
            channel_pairing_action=lambda channel, instance: pairing_calls.append((channel, instance)),
        ),
        channel_name="relay",
        connect_action="poll",
    )

    assert result.status == 200
    assert result.payload is not None
    assert result.payload["pairing_listener_error"] == "pairing listener could not be started"
    assert connector.calls == [("poll", {**query, "_actor_user_id": ["connector-operator"]})]
    assert adapter.effects == 0
    assert pairing_calls == []


@pytest.mark.asyncio
async def test_pairing_install_precedes_one_listener_effect_after_authorization(tmp_path) -> None:
    """An acknowledged pairing completion installs its package once before starting its exact listener."""
    package = replace(_channel_package(), actions=frozenset({ExtensionAction.INSTALL}))
    registry, adapter = _registry(package)
    office_id = next(component.id for component in package.components if component.name == "office")
    connector = _SequencedConnector([
        {"status": "pending", "session_id": "pair-session"},
        {"status": "succeeded", "instance_id": "office", "pairing_required": True},
    ])
    plugin = type("Plugin", (), {"load_connector": lambda _self: connector})()
    handler = _system_handler(tmp_path, registry)
    listener_request_counts: list[int] = []
    operations = _system_operations(
        plugin=plugin,
        channel_pairing_action=lambda _channel, _instance: listener_request_counts.append(len(adapter.requests)),
    )
    query = {
        "name": ["relay"],
        "instance_id": ["office"],
        "extension_id": [office_id],
        "expected_revision": ["relay-package-r1"],
        "session_id": ["pair-session"],
    }

    started = await handler.handle(
        "channel-connect",
        SettingsRequest(
            query={**query, "risk_acknowledged": ["true"]},
            actor_user_id="connector-operator",
            system_admin=True,
            local_browser=True,
        ),
        operations,
        channel_name="relay",
        connect_action="start",
    )
    # The poll deliberately omits risk_acknowledged: only the start acknowledgement may authorize it.
    result = await handler.handle(
        "channel-connect",
        SettingsRequest(
            query=query,
            actor_user_id="connector-operator",
            system_admin=True,
            local_browser=True,
        ),
        operations,
        channel_name="relay",
        connect_action="poll",
    )

    assert started.status == 200
    assert result.status == 200
    assert [(request.action, request.target_id, request.expected_revision) for request in adapter.requests] == [
        (ExtensionAction.INSTALL, package.id, "relay-package-r1")
    ]
    assert adapter.effects == 1
    assert listener_request_counts == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extension_id", "expected_revision", "risk_acknowledged", "status"),
    [
        (None, "api-r1", True, 404),
        ("ext:optional_feature:other", "api-r1", True, 404),
        ("ext:optional_feature:api", "stale-api-r0", True, 409),
        ("ext:optional_feature:api", "api-r1", False, 409),
    ],
)
async def test_api_start_requires_the_echoed_target_revision_and_install_acknowledgement(
    tmp_path,
    extension_id: str | None,
    expected_revision: str,
    risk_acknowledged: bool,
    status: int,
) -> None:
    """Malformed API start approval cannot persist settings, install, or start the service."""
    package = _optional_package()
    registry, adapter = _registry(package)
    settings = WebUISettingsServices.create(tmp_path / "config.json", extension_registry=registry)
    runtime = _ApiRuntime(adapter, tmp_path / "api.log")
    update_calls: list[object] = []

    def update_api(*_args: object, **_kwargs: object) -> dict[str, Any]:
        update_calls.append(object())
        return {}

    query: dict[str, list[str]] = {"expected_revision": [expected_revision]}
    if extension_id is not None:
        query["extension_id"] = [extension_id]
    if risk_acknowledged:
        query["risk_acknowledged"] = ["true"]
    result = await CapabilitySettingsHandler(
        settings,
        logger=type("Logger", (), {"exception": staticmethod(lambda *_args: None)})(),
    ).handle(
        "api-start",
        SettingsRequest(
            query=query,
            actor_user_id="api-operator",
            system_admin=True,
            local_browser=True,
        ),
        _capability_operations(runtime, update_api=update_api),
    )

    assert result.status == status
    assert update_calls == []
    assert adapter.effects == 0
    assert runtime.start_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin", "status"),
    [(None, True, 403), ("member", False, 403), ("operator", True, 200)],
)
async def test_api_stop_requires_the_server_derived_administrator(
    tmp_path,
    actor_user_id: str | None,
    system_admin: bool,
    status: int,
) -> None:
    """Only a server-derived administrator can cause the API runtime stop effect."""
    package = _optional_package()
    registry, adapter = _registry(package)
    settings = WebUISettingsServices.create(tmp_path / "config.json", extension_registry=registry)
    runtime = _ApiRuntime(adapter, tmp_path / "api.log")

    result = await CapabilitySettingsHandler(
        settings,
        logger=type("Logger", (), {"exception": staticmethod(lambda *_args: None)})(),
    ).handle(
        "api-stop",
        SettingsRequest(
            query={}, actor_user_id=actor_user_id, system_admin=system_admin, local_browser=True
        ),
        _capability_operations(runtime),
    )

    assert result.status == status
    assert runtime.stop_calls == (1 if status == 200 else 0)
    assert adapter.effects == 0


@pytest.mark.asyncio
async def test_settings_sanitizes_connector_error_details_before_returning_them(tmp_path) -> None:
    """Connector errors may retain their status but cannot disclose raw host paths."""
    from nanobot.channels.connect import ChannelConnectError

    package = _channel_package()
    registry, adapter = _registry(package)
    office_id = next(component.id for component in package.components if component.name == "office")
    connector = _FailingConnector(
        ChannelConnectError("connector failed at /private/connector-state", status=418)
    )
    plugin = type("Plugin", (), {"load_connector": lambda _self: connector})()

    result = await _system_handler(tmp_path, registry).handle(
        "channel-connect",
        SettingsRequest(
            query={
                "name": ["relay"],
                "instance_id": ["office"],
                "extension_id": [office_id],
                "expected_revision": ["relay-package-r1"],
            },
            actor_user_id="connector-operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(plugin=plugin),
        channel_name="relay",
        connect_action="start",
    )

    assert result.status == 418
    assert result.error is not None
    assert "connector-state" not in result.error
    assert "/private/connector-state" not in result.error
    assert "<path>" in result.error
    assert connector.calls == 1
    assert adapter.effects == 0


@pytest.mark.asyncio
async def test_settings_sanitizes_connector_payload_diagnostics_before_returning_them(tmp_path) -> None:
    """Pending connector payload diagnostics cannot disclose either POSIX or Windows host paths."""
    package = _channel_package()
    registry, adapter = _registry(package)
    office_id = next(component.id for component in package.components if component.name == "office")
    connector = _Connector({
        "status": "pending",
        "message": "connector state is /private/connector-state",
        "error": r"refresh failed at C:\private\connector-state",
    })
    plugin = type("Plugin", (), {"load_connector": lambda _self: connector})()

    result = await _system_handler(tmp_path, registry).handle(
        "channel-connect",
        SettingsRequest(
            query={
                "name": ["relay"],
                "instance_id": ["office"],
                "extension_id": [office_id],
                "expected_revision": ["relay-package-r1"],
            },
            actor_user_id="connector-operator",
            system_admin=True,
            local_browser=True,
        ),
        _system_operations(plugin=plugin),
        channel_name="relay",
        connect_action="poll",
    )

    assert result.status == 200
    assert result.payload is not None
    for field in ("message", "error"):
        assert "connector-state" not in result.payload[field]
        assert "<path>" in result.payload[field]
    assert len(connector.calls) == 1
    assert adapter.effects == 0
