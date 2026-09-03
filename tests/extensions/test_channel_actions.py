"""Behavioral coverage for canonical exact-instance channel lifecycle actions."""
from __future__ import annotations

import sys
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.channels.contracts import (
    CHANNEL_INSTANCE_REVISION_FIELD,
    ChannelFieldSpec,
    ChannelInstanceSpec,
    ChannelManagementSpec,
    ChannelSetupSpec,
    SetupRequirement,
    channel_instance_revision,
)
from nanobot.channels.plugin import ChannelPlugin
from nanobot.extensions.adapters import channels as channel_adapters
from nanobot.extensions.adapters.channels import (
    ChannelExtensionAdapter,
    ChannelExtensionServices,
)
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionComponentDescriptor,
    ExtensionLifecycle,
)
from nanobot.optional_features import ChannelDependencyPreparation


def _instances(section: object, *, enabled_only: bool) -> list[ChannelInstanceSpec]:
    if not isinstance(section, dict):
        return []
    rows = section.get("instances")
    if not isinstance(rows, list):
        return []
    return [
        ChannelInstanceSpec(instance_id=str(row["instance_id"]), config=row)
        for row in rows
        if isinstance(row, dict)
        and "instance_id" in row
        and (not enabled_only or bool(row.get("enabled")))
    ]


def _update_instance(section: object, values: dict[str, Any], *, instance_id: str) -> dict[str, Any]:
    """Apply one managed-instance update while preserving every sibling row."""
    updated = deepcopy(section) if isinstance(section, dict) else {}
    rows = updated.get("instances")
    if not isinstance(rows, list):
        raise ValueError("managed instances are missing")
    for row in rows:
        if isinstance(row, dict) and row.get("instance_id") == instance_id:
            row.update(values)
            return updated
    raise ValueError("managed instance is missing")


def _plugin(*, dependencies: tuple[str, ...] = ()) -> ChannelPlugin:
    return ChannelPlugin(
        name="Demo",
        display_name="Demo channel",
        runtime="channel_actions_runtime_sentinel:Runtime",
        connector="channel_actions_runtime_sentinel:Connector",
        setup=ChannelSetupSpec(
            fields={"token": ChannelFieldSpec(kind="secret")},
            required=(SetupRequirement.field("token"),),
        ),
        management=ChannelManagementSpec(
            multi_instance=True,
            instance_specs=_instances,
            update_instance_config=_update_instance,
            runtime_name=lambda channel, instance_id: f"{channel}.{instance_id}",
        ),
        dependencies=dependencies,
    )


def _config(
    *,
    first_token: str = "first-token",
    first_pairing_required: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        channels=SimpleNamespace(
            Demo={
                "instances": [
                    {
                        "instance_id": "first",
                        "enabled": False,
                        "token": first_token,
                        "pairingRequired": first_pairing_required,
                    },
                    {"instance_id": "sibling", "enabled": True, "token": "sibling-token"},
                ]
            }
        )
    )


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    config: SimpleNamespace,
    *,
    dependencies_installed: bool | Callable[[str, list[str] | None], bool],
    services: ChannelExtensionServices,
    plugin: ChannelPlugin | None = None,
) -> ChannelExtensionAdapter:
    plugin = plugin or _plugin(dependencies=("demo-sdk>=1",))
    monkeypatch.setattr(
        channel_adapters,
        "discover_plugins",
        lambda _names=None: {plugin.name: plugin},
    )
    dependency_check: Callable[[str, list[str] | None], bool]
    if callable(dependencies_installed):
        dependency_check = dependencies_installed
    else:
        def dependency_check(_name: str, _requirements: list[str] | None) -> bool:
            return dependencies_installed
    return ChannelExtensionAdapter(
        lambda: config,  # type: ignore[arg-type]
        dependencies_installed=dependency_check,
        services=services,
    )


