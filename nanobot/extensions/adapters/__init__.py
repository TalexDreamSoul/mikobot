"""Family adapters for the canonical extension registry."""

from nanobot.extensions.adapters.agent_plugins import AgentPluginExtensionAdapter
from nanobot.extensions.adapters.capabilities import (
    ConfiguredMcpExtensionAdapter,
    EffectiveSkillsExtensionAdapter,
)
from nanobot.extensions.adapters.channels import (
    ChannelExtensionAdapter,
    ChannelExtensionServices,
)
from nanobot.extensions.adapters.common import canonical_extension_name, safe_extension_label
from nanobot.extensions.adapters.hooks import (
    HookDeclaration,
    HookExtensionAdapter,
    LongLivedHooks,
    RegisteredHook,
    RegisteredHookFactory,
    external_hook,
    external_hook_factory,
    long_lived_hooks,
    registered_hook,
    registered_hook_factory,
)
from nanobot.extensions.adapters.optional_features import (
    OptionalFeatureExtensionAdapter,
    OptionalFeatureExtensionServices,
)
from nanobot.extensions.adapters.providers import (
    ProviderExtensionServices,
    ProviderRegistryExtensionAdapter,
)
from nanobot.extensions.adapters.tools import CoreToolsExtensionAdapter

__all__ = [
    "AgentPluginExtensionAdapter",
    "ChannelExtensionAdapter",
    "ChannelExtensionServices",
    "ConfiguredMcpExtensionAdapter",
    "CoreToolsExtensionAdapter",
    "EffectiveSkillsExtensionAdapter",
    "HookDeclaration",
    "HookExtensionAdapter",
    "LongLivedHooks",
    "OptionalFeatureExtensionAdapter",
    "OptionalFeatureExtensionServices",
    "ProviderExtensionServices",
    "ProviderRegistryExtensionAdapter",
    "RegisteredHook",
    "RegisteredHookFactory",
    "canonical_extension_name",
    "external_hook",
    "external_hook_factory",
    "long_lived_hooks",
    "registered_hook",
    "registered_hook_factory",
    "safe_extension_label",
]
