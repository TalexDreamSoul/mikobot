"""Settings compatibility projection for canonical channel and feature snapshots."""
from __future__ import annotations

from collections.abc import Mapping
from itertools import islice
from pathlib import Path
from typing import Any, cast

from nanobot.channels.settings_payload import channel_settings_details
from nanobot.extensions.adapters.common import canonical_extension_name
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSnapshot,
    ExtensionSource,
    safe_extension_message,
)
from nanobot.extensions.registry import ExtensionRegistry, ExtensionRegistryError
from nanobot.extensions.targets import (
    resolve_extension_feature_target as resolve_nanobot_feature_target,
)
from nanobot.extensions.targets import (
    resolve_extension_package_target,
)
from nanobot.optional_features import OptionalFeatureError


async def execute_nanobot_extension_action(
    registry: ExtensionRegistry,
    *,
    action: ExtensionAction,
    name: str,
    instance_id: str | None,
    extension_id: str,
    expected_revision: str,
    risk_acknowledged: bool,
    actor_id: str,
    is_system_admin: bool,
    package_install_allowed: bool,
    values: Mapping[str, object] | None = None,
    channel_pairing_completed: bool = False,
    package_target: bool = False,
    package_source: ExtensionSource | None = None,
) -> ExtensionActionResult:
    """Dispatch one Settings action against the fresh exact canonical target."""
    if not expected_revision.strip():
        raise OptionalFeatureError("extension action requires a current revision", status=409)
    try:
        snapshot = registry.snapshot()
        target = (
            resolve_extension_package_target(
                snapshot, name, source=package_source
            )
            if package_target
            else resolve_nanobot_feature_target(snapshot, name, instance_id)
        )
        if target is None or target.target_id != extension_id:
            raise OptionalFeatureError("extension action target is unavailable", status=404)
        if target.revision != expected_revision:
            raise OptionalFeatureError("extension action revision is stale", status=409)
        package = next(
            (item for item in snapshot.packages if item.id == target.package_id), None
        )
        if package is None:
            raise OptionalFeatureError("extension action target is unavailable", status=404)
        may_install = action is ExtensionAction.INSTALL or (
            action is ExtensionAction.ENABLE
            and ExtensionAction.INSTALL in package.actions
        )
        if may_install and not risk_acknowledged:
            raise OptionalFeatureError("risk acknowledgement is required for this extension action", status=409)
        if may_install and not package_install_allowed:
            raise OptionalFeatureError(
                "Installing optional features from a remote WebUI is disabled. "
                "Run this action from localhost or set tools.webuiAllowRemotePackageInstall to true.",
                status=403,
            )
        return await registry.execute(
            ExtensionActionRequest(
                context=ExtensionActionContext(
                    actor_id=actor_id or "webui",
                    is_system_admin=is_system_admin,
                    package_install_allowed=package_install_allowed,
                    channel_pairing_completed=channel_pairing_completed,
                ),
                target_id=target.target_id,
                action=action,
                expected_revision=expected_revision,
                risk_acknowledged=risk_acknowledged,
                values=values or {},
            )
        )
    except OptionalFeatureError:
        raise
    except ExtensionRegistryError as exc:
        raise OptionalFeatureError(safe_extension_message(exc), status=exc.status) from None
    except ValueError:
        raise OptionalFeatureError("extension action request is invalid", status=400) from None
    except Exception:
        raise OptionalFeatureError("extension action could not be completed", status=500) from None


_CHANNEL_PRESENTATION_ERROR = "Channel metadata could not be projected."
_MAX_LAST_ACTION_ITEMS = 64
_MAX_LAST_ACTION_DEPTH = 8
_MAX_LAST_ACTION_VALUES = 1_024
_SECRET_ACTION_KEYWORDS = ("credential", "password", "secret", "token")


def restricted_nanobot_features_payload() -> dict[str, Any]:
    """Return the inventory projection a caller without host administration may read.

    Package names, revisions, lifecycles, trust, execution location, and adapter
    diagnostics are all host-wide facts, and nanobot has no per-member visibility model
    that could filter them, so the inventory is withheld whole rather than sampled.
    `restricted` lets Settings say an administrator is required instead of claiming that
    nothing is installed.
    """
    return {"features": [], "enabled_count": 0, "restricted": True}