def _request(
    target_id: str,
    action: ExtensionAction,
    *,
    expected_revision: str | None,
    values: dict[str, object] | None = None,
    is_system_admin: bool = True,
    package_install_allowed: bool = True,
    channel_pairing_completed: bool = False,
    risk_acknowledged: bool = True,
) -> ExtensionActionRequest:
    return ExtensionActionRequest(
        context=ExtensionActionContext(
            actor_id="operator",
            is_system_admin=is_system_admin,
            package_install_allowed=package_install_allowed,
            channel_pairing_completed=channel_pairing_completed,
        ),
        target_id=target_id,
        action=action,
        expected_revision=expected_revision,
        values={} if values is None else values,
        risk_acknowledged=risk_acknowledged,
    )


def _component(adapter: ChannelExtensionAdapter, name: str) -> ExtensionComponentDescriptor:
    [package] = adapter.snapshot().packages
    return next(component for component in package.components if component.name == name)


@pytest.mark.asyncio
async def test_snapshot_declares_exact_instance_actions_and_revisions_without_runtime_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action metadata remains manifest-only and every sibling retains its own revision."""
    events: list[str] = []
    config = _config()
    services = ChannelExtensionServices(
        mutate_config=lambda change: change(config),
        runtime_action=lambda action, channel, instance: _successful_runtime_action(
            events, action, channel, instance
        ),
        prepare_dependencies=lambda _name, _requirements, *, allow_install: ChannelDependencyPreparation(
            ready=allow_install
        ),
    )
    adapter = _adapter(
        monkeypatch,
        config,
        dependencies_installed=False,
        services=services,
    )

    [package] = adapter.snapshot().packages
    components = {component.name: component for component in package.components}

    assert "channel_actions_runtime_sentinel" not in sys.modules
    assert package.actions == frozenset({ExtensionAction.INSPECT, ExtensionAction.INSTALL})
    assert components["first"].actions == frozenset(
        {
            ExtensionAction.INSPECT,
            ExtensionAction.CONFIGURE,
            ExtensionAction.ENABLE,
            ExtensionAction.DISABLE,
            ExtensionAction.RECONNECT,
        }
    )
    assert components["first"].revision is not None
    assert components["sibling"].revision is not None
    assert components["first"].revision != components["sibling"].revision
    assert events == []


@pytest.mark.asyncio
async def test_actions_apply_once_in_order_to_the_selected_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inspect, install, configure, enable, disable, and reconnect never touch a sibling."""
    events: list[str] = []
    installed = False
    config = _config(first_pairing_required=True)

    def mutate_config(change: object) -> object:
        events.append("config")
        return change(config)  # type: ignore[operator]

    async def runtime_action(action: str, channel: str, instance: str) -> dict[str, object]:
        events.append(f"manager:{action}:{channel}:{instance}")
        return {"handled": True, "ok": True, "requires_restart": False, "message": "private"}

    def prepare_dependencies(
        name: str,
        requirements: list[str] | None,
        *,
        allow_install: bool,
    ) -> ChannelDependencyPreparation:
        nonlocal installed
        events.append(f"install:{name}:{requirements}")
        assert allow_install is True
        installed = True
        return ChannelDependencyPreparation(ready=True, installed=True)

    def refresh_metadata(channel: str, instance: str) -> None:
        events.append(f"metadata:{channel}:{instance}")

    services = ChannelExtensionServices(
        mutate_config=mutate_config,
        runtime_action=runtime_action,
        prepare_dependencies=prepare_dependencies,
        refresh_metadata=refresh_metadata,
    )
    adapter = _adapter(
        monkeypatch,
        config,
        dependencies_installed=lambda _name, _requirements: installed,
        services=services,
    )

    [package] = adapter.snapshot().packages
    inspected = await adapter.execute(
        _request(package.id, ExtensionAction.INSPECT, expected_revision=None)
    )
    installed_result = await adapter.execute(
        _request(package.id, ExtensionAction.INSTALL, expected_revision=package.revision)
    )
    first = _component(adapter, "first")
    configured = await adapter.execute(
        _request(
            first.id,
            ExtensionAction.CONFIGURE,
            expected_revision=first.revision,
            values={"token": "replacement-token"},
        )
    )
    first = _component(adapter, "first")
    enabled = await adapter.execute(
        _request(
            first.id,
            ExtensionAction.ENABLE,
            expected_revision=first.revision,
            channel_pairing_completed=True,
        )
    )
    enabled_marker = channel_instance_revision(config.channels.Demo["instances"][0])
    assert enabled_marker is not None
    assert config.channels.Demo["instances"] == [
        {
            "instance_id": "first",
            "enabled": True,
            "token": "replacement-token",
            "pairingRequired": False,
            CHANNEL_INSTANCE_REVISION_FIELD: enabled_marker,
        },
        {"instance_id": "sibling", "enabled": True, "token": "sibling-token"},
    ]
    assert events == [
        "install:Demo:['demo-sdk>=1']",
        "config",
        "config",
        "manager:enable:Demo:first",
        "metadata:Demo:first",
    ]
    first = _component(adapter, "first")
    disabled = await adapter.execute(
        _request(first.id, ExtensionAction.DISABLE, expected_revision=first.revision)
    )
    first = _component(adapter, "first")
    reconnected = await adapter.execute(
        _request(first.id, ExtensionAction.RECONNECT, expected_revision=first.revision)
    )

    assert inspected.lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert installed_result.lifecycle is ExtensionLifecycle.FAILED
    assert configured.lifecycle is ExtensionLifecycle.DISABLED
    assert enabled.lifecycle is ExtensionLifecycle.ENABLED
    assert disabled.lifecycle is ExtensionLifecycle.DISABLED
    assert reconnected.lifecycle is ExtensionLifecycle.ENABLED
    assert events == [
        "install:Demo:['demo-sdk>=1']",
        "config",
        "config",
        "manager:enable:Demo:first",
        "metadata:Demo:first",
        "config",
        "manager:disable:Demo:first",
        "manager:reconnect:Demo:first",
    ]
    disabled_marker = channel_instance_revision(config.channels.Demo["instances"][0])
    assert disabled_marker is not None
    assert disabled_marker != enabled_marker
    rows = config.channels.Demo["instances"]
    assert rows == [
        {
            "instance_id": "first",
            "enabled": False,
            "token": "replacement-token",
            "pairingRequired": False,
            CHANNEL_INSTANCE_REVISION_FIELD: disabled_marker,
        },
        {"instance_id": "sibling", "enabled": True, "token": "sibling-token"},
    ]


