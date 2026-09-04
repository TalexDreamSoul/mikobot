"""Path-scoped registry composition for local extension management."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from filelock import FileLock
from loguru import logger

from nanobot.config.loader import load_config, save_config, set_config_path
from nanobot.extensions.adapters import (
    ChannelExtensionAdapter,
    ChannelExtensionServices,
    CliAppExtensionAdapter,
    CliAppOwner,
    OptionalFeatureExtensionAdapter,
    OptionalFeatureExtensionServices,
)
from nanobot.extensions.registry import ExtensionRegistry

if TYPE_CHECKING:
    from nanobot.config.schema import Config


def build_management_extension_registry(config_path: Path) -> ExtensionRegistry:
    """Build a dependency-light registry bound to one config file.

    Local CLI actions persist desired state but cannot control an already-running
    process, so successful channel runtime actions truthfully require restart.
    """
    path = config_path.expanduser().resolve(strict=False)
    set_config_path(path)

    def load_current_config() -> Config:
        return load_config(path)

    def mutate_config(mutation: Callable[[Config], object]) -> object:
        lock = FileLock(str(path.with_suffix(f"{path.suffix}.lock")))
        with lock:
            config = load_config(path)
            result = mutation(config)
            save_config(config, path)
            return result

    async def acknowledge_runtime_action(
        _action: str, _channel_type: str, _instance_id: str
    ) -> dict[str, bool]:
        return {"handled": True, "ok": True, "requires_restart": True}

    def refresh_channel_metadata(channel_type: str, instance_id: str) -> None:
        try:
            from nanobot.channels.contracts import refresh_channel_feature_metadata
            from nanobot.channels.registry import load_channel_plugin

            plugin = load_channel_plugin(channel_type)
            refresh_channel_feature_metadata(
                plugin.load_channel_class(), path, instance_id=instance_id
            )
        except Exception:
            logger.warning("Channel metadata refresh failed after enablement")

    def load_current_cli_app_owner() -> CliAppOwner:
        """Bind the CLI app manager to this config file, built at first use.

        Unlike a channel runtime, a CLI app's state is entirely on disk and every
        reader re-reads it per invocation, so a local action here needs no running
        process and truthfully requires no restart.
        """
        from nanobot.apps.cli import CliAppManager, CliAppsRuntimeConfig

        current = load_config(path)
        cli_config = current.tools.cli_apps
        return CliAppManager(
            workspace=current.workspace_path,
            runtime=CliAppsRuntimeConfig(
                install_timeout=cli_config.install_timeout,
                run_timeout=cli_config.run_timeout,
                catalog_ttl_seconds=cli_config.catalog_ttl_seconds,
            ),
        )

    registry = ExtensionRegistry()
    registry.register(
        ChannelExtensionAdapter(
            load_current_config,
            services=ChannelExtensionServices(
                mutate_config=mutate_config,
                runtime_action=acknowledge_runtime_action,
                refresh_metadata=refresh_channel_metadata,
            ),
        )
    )
    registry.register(OptionalFeatureExtensionAdapter(services=OptionalFeatureExtensionServices()))
    registry.register(CliAppExtensionAdapter(load_current_cli_app_owner))
    return registry


__all__ = ["build_management_extension_registry"]
