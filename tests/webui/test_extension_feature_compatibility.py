"""Settings compatibility projection contracts for canonical extension snapshots."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.channels.plugin import ChannelPlugin
from nanobot.channels.weixin.manifest import PLUGIN as WEIXIN_PLUGIN
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSnapshot,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)
from nanobot.webui import nanobot_features_api as feature_api


def _channel_component(
    package_id: str,
    name: str,
    *,
    lifecycle: ExtensionLifecycle,
    revision: str,
    actions: frozenset[ExtensionAction],
) -> ExtensionComponentDescriptor:
    return ExtensionComponentDescriptor(
        id=extension_component_id(package_id, ExtensionComponentKind.CHANNEL, name),
        package_id=package_id,
        kind=ExtensionComponentKind.CHANNEL,
        name=name,
        display_name=name.title(),
        execution=ExtensionExecution.IN_PROCESS,
        lifecycle=lifecycle,
        revision=revision,
        actions=actions,
    )


def _package(
    name: str,
    *,
    source: ExtensionSource,
    lifecycle: ExtensionLifecycle,
    revision: str,
    actions: frozenset[ExtensionAction],
    components: tuple[ExtensionComponentDescriptor, ...] = (),
) -> ExtensionPackageDescriptor:
    return ExtensionPackageDescriptor(
        id=extension_package_id(source, name),
        name=name,
        display_name=name.title(),
        source=source,
        trust=ExtensionTrust.FIRST_PARTY,
        execution=ExtensionExecution.IN_PROCESS,
        lifecycle=lifecycle,
        revision=revision,
        actions=actions,
        components=components,
    )


def _snapshot() -> ExtensionSnapshot:
    relay_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, "relay")
    default = _channel_component(
        relay_id,
        "default",
        lifecycle=ExtensionLifecycle.ENABLED,
        revision="default-revision",
        actions=frozenset({ExtensionAction.CONFIGURE, ExtensionAction.DISABLE}),
    )
    office = _channel_component(
        relay_id,
        "office",
        lifecycle=ExtensionLifecycle.RELOADING,
        revision="office-revision",
        actions=frozenset({ExtensionAction.ENABLE, ExtensionAction.RECONNECT}),
    )
    relay = _package(
        "relay",
        source=ExtensionSource.CHANNEL_PACKAGE,
        lifecycle=ExtensionLifecycle.RELOADING,
        revision="relay-package-revision",
        actions=frozenset({ExtensionAction.INSPECT}),
        components=(default, office),
    )
    return ExtensionSnapshot(
        packages=(
            relay,
            # A colliding optional extra must never become a second Settings row.
            _package(
                "relay",
                source=ExtensionSource.OPTIONAL_FEATURE,
                lifecycle=ExtensionLifecycle.ENABLED,
                revision="shadow-revision",
                actions=frozenset({ExtensionAction.INSPECT}),
            ),
            _package(
                "catalog",
                source=ExtensionSource.OPTIONAL_FEATURE,
                lifecycle=ExtensionLifecycle.ENABLED,
                revision="catalog-revision",
                actions=frozenset({ExtensionAction.INSPECT, ExtensionAction.RESTART_REQUIRED}),
            ),
            # There is no manifest enrichment for this canonical channel package.
            _package(
                "broken",
                source=ExtensionSource.CHANNEL_PACKAGE,
                lifecycle=ExtensionLifecycle.UNAVAILABLE,
                revision="broken-revision",
                actions=frozenset({ExtensionAction.INSTALL}),
            ),
        )
    )


def test_resolve_nanobot_feature_target_uses_the_exact_channel_component() -> None:
    """A named channel action never falls back to default or a sibling component."""
    snapshot = _snapshot()
    relay_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, "relay")
    office_id = extension_component_id(relay_id, ExtensionComponentKind.CHANNEL, "office")

    default = feature_api.resolve_nanobot_feature_target(snapshot, "relay")
    office = feature_api.resolve_nanobot_feature_target(snapshot, "relay", "office")
    missing = feature_api.resolve_nanobot_feature_target(snapshot, "relay", "missing")
    catalog = feature_api.resolve_nanobot_feature_target(snapshot, "catalog")

    assert default is not None
    assert (default.package_id, default.target_id, default.revision, default.actions) == (
        relay_id,
        extension_component_id(relay_id, ExtensionComponentKind.CHANNEL, "default"),
        "default-revision",
        frozenset({ExtensionAction.CONFIGURE, ExtensionAction.DISABLE}),
    )
    assert office is not None
    assert (
        office.package_id,
        office.target_id,
        office.revision,
        office.lifecycle,
        office.actions,
        office.source,
    ) == (
        relay_id,
        office_id,
        "office-revision",
        ExtensionLifecycle.RELOADING,
        frozenset({ExtensionAction.ENABLE, ExtensionAction.RECONNECT}),
        ExtensionSource.CHANNEL_PACKAGE,
    )
    assert missing is None
    assert catalog is not None
    assert (catalog.target_id, catalog.revision, catalog.source) == (
        extension_package_id(ExtensionSource.OPTIONAL_FEATURE, "catalog"),
        "catalog-revision",
        ExtensionSource.OPTIONAL_FEATURE,
    )


@pytest.mark.parametrize(
    "state_dir",
    [
        pytest.param("/private/weixin-state", id="posix"),
        pytest.param(r"C:\private\weixin-state", id="windows"),
    ],
)
def test_weixin_snapshot_omits_state_directory_from_channel_and_instance_compatibility(
    state_dir: str,
) -> None:
    """Settings never projects an absolute Weixin state path on either host-path syntax."""
    details = feature_api.channel_settings_details(
        SimpleNamespace(
            channels=SimpleNamespace(
                weixin={"token": "weixin-secret", "stateDir": state_dir}
            )
        ),
        WEIXIN_PLUGIN,
    )

    assert "channels.weixin.stateDir" not in details.get("config_values", {})
    assert "channels.weixin.stateDir" not in details.get("configured_fields", [])
    for instance in details["instances"]:
        assert "channels.weixin.stateDir" not in instance["config_values"]
        assert "channels.weixin.stateDir" not in instance["configured_fields"]

    exposed = json.dumps(details)
    assert "weixin-secret" not in exposed
    assert state_dir not in exposed
    assert state_dir.replace("\\", "\\\\") not in exposed


def test_nanobot_features_payload_projects_canonical_truth_without_runtime_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings preserves channel presentation but takes lifecycle, IDs, and actions only from the snapshot."""
    runtime_sentinel = "feature_projection_runtime_sentinel"
    monkeypatch.delitem(sys.modules, runtime_sentinel, raising=False)
    relay_plugin = ChannelPlugin(
        name="relay",
        display_name="Relay Console",
        runtime=f"{runtime_sentinel}:Runtime",
    )
    broken_plugin = ChannelPlugin(
        name="broken",
        display_name="Broken Console",
        runtime=f"{runtime_sentinel}:BrokenRuntime",
    )
    monkeypatch.setattr(
        "nanobot.channels.registry.discover_plugins",
        lambda: {"relay": relay_plugin, "broken": broken_plugin},
    )
    monkeypatch.setattr(feature_api, "_load_config", lambda _path: SimpleNamespace())

    def channel_details(_config: object, plugin: ChannelPlugin) -> dict[str, object]:
        if plugin is broken_plugin:
            raise RuntimeError("broken-secret /private/runtime-error.log")
        return {
            "capabilities": ["inbox"],
            "settings_visible": True,
            "webui": "relay-settings",
            "enabled": True,
            "configured": True,
            "setup": {
                "fields": [
                    {
                        "key": "channels.relay.token",
                        "field": "token",
                        "kind": "secret",
                        "choices": [],
                        "required": True,
                    },
                    {
                        "key": "channels.relay.region",
                        "field": "region",
                        "kind": "enum",
                        "choices": ["eu", "us"],
                        "required": False,
                    },
                ]
            },
            "config_values": {"channels.relay.region": "eu"},
            "configured_fields": ["channels.relay.token", "channels.relay.region"],
            "instances": [
                {"id": "default", "enabled": True, "configured": True},
                {"id": "office", "enabled": True, "configured": True},
            ],
        }

    monkeypatch.setattr(feature_api, "channel_settings_details", channel_details)

    payload = feature_api.nanobot_features_payload(
        extension_snapshot=_snapshot(),
        last_action={
            "target_id": "ext:channel_package:relay/channel:office",
            "token": "do-not-expose",
            "nested": {
                "password": "also-private",
                "result": "reconnect queued",
                "diagnostic": "/private/runtime-error.log",
            },
        },
    )

    rows = {row["name"]: row for row in payload["features"]}
    relay_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, "relay")
    default_id = extension_component_id(relay_id, ExtensionComponentKind.CHANNEL, "default")
    office_id = extension_component_id(relay_id, ExtensionComponentKind.CHANNEL, "office")

    assert list(row["name"] for row in payload["features"]).count("relay") == 1
    assert payload["enabled_count"] == 2
    assert runtime_sentinel not in sys.modules

    relay = rows["relay"]
    assert relay["display_name"] == "Relay Console"
    assert relay["setup"]["fields"][1]["key"] == "channels.relay.region"
    assert relay["config_values"] == {"channels.relay.region": "eu"}
    assert relay["configured_fields"] == ["channels.relay.token", "channels.relay.region"]
    assert {
        key: relay[key]
        for key in (
            "extension_id",
            "extension_revision",
            "extension_actions",
            "extension_lifecycle",
            "extension_trust",
            "extension_execution",
            "action_target_id",
            "action_target_revision",
            "action_target_actions",
            "enabled",
            "configured",
            "running",
            "ready",
            "installed",
            "runtime_status",
        )
    } == {
        "extension_id": relay_id,
        "extension_revision": "relay-package-revision",
        "extension_actions": ["inspect"],
        "extension_lifecycle": "reloading",
        "extension_trust": "first_party",
        "extension_execution": "in_process",
        "action_target_id": default_id,
        "action_target_revision": "default-revision",
        "action_target_actions": ["configure", "disable"],
        "enabled": True,
        "configured": True,
        "running": True,
        "ready": True,
        "installed": True,
        "runtime_status": "running",
    }
    assert relay["instances"] == [
        {
            "id": "default",
            "enabled": True,
            "configured": True,
            "extension_id": default_id,
            "extension_revision": "default-revision",
            "extension_actions": ["configure", "disable"],
            "extension_lifecycle": "enabled",
            "runtime_status": "running",
            "running": True,
        },
        {
            "id": "office",
            "enabled": True,
            "configured": True,
            "extension_id": office_id,
            "extension_revision": "office-revision",
            "extension_actions": ["enable", "reconnect"],
            "extension_lifecycle": "reloading",
            "runtime_status": "starting",
            "running": False,
            "pairing_only": True,
        },
    ]
    assert rows["catalog"] == {
        "name": "catalog",
        "display_name": "Catalog",
        "type": "feature",
        "install_supported": False,
        "requires_restart": True,
        "extension_id": extension_package_id(ExtensionSource.OPTIONAL_FEATURE, "catalog"),
        "extension_revision": "catalog-revision",
        "extension_actions": ["inspect", "restart_required"],
        "extension_lifecycle": "enabled",
        "extension_trust": "first_party",
        "extension_execution": "in_process",
        "enabled": True,
        "configured": True,
        "ready": True,
        "running": True,
        "installed": True,
    }
    assert rows["broken"] == {
        "name": "broken",
        "display_name": "Broken",
        "type": "channel",
        "install_supported": True,
        "requires_restart": False,
        "extension_id": extension_package_id(ExtensionSource.CHANNEL_PACKAGE, "broken"),
        "extension_revision": "broken-revision",
        "extension_actions": ["install"],
        "extension_lifecycle": "unavailable",
        "extension_trust": "first_party",
        "extension_execution": "in_process",
        "installed": False,
        "running": False,
        "ready": False,
        "runtime_status": "stopped",
        "enabled": False,
        "configured": False,
        "error": "Channel metadata could not be projected.",
    }

    serialized = json.dumps(payload)
    for private_value in (
        "do-not-expose",
        "also-private",
        "broken-secret",
        "/private/runtime-error.log",
    ):
        assert private_value not in serialized
    assert payload["last_action"] == {
        "target_id": office_id,
        "nested": {"result": "reconnect queued", "diagnostic": "<path>"},
    }