@pytest.mark.asyncio
async def test_enable_treats_setup_free_and_requirement_free_channels_as_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an explicit local-state checker can block a setup-free or requirement-free channel."""
    events: list[str] = []
    config = SimpleNamespace(
        channels=SimpleNamespace(
            Plain={"enabled": False},
            Optional={"enabled": False},
            Unready={"enabled": False},
            Ready={"enabled": False},
        )
    )
    plugins = {
        "Plain": ChannelPlugin(
            name="Plain",
            display_name="Plain channel",
            runtime="channel_actions_runtime_sentinel:Runtime",
        ),
        "Optional": ChannelPlugin(
            name="Optional",
            display_name="Optional channel",
            runtime="channel_actions_runtime_sentinel:Runtime",
            setup=ChannelSetupSpec(
                fields={"label": ChannelFieldSpec(kind="string")},
            ),
        ),
        "Unready": ChannelPlugin(
            name="Unready",
            display_name="Unready channel",
            runtime="channel_actions_runtime_sentinel:Runtime",
            management=ChannelManagementSpec(local_state_present=lambda _section: False),
        ),
        "Ready": ChannelPlugin(
            name="Ready",
            display_name="Ready channel",
            runtime="channel_actions_runtime_sentinel:Runtime",
            management=ChannelManagementSpec(local_state_present=lambda _section: True),
        ),
    }
    monkeypatch.setattr(channel_adapters, "discover_plugins", lambda _names=None: plugins)

    def mutate_config(change: object) -> object:
        events.append("config")
        return change(config)  # type: ignore[operator]

    async def runtime_action(action: str, channel: str, instance: str) -> dict[str, object]:
        events.append(f"manager:{action}:{channel}:{instance}")
        return {"handled": True, "ok": True, "requires_restart": False, "message": ""}

    adapter = ChannelExtensionAdapter(
        lambda: config,  # type: ignore[arg-type]
        services=ChannelExtensionServices(
            mutate_config=mutate_config,
            runtime_action=runtime_action,
        ),
    )
    components = {
        package.display_name: package.components[0] for package in adapter.snapshot().packages
    }

    plain = components["Plain channel"]
    plain_result = await adapter.execute(
        _request(plain.id, ExtensionAction.ENABLE, expected_revision=plain.revision)
    )
    assert plain_result.ok is True
    assert config.channels.Plain["enabled"] is True
    assert events == ["config", "manager:enable:Plain:default"]
    optional = components["Optional channel"]
    optional_result = await adapter.execute(
        _request(optional.id, ExtensionAction.ENABLE, expected_revision=optional.revision)
    )
    assert optional_result.ok is True
    assert config.channels.Optional["enabled"] is True
    assert events == [
        "config",
        "manager:enable:Plain:default",
        "config",
        "manager:enable:Optional:default",
    ]

    unready = components["Unready channel"]
    unready_result = await adapter.execute(
        _request(unready.id, ExtensionAction.ENABLE, expected_revision=unready.revision)
    )
    assert unready_result.ok is False
    assert config.channels.Unready["enabled"] is False
    assert events == [
        "config",
        "manager:enable:Plain:default",
        "config",
        "manager:enable:Optional:default",
    ]

    ready = components["Ready channel"]
    ready_result = await adapter.execute(
        _request(ready.id, ExtensionAction.ENABLE, expected_revision=ready.revision)
    )
    assert ready_result.ok is True
    assert config.channels.Ready["enabled"] is True
    assert events == [
        "config",
        "manager:enable:Plain:default",
        "config",
        "manager:enable:Optional:default",
        "config",
        "manager:enable:Ready:default",
    ]


@pytest.mark.asyncio
async def test_locked_enable_rejects_a_concurrent_instance_change_without_write_or_manager_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config edit after revision precheck cannot enable the now-stale exact instance."""
    config = _config()
    writes: list[dict[str, Any]] = []
    manager_events: list[str] = []

    def update(section: object, values: dict[str, Any], *, instance_id: str) -> dict[str, Any]:
        writes.append(deepcopy(values))
        return _update_instance(section, values, instance_id=instance_id)

    plugin = replace(
        _plugin(),
        management=ChannelManagementSpec(
            multi_instance=True,
            instance_specs=_instances,
            update_instance_config=update,
            runtime_name=lambda channel, instance_id: f"{channel}.{instance_id}",
        ),
    )

    def mutate_config(change: object) -> object:
        config.channels.Demo["instances"][0]["token"] = "concurrent-secret"
        return change(config)  # type: ignore[operator]

    async def runtime_action(action: str, channel: str, instance: str) -> dict[str, object]:
        manager_events.append(f"{action}:{channel}:{instance}")
        return {"handled": True, "ok": True, "requires_restart": False, "message": ""}

    adapter = _adapter(
        monkeypatch,
        config,
        dependencies_installed=True,
        plugin=plugin,
        services=ChannelExtensionServices(
            mutate_config=mutate_config,
            runtime_action=runtime_action,
        ),
    )
    first = _component(adapter, "first")

    result = await adapter.execute(
        _request(first.id, ExtensionAction.ENABLE, expected_revision=first.revision)
    )

    assert result.ok is False
    assert result.message == "Channel action revision is stale."
    assert writes == []
    assert manager_events == []
    assert config.channels.Demo["instances"][0] == {
        "instance_id": "first",
        "enabled": False,
        "token": "concurrent-secret",
        "pairingRequired": False,
    }


