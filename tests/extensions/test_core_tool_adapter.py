from __future__ import annotations

from unittest.mock import patch

import pytest

from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import (
    ToolRegistrationMetadata,
    ToolRegistry,
    is_tool_error_result,
)
from nanobot.extensions.adapters.common import canonical_extension_name
from nanobot.extensions.adapters.tools import CoreToolsExtensionAdapter
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionComponentKind,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionSource,
    ExtensionTrust,
    extension_package_id,
)


class _EntryPoint:
    """A deterministic entry point that rejects an accidental second import."""

    def __init__(self, name: str, tool_class: type[Tool]) -> None:
        self.name = name
        self._tool_class = tool_class
        self.loads = 0

    def load(self) -> type[Tool]:
        self.loads += 1
        if self.loads > 1:
            raise AssertionError(f"entry point {self.name} was loaded more than once")
        return self._tool_class


def _tool_class(
    class_name: str,
    tool_name: str,
    *,
    constructions: list[str],
    scopes: set[str] | None = None,
    enabled: bool = True,
    result: str = "ok",
) -> type[Tool]:
    class _FixtureTool(Tool):
        _scopes = {"core"} if scopes is None else scopes

        @property
        def name(self) -> str:
            return tool_name

        @property
        def description(self) -> str:
            return f"Fixture tool {tool_name}"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object"}

        @classmethod
        def enabled(cls, _ctx: ToolContext) -> bool:
            return enabled

        @classmethod
        def create(cls, _ctx: ToolContext) -> Tool:
            constructions.append(class_name)
            return cls()

        async def execute(self, **_kwargs: object) -> str:
            return result

    _FixtureTool.__name__ = class_name
    return _FixtureTool


def _metadata(source: ExtensionSource, owner_name: str) -> ToolRegistrationMetadata:
    return ToolRegistrationMetadata(
        source=source,
        owner_name=owner_name,
        class_name="FixtureTool",
        scope="core",
    )


def _packages_by_id(registry: ToolRegistry) -> dict[str, object]:
    return {
        package.id: package
        for package in CoreToolsExtensionAdapter(registry).snapshot().packages
    }


@pytest.mark.asyncio
async def test_loader_records_only_successful_core_tools_without_reimporting_entry_points(
    tmp_path,
) -> None:
    """Only successfully registered core tools gain live provenance and projection ownership."""
    constructions: list[str] = []
    builtin = _tool_class("BuiltinTool", "builtin_tool", constructions=constructions)
    builtin_collision = _tool_class("BuiltinCollision", "shared_tool", constructions=constructions)
    disabled = _tool_class(
        "DisabledTool", "disabled_tool", constructions=constructions, enabled=False
    )
    subagent_only = _tool_class(
        "SubagentTool", "subagent_tool", constructions=constructions, scopes={"subagent"}
    )
    entry_tool = _tool_class(
        "EntryPointTool", "entry_tool", constructions=constructions, result="Error: plugin failed"
    )
    entry_collision = _tool_class("EntryPointCollision", "shared_tool", constructions=constructions)
    entry_owner = "third party tools!"
    entry_points = (
        _EntryPoint(entry_owner, entry_tool),
        _EntryPoint("collision-owner", entry_collision),
    )

    tools = ToolRegistry()
    with patch("nanobot.agent.tools.loader.entry_points", return_value=entry_points):
        registered = ToolLoader(
            test_classes=[builtin, builtin_collision, disabled, subagent_only]
        ).load(ToolContext(config=None, workspace=str(tmp_path)), tools, scope="core")  # type: ignore[arg-type]

    assert registered == ["builtin_tool", "shared_tool", "entry_tool"]
    assert constructions == ["BuiltinTool", "BuiltinCollision", "EntryPointTool", "EntryPointCollision"]
    assert entry_points[0].loads == 1
    assert entry_points[1].loads == 1

    live_rows = tools.registration_snapshot()
    assert [
        (row.name, row.metadata.source, row.metadata.owner_name, row.metadata.class_name, row.metadata.scope)
        for row in live_rows
    ] == [
        ("builtin_tool", ExtensionSource.BUILTIN, "nanobot-tools", "BuiltinTool", "core"),
        ("shared_tool", ExtensionSource.BUILTIN, "nanobot-tools", "BuiltinCollision", "core"),
        ("entry_tool", ExtensionSource.PYTHON_ENTRY_POINT, entry_owner, "EntryPointTool", "core"),
    ]

    packages = _packages_by_id(tools)
    builtin_package = packages[extension_package_id(ExtensionSource.BUILTIN, "nanobot-tools")]
    entry_package_id = extension_package_id(
        ExtensionSource.PYTHON_ENTRY_POINT, canonical_extension_name(entry_owner)
    )
    entry_package = packages[entry_package_id]

    assert [component.name for component in builtin_package.components] == [
        "builtin_tool",
        "shared_tool",
    ]
    assert [component.kind for component in builtin_package.components] == [
        ExtensionComponentKind.TOOL,
        ExtensionComponentKind.TOOL,
    ]
    assert entry_package.source is ExtensionSource.PYTHON_ENTRY_POINT
    assert entry_package.trust is ExtensionTrust.OPERATOR_TRUSTED
    assert entry_package.execution is ExtensionExecution.IN_PROCESS
    assert entry_package.lifecycle is ExtensionLifecycle.ENABLED
    assert entry_package.isolated is False
    assert entry_package.permissions_enforced is False
    assert ExtensionAction.RESTART_REQUIRED in entry_package.actions
    assert [component.name for component in entry_package.components] == ["entry_tool"]

    result = await tools.execute("entry_tool", {})
    assert is_tool_error_result(result)
    assert isinstance(result, ToolResult)
    assert str(result).startswith("Error: plugin failed")
    assert entry_points[0].loads == 1
    assert entry_points[1].loads == 1


