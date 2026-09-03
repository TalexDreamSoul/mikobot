"""Canonical extension inventory and lifecycle contracts."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.extensions.runtime import build_core_extension_registry


from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapter,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionConfigurationTarget,
    ExtensionDiagnostic,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSnapshot,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
    requires_risk_acknowledgement,
    safe_extension_message,
)
from nanobot.extensions.registry import ExtensionRegistry, ExtensionRegistryError

__all__ = [
    "ExtensionAction",
    "ExtensionActionContext",
    "ExtensionActionRequest",
    "ExtensionActionResult",
    "ExtensionAdapter",
    "ExtensionAdapterSnapshot",
    "ExtensionComponentDescriptor",
    "ExtensionComponentKind",
    "ExtensionConfigurationTarget",
    "ExtensionDiagnostic",
    "ExtensionExecution",
    "ExtensionLifecycle",
    "ExtensionPackageDescriptor",
    "ExtensionRegistry",
    "ExtensionRegistryError",
    "ExtensionSnapshot",
    "ExtensionSource",
    "ExtensionTrust",
    "build_core_extension_registry",
    "extension_component_id",
    "extension_package_id",
    "requires_risk_acknowledgement",
    "safe_extension_message",
]


def __getattr__(name: str) -> object:
    """Lazily expose runtime composition without importing tool dependencies."""
    if name == "build_core_extension_registry":
        from nanobot.extensions.runtime import build_core_extension_registry

        return build_core_extension_registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
