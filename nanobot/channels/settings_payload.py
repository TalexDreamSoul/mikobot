"""Dependency-free channel presentation facts for Settings compatibility payloads."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.channels._setup import channel_setup_spec
from nanobot.channels.contracts import (
    ChannelSetupSpec,
    channel_feature_instances,
    channel_field_value,
    channel_instance_specs,
    channel_local_state_present,
    channel_value_present,
    stringify_channel_value,
)
from nanobot.channels.registry import channel_default_enabled

if TYPE_CHECKING:
    from nanobot.channels.plugin import ChannelPlugin
    from nanobot.config.schema import Config


def channel_enabled(
    config: Config,
    name: str,
    plugin: ChannelPlugin | None = None,
    *,
    default_enabled: bool | None = None,
) -> bool:
    """Return the saved desired enablement for a channel's instances."""
    section = getattr(config.channels, name, None)
    if default_enabled is None:
        default_enabled = plugin.default_enabled if plugin is not None else channel_default_enabled(name)
    if section is None:
        return bool(default_enabled)
    if plugin is None:
        from nanobot.channels.registry import load_channel_plugin

        plugin = load_channel_plugin(name)
    return bool(channel_instance_specs(plugin, section, enabled_only=True))


def channel_config_snapshot(
    section: Any,
    name: str,
    spec: ChannelSetupSpec | None,
) -> tuple[dict[str, str], list[str]]:
    """Return the safe, non-secret Settings snapshot for one channel section."""
    if hasattr(section, "model_dump"):
        section = section.model_dump(mode="json", by_alias=True)
    if not isinstance(section, dict) or spec is None:
        return {}, []

    values: dict[str, str] = {}
    configured_fields: list[str] = []
    for field in spec.snapshot_fields:
        value = channel_field_value(section, field)
        if not channel_value_present(value):
            continue
        key = f"channels.{name}.{field}"
        configured_fields.append(key)
        if field not in spec.secrets:
            values[key] = stringify_channel_value(value)
    return values, configured_fields


def channel_configured(
    config: Config,
    name: str,
    spec: ChannelSetupSpec | None = None,
    plugin: ChannelPlugin | None = None,
    *,
    default_enabled: bool | None = None,
) -> bool:
    """Return whether a channel has enough saved setup to be enabled directly."""
    section = getattr(config.channels, name, None)
    if plugin is None:
        from nanobot.channels.registry import load_channel_plugin

        plugin = load_channel_plugin(name)

    if channel_local_state_present(plugin, section):
        return True
    if section is None:
        return False

    if plugin.management.multi_instance:
        return any(
            bool(spec and spec.is_configured(instance.config))
            for instance in channel_instance_specs(plugin, section, enabled_only=False)
        )

    if not spec or not spec.required:
        return channel_enabled(
            config,
            name,
            plugin,
            default_enabled=default_enabled,
        )
    return spec.is_configured(section)


def channel_settings_details(config: Config, plugin: ChannelPlugin) -> dict[str, Any]:
    """Project channel-owned configuration and presentation facts for Settings."""
    name = plugin.name
    section = getattr(config.channels, name, None)
    setup_spec = channel_setup_spec(name, plugin=plugin)
    details: dict[str, Any] = {
        "capabilities": sorted(plugin.capabilities),
        "settings_visible": plugin.settings_visible,
        "enabled": channel_enabled(
            config,
            name,
            plugin,
            default_enabled=plugin.default_enabled,
        ),
        "configured": channel_configured(
            config,
            name,
            setup_spec,
            plugin,
            default_enabled=plugin.default_enabled,
        ),
    }
    if plugin.webui is not None:
        details["webui"] = plugin.webui
    if setup_spec is not None:
        details["setup"] = setup_spec.to_public_dict(name)
    config_values, configured_fields = channel_config_snapshot(section, name, setup_spec)
    if config_values:
        details["config_values"] = config_values
    if configured_fields:
        details["configured_fields"] = configured_fields
    instances = channel_feature_instances(plugin, section, setup_spec=setup_spec)
    if instances is not None:
        details["instances"] = instances
    return details


__all__ = [
    "channel_config_snapshot",
    "channel_configured",
    "channel_enabled",
    "channel_settings_details",
]
