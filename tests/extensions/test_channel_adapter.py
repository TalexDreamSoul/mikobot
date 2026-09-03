"""Behavioral coverage for the dependency-free channel extension projection."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping
from hashlib import sha256
from types import SimpleNamespace

import pytest

from nanobot.channels.contracts import (
    ChannelFieldSpec,
    ChannelInstanceSpec,
    ChannelManagementSpec,
    ChannelSetupSpec,
    SetupRequirement,
    channel_instance_specs,
)
from nanobot.channels.feishu.instances import FEISHU_MANAGEMENT
from nanobot.channels.plugin import ChannelPlugin
from nanobot.extensions.adapters import channels as channel_adapters
from nanobot.extensions.adapters.channels import ChannelExtensionAdapter
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionComponentKind,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)


def _instances(section: object, *, enabled_only: bool) -> list[ChannelInstanceSpec]:
    values = section if isinstance(section, dict) else {}
    instances = values.get("instances", [])
    if not isinstance(instances, list):
        return []
    return [
        ChannelInstanceSpec(instance_id=str(instance["instance_id"]), config=instance)
        for instance in instances
        if isinstance(instance, dict)
        and (not enabled_only or bool(instance.get("enabled")))
    ]


def _management(
    *,
    instance_specs: Callable[..., Iterable[ChannelInstanceSpec]] = _instances,
    local_state_present: Callable[[object], bool] | None = None,
) -> ChannelManagementSpec:
    return ChannelManagementSpec(
        multi_instance=True,
        instance_specs=instance_specs,
        update_instance_config=lambda _section, values, *, instance_id: values,
        runtime_name=lambda channel, instance_id: (
            channel if instance_id == "default" else f"{channel}.{instance_id}"
        ),
        local_state_present=local_state_present,
    )


def _plugin(
    name: str,
    *,
    management: ChannelManagementSpec | None = None,
    setup: ChannelSetupSpec | None = None,
    dependencies: tuple[str, ...] = (),
    runtime: str = "channel_adapter_runtime_sentinel:Runtime",
) -> ChannelPlugin:
    return ChannelPlugin(
        name=name,
        display_name=f"{name} channel",
        runtime=runtime,
        management=management or _management(),
        setup=setup,
        dependencies=dependencies,
    )


def _config(**sections: object) -> SimpleNamespace:
    return SimpleNamespace(channels=SimpleNamespace(**sections))


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    plugins: Mapping[str, ChannelPlugin],
    config: SimpleNamespace,
    *,
    statuses: Mapping[str, Mapping[str, object]] | None = None,
    installed: Callable[[str, list[str] | None], bool] | None = None,
) -> ChannelExtensionAdapter:
    monkeypatch.setattr(channel_adapters, "discover_plugins", lambda: dict(plugins))
    return ChannelExtensionAdapter(
        lambda: config,  # type: ignore[arg-type]
        runtime_status=lambda: statuses or {},
        dependencies_installed=installed or (lambda _name, _requirements: True),
    )


def _configured_setup() -> ChannelSetupSpec:
    return ChannelSetupSpec(
        fields={"token": ChannelFieldSpec(kind="secret")},
        required=(SetupRequirement.field("token"),),
    )


def test_snapshot_uses_dependency_free_manifest_inventory_with_canonical_single_instance_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listing a disabled channel publishes canonical metadata without importing its runtime SDK."""
    runtime_module = "channel_adapter_runtime_sentinel"
    plugin = _plugin(
        "Catalog",
        management=ChannelManagementSpec(),
        runtime=f"{runtime_module}:Runtime",
    )

    snapshot = _adapter(
        monkeypatch,
        {plugin.name: plugin},
        _config(Catalog={"enabled": False}),
    ).snapshot()

    canonical_name = f"catalog-{sha256(b'Catalog').hexdigest()[:32]}"
    package_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, canonical_name)
    [package] = snapshot.packages
    [component] = package.components

    assert runtime_module not in sys.modules
    assert package.id == package_id
    assert package.source is ExtensionSource.CHANNEL_PACKAGE
    assert package.trust is ExtensionTrust.FIRST_PARTY
    assert package.execution is ExtensionExecution.IN_PROCESS
    assert package.isolated is None
    assert package.permissions_enforced is False
    assert package.actions == frozenset({ExtensionAction.INSPECT})
    assert package.configuration is not None
    assert package.configuration.section == "channels"
    assert package.configuration.item == canonical_name
    assert component.id == extension_component_id(
        package_id,
        ExtensionComponentKind.CHANNEL,
        "default",
    )
    assert component.lifecycle is ExtensionLifecycle.DISABLED
    assert component.actions == frozenset({ExtensionAction.INSPECT})
    assert component.configuration == package.configuration


