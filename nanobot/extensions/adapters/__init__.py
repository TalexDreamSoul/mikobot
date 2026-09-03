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
from nanobot.extensions.adapters.optional_features import (
    OptionalFeatureExtensionAdapter,
    OptionalFeatureExtensionServices,
)
from nanobot.extensions.adapters.tools import CoreToolsExtensionAdapter

__all__ = [
    "AgentPluginExtensionAdapter",
    "ChannelExtensionAdapter",
    "ChannelExtensionServices",
    "ConfiguredMcpExtensionAdapter",
    "CoreToolsExtensionAdapter",
    "EffectiveSkillsExtensionAdapter",
    "OptionalFeatureExtensionAdapter",
    "OptionalFeatureExtensionServices",
    "canonical_extension_name",
    "safe_extension_label",
]