@pytest.mark.asyncio
async def test_action_gates_reject_before_mutation_or_runtime_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorization, revision, acknowledgement, policy, setup, and target gates are side-effect free."""
    secret = "secret-token /private/channel-state"

    async def runtime_action(_action: str, _channel: str, _instance: str) -> dict[str, object]:
        raise AssertionError("gated action reached the manager")

    for case in (
        "non_admin",
        "missing_revision",
        "stale_revision",
        "missing_acknowledgement",
        "blocked_install_policy",
        "incomplete_setup",
        "missing_target",
        "unsupported_action",
    ):
        events: list[str] = []
        config = _config(first_token="" if case == "incomplete_setup" else secret)
        dependencies_installed = case != "missing_acknowledgement" and case != "blocked_install_policy"

        def mutate_config(change: object) -> object:
            events.append("config")
            return change(config)  # type: ignore[operator]

        def prepare_dependencies(*_args: object, **_kwargs: object) -> ChannelDependencyPreparation:
            events.append("install")
            return ChannelDependencyPreparation(ready=True, installed=True)

        services = ChannelExtensionServices(
            mutate_config=mutate_config,
            runtime_action=runtime_action,
            prepare_dependencies=prepare_dependencies,
            refresh_metadata=lambda _channel, _instance: events.append("metadata"),
        )
        adapter = _adapter(
            monkeypatch,
            config,
            dependencies_installed=dependencies_installed,
            services=services,
        )
        [package] = adapter.snapshot().packages
        first = _component(adapter, "first")
        if case == "non_admin":
            request = _request(
                first.id,
                ExtensionAction.ENABLE,
                expected_revision=first.revision,
                is_system_admin=False,
            )
        elif case == "missing_revision":
            request = _request(first.id, ExtensionAction.ENABLE, expected_revision=None)
        elif case == "stale_revision":
            request = _request(first.id, ExtensionAction.ENABLE, expected_revision="obsolete")
        elif case == "missing_acknowledgement":
            request = _request(
                package.id,
                ExtensionAction.INSTALL,
                expected_revision=package.revision,
                risk_acknowledged=False,
            )
        elif case == "blocked_install_policy":
            request = _request(
                package.id,
                ExtensionAction.INSTALL,
                expected_revision=package.revision,
                package_install_allowed=False,
            )
        elif case == "incomplete_setup":
            request = _request(first.id, ExtensionAction.ENABLE, expected_revision=first.revision)
        elif case == "missing_target":
            request = _request(
                "ext:channel_package:missing/channel:default",
                ExtensionAction.ENABLE,
                expected_revision="current",
            )
        else:
            request = _request(first.id, ExtensionAction.RELOAD, expected_revision=first.revision)

        result = await adapter.execute(request)

        assert result.ok is False, case
        assert events == [], case
        assert secret not in result.message, case
        assert "/private/channel-state" not in result.message, case


@pytest.mark.asyncio
async def test_action_exceptions_and_manager_failure_remain_safe_and_keep_desired_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failures expose no private detail, skip later effects, and do not roll back requested enablement."""
    secret = "secret-token /private/channel-state"
    config = _config(first_token=secret)
    events: list[str] = []

    async def runtime_action(action: str, _channel: str, _instance: str) -> dict[str, object]:
        events.append(f"manager:{action}")
        if action == "enable":
            return {
                "handled": True,
                "ok": False,
                "requires_restart": True,
                "message": secret,
            }
        raise AssertionError("only enable should reach the manager")

    def mutate_config(change: object) -> object:
        events.append("config")
        return change(config)  # type: ignore[operator]

    services = ChannelExtensionServices(
        mutate_config=mutate_config,
        runtime_action=runtime_action,
        prepare_dependencies=lambda *_args, **_kwargs: ChannelDependencyPreparation(ready=True),
        refresh_metadata=lambda _channel, _instance: events.append("metadata"),
    )
    adapter = _adapter(monkeypatch, config, dependencies_installed=True, services=services)
    first = _component(adapter, "first")

    failed_enable = await adapter.execute(
        _request(first.id, ExtensionAction.ENABLE, expected_revision=first.revision)
    )

    assert failed_enable.ok is False
    assert failed_enable.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
    assert events == ["config", "manager:enable"]
    assert config.channels.Demo["instances"][0]["enabled"] is True
    assert config.channels.Demo["instances"][1] == {
        "instance_id": "sibling",
        "enabled": True,
        "token": "sibling-token",
    }
    assert secret not in failed_enable.message
    assert "/private/channel-state" not in failed_enable.message

    def failing_mutation(_change: object) -> object:
        events.append("config-error")
        raise RuntimeError(secret)

    config_services = ChannelExtensionServices(
        mutate_config=failing_mutation,
        runtime_action=runtime_action,
        prepare_dependencies=lambda *_args, **_kwargs: ChannelDependencyPreparation(ready=True),
        refresh_metadata=lambda _channel, _instance: events.append("metadata-error"),
    )
    config_adapter = _adapter(
        monkeypatch,
        _config(first_token=secret),
        dependencies_installed=True,
        services=config_services,
    )
    component = _component(config_adapter, "first")
    config_failure = await config_adapter.execute(
        _request(
            component.id,
            ExtensionAction.CONFIGURE,
            expected_revision=component.revision,
            values={"token": "replacement"},
        )
    )

    assert config_failure.ok is False
    assert events == ["config", "manager:enable", "config-error"]
    assert secret not in config_failure.message
    assert "/private/channel-state" not in config_failure.message
    runtime_events: list[str] = []
    runtime_config = _config(first_token=secret)

    def runtime_mutation(change: object) -> object:
        runtime_events.append("config")
        return change(runtime_config)  # type: ignore[operator]

    async def failing_runtime(_action: str, _channel: str, _instance: str) -> dict[str, object]:
        runtime_events.append("manager-error")
        raise RuntimeError(secret)

    runtime_services = ChannelExtensionServices(
        mutate_config=runtime_mutation,
        runtime_action=failing_runtime,
        prepare_dependencies=lambda *_args, **_kwargs: ChannelDependencyPreparation(ready=True),
        refresh_metadata=lambda _channel, _instance: runtime_events.append("metadata"),
    )
    runtime_adapter = _adapter(
        monkeypatch,
        runtime_config,
        dependencies_installed=True,
        services=runtime_services,
    )
    component = _component(runtime_adapter, "first")
    runtime_failure = await runtime_adapter.execute(
        _request(component.id, ExtensionAction.ENABLE, expected_revision=component.revision)
    )

    assert runtime_failure.ok is False
    assert runtime_failure.lifecycle is ExtensionLifecycle.FAILED
    assert runtime_events == ["config", "manager-error"]
    assert runtime_config.channels.Demo["instances"][0]["enabled"] is True
    assert secret not in runtime_failure.message
    assert "/private/channel-state" not in runtime_failure.message


    install_events: list[str] = []
    install_config = _config(first_token=secret)

    def failing_install(*_args: object, **_kwargs: object) -> ChannelDependencyPreparation:
        install_events.append("install-error")
        raise RuntimeError(secret)

    install_services = ChannelExtensionServices(
        mutate_config=lambda _change: install_events.append("config"),
        runtime_action=lambda _action, _channel, _instance: _unexpected_manager(install_events),
        prepare_dependencies=failing_install,
        refresh_metadata=lambda _channel, _instance: install_events.append("metadata"),
    )
    install_adapter = _adapter(
        monkeypatch,
        install_config,
        dependencies_installed=False,
        services=install_services,
    )
    [package] = install_adapter.snapshot().packages
    install_failure = await install_adapter.execute(
        _request(package.id, ExtensionAction.INSTALL, expected_revision=package.revision)
    )

    assert install_failure.ok is False
    assert install_events == ["install-error"]
    assert secret not in install_failure.message
    assert "/private/channel-state" not in install_failure.message


async def _successful_runtime_action(
    events: list[str], action: str, channel: str, instance: str
) -> dict[str, object]:
    events.append(f"manager:{action}:{channel}:{instance}")
    return {"handled": True, "ok": True, "requires_restart": False, "message": ""}


async def _unexpected_manager(events: list[str]) -> dict[str, object]:
    events.append("manager")
    raise AssertionError("the manager must not run")
