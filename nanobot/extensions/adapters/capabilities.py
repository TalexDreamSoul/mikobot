"""Read-only extension projections for effective Skills and configured MCP servers."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from nanobot.agent.skills import SkillsLoader
from nanobot.config.schema import Config
from nanobot.extensions.adapters.common import canonical_extension_name, safe_extension_label
from nanobot.extensions.contracts import (
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionConfigurationTarget,
    ExtensionDiagnostic,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)

_MAX_SKILL_BYTES = 1 << 20
_MCP_CONFIGURATION = ExtensionConfigurationTarget(section="apps", item="mcp")


def _skill_revision(path: object) -> tuple[str | None, str | None]:
    """Hash at most one MiB of a selected Skill file without retaining its content."""
    if not isinstance(path, (str, os.PathLike)):
        return None, "skill_revision_unavailable"
    skill_path = Path(cast(str | os.PathLike[str], path))
    try:
        with skill_path.open("rb") as skill_file:
            content = skill_file.read(_MAX_SKILL_BYTES + 1)
    except (OSError, TypeError, ValueError):
        return None, "skill_revision_unavailable"
    if len(content) > _MAX_SKILL_BYTES:
        return None, "skill_revision_oversized"
    return hashlib.sha256(content).hexdigest(), None


def _mcp_transport(server: object) -> str:
    """Resolve transport with the same precedence used by MCP connection setup."""
    configured_type = getattr(server, "type", None)
    if configured_type:
        return str(configured_type)
    command = getattr(server, "command", "")
    if command:
        return "stdio"
    url = getattr(server, "url", "")
    if isinstance(url, str) and url:
        return "sse" if url.rstrip("/").endswith("/sse") else "streamableHttp"
    return "stdio"


def _mapping_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    mapping = cast(Mapping[object, object], value)
    return tuple(sorted(str(key) for key in mapping))


def _enabled_tool_names(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        return (str(value),)
    if not isinstance(value, Iterable):
        return ()
    return tuple(sorted(str(name) for name in cast(Iterable[object], value)))


def _safe_url_origin(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        return "", ""
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower(), (parsed.hostname or "").lower()
    except ValueError:
        return "", ""


def _mcp_revision(server: object, transport: str) -> str:
    """Hash only structural server facts; configuration values never enter the digest."""
    url_scheme, url_host = _safe_url_origin(getattr(server, "url", ""))
    signature: dict[str, object] = {
        "transport": transport,
        "auth": getattr(server, "auth", None),
        "command_configured": bool(getattr(server, "command", "")),
        "args_configured": bool(getattr(server, "args", ())),
        "environment_keys": _mapping_keys(getattr(server, "env", {})),
        "working_directory_configured": bool(getattr(server, "cwd", "")),
        "url_scheme": url_scheme,
        "url_host": url_host,
        "header_keys": _mapping_keys(getattr(server, "headers", {})),
        "enabled_tools": _enabled_tool_names(getattr(server, "enabled_tools", ())),
        "tool_timeout": getattr(server, "tool_timeout", None),
    }
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mcp_lifecycle(status: object) -> ExtensionLifecycle:
    if not isinstance(status, str):
        return ExtensionLifecycle.DISCOVERED
    return {
        "connected": ExtensionLifecycle.ENABLED,
        "connecting": ExtensionLifecycle.RELOADING,
        "failed": ExtensionLifecycle.FAILED,
    }.get(status, ExtensionLifecycle.DISCOVERED)


class EffectiveSkillsExtensionAdapter:
    """Project the SkillsLoader's already-resolved standalone Skill inventory."""

    name = "effective-skills"

    def __init__(self, loader: SkillsLoader):
        self._loader = loader

    def snapshot(self) -> ExtensionAdapterSnapshot:
        packages: list[ExtensionPackageDescriptor] = []
        diagnostics: list[ExtensionDiagnostic] = []
        for skill in self._loader.list_skills(filter_unavailable=False):
            source = skill.get("source")
            if source == "plugin":
                continue
            if source == "workspace":
                extension_source = ExtensionSource.WORKSPACE
                trust = ExtensionTrust.WORKSPACE_CONTENT
            elif source == "builtin":
                extension_source = ExtensionSource.BUILTIN
                trust = ExtensionTrust.FIRST_PARTY
            else:
                continue

            raw_name = skill.get("name")
            if not isinstance(raw_name, str):
                continue
            name = canonical_extension_name(raw_name, fallback="skill")
            package_id = extension_package_id(extension_source, name)
            revision, diagnostic_code = _skill_revision(skill.get("path"))
            if diagnostic_code is not None:
                diagnostics.append(
                    ExtensionDiagnostic(
                        owner_id=self.name,
                        code=diagnostic_code,
                        message="Skill revision is unavailable.",
                    )
                )
            display_name = safe_extension_label(raw_name, fallback="skill")
            component = ExtensionComponentDescriptor(
                id=extension_component_id(package_id, ExtensionComponentKind.SKILL, name),
                package_id=package_id,
                kind=ExtensionComponentKind.SKILL,
                name=name,
                display_name=display_name,
                execution=ExtensionExecution.DATA,
                lifecycle=ExtensionLifecycle.ENABLED,
            )
            packages.append(
                ExtensionPackageDescriptor(
                    id=package_id,
                    name=name,
                    display_name=display_name,
                    source=extension_source,
                    trust=trust,
                    execution=ExtensionExecution.DATA,
                    lifecycle=ExtensionLifecycle.ENABLED,
                    revision=revision,
                    permissions_enforced=False,
                    components=(component,),
                )
            )
        return ExtensionAdapterSnapshot(
            adapter_name=self.name,
            packages=tuple(packages),
            diagnostics=tuple(diagnostics),
        )

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        del request
        raise ValueError("effective Skills do not support lifecycle actions")