def _client_union_members(type_name: str) -> set[str]:
    """Read one closed string union out of the client contract."""
    source = (
        Path(__file__).resolve().parents[2] / "webui" / "src" / "lib" / "types.ts"
    ).read_text(encoding="utf-8")
    match = re.search(
        rf"^export type {type_name} =(.*?);$",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{type_name} is not declared in webui/src/lib/types.ts"
    body = match.group(1)
    assert "string" not in re.sub(r'"[^"]*"', "", body), (
        f"{type_name} must stay closed; an open `| string` lets an unmatched value "
        "through the way `runtime_status` did"
    )
    return set(re.findall(r'"([^"]+)"', body))


def test_channel_runtime_status_matches_the_closed_client_vocabulary() -> None:
    """AC3: one vocabulary on the wire, bound to the consumer that reads it.

    The cutover replaced the ChannelManager states with the package lifecycle, so an
    enabled channel reported `enabled` while every consumer compared `running`. Nothing
    failed, because each side was tested against its own vocabulary. This binds them.
    """
    produced = {
        feature_api.channel_runtime_status(lifecycle)
        for lifecycle in ExtensionLifecycle
    }

    assert produced == set(feature_api.CHANNEL_RUNTIME_STATUSES)
    assert produced == _client_union_members("ChannelRuntimeStatus")


def test_an_enabled_channel_reports_running_to_the_surfaces_that_read_it() -> None:
    """The regression itself: `enabled` is a lifecycle, `running` is the runtime state."""
    # Exhaustive on purpose. Set equality alone would still pass if two lifecycles
    # swapped their runtime states, which is exactly the shape of the bug this replaces.
    assert {
        lifecycle.name: feature_api.channel_runtime_status(lifecycle)
        for lifecycle in ExtensionLifecycle
    } == {
        "DISCOVERED": "stopped",
        "UNAVAILABLE": "stopped",
        "DISABLED": "stopped",
        "ENABLING": "starting",
        "ENABLED": "running",
        "RELOADING": "starting",
        "FAILED": "failed",
        # A restart-bound channel is genuinely not running. The canonical state travels
        # beside it as `extension_lifecycle`, so nothing is lost by saying "stopped".
        "RESTART_REQUIRED": "stopped",
    }
