from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.registry import ToolRegistrationMetadata, ToolRegistry
from nanobot.channels.plugin import ChannelPlugin
from nanobot.config.schema import Config
from nanobot.extensions.contracts import (
    ExtensionAdapter,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionLifecycle,
    ExtensionSnapshot,
    ExtensionSource,
    extension_package_id,
)
from nanobot.extensions.registry import ExtensionRegistry
from nanobot.extensions.runtime import build_core_extension_registry


class _LiveTool(Tool):
    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "composition-live"

    @property
    def description(self) -> str:
        return "A core tool retained during channel adapter composition."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object"}

    @classmethod
    def create(cls, _ctx: ToolContext) -> Tool:
        return cls()

    async def execute(self, **_kwargs: object) -> str:
        return "ok"


class _ChannelManager:
    def __init__(self, status: dict[str, dict[str, object]]) -> None:
        self._status = status
        self.status_calls = 0

    def get_status(self) -> dict[str, dict[str, object]]:
        self.status_calls += 1
        return self._status


def _config(workspace: Path, *, channel_enabled: bool) -> Config:
    return Config.model_validate(
        {
            "agents": {"defaults": {"workspace": str(workspace)}},
            "channels": {"demo": {"enabled": channel_enabled}},
        }
    )


def _channel_plugin() -> ChannelPlugin:
    return ChannelPlugin(
        name="demo",
        display_name="Demo",
        runtime="missing_channel_sdk.runtime:Channel",
    )


def _channel_component(snapshot: ExtensionSnapshot) -> ExtensionComponentDescriptor:
    package_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, "demo")
    package = next(package for package in snapshot.packages if package.id == package_id)
    return next(
        component
        for component in package.components
        if component.kind is ExtensionComponentKind.CHANNEL
    )


def test_runtime_registry_registers_core_adapters_and_keeps_config_only_channel_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One runtime-local registry retains core tools while a disabled channel stays config-only."""
    plugin = _channel_plugin()
    monkeypatch.setattr(
        "nanobot.extensions.adapters.channels.discover_plugins",
        lambda: {"demo": plugin},
    )
    tools = ToolRegistry()
    tools.register(
        _LiveTool(),
        metadata=ToolRegistrationMetadata(
            source=ExtensionSource.BUILTIN,
            owner_name="nanobot-tools",
            class_name="LiveTool",
            scope="core",
        ),
    )
    registrations: list[str] = []
    original_register = ExtensionRegistry.register

    def record_registration(self: ExtensionRegistry, adapter: ExtensionAdapter):
        registrations.append(adapter.name)
        return original_register(self, adapter)

    monkeypatch.setattr(ExtensionRegistry, "register", record_registration)
    registry = build_core_extension_registry(
        _config(tmp_path, channel_enabled=False),
        tools,
    )
    snapshot = registry.snapshot()
    core_package_id = extension_package_id(ExtensionSource.BUILTIN, "nanobot-tools")
    core_package = next(package for package in snapshot.packages if package.id == core_package_id)

    assert registrations == [
        "core-tools",
        "agent-plugins",
        "effective-skills",
        "configured-mcp",
        "channels",
        "optional-features",
        "provider-registry",
    ]
    assert [component.name for component in core_package.components] == ["composition-live"]
    assert _channel_component(snapshot).lifecycle is ExtensionLifecycle.DISABLED


def test_runtime_registry_late_binds_gateway_channel_manager_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway can compose once before ChannelManager exists and later project its status."""
    plugin = _channel_plugin()
    monkeypatch.setattr(
        "nanobot.extensions.adapters.channels.discover_plugins",
        lambda: {"demo": plugin},
    )
    manager: _ChannelManager | None = None

    def channel_runtime_status() -> dict[str, dict[str, object]]:
        assert manager is not None
        return manager.get_status()

    registry = build_core_extension_registry(
        _config(tmp_path, channel_enabled=True),
        ToolRegistry(),
        channel_runtime_status=channel_runtime_status,
    )
    manager = _ChannelManager(
        {
            "opaque-runtime-key": {
                "owner": "demo",
                "instance_id": "default",
                "state": "running",
            }
        }
    )

    snapshot = registry.snapshot()
    assert _channel_component(snapshot).lifecycle is ExtensionLifecycle.ENABLED
    assert manager.status_calls == 1