class ConfiguredMcpExtensionAdapter:
    """Project configured MCP declarations without touching MCP runtime ownership."""

    name = "configured-mcp"

    def __init__(
        self,
        config_loader: Callable[[], Config],
        runtime_status: Callable[[], Mapping[str, str]] | None = None,
    ):
        self._config_loader = config_loader
        self._runtime_status = runtime_status

    def snapshot(self) -> ExtensionAdapterSnapshot:
        config = self._config_loader()
        statuses: Mapping[str, str] = (
            self._runtime_status() if self._runtime_status is not None else {}
        )
        packages: list[ExtensionPackageDescriptor] = []
        for raw_name, server in config.tools.mcp_servers.items():
            name = canonical_extension_name(raw_name, fallback="mcp")
            package_id = extension_package_id(ExtensionSource.CONFIGURED, name)
            transport = _mcp_transport(server)
            remote = transport in {"sse", "streamableHttp"}
            trust = ExtensionTrust.REMOTE_SERVICE if remote else ExtensionTrust.OPERATOR_TRUSTED
            execution = ExtensionExecution.REMOTE if remote else ExtensionExecution.CHILD_PROCESS
            lifecycle = _mcp_lifecycle(statuses.get(raw_name))
            display_name = safe_extension_label(raw_name, fallback="mcp")
            component = ExtensionComponentDescriptor(
                id=extension_component_id(package_id, ExtensionComponentKind.MCP_SERVER, name),
                package_id=package_id,
                kind=ExtensionComponentKind.MCP_SERVER,
                name=name,
                display_name=display_name,
                execution=execution,
                lifecycle=lifecycle,
                configuration=_MCP_CONFIGURATION,
            )
            packages.append(
                ExtensionPackageDescriptor(
                    id=package_id,
                    name=name,
                    display_name=display_name,
                    source=ExtensionSource.CONFIGURED,
                    trust=trust,
                    execution=execution,
                    lifecycle=lifecycle,
                    revision=_mcp_revision(server, transport),
                    isolated=False if not remote else None,
                    permissions_enforced=False,
                    configuration=_MCP_CONFIGURATION,
                    components=(component,),
                )
            )
        return ExtensionAdapterSnapshot(adapter_name=self.name, packages=tuple(packages))

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        del request
        raise ValueError("configured MCP servers do not support lifecycle actions")


__all__ = ["ConfiguredMcpExtensionAdapter", "EffectiveSkillsExtensionAdapter"]
