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

    registry = ExtensionRegistry()
    registry.register(CoreToolsExtensionAdapter(tools))
    registry.register(AgentPluginExtensionAdapter(config.workspace_path))
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
    if hooks is not None:
        registry.register(HookExtensionAdapter(hooks))
    return registry


__all__ = ["build_core_extension_registry", "runtime_skills_loader"]