def test_snapshot_keeps_real_feishu_instances_distinct_and_maps_status_by_exact_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running sibling cannot make another Feishu instance enabled, even with an arbitrary runtime key."""
    plugin = _plugin("Feishu", management=FEISHU_MANAGEMENT, setup=_configured_setup())
    statuses: dict[str, Mapping[str, object]] = {
        "unrelated-runtime-key": {
            "owner": "Feishu",
            "instance_id": "default",
            "state": "running",
        },
        "wrong-owner": {
            "owner": "SomeoneElse",
            "instance_id": "office",
            "state": "running",
        },
    }
    config = _config(
        Feishu={
            "instances": [
                {"instanceId": "default", "enabled": True, "token": "first-secret"},
                {"instanceId": "office", "enabled": True, "token": "second-secret"},
            ]
        }
    )
    adapter = _adapter(monkeypatch, {plugin.name: plugin}, config, statuses=statuses)

    [package] = adapter.snapshot().packages
    first_components = {component.name: component for component in package.components}
    statuses["wrong-owner"] = {
        "owner": "Feishu",
        "instance_id": "office",
        "state": "running",
    }
    [package] = adapter.snapshot().packages
    second_components = {component.name: component for component in package.components}

    canonical_name = f"feishu-{sha256(b'Feishu').hexdigest()[:32]}"
    package_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, canonical_name)
    assert set(first_components) == {"default", "office"}
    assert first_components["default"].id == extension_component_id(
        package_id,
        ExtensionComponentKind.CHANNEL,
        "default",
    )
    assert first_components["office"].id == extension_component_id(
        package_id,
        ExtensionComponentKind.CHANNEL,
        "office",
    )
    assert first_components["default"].lifecycle is ExtensionLifecycle.ENABLED
    assert first_components["office"].lifecycle is ExtensionLifecycle.FAILED
    assert second_components["office"].lifecycle is ExtensionLifecycle.ENABLED


@pytest.mark.parametrize(
    ("status_row", "expected"),
    [
        ({"state": "starting"}, ExtensionLifecycle.ENABLING),
        ({"pairing_only": True}, ExtensionLifecycle.RELOADING),
        ({"state": "failed"}, ExtensionLifecycle.FAILED),
        ({"state": "stopped"}, ExtensionLifecycle.FAILED),
        (None, ExtensionLifecycle.FAILED),
    ],
)
def test_snapshot_maps_runtime_statuses_for_a_desired_enabled_instance(
    monkeypatch: pytest.MonkeyPatch,
    status_row: Mapping[str, object] | None,
    expected: ExtensionLifecycle,
) -> None:
    """Desired enabled channels surface each manager state, including a missing manager row, truthfully."""
    plugin = _plugin("Status", setup=_configured_setup())
    statuses: Mapping[str, Mapping[str, object]] = (
        {}
        if status_row is None
        else {
            "not-an-identity": {
                "owner": "Status",
                "instance_id": "default",
                **status_row,
            }
        }
    )

    [package] = _adapter(
        monkeypatch,
        {plugin.name: plugin},
        _config(Status={"instances": [{"instance_id": "default", "enabled": True, "token": "set"}]}),
        statuses=statuses,
    ).snapshot().packages

    assert package.components[0].lifecycle is expected


def test_snapshot_prioritizes_dependency_desired_configuration_and_local_state_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dependency, desired state, setup, and local-state facts each control lifecycle before runtime state."""
    configured = _configured_setup()
    missing_dependency = _plugin("Dependency", setup=configured, dependencies=("sdk>=1",))
    disabled = _plugin("Disabled", setup=configured)
    unconfigured = _plugin("Unconfigured", setup=configured)
    local_state = _plugin(
        "Local",
        management=_management(local_state_present=lambda section: bool(section["present"])),
    )
    statuses = {
        "dependency": {"owner": "Dependency", "instance_id": "default", "state": "running"},
        "disabled": {"owner": "Disabled", "instance_id": "default", "state": "running"},
        "unconfigured": {"owner": "Unconfigured", "instance_id": "default", "state": "running"},
        "local": {"owner": "Local", "instance_id": "default", "state": "running"},
    }
    adapter = _adapter(
        monkeypatch,
        {
            plugin.name: plugin
            for plugin in (missing_dependency, disabled, unconfigured, local_state)
        },
        _config(
            Dependency={"instances": [{"instance_id": "default", "enabled": True, "token": "set"}]},
            Disabled={"instances": [{"instance_id": "default", "enabled": False, "token": "set"}]},
            Unconfigured={"instances": [{"instance_id": "default", "enabled": True, "token": ""}]},
            Local={"present": True, "instances": [{"instance_id": "default", "enabled": True}]},
        ),
        statuses=statuses,
        installed=lambda name, _requirements: name != "Dependency",
    )

    packages = {package.display_name.split()[0]: package for package in adapter.snapshot().packages}

    assert packages["Dependency"].components[0].lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert packages["Disabled"].components[0].lifecycle is ExtensionLifecycle.DISABLED
    assert packages["Unconfigured"].components[0].lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert packages["Local"].components[0].lifecycle is ExtensionLifecycle.ENABLED


