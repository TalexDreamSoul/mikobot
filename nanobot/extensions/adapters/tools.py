"""Project live core tool registrations into the extension registry."""

from __future__ import annotations

from nanobot.agent.tools.registry import RegisteredTool, ToolRegistry
from nanobot.extensions.adapters.common import canonical_extension_name, safe_extension_label
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)


class CoreToolsExtensionAdapter:
    """Expose only live provenance captured by the main core tool loader."""

    name = "core-tools"

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def snapshot(self) -> ExtensionAdapterSnapshot:
        """Return packages for currently live built-in and entry-point tools."""
        groups: dict[tuple[ExtensionSource, str], list[RegisteredTool]] = {}
        for row in self._registry.registration_snapshot():
            metadata = row.metadata
            owner_name = (
                "nanobot-tools"
                if metadata.source is ExtensionSource.BUILTIN
                else canonical_extension_name(metadata.owner_name, fallback="tool-package")
            )
            groups.setdefault((metadata.source, owner_name), []).append(row)

        packages = tuple(
            self._package(source, owner_name, rows)
            for (source, owner_name), rows in groups.items()
        )
        return ExtensionAdapterSnapshot(adapter_name=self.name, packages=packages)

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        del request
        raise ValueError("core tools do not support lifecycle actions")

    @staticmethod
    def _package(
        source: ExtensionSource,
        owner_name: str,
        rows: list[RegisteredTool],
    ) -> ExtensionPackageDescriptor:
        package_id = extension_package_id(source, owner_name)
        external = source is ExtensionSource.PYTHON_ENTRY_POINT
        lifecycle = ExtensionLifecycle.ENABLED
        components = tuple(
            CoreToolsExtensionAdapter._component(package_id, row, lifecycle=lifecycle)
            for row in rows
        )
        return ExtensionPackageDescriptor(
            id=package_id,
            name=owner_name,
            display_name=(
                safe_extension_label(rows[0].metadata.owner_name, fallback=owner_name)
                if external
                else "Nanobot Tools"
            ),
            source=source,
            trust=ExtensionTrust.OPERATOR_TRUSTED if external else ExtensionTrust.FIRST_PARTY,
            execution=ExtensionExecution.IN_PROCESS,
            lifecycle=lifecycle,
            isolated=False if external else None,
            permissions_enforced=False,
            actions=frozenset({ExtensionAction.RESTART_REQUIRED}) if external else frozenset(),
            components=components,
        )

    @staticmethod
    def _component(
        package_id: str,
        row: RegisteredTool,
        *,
        lifecycle: ExtensionLifecycle,
    ) -> ExtensionComponentDescriptor:
        name = canonical_extension_name(row.name, fallback="tool")
        return ExtensionComponentDescriptor(
            id=extension_component_id(package_id, ExtensionComponentKind.TOOL, name),
            package_id=package_id,
            kind=ExtensionComponentKind.TOOL,
            name=name,
            display_name=safe_extension_label(row.name, fallback=name),
            execution=ExtensionExecution.IN_PROCESS,
            lifecycle=lifecycle,
        )


__all__ = ["CoreToolsExtensionAdapter"]
