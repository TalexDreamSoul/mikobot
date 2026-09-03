"""Behavioral composition coverage for channel runtime action services."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import nanobot.cli.gateway_runtime as gateway_runtime
from nanobot.agent import turn_delivery
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.channels.plugin import ChannelPlugin
from nanobot.config.schema import Config, _resolve_tool_config_refs
from nanobot.extensions.adapters import channels as channel_adapters
from nanobot.extensions.adapters.channels import ChannelExtensionServices
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionAdapter,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionSource,
)
from nanobot.extensions.registry import ExtensionRegistry
from nanobot.extensions.runtime import build_core_extension_registry

_resolve_tool_config_refs()


def _config(workspace: Path) -> Config:
    return Config.model_validate(
        {
            "agents": {"defaults": {"workspace": str(workspace)}},
            "channels": {"demo": {"enabled": False}},
        }
    )


def _channel_plugin() -> ChannelPlugin:
    return ChannelPlugin(
        name="demo",
        display_name="Demo",
        runtime="channel_runtime_composition_sentinel:Channel",
        connector="channel_runtime_composition_sentinel:Connector",
    )


def _channel_component(registry: ExtensionRegistry) -> ExtensionComponentDescriptor:
    package = next(
        package
        for package in registry.snapshot().packages
        if package.source is ExtensionSource.CHANNEL_PACKAGE and package.name == "demo"
    )
    return next(
        component
        for component in package.components
        if component.kind is ExtensionComponentKind.CHANNEL
    )


def test_builder_keeps_omitted_channel_services_read_only_and_declares_actions_when_supplied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sole channel adapter is read-only without services and exposes actions only with them."""
    plugin = _channel_plugin()
    monkeypatch.setattr(channel_adapters, "discover_plugins", lambda: {"demo": plugin})
    monkeypatch.setattr(channel_adapters, "extra_installed", lambda _name, _requirements: True)
    registrations: list[tuple[int, str]] = []
    original_register = ExtensionRegistry.register

    def record_registration(
        self: ExtensionRegistry, adapter: ExtensionAdapter
    ) -> Callable[[], None]:
        registrations.append((id(self), adapter.name))
        return original_register(self, adapter)

    monkeypatch.setattr(ExtensionRegistry, "register", record_registration)
    callbacks: list[str] = []

    async def runtime_action(_action: str, _channel_type: str, _instance_id: str) -> dict[str, object]:
        callbacks.append("runtime")
        return {"ok": True}

    services = ChannelExtensionServices(
        mutate_config=lambda _mutation: callbacks.append("config"),
        runtime_action=runtime_action,
        refresh_metadata=lambda _channel_type, _instance_id: callbacks.append("metadata"),
    )

    read_only_registry = build_core_extension_registry(_config(tmp_path), tools=ToolRegistry())
    actionable_registry = build_core_extension_registry(
        _config(tmp_path), tools=ToolRegistry(), channel_services=services
    )
    assert read_only_registry.snapshot() == read_only_registry.snapshot()
    assert actionable_registry.snapshot() == actionable_registry.snapshot()


    read_only_component = _channel_component(read_only_registry)
    actionable_component = _channel_component(actionable_registry)

    assert read_only_component.actions == frozenset({ExtensionAction.INSPECT})
    assert actionable_component.actions == frozenset(
        {
            ExtensionAction.INSPECT,
            ExtensionAction.ENABLE,
            ExtensionAction.DISABLE,
            ExtensionAction.RECONNECT,
        }
    )
    assert callbacks == []
    assert [
        name for registry_id, name in registrations if registry_id == id(read_only_registry)
    ].count("channels") == 1
    assert [
        name for registry_id, name in registrations if registry_id == id(actionable_registry)
    ].count("channels") == 1


