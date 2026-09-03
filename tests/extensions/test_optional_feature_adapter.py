from __future__ import annotations

from typing import cast

import pytest

from nanobot.channels.plugin import ChannelPlugin
from nanobot.extensions.adapters.optional_features import OptionalFeatureExtensionAdapter
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


def _channel_plugin(name: str) -> ChannelPlugin:
    return ChannelPlugin(
        name=name,
        display_name=name.replace("_", " ").title(),
        runtime="nanobot.channels.fake:Channel",
    )


def _request(target_id: str) -> ExtensionActionRequest:
    return ExtensionActionRequest(
        context=ExtensionActionContext(actor_id="operator", is_system_admin=True),
        target_id=target_id,
        action=ExtensionAction.INSPECT,
    )


def test_snapshot_excludes_channel_owned_hidden_and_bundled_optional_names() -> None:
    """Channel and legacy-hidden names never receive duplicate optional-feature packages."""
    adapter = OptionalFeatureExtensionAdapter(
        groups_loader=lambda: {
            "owned_channel": ["channel-sdk>=1"],
            "documents": ["document-sdk>=1"],
            "pdf": ["pdf-sdk>=1"],
            "standalone": ["standalone-sdk>=1"],
        },
        channel_loader=lambda: {"registered_as": _channel_plugin("owned_channel")},
        installed=lambda _name, _requirements: True,
    )

    snapshot = adapter.snapshot()

    assert [package.name for package in snapshot.packages] == ["standalone"]
    assert snapshot.packages[0].id == extension_package_id(
        ExtensionSource.OPTIONAL_FEATURE, "standalone"
    )


def test_snapshot_projects_standalone_extra_as_canonical_package_and_component() -> None:
    """A visible extra owns exactly one canonical optional-feature package and component."""
    adapter = OptionalFeatureExtensionAdapter(
        groups_loader=lambda: {"web_search": ["search-sdk>=2"]},
        channel_loader=lambda: {},
        installed=lambda _name, _requirements: True,
    )

    [package] = adapter.snapshot().packages
    [component] = package.components
    package_id = extension_package_id(ExtensionSource.OPTIONAL_FEATURE, "web_search")

    assert package.id == package_id
    assert package.source is ExtensionSource.OPTIONAL_FEATURE
    assert package.trust is ExtensionTrust.FIRST_PARTY
    assert package.execution is ExtensionExecution.IN_PROCESS
    assert package.permissions_enforced is False
    assert package.lifecycle is ExtensionLifecycle.ENABLED
    assert component.id == extension_component_id(
        package_id, ExtensionComponentKind.OPTIONAL_FEATURE, "web_search"
    )
    assert component.package_id == package_id
    assert component.kind is ExtensionComponentKind.OPTIONAL_FEATURE
    assert component.lifecycle is ExtensionLifecycle.ENABLED
    assert package.actions == frozenset({ExtensionAction.INSPECT})
    assert component.actions == frozenset({ExtensionAction.INSPECT})


def test_snapshot_maps_installed_and_missing_extra_dependencies_to_truthful_lifecycle() -> None:
    """Optional-feature lifecycle reflects dependency availability for each independent extra."""
    adapter = OptionalFeatureExtensionAdapter(
        groups_loader=lambda: {
            "ready": ["ready-sdk>=1"],
            "missing": ["missing-sdk>=1"],
        },
        channel_loader=lambda: {},
        installed=lambda name, _requirements: name == "ready",
    )

    packages = {package.name: package for package in adapter.snapshot().packages}

    assert packages["ready"].lifecycle is ExtensionLifecycle.ENABLED
    assert packages["ready"].components[0].lifecycle is ExtensionLifecycle.ENABLED
    assert packages["missing"].lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert packages["missing"].components[0].lifecycle is ExtensionLifecycle.UNAVAILABLE


def test_snapshot_revision_tracks_safe_requirement_structure_without_exposing_requirements() -> None:
    """Changing requirement structure refreshes the opaque revision without publishing requirement text."""
    first = OptionalFeatureExtensionAdapter(
        groups_loader=lambda: {"catalog": ["catalog-client>=1"]},
        channel_loader=lambda: {},
        installed=lambda _name, _requirements: True,
    ).snapshot()
    replacement = OptionalFeatureExtensionAdapter(
        groups_loader=lambda: {"catalog": ["catalog-client>=2"]},
        channel_loader=lambda: {},
        installed=lambda _name, _requirements: True,
    ).snapshot()

    assert first.packages[0].revision != replacement.packages[0].revision
    assert first.packages[0].components[0].revision == first.packages[0].revision
    assert replacement.packages[0].components[0].revision == replacement.packages[0].revision
    assert "catalog-client>=1" not in repr(first)
    assert "catalog-client>=2" not in repr(replacement)


def test_snapshot_orders_visible_extras_deterministically() -> None:
    """Equivalent extra inventories publish canonical package order independently of loader insertion order."""
    adapter = OptionalFeatureExtensionAdapter(
        groups_loader=lambda: {
            "zeta": ["zeta-sdk>=1"],
            "alpha": ["alpha-sdk>=1"],
            "middle": ["middle-sdk>=1"],
        },
        channel_loader=lambda: {},
        installed=lambda _name, _requirements: True,
    )

    assert [package.name for package in adapter.snapshot().packages] == [
        "alpha",
        "middle",
        "zeta",
    ]


def test_snapshot_isolates_malformed_extra_without_hiding_healthy_extras() -> None:
    """One malformed dependency declaration produces a local diagnostic while healthy extras remain visible."""
    adapter = OptionalFeatureExtensionAdapter(
        groups_loader=lambda: cast(
            dict[str, list[str] | None],
            {
                "healthy": ["healthy-sdk>=1"],
                "broken": [object()],
            },
        ),
        channel_loader=lambda: {},
        installed=lambda _name, _requirements: True,
    )

    snapshot = adapter.snapshot()

    assert [package.name for package in snapshot.packages] == ["healthy"]
    assert [diagnostic.owner_id for diagnostic in snapshot.diagnostics] == [adapter.name]
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == ["optional_feature_projection_failed"]


@pytest.mark.asyncio
async def test_snapshot_and_inspect_without_services_never_trigger_optional_feature_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dependency-light adapter exposes only inspection without invoking the installer."""
    from nanobot import optional_features

    installer_calls: list[object] = []

    def unexpected_installer(*args: object, **kwargs: object) -> object:
        installer_calls.append((args, kwargs))
        raise AssertionError("snapshot and inspection must not install optional dependencies")

    monkeypatch.setattr(optional_features, "install_extra", unexpected_installer)
    adapter = OptionalFeatureExtensionAdapter(
        groups_loader=lambda: {"catalog": ["catalog-sdk>=1"]},
        channel_loader=lambda: {},
        installed=lambda _name, _requirements: False,
    )

    [package] = adapter.snapshot().packages
    [component] = package.components

    assert package.actions == frozenset({ExtensionAction.INSPECT})
    assert component.actions == frozenset({ExtensionAction.INSPECT})

    inspected = await adapter.execute(_request(component.id))

    assert inspected.ok is True
    assert inspected.action is ExtensionAction.INSPECT
    assert inspected.package_id == package.id
    assert inspected.target_id == component.id
    assert inspected.lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert installer_calls == []