def nanobot_features_payload(
    *,
    extension_snapshot: ExtensionSnapshot,
    config_path: Path | None = None,
    last_action: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Translate canonical channel/optional descriptors into the current Settings shape."""

    channel_packages = tuple(
        package
        for package in extension_snapshot.packages
        if package.source is ExtensionSource.CHANNEL_PACKAGE
    )
    channel_names = frozenset(package.name for package in channel_packages)
    config = _load_config(config_path) if channel_packages else None
    channel_plugins: Mapping[str, Any] = _channel_plugins() if channel_packages else {}
    features: list[dict[str, Any]] = []
    for package in extension_snapshot.packages:
        if package.source is ExtensionSource.CHANNEL_PACKAGE:
            features.append(_channel_feature(package, config, channel_plugins))
        elif (
            package.source is ExtensionSource.OPTIONAL_FEATURE
            and package.name not in channel_names
        ):
            features.append(_optional_feature(package))

    payload: dict[str, Any] = {
        "features": features,
        "enabled_count": sum(
            1
            for feature in features
            if (
                feature["type"] == "channel" and feature["running"]
            )
            or (
                feature["type"] == "feature" and feature["enabled"]
            )
        ),
    }
    bounded_last_action = _bounded_last_action(last_action)
    if bounded_last_action:
        payload["last_action"] = bounded_last_action
    return payload



def _channel_feature(
    package: ExtensionPackageDescriptor,
    config: Any | None,
    channel_plugins: Mapping[str, Any],
) -> dict[str, Any]:
    feature = _feature_base(package, feature_type="channel")
    components = {
        component.name: component
        for component in package.components
        if component.kind is ExtensionComponentKind.CHANNEL
    }
    default_component = components.get("default")
    # The row is live when any instance is live. The package lifecycle aggregates away
    # that fact, so it stays in extension_lifecycle and never drives the runtime fields.
    runtime_lifecycle = (
        ExtensionLifecycle.ENABLED
        if package.lifecycle is ExtensionLifecycle.ENABLED
        or any(
            component.lifecycle is ExtensionLifecycle.ENABLED
            for component in components.values()
        )
        else package.lifecycle
    )
    if default_component is not None:
        feature.update(_action_target_fields(default_component))
    plugin = channel_plugins.get(package.name)
    if plugin is None or config is None:
        feature.update(_channel_lifecycle_fields(package, runtime_lifecycle))
        feature["enabled"] = False
        feature["configured"] = False
        feature["error"] = _CHANNEL_PRESENTATION_ERROR
        return feature
    try:
        details = channel_settings_details(config, plugin)
    except Exception:
        feature.update(_channel_lifecycle_fields(package, runtime_lifecycle))
        feature["enabled"] = False
        feature["configured"] = False
        feature["error"] = _CHANNEL_PRESENTATION_ERROR
        return feature

    feature["name"] = plugin.name
    feature["display_name"] = plugin.display_name
    feature.update(details)
    if default_component is not None:
        feature["capabilities"] = list(default_component.capabilities)
    feature.update(_channel_lifecycle_fields(package, runtime_lifecycle))
    feature["enabled"] = bool(details["enabled"])
    feature["configured"] = bool(details["configured"])

    _decorate_channel_instances(feature, components)
    return feature


def _optional_feature(package: ExtensionPackageDescriptor) -> dict[str, Any]:
    feature = _feature_base(package, feature_type="feature")
    enabled = package.lifecycle is ExtensionLifecycle.ENABLED
    feature.update({
        "enabled": enabled,
        "configured": enabled,
        "ready": enabled,
        "running": enabled,
        "installed": enabled,
        # No second status vocabulary: the row already carries `extension_lifecycle`,
        # and the booleans above are what the surfaces branch on.
    })
    return feature


def _feature_base(
    package: ExtensionPackageDescriptor,
    *,
    feature_type: str,
) -> dict[str, Any]:
    return {
        "name": package.name,
        "display_name": package.display_name,
        "type": feature_type,
        "install_supported": ExtensionAction.INSTALL in package.actions,
        "requires_restart": (
            package.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
            or ExtensionAction.RESTART_REQUIRED in package.actions
        ),
        "extension_id": package.id,
        "extension_revision": package.revision,
        "extension_actions": sorted(action.value for action in package.actions),
        "extension_lifecycle": package.lifecycle.value,
        "extension_trust": package.trust.value,
        "extension_execution": package.execution.value,
    }


def _channel_lifecycle_fields(
    package: ExtensionPackageDescriptor,
    lifecycle: ExtensionLifecycle,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "installed": ExtensionAction.INSTALL not in package.actions,
        "running": lifecycle is ExtensionLifecycle.ENABLED,
        "ready": lifecycle is ExtensionLifecycle.ENABLED,
        "runtime_status": channel_runtime_status(lifecycle),
    }
    if lifecycle is ExtensionLifecycle.RELOADING:
        fields["pairing_only"] = True
    if lifecycle is ExtensionLifecycle.FAILED:
        fields["runtime_error"] = "Channel runtime failed. Check gateway logs."
    return fields


# The runtime vocabulary the channel surfaces compare against, declared once so the
# Python producer and the TypeScript consumers cannot drift apart again. It is kept
# separate from `ExtensionLifecycle` because it is a channel *runtime* state, not the
# package lifecycle: `CHANNEL_RUNTIME_STATUSES` is asserted against the client union by
# `tests/webui/test_extension_feature_compatibility.py`.
CHANNEL_RUNTIME_STATUSES = ("running", "starting", "failed", "stopped")


def channel_runtime_status(lifecycle: ExtensionLifecycle) -> str:
    """Report the channel runtime state the Channels surfaces render.

    Before the extension cutover this came from `ChannelManager` as `running`, and every
    consumer still compares against that word, so an enabled channel reports `running`
    rather than the package lifecycle's `enabled`. Emitting the lifecycle value here
    instead silently turned every `runtime_status === "running"` check false.
    """
    if lifecycle is ExtensionLifecycle.ENABLED:
        return "running"
    if lifecycle in {ExtensionLifecycle.ENABLING, ExtensionLifecycle.RELOADING}:
        return "starting"
    if lifecycle is ExtensionLifecycle.FAILED:
        return "failed"
    return "stopped"


def _action_target_fields(component: ExtensionComponentDescriptor) -> dict[str, object]:
    return {
        "action_target_id": component.id,
        "action_target_revision": component.revision,
        "action_target_actions": sorted(action.value for action in component.actions),
    }


def _decorate_channel_instances(
    feature: dict[str, Any],
    components: Mapping[str, ExtensionComponentDescriptor],
) -> None:
    raw_instances_value: object = feature.get("instances")
    if not isinstance(raw_instances_value, list):
        return
    raw_instances = cast(list[object], raw_instances_value)
    instances: list[dict[str, Any]] = []
    for raw_instance in raw_instances:
        if not isinstance(raw_instance, Mapping):
            continue
        instance = dict(cast(Mapping[str, Any], raw_instance))
        raw_instance_id = instance.get("id")
        component = (
            components.get(canonical_extension_name(raw_instance_id, fallback="instance"))
            if isinstance(raw_instance_id, str)
            else None
        )
        if component is None:
            instance["error"] = _CHANNEL_PRESENTATION_ERROR
            instances.append(instance)
            continue
        instance.update({
            "extension_id": component.id,
            "extension_revision": component.revision,
            "extension_actions": sorted(action.value for action in component.actions),
            "extension_lifecycle": component.lifecycle.value,
            "runtime_status": channel_runtime_status(component.lifecycle),
            "running": component.lifecycle is ExtensionLifecycle.ENABLED,
        })
        if component.lifecycle is ExtensionLifecycle.RELOADING:
            instance["pairing_only"] = True
        instances.append(instance)
    feature["instances"] = instances


def _channel_plugins() -> Mapping[str, Any]:
    try:
        from nanobot.channels.registry import discover_plugins

        plugins_value = cast(object, discover_plugins())
    except Exception:
        empty_plugins: Mapping[str, Any] = {}
        return empty_plugins
    indexed: dict[str, Any] = {}
    if not isinstance(plugins_value, Mapping):
        return indexed
    plugins = cast(Mapping[object, object], plugins_value)
    for plugin in islice(plugins.values(), 1_024):
        try:
            raw_name = getattr(plugin, "name", None)
            if isinstance(raw_name, str):
                indexed[canonical_extension_name(raw_name, fallback="channel")] = plugin
        except Exception:
            continue
    return indexed


def _load_config(config_path: Path | None) -> Any:
    from nanobot.config.loader import load_config

    return load_config(config_path) if config_path is not None else load_config()


def _bounded_last_action(value: Mapping[str, object] | None) -> dict[str, Any] | None:
    raw_value: object = value
    if not isinstance(raw_value, Mapping):
        return None
    budget = [_MAX_LAST_ACTION_VALUES]
    copied = _copy_public_value(raw_value, depth=0, budget=budget)
    return cast(dict[str, Any], copied) if isinstance(copied, dict) else None


def _copy_public_value(value: object, *, depth: int, budget: list[int]) -> Any:
    if depth > _MAX_LAST_ACTION_DEPTH or budget[0] <= 0:
        return None
    budget[0] -= 1
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        return safe_extension_message(value)
    raw_mapping: object = value
    if isinstance(raw_mapping, Mapping):
        mapping = cast(Mapping[object, object], raw_mapping)
        copied: dict[str, Any] = {}
        for raw_key, raw_item in islice(mapping.items(), _MAX_LAST_ACTION_ITEMS):
            if not isinstance(raw_key, str):
                continue
            if any(keyword in raw_key.lower() for keyword in _SECRET_ACTION_KEYWORDS):
                continue
            copied[safe_extension_message(raw_key)[:128]] = _copy_public_value(
                raw_item,
                depth=depth + 1,
                budget=budget,
            )
        return copied
    raw_sequence: object = value
    if isinstance(raw_sequence, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], raw_sequence)
        return [
            _copy_public_value(item, depth=depth + 1, budget=budget)
            for item in sequence[:_MAX_LAST_ACTION_ITEMS]
        ]
    return safe_extension_message(value)
