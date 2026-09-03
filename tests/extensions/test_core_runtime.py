from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistrationMetadata, ToolRegistry
from nanobot.channels.websocket.runtime import WebSocketConfig
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.extensions.contracts import (
    ExtensionAdapter,
    ExtensionLifecycle,
    ExtensionSource,
    extension_package_id,
)
from nanobot.extensions.registry import ExtensionRegistry
from nanobot.extensions.runtime import build_core_extension_registry
from nanobot.nanobot import Nanobot
from nanobot.webui.gateway_services import build_gateway_services


class _LiveTool(Tool):
    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "runtime_live"

    @property
    def description(self) -> str:
        return "A tool registered before runtime extension composition."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object"}

    @classmethod
    def create(cls, _ctx: ToolContext) -> Tool:
        return cls()

    async def execute(self, **_kwargs: object) -> str:
        return "ok"


class _Skills:
    def __init__(self, skills: list[dict[str, str]]) -> None:
        self.skills = skills
        self.filter_values: list[bool] = []

    def list_skills(self, *, filter_unavailable: bool = True) -> list[dict[str, str]]:
        self.filter_values.append(filter_unavailable)
        return self.skills


def _config(workspace: Path) -> Config:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    return config


def test_core_runtime_composes_exact_adapters_from_live_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime registry projects loaded tools, effective Skills, and current MCP status."""
    skill_file = tmp_path / "skills" / "runtime-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("runtime skill", encoding="utf-8")
    skills = _Skills([
        {"name": "runtime-skill", "source": "workspace", "path": str(skill_file)},
    ])
    tools = ToolRegistry()
    config = _config(tmp_path)
    configured = _config(tmp_path)
    configured.tools.mcp_servers["runtime-mcp"] = MCPServerConfig(
        type="stdio", command="runtime-mcp"
    )
    loaded_config_calls = 0
    status_calls = 0

    def load_current_config() -> Config:
        nonlocal loaded_config_calls
        loaded_config_calls += 1
        return configured

    def runtime_status() -> Mapping[str, str]:
        nonlocal status_calls
        status_calls += 1
        return {"runtime-mcp": "connected"}

    with patch("nanobot.agent.tools.loader.entry_points", return_value=()):
        loaded = ToolLoader(test_classes=[_LiveTool]).load(
            ToolContext(config=config.tools, workspace=str(tmp_path)),
            tools,
            scope="core",
        )

    registrations: list[str] = []
    original_register = ExtensionRegistry.register

    def record_registration(self: ExtensionRegistry, adapter: ExtensionAdapter):
        registrations.append(adapter.name)
        return original_register(self, adapter)

    monkeypatch.setattr(ExtensionRegistry, "register", record_registration)
    registry = build_core_extension_registry(
        config,
        tools,
        skills_loader=skills,  # type: ignore[arg-type]
        config_loader=load_current_config,
        mcp_runtime_status=runtime_status,
    )
    packages = {(package.source, package.name): package for package in registry.snapshot().packages}

    assert loaded == ["runtime_live"]
    assert [(row.name, row.metadata.source, row.metadata.scope) for row in tools.registration_snapshot()] == [
        ("runtime_live", ExtensionSource.BUILTIN, "core"),
    ]
    assert registrations == [
        "core-tools",
        "agent-plugins",
        "effective-skills",
        "configured-mcp",
        "channels",
        "optional-features",
    ]
    assert [component.name for component in packages[ExtensionSource.BUILTIN, "nanobot-tools"].components] == [
        "runtime_live",
    ]
    assert packages[ExtensionSource.WORKSPACE, "runtime-skill"].revision is not None
    assert packages[ExtensionSource.CONFIGURED, "runtime-mcp"].lifecycle is ExtensionLifecycle.ENABLED
    assert skills.filter_values == [False]
    assert loaded_config_calls == 2  # Configured MCP and channel adapters each load fresh config.
    assert status_calls == 1  # Only the MCP adapter consumes its runtime-status callback.


def test_core_runtime_builder_returns_independent_registries(tmp_path: Path) -> None:
    """Every application composition receives its own extension registry rather than shared state."""
    config = _config(tmp_path)
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

    first = build_core_extension_registry(config, tools)
    second = build_core_extension_registry(config, tools)

    assert first is not second
    assert first.snapshot().packages == second.snapshot().packages


def test_gateway_services_pass_the_runtime_registry_to_settings(tmp_path: Path) -> None:
    """Gateway settings retain the exact runtime registry supplied by their caller."""
    extensions = ExtensionRegistry()

    gateway = build_gateway_services(
        config=WebSocketConfig(),
        bus=MagicMock(),
        session_manager=None,
        static_dist_path=None,
        workspace_path=tmp_path,
        default_restrict_to_workspace=False,
        config_path=tmp_path / "config.json",
        extension_registry=extensions,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
    )

    assert gateway.settings.extensions is extensions
    assert gateway.http.settings_routes.settings.extensions is extensions


def test_sdk_exposes_registry_built_after_default_tool_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK owns the composed registry while AgentLoop remains only the tool-runtime owner."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "providers": {"openrouter": {"apiKey": "test-key"}},
            "agents": {"defaults": {"model": "openai/test", "workspace": str(tmp_path / "workspace")}},
        }),
        encoding="utf-8",
    )
    observed_rows: list[tuple[str, ExtensionSource]] = []
    built_registries: list[ExtensionRegistry] = []
    original_builder = build_core_extension_registry

    def record_builder(
        config: Config,
        tools: ToolRegistry,
        *,
        skills_loader: SkillsLoader | None = None,
        config_loader: Callable[[], Config] | None = None,
        mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    ) -> ExtensionRegistry:
        observed_rows.extend((row.name, row.metadata.source) for row in tools.registration_snapshot())
        registry = original_builder(
            config,
            tools,
            skills_loader=skills_loader,
            config_loader=config_loader,
            mcp_runtime_status=mcp_runtime_status,
        )
        built_registries.append(registry)
        return registry

    monkeypatch.setattr("nanobot.nanobot.build_core_extension_registry", record_builder)
    bot = Nanobot.from_config(config_path)

    assert any(source is ExtensionSource.BUILTIN for _, source in observed_rows)
    assert bot.extensions is built_registries[0]
    assert not hasattr(bot._loop, "extensions")
    assert extension_package_id(ExtensionSource.BUILTIN, "nanobot-tools") in {
        package.id for package in bot.extensions.snapshot().packages
    }
