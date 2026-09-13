"""Project installed Agent Plugins into the extension registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
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

    def __init__(
        self,
        workspace: Path,
        *,
        owned_plugin_names: Callable[[], frozenset[str]] | None = None,
        mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self._workspace = workspace
        self._owned_plugin_names = owned_plugin_names
        self._mcp_runtime_status = mcp_runtime_status

    def snapshot(self) -> ExtensionAdapterSnapshot:
        """Return marker intent together with the MCP owner's runtime truth."""
        packages: list[ExtensionPackageDescriptor] = []
        owned = self._owned_names()
        plugins = tuple(discover_agent_plugins(self._workspace))
        statuses, runtime_diagnostic = self._mcp_statuses(
            query=any(plugin.mcp_servers for plugin in plugins)
        )
        for plugin in plugins:
            if self._managed_elsewhere(plugin, owned):
                continue
            try:
                packages.append(self._package(plugin, statuses))
            except Exception:
                packages.append(self._failed_package(plugin))
        diagnostics = (runtime_diagnostic,) if runtime_diagnostic is not None else ()
        return ExtensionAdapterSnapshot(
            adapter_name=self.name,
            packages=tuple(packages),
            diagnostics=diagnostics,
        )

    def _owned_names(self) -> frozenset[str]:
        if self._owned_plugin_names is None:
            return frozenset()
        try:
            return frozenset(self._owned_plugin_names())
        except Exception:
            return frozenset()

    @staticmethod
    def _managed_elsewhere(plugin: AgentPlugin, owned: frozenset[str]) -> bool:
        """Skip roots another manager generated; that manager publishes them itself.

        A generated root is a real Agent Plugin, but it is not an independently
        installed one, and listing it here would give one extension two packages an
        operator could act on from unrelated pages.

        Ownership comes from the generating manager's own durable inventory, which
        names exactly one root per installed item. It is deliberately not a marker
        inside the manifest: those bytes are part of the package fingerprint that
        binds plugin enablement, so writing one would perturb a value whose failure
        mode is a Skill that silently stops loading. It is equally not a name
        prefix, which any operator can choose.
        """
        return plugin.name in owned

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

    def _package(
        self,
        plugin: AgentPlugin,
        statuses: Mapping[str, object],
    ) -> ExtensionPackageDescriptor:
        # ``discover_agent_plugins`` is the single owner of manifest, component,
        # marker, and fingerprint validation. The descriptor deliberately projects
        # only its safe fields, never package/process/storage details.
        package_id = extension_package_id(ExtensionSource.AGENT_PLUGIN, plugin.name)
        skill_lifecycle = (
            ExtensionLifecycle.ENABLED if plugin.enabled else ExtensionLifecycle.DISABLED
        )
        mcp_lifecycles = tuple(
            self._mcp_lifecycle(
                plugin.enabled,
                statuses.get(self._runtime_server_name(plugin, server_name)),
            )
            for server_name in plugin.mcp_servers
        )
        lifecycle = self._package_lifecycle(skill_lifecycle, mcp_lifecycles)
        has_mcp_servers = bool(plugin.mcp_servers)
        execution = ExtensionExecution.CHILD_PROCESS if has_mcp_servers else ExtensionExecution.DATA
        components = tuple(
            self._component(
                package_id,
                ExtensionComponentKind.SKILL,
                skill_name,
                lifecycle=skill_lifecycle,
                execution=ExtensionExecution.DATA,
            )
            for skill_name in plugin.skills
        ) + tuple(
            self._component(
                package_id,
                ExtensionComponentKind.MCP_SERVER,
                server_name,
                lifecycle=mcp_lifecycle,
                execution=ExtensionExecution.CHILD_PROCESS,
            )
            for server_name, mcp_lifecycle in zip(plugin.mcp_servers, mcp_lifecycles, strict=True)
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
        lifecycle = ExtensionLifecycle.FAILED if plugin.enabled else ExtensionLifecycle.DISABLED
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

    def _mcp_statuses(
        self,
        *,
        query: bool,
    ) -> tuple[Mapping[str, object], ExtensionDiagnostic | None]:
        if not query or self._mcp_runtime_status is None:
            return {}, None
        try:
            statuses = self._mcp_runtime_status()
        except Exception:
            return {}, ExtensionDiagnostic(
                owner_id=self.name,
                code="agent_plugin_mcp_runtime_status_unavailable",
                message="Agent Plugin MCP runtime status is unavailable.",
            )
        return statuses, None

    @staticmethod
    def _runtime_server_name(plugin: AgentPlugin, server_name: str) -> str:
        return plugin.name if len(plugin.mcp_servers) == 1 else f"{plugin.name}--{server_name}"

    @staticmethod
    def _mcp_lifecycle(enabled: bool, status: object) -> ExtensionLifecycle:
        if not enabled:
            return ExtensionLifecycle.DISABLED
        if not isinstance(status, str):
            return ExtensionLifecycle.UNAVAILABLE
        return {
            "connected": ExtensionLifecycle.ENABLED,
            "connecting": ExtensionLifecycle.RELOADING,
            "failed": ExtensionLifecycle.FAILED,
        }.get(status, ExtensionLifecycle.UNAVAILABLE)

    @staticmethod
    def _package_lifecycle(
        skill_lifecycle: ExtensionLifecycle,
        mcp_lifecycles: tuple[ExtensionLifecycle, ...],
    ) -> ExtensionLifecycle:
        if not mcp_lifecycles or all(state is ExtensionLifecycle.DISABLED for state in mcp_lifecycles):
            return skill_lifecycle
        if ExtensionLifecycle.FAILED in mcp_lifecycles:
            return ExtensionLifecycle.FAILED
        if ExtensionLifecycle.UNAVAILABLE in mcp_lifecycles:
            return ExtensionLifecycle.UNAVAILABLE
        if ExtensionLifecycle.RELOADING in mcp_lifecycles:
            return ExtensionLifecycle.RELOADING
        return ExtensionLifecycle.ENABLED

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
