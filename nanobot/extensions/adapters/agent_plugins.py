"""Project installed Agent Plugins into the extension registry."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.plugins import AgentPlugin, discover_agent_plugins, set_agent_plugin_enabled
from nanobot.extensions.adapters.common import canonical_extension_name, safe_extension_label
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionDiagnostic,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)


class AgentPluginExtensionAdapter:
    """Expose Agent Plugin packages while leaving their runtime owner unchanged."""

    name = "agent-plugins"

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def snapshot(self) -> ExtensionAdapterSnapshot:
        """Return every plugin's independent, marker-validated projection."""
        packages: list[ExtensionPackageDescriptor] = []
        for plugin in discover_agent_plugins(self._workspace):
            try:
                packages.append(self._package(plugin))
            except Exception:
                packages.append(self._failed_package(plugin))
        return ExtensionAdapterSnapshot(adapter_name=self.name, packages=tuple(packages))

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        """Mutate only the package's existing fingerprint-bound activation marker."""
        plugin_name = self._plugin_name_from_target(request.target_id)
        if request.action not in {ExtensionAction.ENABLE, ExtensionAction.DISABLE}:
            raise ValueError("Agent Plugin supports only enable and disable actions")

        enabled = request.action is ExtensionAction.ENABLE
        if enabled:
            set_agent_plugin_enabled(
                self._workspace,
                plugin_name,
                True,
                expected_revision=request.expected_revision,
            )
        else:
            set_agent_plugin_enabled(self._workspace, plugin_name, False)

        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=request.target_id,
            target_id=request.target_id,
            lifecycle=ExtensionLifecycle.ENABLED if enabled else ExtensionLifecycle.DISABLED,
        )

    def _package(self, plugin: AgentPlugin) -> ExtensionPackageDescriptor:
        # ``discover_agent_plugins`` is the single owner of manifest, component,
        # marker, and fingerprint validation. The descriptor deliberately projects
        # only its safe fields, never package/process/storage details.
        package_id = extension_package_id(ExtensionSource.AGENT_PLUGIN, plugin.name)
        lifecycle = ExtensionLifecycle.ENABLED if plugin.enabled else ExtensionLifecycle.DISABLED
        has_mcp_servers = bool(plugin.mcp_servers)
        execution = ExtensionExecution.CHILD_PROCESS if has_mcp_servers else ExtensionExecution.DATA
        components = tuple(
            self._component(
                package_id,
                ExtensionComponentKind.SKILL,
                skill_name,
                lifecycle=lifecycle,
                execution=ExtensionExecution.DATA,
            )
            for skill_name in plugin.skills
        ) + tuple(
            self._component(
                package_id,
                ExtensionComponentKind.MCP_SERVER,
                server_name,
                lifecycle=lifecycle,
                execution=ExtensionExecution.CHILD_PROCESS,
            )
            for server_name in plugin.mcp_servers
        )
        return ExtensionPackageDescriptor(
            id=package_id,
            name=plugin.name,
            display_name=safe_extension_label(plugin.display_name, fallback=plugin.name),
            source=ExtensionSource.AGENT_PLUGIN,
            trust=ExtensionTrust.OPERATOR_TRUSTED,
            execution=execution,
            lifecycle=lifecycle,
            description=safe_extension_label(plugin.description, fallback=""),
            revision=plugin.revision,
            isolated=False,
            permissions=tuple(
                safe_extension_label(permission, fallback="declared-permission")
                for permission in plugin.permissions
            ),
            permissions_enforced=False,
            actions=frozenset({ExtensionAction.ENABLE, ExtensionAction.DISABLE}),
            components=components,
        )

    @staticmethod
    def _failed_package(plugin: AgentPlugin) -> ExtensionPackageDescriptor:
        """Retain one safe package diagnostic when a local projection is malformed."""
        package_id = extension_package_id(ExtensionSource.AGENT_PLUGIN, plugin.name)
        execution = (
            ExtensionExecution.CHILD_PROCESS
            if plugin.mcp_servers
            else ExtensionExecution.DATA
        )
        lifecycle = ExtensionLifecycle.ENABLED if plugin.enabled else ExtensionLifecycle.DISABLED
        return ExtensionPackageDescriptor(
            id=package_id,
            name=plugin.name,
            display_name=safe_extension_label(plugin.display_name, fallback=plugin.name),
            source=ExtensionSource.AGENT_PLUGIN,
            trust=ExtensionTrust.OPERATOR_TRUSTED,
            execution=execution,
            lifecycle=lifecycle,
            revision=plugin.revision,
            isolated=False,
            permissions_enforced=False,
            actions=frozenset({ExtensionAction.ENABLE, ExtensionAction.DISABLE}),
            diagnostic=ExtensionDiagnostic(
                owner_id=package_id,
                code="projection_failed",
                message="Agent Plugin metadata could not be projected.",
            ),
        )

    @staticmethod
    def _component(
        package_id: str,
        kind: ExtensionComponentKind,
        raw_name: str,
        *,
        lifecycle: ExtensionLifecycle,
        execution: ExtensionExecution,
    ) -> ExtensionComponentDescriptor:
        name = canonical_extension_name(raw_name, fallback=kind.value)
        return ExtensionComponentDescriptor(
            id=extension_component_id(package_id, kind, name),
            package_id=package_id,
            kind=kind,
            name=name,
            display_name=safe_extension_label(raw_name, fallback=name),
            execution=execution,
            lifecycle=lifecycle,
        )

    @staticmethod
    def _plugin_name_from_target(target_id: str) -> str:
        prefix = f"ext:{ExtensionSource.AGENT_PLUGIN.value}:"
        if not target_id.startswith(prefix):
            raise ValueError("Agent Plugin action target must be an Agent Plugin package")
        name = target_id.removeprefix(prefix)
        if "/" in name or extension_package_id(ExtensionSource.AGENT_PLUGIN, name) != target_id:
            raise ValueError("Agent Plugin action target must be a canonical package ID")
        return name


__all__ = ["AgentPluginExtensionAdapter"]
