"""Explicit composition of core runtime extension adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import Config
from nanobot.extensions.adapters import (
    AgentPluginExtensionAdapter,
    ChannelExtensionAdapter,
    ChannelExtensionServices,
    CliAppExtensionAdapter,
    CliAppOwner,
    ConfiguredMcpExtensionAdapter,
    CoreToolsExtensionAdapter,
    EffectiveSkillsExtensionAdapter,
    HookExtensionAdapter,
    LongLivedHooks,
    OptionalFeatureExtensionAdapter,
    OptionalFeatureExtensionServices,
    ProviderExtensionServices,
    ProviderRegistryExtensionAdapter,
)
from nanobot.extensions.adapters.providers import OAuthStatusReader
from nanobot.extensions.registry import ExtensionRegistry


def runtime_skills_loader(runtime: object) -> SkillsLoader | None:
    """Return a real runtime SkillsLoader without requiring it from test doubles."""
    context = getattr(runtime, "context", None)
    loader = getattr(context, "skills", None)
    return loader if isinstance(loader, SkillsLoader) else None


def _default_cli_app_owner(config: Config) -> CliAppOwner:
    """Build the CLI app manager lazily, at the first snapshot rather than at wiring.

    Constructing one creates its runtime data directory, so deferring it keeps
    building a registry free of filesystem side effects.
    """
    from nanobot.apps.cli import CliAppManager, CliAppsRuntimeConfig

    cli_config = config.tools.cli_apps
    return CliAppManager(
        workspace=config.workspace_path,
        runtime=CliAppsRuntimeConfig(
            install_timeout=cli_config.install_timeout,
            run_timeout=cli_config.run_timeout,
            catalog_ttl_seconds=cli_config.catalog_ttl_seconds,
        ),
    )


def build_core_extension_registry(
    config: Config,
    tools: ToolRegistry,
    *,
    skills_loader: SkillsLoader | None = None,
    config_loader: Callable[[], Config] | None = None,
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    channel_runtime_status: Callable[[], Mapping[str, Mapping[str, object]]] | None = None,
    channel_services: ChannelExtensionServices | None = None,
    optional_feature_services: OptionalFeatureExtensionServices | None = None,
    provider_oauth_status: OAuthStatusReader | None = None,
    provider_services: ProviderExtensionServices | None = None,
    cli_app_owner: Callable[[], CliAppOwner] | None = None,
    hooks: LongLivedHooks | None = None,
) -> ExtensionRegistry:
    """Compose the current runtime's core extension inventory without global state."""
    if skills_loader is None:
        skills_loader = SkillsLoader(
            config.workspace_path,
            disabled_skills=set(config.agents.defaults.disabled_skills),
        )
    if config_loader is None:
        def current_config_loader() -> Config:
            return config

        config_loader = current_config_loader
    if cli_app_owner is None:
        def default_cli_app_owner() -> CliAppOwner:
            return _default_cli_app_owner(config)

        cli_app_owner = default_cli_app_owner

    # One installed CLI app is one extension. The CLI app adapter owns its executable
    # and its generated Skill, and tells the Agent Plugin adapter which generated
    # roots it already publishes so that one app never becomes two packages.
    cli_apps = CliAppExtensionAdapter(cli_app_owner)

    registry = ExtensionRegistry()
    registry.register(CoreToolsExtensionAdapter(tools))
    registry.register(
        AgentPluginExtensionAdapter(
            config.workspace_path,
            owned_plugin_names=cli_apps.owned_plugin_names,
        )
    )
    registry.register(EffectiveSkillsExtensionAdapter(skills_loader))
    registry.register(
        ConfiguredMcpExtensionAdapter(
            config_loader,
            runtime_status=mcp_runtime_status,
        )
    )
    registry.register(
        ChannelExtensionAdapter(
            config_loader,
            runtime_status=channel_runtime_status,
            services=channel_services,
        )
    )
    registry.register(OptionalFeatureExtensionAdapter(services=optional_feature_services))
    registry.register(
        ProviderRegistryExtensionAdapter(
            config_loader,
            oauth_status=provider_oauth_status,
            services=provider_services,
        )
    )
    registry.register(cli_apps)
    if hooks is not None:
        registry.register(HookExtensionAdapter(hooks))
    return registry


__all__ = ["build_core_extension_registry", "runtime_skills_loader"]