def test_core_adapter_excludes_mcp_and_image_tools_without_live_provenance() -> None:
    """A main registry's core-tool snapshot contains only tools registered with core provenance."""
    tools = ToolRegistry()
    tools.register(
        _tool_class("BuiltinTool", "builtin_tool", constructions=[])(),
        metadata=_metadata(ExtensionSource.BUILTIN, "nanobot-tools"),
    )
    tools.register(_tool_class("McpTool", "mcp_remote", constructions=[])())
    tools.register(_tool_class("ImageTool", "generate_image", constructions=[])())

    packages = _packages_by_id(tools)
    builtin_package = packages[extension_package_id(ExtensionSource.BUILTIN, "nanobot-tools")]

    assert set(packages) == {extension_package_id(ExtensionSource.BUILTIN, "nanobot-tools")}
    assert [component.name for component in builtin_package.components] == ["builtin_tool"]
    assert [row.name for row in tools.registration_snapshot()] == ["builtin_tool"]


def test_core_adapter_removes_overwritten_and_unregistered_tool_provenance() -> None:
    """Replacing or removing a live tool also removes its prior core ownership projection."""
    tools = ToolRegistry()
    tools.register(
        _tool_class("BuiltinTool", "replaceable", constructions=[])(),
        metadata=_metadata(ExtensionSource.BUILTIN, "nanobot-tools"),
    )

    assert set(_packages_by_id(tools)) == {extension_package_id(ExtensionSource.BUILTIN, "nanobot-tools")}

    tools.register(_tool_class("McpReplacement", "replaceable", constructions=[])())
    assert tools.registration_snapshot() == ()
    assert _packages_by_id(tools) == {}

    entry_owner = "installed-owner"
    tools.register(
        _tool_class("EntryPointTool", "replaceable", constructions=[])(),
        metadata=_metadata(ExtensionSource.PYTHON_ENTRY_POINT, entry_owner),
    )
    entry_package_id = extension_package_id(
        ExtensionSource.PYTHON_ENTRY_POINT, canonical_extension_name(entry_owner)
    )
    packages = _packages_by_id(tools)
    assert set(packages) == {entry_package_id}
    assert [component.name for component in packages[entry_package_id].components] == ["replaceable"]

    tools.unregister("replaceable")
    assert tools.registration_snapshot() == ()
    assert _packages_by_id(tools) == {}