def test_gateway_channel_services_defer_manager_actions_and_scope_config_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway builds its registry first, then services act on its manager and exact config file."""
    class StopGatewayError(RuntimeError):
        pass

    timeline: list[str] = []
    captured: dict[str, object] = {}
    locks: list[str] = []
    loaded_paths: list[Path] = []
    saved: list[tuple[object, Path]] = []
    metadata_calls: list[tuple[object, Path, str]] = []
    loaded_config = _config(tmp_path)

    class FakeLock:
        def __init__(self, path: str) -> None:
            locks.append(path)

        def __enter__(self) -> FakeLock:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeProvider:
        def set_llm_call_observer(self, _observer: object) -> None:
            return None

    class FakeSnapshot:
        provider = FakeProvider()
        model = "test-model"
        context_window_tokens = 1
        signature = "test-signature"

    class FakeMcpProvider:
        runtime_status = staticmethod(lambda: {})

        async def reload(self) -> None:
            return None

    class FakeAgent:
        tools: dict[str, object] = {}
        context = SimpleNamespace(memory=object(), skills=None)
        model = "test-model"
        collaboration = None

        def pending_cron_job_ids_for_session(self, _session_id: str) -> tuple[object, ...]:
            return ()

        def pending_local_trigger_ids_for_session(self, _session_id: str) -> tuple[object, ...]:
            return ()

        def schedule_background(self, _awaitable: object) -> None:
            return None

    class FakeCron:
        on_job: object | None = None

        def __init__(self, _path: Path) -> None:
            return None

        def register_system_job(self, _job: object) -> None:
            return None

        def remove_system_job(self, _job_id: str) -> None:
            return None

        def status(self) -> dict[str, int]:
            raise StopGatewayError

    class FakeCoordinator:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def subscribe(self, _events: object) -> None:
            return None

    manager_calls: list[tuple[str, str, str]] = []

    class FakeManager:
        enabled_channels: tuple[str, ...] = ()

        def __init__(self, *_args: object, **kwargs: object) -> None:
            timeline.append("manager")
            assert kwargs["webui_extension_registry"] is captured["registry"]

        def get_status(self) -> dict[str, object]:
            return {}

        async def apply_channel_instance_action(
            self, action: str, channel_type: str, instance_id: str
        ) -> dict[str, object]:
            manager_calls.append((action, channel_type, instance_id))
            return {"ok": True}

    config_path = tmp_path / "instance" / "config.json"
    instance = SimpleNamespace(
        config_path=config_path,
        paths=object(),
        start_options=lambda *, port: {},
    )
    original_builder = gateway_runtime.build_core_extension_registry

    def capture_registry(*args: object, **kwargs: object) -> ExtensionRegistry:
        timeline.append("registry")
        captured["services"] = kwargs["channel_services"]
        registry = original_builder(*args, **kwargs)
        captured["registry"] = registry
        registry.snapshot()
        return registry

    def load_config(path: Path) -> object:
        loaded_paths.append(path)
        return loaded_config

    def save_config(config: object, path: Path) -> None:
        saved.append((config, path))

    plugin = _channel_plugin()
    monkeypatch.setattr(channel_adapters, "discover_plugins", lambda: {"demo": plugin})
    monkeypatch.setattr(channel_adapters, "extra_installed", lambda _name, _requirements: True)
    channel_class = type("SelectedChannel", (), {})
    monkeypatch.setattr(gateway_runtime, "FileLock", FakeLock)
    monkeypatch.setattr(gateway_runtime, "_tcp_endpoint_reachable", lambda *_args: False)
    monkeypatch.setattr(gateway_runtime, "_webui_channel_enabled", lambda _config: False)
    monkeypatch.setattr(gateway_runtime, "_prepare_webui_bundle_for_gateway", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gateway_runtime, "sync_workspace_templates", lambda _workspace: None)
    monkeypatch.setattr(gateway_runtime, "is_default_workspace", lambda _workspace: False)
    monkeypatch.setattr(gateway_runtime, "_advance_dream_cursor_if_behind", lambda _memory: None)
    monkeypatch.setattr(gateway_runtime, "build_core_extension_registry", capture_registry)
    monkeypatch.setattr(gateway_runtime, "MCPProvider", SimpleNamespace(
        from_config=lambda _config, _tools: FakeMcpProvider()
    ))
    monkeypatch.setattr(gateway_runtime, "AgentLoop", SimpleNamespace(
        from_config=lambda *_args, **_kwargs: FakeAgent()
    ))
    monkeypatch.setattr("nanobot.config.loader.load_config", load_config)
    monkeypatch.setattr("nanobot.config.loader.save_config", save_config)
    monkeypatch.setattr("nanobot.providers.factory.build_provider_snapshot", lambda _config: FakeSnapshot())
    monkeypatch.setattr("nanobot.gateway.runtime.GatewayRuntime", lambda *, paths: object())
    monkeypatch.setattr("nanobot.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("nanobot.bus.runtime_events.RuntimeEventBus", lambda: object())
    monkeypatch.setattr("nanobot.session.manager.SessionManager", lambda _workspace: object())
    monkeypatch.setattr("nanobot.cron.service.CronService", FakeCron)
    monkeypatch.setattr(
        "nanobot.triggers.local_store.LocalTriggerStore", lambda _workspace: object()
    )
    monkeypatch.setattr(turn_delivery, "TurnDeliveryFactory", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "nanobot.session.webui_turns.WebuiTurnRoutePolicy", lambda _sessions: object()
    )
    monkeypatch.setattr("nanobot.session.webui_turns.WebuiTurnCoordinator", FakeCoordinator)
    monkeypatch.setattr(
        "nanobot.session.recovery.RecoveryCoordinator",
        lambda **_kwargs: SimpleNamespace(handle_action=lambda *_args: None),
    )
    monkeypatch.setattr(
        "nanobot.providers.image_generation.image_gen_provider_configs", lambda _config: []
    )
    monkeypatch.setattr("nanobot.channels.manager.ChannelManager", FakeManager)
    monkeypatch.setattr("nanobot.channels.registry.load_channel_class", lambda _name: channel_class)
    monkeypatch.setattr(
        "nanobot.channels.contracts.refresh_channel_feature_metadata",
        lambda channel, path, *, instance_id: metadata_calls.append((channel, path, instance_id)),
    )

    with pytest.raises(StopGatewayError):
        gateway_runtime._run_gateway(
            _config(tmp_path),
            health_server_enabled=False,
            gateway_instance=instance,
        )

    services = captured["services"]
    assert isinstance(services, ChannelExtensionServices)
    assert timeline == ["registry", "manager"]
    assert metadata_calls == []

    assert services.refresh_metadata is not None
    runtime_result = asyncio.run(services.runtime_action("enable", "demo", "selected"))
    services.refresh_metadata("demo", "selected")
    assert runtime_result == {"ok": True}
    assert manager_calls == [("enable", "demo", "selected")]
    assert metadata_calls == [(channel_class, config_path, "selected")]

    loaded_paths.clear()
    result = services.mutate_config(lambda config: setattr(config.api, "port", 1777))
    assert result is None
    assert loaded_config.api.port == 1777
    assert locks == [str(config_path.with_suffix(".json.lock"))]
    assert loaded_paths == [config_path]
    assert saved == [(loaded_config, config_path)]

    with pytest.raises(ValueError, match="mutation failed"):
        services.mutate_config(
            lambda _config: (_ for _ in ()).throw(ValueError("mutation failed"))
        )
    assert locks == [str(config_path.with_suffix(".json.lock"))] * 2
    assert loaded_paths == [config_path, config_path]
    assert saved == [(loaded_config, config_path)]