def test_snapshot_revision_rotates_for_private_value_replacements_without_disclosing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact private config changes invalidate opaque revisions without becoming public metadata."""
    plugin = _plugin(
        "Private",
        setup=ChannelSetupSpec(
            fields={
                "token": ChannelFieldSpec(kind="secret"),
                "label": ChannelFieldSpec(),
                "stateDir": ChannelFieldSpec(snapshot=False),
            },
            required=(SetupRequirement.field("token"),),
        ),
    )
    values = {
        "instances": [
            {
                "instance_id": "default",
                "enabled": True,
                "token": "first-secret",
                "label": "first-label",
                "stateDir": "/private/first-state",
            }
        ]
    }
    adapter = _adapter(monkeypatch, {plugin.name: plugin}, _config(Private=values))

    [first] = adapter.snapshot().packages
    [first_component] = first.components
    [unchanged] = adapter.snapshot().packages
    values["instances"][0]["token"] = "replacement-secret"
    [secret_replacement] = adapter.snapshot().packages
    [secret_component] = secret_replacement.components
    values["instances"][0]["label"] = "replacement-label"
    [nonsecret_replacement] = adapter.snapshot().packages
    [nonsecret_component] = nonsecret_replacement.components
    values["instances"][0]["stateDir"] = "/private/replacement-state"
    [path_replacement] = adapter.snapshot().packages
    [path_component] = path_replacement.components

    assert unchanged.revision == first.revision
    assert secret_replacement.revision != first.revision
    assert nonsecret_replacement.revision != secret_replacement.revision
    assert path_replacement.revision != nonsecret_replacement.revision
    assert secret_component.revision != first_component.revision
    assert nonsecret_component.revision != secret_component.revision
    assert path_component.revision != nonsecret_component.revision
    exposed = repr((first, secret_replacement, nonsecret_replacement, path_replacement))
    for private_value in (
        "first-secret",
        "replacement-secret",
        "first-label",
        "replacement-label",
        "/private/first-state",
        "/private/replacement-state",
    ):
        assert private_value not in exposed


def test_snapshot_isolates_malformed_package_without_leaking_its_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One invalid manifest projection keeps healthy channel descriptors while its diagnostic stays safe."""
    healthy = _plugin("Healthy")

    def broken_instances(_section: object, *, enabled_only: bool) -> Iterable[ChannelInstanceSpec]:
        raise RuntimeError("raw-secret /private/channel-state.sqlite")
        yield from ()

    broken = _plugin("Broken", management=_management(instance_specs=broken_instances))
    snapshot = _adapter(
        monkeypatch,
        {healthy.name: healthy, broken.name: broken},
        _config(Healthy={"instances": [{"instance_id": "default", "enabled": False}]}, Broken={}),
    ).snapshot()

    assert [package.display_name for package in snapshot.packages] == ["Healthy channel"]
    assert len(snapshot.diagnostics) == 1
    assert snapshot.diagnostics[0].owner_id == "channels"
    exposed = repr(snapshot.diagnostics[0])
    assert "raw-secret" not in exposed
    assert "/private/channel-state.sqlite" not in exposed


def test_channel_instance_specs_rejects_unbounded_management_generator_after_limit() -> None:
    """A malformed management iterator is stopped at the contract bound instead of being materialized."""
    pulls = 0

    def endless(_section: object, *, enabled_only: bool) -> Iterable[ChannelInstanceSpec]:
        nonlocal pulls
        for index in range(10_000):
            pulls += 1
            yield ChannelInstanceSpec(instance_id=f"instance-{index}", config={"enabled": False})

    plugin = _plugin("Bounded", management=_management(instance_specs=endless))

    with pytest.raises(ValueError):
        channel_instance_specs(plugin, {}, enabled_only=False)

    assert pulls == 513


@pytest.mark.asyncio
async def test_channel_adapter_without_services_inspects_without_mutation_and_rejects_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dependency-light adapter can inspect a current component but cannot mutate it."""
    section = {"instances": [{"instance_id": "default", "enabled": False}]}
    adapter = _adapter(
        monkeypatch,
        {"Inspect": _plugin("Inspect")},
        _config(Inspect=section),
    )
    [package] = adapter.snapshot().packages
    [component] = package.components
    before = {"instances": [dict(instance) for instance in section["instances"]]}

    inspected = await adapter.execute(
        ExtensionActionRequest(
            context=ExtensionActionContext(actor_id="operator", is_system_admin=True),
            target_id=component.id,
            action=ExtensionAction.INSPECT,
        )
    )

    assert inspected.ok is True
    assert inspected.action is ExtensionAction.INSPECT
    assert inspected.package_id == package.id
    assert inspected.target_id == component.id
    assert inspected.lifecycle is component.lifecycle
    assert section == before

    package_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, "channel")
    target_id = extension_component_id(package_id, ExtensionComponentKind.CHANNEL, "default")
    unsupported = await ChannelExtensionAdapter(
        lambda: (_ for _ in ()).throw(AssertionError("unsupported action must not load config")),
        dependencies_installed=lambda _name, _requirements: True,
    ).execute(
        ExtensionActionRequest(
            context=ExtensionActionContext(actor_id="operator", is_system_admin=True),
            target_id=target_id,
            action=ExtensionAction.ENABLE,
        )
    )

    assert unsupported.ok is False
    assert unsupported.action is ExtensionAction.ENABLE
    assert unsupported.package_id == package_id
    assert unsupported.target_id == target_id
