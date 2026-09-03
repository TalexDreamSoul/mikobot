"""Tests for channel package discovery, management, and config behavior."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.outbound_events import (
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    outbound_message_for_event,
)
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.contracts import (
    CHANNEL_INSTANCE_REVISION_FIELD,
    ChannelFieldSpec,
    ChannelInstanceSpec,
    ChannelManagementSpec,
    ChannelSetupSpec,
    SetupRequirement,
    channel_default_config,
)
from nanobot.channels.manager import ChannelManager
from nanobot.channels.plugin import ChannelPlugin, load_channel_package
from nanobot.config.loader import save_config
from nanobot.config.schema import ChannelsConfig, Config, _resolve_tool_config_refs
from nanobot.providers.transcription import GroqTranscriptionProvider as _GroqProvider
from nanobot.providers.transcription import OpenAITranscriptionProvider as _OpenAIProvider
from nanobot.utils.restart import RestartNotice

# Config may be imported during a circular tool-config import; resolve its
# forward references after all test dependencies have loaded.
_resolve_tool_config_refs()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakePlugin(BaseChannel):
    name = "fakeplugin"
    display_name = "Fake Plugin"

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self.login_calls: list[bool] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        pass

    async def login(self, force: bool = False) -> bool:
        self.login_calls.append(force)
        return True



class _FakeLine(_FakePlugin):
    name = "line"
    display_name = "Line"


class _FakeMultiChannel(BaseChannel):
    name = "multi"
    display_name = "Multi"

    @classmethod
    def default_config(cls) -> dict:
        return {
            "instanceId": "default",
            "name": "nanobot",
            "enabled": False,
            "token": "",
        }

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        pass


def _fake_multi_instance_specs(section, *, enabled_only=True):
    instances = section.get("instances", []) if isinstance(section, dict) else []
    return [
        ChannelInstanceSpec(
            instance_id=item["id"],
            config=item,
        )
        for item in instances
        if not enabled_only or item.get("enabled", False)
    ]


def _fake_multi_update(section, values, *, instance_id="default"):
    updated = dict(section)
    instances = [dict(item) for item in section.get("instances", [])]
    for item in instances:
        if item.get("id") == instance_id:
            item.update(values)
            break
    updated["instances"] = instances
    return updated


def _fake_multi_management() -> ChannelManagementSpec:
    return ChannelManagementSpec(
        multi_instance=True,
        default_config=_FakeMultiChannel.default_config,
        instance_specs=_fake_multi_instance_specs,
        update_instance_config=_fake_multi_update,
        runtime_name=lambda name, instance_id: (
            name if instance_id == "default" else f"{name}.{instance_id}"
        ),
    )


def _channel_plugin(
    channel_cls: type[BaseChannel],
    *,
    setup: ChannelSetupSpec | None = None,
    dependencies: tuple[str, ...] = (),
    default_enabled: bool = False,
    management: ChannelManagementSpec | None = None,
) -> ChannelPlugin:
    """Create a descriptor whose lazy runtime resolves inside this test module."""
    runtime_attr = f"_runtime_{channel_cls.name.replace('-', '_')}"
    globals()[runtime_attr] = channel_cls
    if management is None:
        management = (
            _fake_multi_management()
            if issubclass(channel_cls, _FakeMultiChannel)
            else ChannelManagementSpec(default_config=channel_cls.default_config)
        )
    if setup is None and management.multi_instance:
        setup = ChannelSetupSpec(fields={})
    return ChannelPlugin(
        name=channel_cls.name,
        display_name=channel_cls.display_name,
        runtime=f"{__name__}:{runtime_attr}",
        setup=setup,
        management=management,
        dependencies=dependencies,
        default_enabled=default_enabled,
    )




def _stub_channel_registry(
    monkeypatch: pytest.MonkeyPatch,
    *plugins: ChannelPlugin,
) -> None:
    by_name = {plugin.name: plugin for plugin in plugins}

    def discover(enabled_names=None):
        if enabled_names is None:
            return dict(by_name)
        return {name: plugin for name, plugin in by_name.items() if name in enabled_names}

    monkeypatch.setattr("nanobot.channels.registry.discover_plugins", discover)




# ---------------------------------------------------------------------------
# ChannelsConfig extra="allow"
# ---------------------------------------------------------------------------

def test_channels_config_accepts_unknown_keys():
    cfg = ChannelsConfig.model_validate({
        "myplugin": {"enabled": True, "token": "abc"},
    })
    extra = cfg.model_extra
    assert extra is not None
    assert extra["myplugin"]["enabled"] is True
    assert extra["myplugin"]["token"] == "abc"


def test_channels_config_getattr_returns_extra():
    cfg = ChannelsConfig.model_validate({"myplugin": {"enabled": True}})
    section = getattr(cfg, "myplugin", None)
    assert isinstance(section, dict)
    assert section["enabled"] is True


def test_channels_config_has_no_per_channel_fields():
    """After decoupling, ChannelsConfig has no explicit channel fields."""
    cfg = ChannelsConfig()
    assert not hasattr(cfg, "telegram")
    assert cfg.send_progress is True
    assert cfg.send_tool_hints is True
    assert cfg.extract_document_text is True

    opted_out = ChannelsConfig.model_validate({
        "sendToolHints": False,
        "extractDocumentText": False,
    })
    assert opted_out.send_tool_hints is False
    assert opted_out.extract_document_text is False


@pytest.mark.parametrize(
    "name",
    ["websocket", "telegram", "discord", "slack", "email", "feishu", "matrix", "weixin", "whatsapp"],
)
def test_special_setup_validation_is_owned_by_channel_package(name: str):
    plugin = load_channel_package(name)

    assert plugin is not None
    assert plugin.setup is not None
    assert plugin.setup.validator is not None
    assert plugin.setup.validator.__module__ == f"nanobot.channels.{name}.validation"


@pytest.mark.parametrize("name", ["feishu", "weixin"])
def test_interactive_connector_is_owned_by_channel_package(name: str):
    plugin = load_channel_package(name)

    assert plugin is not None
    assert plugin.connector is not None
    assert plugin.connector.startswith(f"nanobot.channels.{name}.")
    assert plugin.load_connector().__class__.__module__ == f"nanobot.channels.{name}.connect"


def test_descriptor_defaults_cover_onboarding_fields_without_runtime_import():
    qq = load_channel_package("qq")
    email = load_channel_package("email")

    assert qq is not None
    assert email is not None
    assert channel_default_config(qq)["msgFormat"] == "plain"
    assert channel_default_config(email)["imapPort"] == 993
    assert channel_default_config(email)["smtpPort"] == 587


def test_channel_manager_delegates_instance_expansion_to_channel(monkeypatch: pytest.MonkeyPatch):
    _stub_channel_registry(monkeypatch, _channel_plugin(_FakeMultiChannel))

    cfg = Config.model_validate({
        "channels": {
            "multi": {
                "enabled": True,
                "instances": [
                    {
                        "id": "default",
                        "enabled": True,
                        "token": "default",
                    },
                    {
                        "id": "product",
                        "enabled": True,
                        "token": "product",
                    },
                    {
                        "id": "off",
                        "enabled": False,
                        "token": "off",
                    },
                ]
            }
        }
    })

    manager = ChannelManager(cfg, MessageBus())

    assert set(manager.channels) == {"multi", "multi.product"}
    assert manager.channels["multi"].name == "multi"
    assert manager.channels["multi.product"].name == "multi.product"


def test_channel_manager_loads_descriptor_but_not_disabled_runtime(monkeypatch):
    load_calls: list[str] = []
    plugin = ChannelPlugin(
        name="fakeplugin",
        display_name="Fake Plugin",
        runtime="missing.fakeplugin.runtime:FakePlugin",
    )
    config = Config.model_validate({
        "channels": {
            "fakeplugin": {
                "enabled": False,
                "instances": [{"enabled": True}],
            }
        }
    })

    monkeypatch.setattr(
        "nanobot.channels.registry._channel_package_names",
        lambda: ["fakeplugin"],
    )
    monkeypatch.setattr(
        "nanobot.channels.registry.load_channel_package",
        lambda _name: load_calls.append("descriptor") or plugin,
    )

    manager = ChannelManager(config, MessageBus())

    assert manager.channels == {}
    assert load_calls == ["descriptor"]


def test_channel_manager_preserves_single_instance_plugin_owned_instances(monkeypatch):
    _stub_channel_registry(monkeypatch, _channel_plugin(_FakePlugin))
    config = Config.model_validate({
        "channels": {
            "fakeplugin": {
                "enabled": True,
                "instances": ["plugin-owned-value"],
            }
        }
    })

    manager = ChannelManager(config, MessageBus())

    assert set(manager.channels) == {"fakeplugin"}
    assert manager.channels["fakeplugin"].config["instances"] == ["plugin-owned-value"]


# ---------------------------------------------------------------------------
# Channel package discovery
# ---------------------------------------------------------------------------

def test_discover_plugins_loads_package_descriptors():
    from nanobot.channels.registry import discover_plugins

    plugin = _channel_plugin(_FakeLine)
    with (
        patch("nanobot.channels.registry._channel_package_names", return_value=["line"]),
        patch("nanobot.channels.registry.load_channel_package", return_value=plugin),
    ):
        result = discover_plugins()

    assert "line" in result
    assert isinstance(result["line"], ChannelPlugin)






def test_generic_plugin_validation_enforces_composite_requirements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from nanobot.channels.validation import validate_channel_config
    from nanobot.config import loader

    class _CompositeSetupPlugin(_FakePlugin):
        name = "compositeplugin"

    setup_spec = ChannelSetupSpec(
        fields={
            "password": ChannelFieldSpec(kind="secret"),
            "accessToken": ChannelFieldSpec(kind="secret"),
            "deviceId": ChannelFieldSpec(),
        },
        required=(
            SetupRequirement.one_of(
                ("password",),
                ("accessToken", "deviceId"),
            ),
        ),
    )

    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(loader, "_current_config_path", config_path)
    _stub_channel_registry(
        monkeypatch,
        _channel_plugin(_CompositeSetupPlugin, setup=setup_spec),
    )

    missing = validate_channel_config("compositeplugin")
    partial = validate_channel_config(
        "compositeplugin",
        {"channels.compositeplugin.accessToken": "token"},
    )
    complete = validate_channel_config(
        "compositeplugin",
        {
            "channels.compositeplugin.accessToken": "token",
            "channels.compositeplugin.deviceId": "DEVICE",
        },
    )

    assert missing["status"] == "needs_setup"
    assert missing["can_enable"] is False
    assert "password" in missing["missing_fields"]
    assert partial["status"] == "needs_setup"
    assert partial["can_enable"] is False
    assert "deviceId" in partial["missing_fields"]
    assert complete["status"] == "configured"
    assert complete["can_enable"] is True




def test_discover_plugins_skips_names_outside_enabled_set():
    from nanobot.channels.registry import discover_plugins

    loaded: list[str] = []

    def _load_disabled(_name: str):
        loaded.append("disabled")
        return _channel_plugin(_FakePlugin)

    with (
        patch("nanobot.channels.registry._channel_package_names", return_value=["disabled"]),
        patch("nanobot.channels.registry.load_channel_package", side_effect=_load_disabled),
    ):
        result = discover_plugins({"enabled"})

    assert result == {}
    assert loaded == []


def test_channel_manifest_rejects_invalid_dependency_metadata():
    with pytest.raises(TypeError, match="tuple of requirements"):
        ChannelPlugin(
            name="broken",
            display_name="Broken",
            runtime="broken.runtime:BrokenChannel",
            dependencies=["broken-sdk>=1"],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="valid requirement"):
        ChannelPlugin(
            name="broken",
            display_name="Broken",
            runtime="broken.runtime:BrokenChannel",
            dependencies=("not a requirement ???",),
        )


def test_discover_plugins_handles_load_error():
    from nanobot.channels.registry import discover_plugins

    def _boom(_name: str):
        raise RuntimeError("broken")

    with (
        patch("nanobot.channels.registry._channel_package_names", return_value=["broken"]),
        patch("nanobot.channels.registry.load_channel_package", side_effect=_boom),
    ):
        result = discover_plugins()

    assert "broken" not in result


def test_management_registry_rejects_duplicate_feishu_instance_mutation(tmp_path: Path) -> None:
    """A duplicate Feishu instance blocks canonical configuration without rewriting saved data."""
    from nanobot.extensions.contracts import (
        ExtensionAction,
        ExtensionActionContext,
        ExtensionActionRequest,
    )
    from nanobot.extensions.management import build_management_extension_registry
    from nanobot.extensions.targets import resolve_extension_feature_target

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "channels": {
                    "feishu": {
                        "instances": [
                            {"id": "default", "enabled": False, "appId": "A"},
                            {"id": "default", "enabled": True, "appId": "B"},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    before = config_path.read_bytes()
    registry = build_management_extension_registry(config_path)
    target = resolve_extension_feature_target(registry.snapshot(), "feishu")

    assert target is not None
    result = asyncio.run(
        registry.execute(
            ExtensionActionRequest(
                context=ExtensionActionContext(actor_id="test", is_system_admin=True),
                target_id=target.target_id,
                action=ExtensionAction.CONFIGURE,
                expected_revision=target.revision,
                values={"appId": "updated"},
            )
        )
    )

    assert result.ok is False
    assert config_path.read_bytes() == before


def test_manager_loads_plugin_from_dict_config(monkeypatch):
    """ChannelManager should instantiate a channel package from a raw dict config."""
    from nanobot.channels.manager import ChannelManager

    fake_config = Config.model_validate({
        "channels": {
            "fakeplugin": {"enabled": True, "allowFrom": ["*"]},
        }
    })
    _stub_channel_registry(monkeypatch, _channel_plugin(_FakePlugin))

    mgr = ChannelManager(fake_config, MessageBus())

    assert "fakeplugin" in mgr.channels
    assert isinstance(mgr.channels["fakeplugin"], _FakePlugin)


def test_manager_installs_manifest_dependencies_before_loading_enabled_channel(monkeypatch):
    from nanobot.optional_features import InstallResult

    plugin = _channel_plugin(
        _FakePlugin,
        dependencies=("fake-sdk>=1",),
    )
    _stub_channel_registry(monkeypatch, plugin)
    installed = False
    installs: list[tuple[str, list[str]]] = []

    def extra_installed(_name: str, _dependencies: list[str] | None) -> bool:
        return installed

    def install_extra(name: str, dependencies: list[str], *, runner):
        nonlocal installed
        installs.append((name, dependencies))
        installed = True
        return InstallResult(True, name, ["pip"])

    monkeypatch.setattr("nanobot.optional_features.extra_installed", extra_installed)
    monkeypatch.setattr("nanobot.optional_features.install_extra", install_extra)
    config = Config.model_validate({
        "channels": {
            "websocket": {"enabled": False},
            "fakeplugin": {"enabled": True},
        }
    })

    manager = ChannelManager(config, MessageBus())

    assert installs == [("fakeplugin", ["fake-sdk>=1"])]
    assert "fakeplugin" in manager.channels


def test_manager_reports_dependency_install_failure_as_runtime_failure(monkeypatch):
    from nanobot.optional_features import InstallResult

    plugin = _channel_plugin(
        _FakePlugin,
        dependencies=("fake-sdk>=1",),
    )
    _stub_channel_registry(monkeypatch, plugin)
    monkeypatch.setattr(
        "nanobot.optional_features.extra_installed",
        lambda _name, _dependencies: False,
    )
    monkeypatch.setattr(
        "nanobot.optional_features.install_extra",
        lambda name, _dependencies, *, runner: InstallResult(False, name, ["pip"]),
    )
    config = Config.model_validate({
        "channels": {
            "websocket": {"enabled": False},
            "fakeplugin": {"enabled": True},
        }
    })

    manager = ChannelManager(config, MessageBus())

    assert manager.channels == {}
    assert manager.get_status()["fakeplugin"] == {
        "enabled": True,
        "running": False,
        "state": "failed",
        "owner": "fakeplugin",
        "instance_id": "default",
        "error": "Channel dependencies could not be installed. Check gateway logs.",
    }


def test_manager_loads_websocket_from_default_config():
    from nanobot.channels.manager import ChannelManager

    class _FakeWebSocket(_FakePlugin):
        name = "websocket"
        display_name = "WebSocket"

        def __init__(self, config, bus, *, gateway):
            super().__init__(config, bus)
            self.gateway = gateway

        @classmethod
        def default_config(cls):
            return {"enabled": True, "host": "127.0.0.1"}

    plugin = _channel_plugin(_FakeWebSocket, default_enabled=True)
    with patch("nanobot.channels.registry.discover_plugins", return_value={"websocket": plugin}):
        mgr = ChannelManager(Config(), MessageBus(), webui_static_dist=False)

    assert "websocket" in mgr.channels
    assert mgr.channels["websocket"].config["enabled"] is True
    assert mgr.channels["websocket"].config["host"] == "127.0.0.1"


def test_manager_respects_explicitly_disabled_websocket_config():
    from nanobot.channels.manager import ChannelManager

    config = Config.model_validate({"channels": {"websocket": {"enabled": False}}})
    plugin = ChannelPlugin(
        name="websocket",
        display_name="WebSocket",
        runtime="missing.websocket.runtime:WebSocketChannel",
        default_enabled=True,
    )
    with patch("nanobot.channels.registry.discover_plugins", return_value={"websocket": plugin}):
        mgr = ChannelManager(config, MessageBus(), webui_static_dist=False)

    assert "websocket" not in mgr.channels


@pytest.mark.asyncio
async def test_base_channel_reads_current_transcription_config_each_call(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """BaseChannel.transcribe_audio resolves config at call time, not manager init time."""
    from nanobot.providers import transcription as transcription_mod

    config_path = tmp_path / "config.json"
    config = Config()
    config.transcription.provider = "openai"
    config.transcription.model = "whisper-custom"
    config.transcription.language = "en"
    config.providers.openai.api_key = "openai-key"
    config.providers.openai.api_base = "http://openai.local/v1/audio/transcriptions"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    channel = _FakePlugin({"enabled": True, "allowFrom": ["*"]}, MessageBus())

    calls: list[dict[str, object]] = []

    class _StubOpenAI:
        def __init__(self, api_key=None, api_base=None, language=None, model=None):
            calls.append({
                "provider": "openai",
                "api_key": api_key,
                "api_base": api_base,
                "language": language,
                "model": model,
            })

        async def transcribe(self, file_path):
            return "openai-ok"

    class _StubGroq:
        def __init__(self, api_key=None, api_base=None, language=None, model=None):
            calls.append({
                "provider": "groq",
                "api_key": api_key,
                "api_base": api_base,
                "language": language,
                "model": model,
            })

        async def transcribe(self, file_path):
            return "groq-ok"

    with (
        patch.object(transcription_mod, "OpenAITranscriptionProvider", _StubOpenAI),
        patch.object(transcription_mod, "GroqTranscriptionProvider", _StubGroq),
    ):
        assert await channel.transcribe_audio("/tmp/does-not-matter.wav") == "openai-ok"

        config.transcription.provider = "groq"
        config.transcription.model = "whisper-large-v3-turbo"
        config.transcription.language = "ko"
        config.providers.groq.api_key = "groq-key"
        config.providers.groq.api_base = "http://groq.local/v1/audio/transcriptions"
        save_config(config, config_path)

        assert await channel.transcribe_audio("/tmp/does-not-matter.wav") == "groq-ok"

    assert calls == [
        {
            "provider": "openai",
            "api_key": "openai-key",
            "api_base": "http://openai.local/v1/audio/transcriptions",
            "language": "en",
            "model": "whisper-custom",
        },
        {
            "provider": "groq",
            "api_key": "groq-key",
            "api_base": "http://groq.local/v1/audio/transcriptions",
            "language": "ko",
            "model": "whisper-large-v3-turbo",
        },
    ]


@pytest.mark.asyncio
async def test_base_channel_respects_disabled_transcription_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "config.json"
    config = Config()
    config.transcription.enabled = False
    config.providers.groq.api_key = "groq-key"
    save_config(config, config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    channel = _FakePlugin({"enabled": True, "allowFrom": ["*"]}, MessageBus())

    with patch("nanobot.providers.transcription.GroqTranscriptionProvider") as provider:
        assert await channel.transcribe_audio("/tmp/does-not-matter.wav") == ""
    provider.assert_not_called()


def test_openai_transcription_provider_honors_api_base_argument():
    from nanobot.providers.transcription import OpenAITranscriptionProvider

    default = OpenAITranscriptionProvider(api_key="k")
    assert default.api_url == "https://api.openai.com/v1/audio/transcriptions"

    custom = OpenAITranscriptionProvider(
        api_key="k", api_base="http://override/v1/audio/transcriptions"
    )
    assert custom.api_url == "http://override/v1/audio/transcriptions"


# ---------------------------------------------------------------------------
# Transcription provider HTTP tests
# ---------------------------------------------------------------------------


class _StubResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"text": "hello"}


def _stub_async_client(captured: dict[str, object]):
    """Return an httpx.AsyncClient stub that records POST calls into *captured*."""
    class _AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, files=None, timeout=None):
            captured["files"] = files
            return _StubResponse()

    return _AsyncClient()


@pytest.mark.parametrize(
    "provider_cls,language",
    [(_GroqProvider, "ko"), (_OpenAIProvider, "en")],
    ids=["groq", "openai"],
)
@pytest.mark.asyncio
async def test_transcription_provider_includes_language(tmp_path, provider_cls, language):
    """Provider must include the 'language' field in multipart body when set."""
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    captured: dict[str, object] = {}

    with patch("nanobot.providers.transcription.httpx.AsyncClient", return_value=_stub_async_client(captured)):
        provider = provider_cls(api_key="k", language=language)
        result = await provider.transcribe(audio)

    assert result == "hello"
    assert captured["files"]["language"] == (None, language)


@pytest.mark.parametrize(
    "provider_cls",
    [_GroqProvider, _OpenAIProvider],
    ids=["groq", "openai"],
)
@pytest.mark.asyncio
async def test_transcription_provider_omits_language_when_none(tmp_path, provider_cls):
    """When language is not set, the 'language' key must be absent from the multipart body."""
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    captured: dict[str, object] = {}

    with patch("nanobot.providers.transcription.httpx.AsyncClient", return_value=_stub_async_client(captured)):
        provider = provider_cls(api_key="k")
        result = await provider.transcribe(audio)

    assert result == "hello"
    assert "language" not in captured["files"]






def test_repository_dependency_installer_selects_all_channel_manifests(monkeypatch):
    from scripts import install_channel_dependencies as dependencies

    plugins = {
        "second": ChannelPlugin(
            name="second",
            display_name="Second",
            runtime="missing.second.runtime:SecondChannel",
            dependencies=("second-sdk>=2",),
        ),
        "first": ChannelPlugin(
            name="first",
            display_name="First",
            runtime="missing.first.runtime:FirstChannel",
            dependencies=("first-sdk>=1",),
        ),
    }
    prepared: list[tuple[set[str], dict[str, ChannelPlugin]]] = []
    monkeypatch.setattr(dependencies, "discover_plugins", lambda: plugins)
    monkeypatch.setattr(
        dependencies,
        "ensure_repository_channel_dependencies",
        lambda names, discovered: prepared.append((names, discovered)) or {},
    )

    assert dependencies.main(["--all-channels"]) == 0
    assert prepared == [(set(plugins), plugins)]


def test_repository_dependency_installer_batches_missing_manifests(monkeypatch):
    from nanobot.optional_features import InstallResult
    from scripts import install_channel_dependencies as dependencies

    plugins = {
        "second": ChannelPlugin(
            name="second",
            display_name="Second",
            runtime="missing.second.runtime:SecondChannel",
            dependencies=("shared-sdk>=1", "second-sdk>=2"),
        ),
        "first": ChannelPlugin(
            name="first",
            display_name="First",
            runtime="missing.first.runtime:FirstChannel",
            dependencies=("first-sdk>=1", "shared-sdk>=1"),
        ),
        "ready": ChannelPlugin(
            name="ready",
            display_name="Ready",
            runtime="missing.ready.runtime:ReadyChannel",
            dependencies=("ready-sdk>=1",),
        ),
    }
    batch_installed = False
    installs: list[tuple[str, list[str]]] = []

    def extra_installed(name: str, _requirements: list[str]) -> bool:
        return name == "ready" or batch_installed

    def install_extra(name: str, requirements: list[str]) -> InstallResult:
        nonlocal batch_installed
        installs.append((name, requirements))
        batch_installed = True
        return InstallResult(True, name, ["pip"])

    monkeypatch.setattr(dependencies, "extra_installed", extra_installed)
    monkeypatch.setattr(dependencies, "install_extra", install_extra)
    monkeypatch.setattr(
        dependencies,
        "ensure_enabled_channel_dependencies",
        lambda _names, _plugins: pytest.fail("verified batch must not use the fallback"),
    )

    failures = dependencies.ensure_repository_channel_dependencies(set(plugins), plugins)

    assert failures == {}
    assert installs == [
        (
            "channel-dependencies",
            ["first-sdk>=1", "shared-sdk>=1", "second-sdk>=2"],
        )
    ]


def test_repository_dependency_installer_falls_back_after_batch_failure(monkeypatch):
    from nanobot.optional_features import InstallResult
    from scripts import install_channel_dependencies as dependencies

    plugins = {
        name: ChannelPlugin(
            name=name,
            display_name=name.title(),
            runtime=f"missing.{name}.runtime:Channel",
            dependencies=(f"{name}-sdk>=1",),
        )
        for name in ("first", "second")
    }
    fallbacks: list[set[str]] = []
    fallback_finished = False

    def extra_installed(name: str, _requirements: list[str]) -> bool:
        return fallback_finished and name == "first"

    def fallback(names: set[str], _plugins: dict[str, ChannelPlugin]) -> dict[str, str]:
        nonlocal fallback_finished
        fallbacks.append(names)
        fallback_finished = True
        return {"second": "install failed"}

    monkeypatch.setattr(dependencies, "extra_installed", extra_installed)
    monkeypatch.setattr(
        dependencies,
        "install_extra",
        lambda name, _requirements: InstallResult(False, name, ["pip"]),
    )
    monkeypatch.setattr(
        dependencies,
        "ensure_enabled_channel_dependencies",
        fallback,
    )

    failures = dependencies.ensure_repository_channel_dependencies(set(plugins), plugins)

    assert failures == {"second": "install failed"}
    assert fallbacks == [set(plugins)]


def test_repository_dependency_installer_rechecks_each_channel_after_batch(monkeypatch):
    from nanobot.optional_features import InstallResult
    from scripts import install_channel_dependencies as dependencies

    plugins = {
        name: ChannelPlugin(
            name=name,
            display_name=name.title(),
            runtime=f"missing.{name}.runtime:Channel",
            dependencies=(f"{name}-sdk>=1",),
        )
        for name in ("first", "second")
    }
    batch_finished = False
    fallback_finished = False
    fallbacks: list[set[str]] = []

    def extra_installed(name: str, _requirements: list[str]) -> bool:
        if fallback_finished:
            return True
        if batch_finished:
            return name == "second"
        return name == "first"

    def install_extra(name: str, _requirements: list[str]) -> InstallResult:
        nonlocal batch_finished
        batch_finished = True
        return InstallResult(True, name, ["pip"])

    def fallback(names: set[str], _plugins: dict[str, ChannelPlugin]) -> dict[str, str]:
        nonlocal fallback_finished
        fallbacks.append(names)
        fallback_finished = True
        return {}

    monkeypatch.setattr(dependencies, "extra_installed", extra_installed)
    monkeypatch.setattr(dependencies, "install_extra", install_extra)
    monkeypatch.setattr(
        dependencies,
        "ensure_enabled_channel_dependencies",
        fallback,
    )

    failures = dependencies.ensure_repository_channel_dependencies(set(plugins), plugins)

    assert failures == {}
    assert fallbacks == [{"first"}]


def test_repository_dependency_installer_reports_conflict_after_fallback(monkeypatch):
    from nanobot.optional_features import InstallResult
    from scripts import install_channel_dependencies as dependencies

    plugins = {
        name: ChannelPlugin(
            name=name,
            display_name=name.title(),
            runtime=f"missing.{name}.runtime:Channel",
            dependencies=(f"{name}-sdk>=1",),
        )
        for name in ("first", "second")
    }
    fallback_finished = False

    def extra_installed(name: str, _requirements: list[str]) -> bool:
        return fallback_finished and name == "second"

    def fallback(_names: set[str], _plugins: dict[str, ChannelPlugin]) -> dict[str, str]:
        nonlocal fallback_finished
        fallback_finished = True
        return {}

    monkeypatch.setattr(dependencies, "extra_installed", extra_installed)
    monkeypatch.setattr(
        dependencies,
        "install_extra",
        lambda name, _requirements: InstallResult(False, name, ["pip"]),
    )
    monkeypatch.setattr(dependencies, "ensure_enabled_channel_dependencies", fallback)

    failures = dependencies.ensure_repository_channel_dependencies(set(plugins), plugins)

    assert failures == {
        "first": "Channel dependencies could not be installed. Check gateway logs."
    }


def test_repository_dependency_installer_rejects_unknown_channel(monkeypatch, capsys):
    from scripts import install_channel_dependencies as dependencies

    monkeypatch.setattr(dependencies, "discover_plugins", lambda: {})

    assert dependencies.main(["missing"]) == 2
    assert "Unknown channels: missing" in capsys.readouterr().err


def test_repository_dependency_installer_propagates_install_failure(monkeypatch, capsys):
    from scripts import install_channel_dependencies as dependencies

    plugin = ChannelPlugin(
        name="demo",
        display_name="Demo",
        runtime="missing.demo.runtime:DemoChannel",
    )
    monkeypatch.setattr(dependencies, "discover_plugins", lambda: {"demo": plugin})
    monkeypatch.setattr(
        dependencies,
        "ensure_repository_channel_dependencies",
        lambda _names, _plugins: {"demo": "dependency install failed"},
    )

    assert dependencies.main(["demo"]) == 1
    assert "demo: dependency install failed" in capsys.readouterr().err


def test_plugins_disable_channel_preserves_unrelated_config(tmp_path):
    from typer.testing import CliRunner

    from nanobot.cli.commands import app

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"channels": {"matrix": {"enabled": True, "homeserver": "keep"}}}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app, ["plugins", "disable", "matrix", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "Channel disabled" in result.output
    data = json.loads(config_path.read_text(encoding="utf-8"))
    matrix = data["channels"]["matrix"]
    revision_marker = matrix.pop(CHANNEL_INSTANCE_REVISION_FIELD)

    # This opaque marker invalidates stale extension actions without disclosing config.
    assert isinstance(revision_marker, str)
    assert revision_marker
    assert matrix == {"enabled": False, "homeserver": "keep"}






@pytest.mark.parametrize(
    "index_url",
    [
        "",
        "https://mirror.example/simple",
    ],
)
def test_enable_uses_uv_when_tool_environment_has_no_pip(
    monkeypatch,
    index_url,
):
    from nanobot import optional_features

    calls: list[list[str]] = []
    call_envs: list[dict[str, str] | None] = []

    def _run(
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        call_envs.append(env)
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="No module named pip")
        if argv[0] == "uv":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="No module named ensurepip",
        )

    monkeypatch.setattr("shutil.which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    if index_url:
        monkeypatch.setenv("PIP_INDEX_URL", index_url)
    else:
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)

    assert optional_features.install_extra("feishu", ["lark-oapi>=1.5.0"], runner=_run).ok is True
    assert calls == [
        [sys.executable, "-m", "pip", "install", "lark-oapi>=1.5.0"],
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "lark-oapi>=1.5.0",
        ],
    ]
    assert call_envs[0] is None
    assert call_envs[1] is not None
    assert call_envs[1]["HTTPS_PROXY"] == "http://proxy.example:8080"
    if index_url:
        assert call_envs[1]["UV_INDEX_URL"] == index_url
    else:
        assert "UV_INDEX_URL" not in call_envs[1]


def test_enable_bootstraps_pip_with_ensurepip(monkeypatch):
    from nanobot import optional_features

    calls: list[list[str]] = []

    def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="No module named pip")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert optional_features.install_extra("bedrock", None, runner=_run).ok is True
    assert calls == [
        [sys.executable, "-m", "pip", "install", "nanobot-ai[bedrock]"],
        [sys.executable, "-m", "ensurepip", "--upgrade"],
        [sys.executable, "-m", "pip", "install", "nanobot-ai[bedrock]"],
    ]


def test_install_extra_logs_command_and_output(monkeypatch):
    from nanobot import optional_features

    records: list[str] = []

    class _Logger:
        def info(self, message: str, *args: object) -> None:
            records.append(message.format(*args))

    def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="install ok", stderr="")

    monkeypatch.setattr(optional_features, "logger", _Logger())

    result = optional_features.install_extra("weixin", ["qrcode[pil]>=8.0"], runner=_run)

    assert result.ok is True
    assert any("Installing optional feature 'weixin':" in record for record in records)
    assert any("Optional feature 'weixin' install exited with code 0" in record for record in records)
    assert any("install ok" in record for record in records)


def test_run_install_command_returns_failure_on_timeout(monkeypatch):
    from nanobot import optional_features

    def _run(*args, **kwargs):
        raise subprocess.TimeoutExpired(["pip"], 300, output="partial", stderr=b"still running")

    monkeypatch.setattr(optional_features.subprocess, "run", _run)

    result = optional_features.run_install_command(["pip"])

    assert result.returncode == 124
    assert result.stdout == "partial"
    assert result.stderr == "still running\nTimed out after 300s"


def test_optional_dependency_metadata_for_enable():
    from nanobot import optional_features
    from nanobot.channels.plugin import load_channel_package

    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["optional-dependencies"]
    required = data["project"]["dependencies"]

    assert "boto3>=1.43.0" not in data["project"]["dependencies"]
    assert deps["bedrock"] == ["boto3>=1.43.0"]
    for dep_name in (
        "aiohttp",
        "dingtalk-stream",
        "lark-oapi",
        "msgpack",
        "python-telegram-bot",
        "python-socketio",
        "qq-botpy",
        "slack-sdk",
        "slackify-markdown",
    ):
        assert not any(dep.startswith(dep_name) for dep in required)
    for dependency in (
        "tzdata>=2025.2",
        "defusedxml>=0.7.1,<1.0.0",
        "pypdf>=5.0.0,<6.0.0",
        "python-docx>=1.1.0,<2.0.0",
        "openpyxl>=3.1.0,<4.0.0",
        "python-pptx>=1.0.0,<2.0.0",
    ):
        assert dependency in required
    assert deps["documents"] == [
        "defusedxml>=0.7.1,<1.0.0",
        "pypdf>=5.0.0,<6.0.0",
        "python-docx>=1.1.0,<2.0.0",
        "openpyxl>=3.1.0,<4.0.0",
        "python-pptx>=1.0.0,<2.0.0",
    ]
    assert deps["pdf"] == ["pypdf>=5.0.0,<6.0.0"]
    assert deps["langfuse"] == ["langfuse>=3.0.0,<4.0.0"]
    assert deps["olostep"] == ["olostep>=0.1.0; python_version < '3.14'"]
    expected_olostep_args = [] if sys.version_info >= (3, 14) else ["olostep>=0.1.0"]
    assert optional_features.install_args_for_extra("olostep", deps["olostep"]) == (
        expected_olostep_args,
        "olostep support",
    )
    channel_names = {
        "dingtalk",
        "discord",
        "feishu",
        "matrix",
        "mochat",
        "msteams",
        "napcat",
        "qq",
        "slack",
        "telegram",
        "wecom",
        "weixin",
        "whatsapp",
    }
    assert channel_names.isdisjoint(deps)
    expected_channel_dependencies = {
        "dingtalk": ("dingtalk-stream>=0.24.0,<1.0.0",),
        "discord": ("discord.py>=2.5.2,<3.0.0",),
        "feishu": ("lark-oapi>=1.5.0,<2.0.0",),
        "matrix": (
            "matrix-nio[e2e]>=0.25.2; sys_platform != 'win32'",
            "matrix-nio>=0.25.2; sys_platform == 'win32'",
            "aiohttp>=3.9.0,<4.0.0",
            "mistune>=3.0.0,<4.0.0",
            "nh3>=0.2.17,<1.0.0",
        ),
        "mochat": (
            "python-socketio>=5.16.0,<6.0.0",
            "msgpack>=1.1.0,<2.0.0",
        ),
        "msteams": ("PyJWT>=2.0,<3.0", "cryptography>=41.0"),
        "napcat": ("aiohttp>=3.9.0,<4.0.0",),
        "qq": (
            "aiohttp>=3.9.0,<4.0.0",
            "qq-botpy>=1.2.0,<2.0.0",
        ),
        "slack": (
            "aiohttp>=3.9.0,<4.0.0",
            "slack-sdk>=3.39.0,<4.0.0",
            "slackify-markdown>=0.2.0,<1.0.0",
        ),
        "telegram": (
            "python-telegram-bot[socks,webhooks]>=22.6,<23.0",
            "socksio>=1.0.0,<2.0.0",
            "python-socks[asyncio]>=2.8.0,<3.0.0; sys_platform != 'win32'",
        ),
        "wecom": ("wecom-aibot-sdk-python>=0.1.5",),
        "weixin": ("qrcode[pil]>=8.0", "pycryptodome>=3.20.0"),
        "whatsapp": (
            "neonize>=0.3.18.post0,<0.4.0",
            "segno>=1.6.1,<2.0.0",
        ),
    }
    for name, expected in expected_channel_dependencies.items():
        plugin = load_channel_package(name)
        assert plugin is not None
        assert plugin.dependencies == expected

    visible = optional_features.optional_dependency_groups()
    assert "documents" not in visible
    assert "pdf" not in visible


def test_optional_dependency_groups_falls_back_to_package_metadata(monkeypatch):
    from nanobot import optional_features

    class _Metadata:
        def get_all(self, key: str):
            assert key == "Provides-Extra"
            return ["bedrock", "dev"]

    monkeypatch.setattr(optional_features, "load_pyproject", lambda _path: {})
    monkeypatch.setattr("importlib.metadata.metadata", lambda _name: _Metadata())
    monkeypatch.setattr(
        "importlib.metadata.requires",
        lambda _name: [
            "packaging>=24.0",
            "boto3>=1.43.0; extra == 'bedrock'",
            "pytest>=8.0; extra == 'dev'",
        ],
    )

    deps = optional_features.optional_dependency_groups()

    assert deps == {"bedrock": ["boto3>=1.43.0; extra == 'bedrock'"]}
    assert optional_features.install_args_for_extra("bedrock", deps["bedrock"]) == (
        ["boto3>=1.43.0"],
        "bedrock support",
    )


def test_load_pyproject_propagates_malformed_toml(tmp_path):
    from nanobot import optional_features

    path = tmp_path / "pyproject.toml"
    path.write_text("[project\nname = 'nanobot'", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        optional_features.load_pyproject(path)


def test_optional_dependency_metadata_propagates_malformed_requirement(monkeypatch):
    from packaging.requirements import InvalidRequirement

    from nanobot import optional_features

    class _Metadata:
        def get_all(self, key: str):
            assert key == "Provides-Extra"
            return ["bedrock"]

    monkeypatch.setattr("importlib.metadata.metadata", lambda _name: _Metadata())
    monkeypatch.setattr(
        "importlib.metadata.requires",
        lambda _name: ["not a valid requirement ???"],
    )

    with pytest.raises(InvalidRequirement):
        optional_features.optional_dependency_groups_from_metadata()


def test_install_args_for_extra_resolves_metadata_markers_for_current_platform():
    from nanobot import optional_features

    current_platform = sys.platform
    deps = [
        f"current-platform-package>=1.0; sys_platform == '{current_platform}' and extra == 'matrix'",
        "other-platform-package>=1.0; sys_platform == 'never' and extra == 'matrix'",
    ]

    assert optional_features.install_args_for_extra("matrix", deps) == (
        ["current-platform-package>=1.0"],
        "matrix support",
    )


def test_requirement_installed_validates_requested_extras(monkeypatch):
    from nanobot import optional_features

    class _Metadata:
        def __init__(self, extras: list[str] | None = None) -> None:
            self._extras = extras or []

        def get_all(self, key: str):
            assert key == "Provides-Extra"
            return self._extras

    class _Distribution:
        def __init__(
            self,
            version: str,
            *,
            requires: list[str] | None = None,
            extras: list[str] | None = None,
        ) -> None:
            self.version = version
            self.requires = requires or []
            self.metadata = _Metadata(extras)

    installed: dict[str, _Distribution] = {
        "qrcode": _Distribution(
            "8.2",
            requires=["pillow>=9.1; extra == 'pil'"],
            extras=["pil"],
        ),
    }

    def _distribution(name: str) -> _Distribution:
        normalized = name.lower()
        if normalized not in installed:
            raise PackageNotFoundError(name)
        return installed[normalized]

    monkeypatch.setattr(optional_features, "distribution", _distribution)

    assert optional_features.requirement_installed("qrcode>=8.0") is True
    assert optional_features.requirement_installed("qrcode[pil]>=8.0") is False

    installed["pillow"] = _Distribution("10.0")

    assert optional_features.requirement_installed("qrcode[pil]>=8.0") is True


@pytest.mark.asyncio
async def test_manager_skips_disabled_channel_package(monkeypatch):
    fake_config = SimpleNamespace(
        channels=ChannelsConfig.model_validate({
            "fakeplugin": {"enabled": False},
        }),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    _stub_channel_registry(monkeypatch, _channel_plugin(_FakePlugin))
    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {}
    mgr._dispatch_task = None
    mgr._init_channels()

    assert "fakeplugin" not in mgr.channels


# ---------------------------------------------------------------------------
# Channel default_config() and dict-to-Pydantic conversion
# ---------------------------------------------------------------------------

def test_channel_default_config():
    """Channels expose default_config() returning a dict with 'enabled': False."""
    from nanobot.channels.dingtalk.runtime import DingTalkChannel
    cfg = DingTalkChannel.default_config()
    assert isinstance(cfg, dict)
    assert cfg["enabled"] is False
    assert "clientId" in cfg


def test_channel_init_from_dict():
    """Channels accept a raw dict and convert to Pydantic internally."""
    from nanobot.channels.dingtalk.runtime import DingTalkChannel
    bus = MessageBus()
    ch = DingTalkChannel({"enabled": False, "clientId": "test-id", "allowFrom": ["*"]}, bus)
    assert ch.config.client_id == "test-id"
    assert ch.config.allow_from == ["*"]


def test_channels_config_send_max_retries_default():
    """ChannelsConfig should have send_max_retries with default value of 3."""
    cfg = ChannelsConfig()
    assert hasattr(cfg, 'send_max_retries')
    assert cfg.send_max_retries == 3


def test_channels_config_send_max_retries_upper_bound():
    """send_max_retries should be bounded to prevent resource exhaustion."""
    from pydantic import ValidationError

    # Value too high should be rejected
    with pytest.raises(ValidationError):
        ChannelsConfig(send_max_retries=100)

    # Negative should be rejected
    with pytest.raises(ValidationError):
        ChannelsConfig(send_max_retries=-1)

    # Boundary values should be allowed
    cfg_min = ChannelsConfig(send_max_retries=0)
    assert cfg_min.send_max_retries == 0

    cfg_max = ChannelsConfig(send_max_retries=10)
    assert cfg_max.send_max_retries == 10

    # Value above upper bound should be rejected
    with pytest.raises(ValidationError):
        ChannelsConfig(send_max_retries=11)


def test_channels_config_transcription_language_pattern():
    """transcription_language must match ISO-639 format (2-3 lowercase letters) or be None."""
    from pydantic import ValidationError

    # Valid values
    assert ChannelsConfig(transcription_language="en").transcription_language == "en"
    assert ChannelsConfig(transcription_language="kor").transcription_language == "kor"
    assert ChannelsConfig(transcription_language=None).transcription_language is None

    # Invalid values
    with pytest.raises(ValidationError):
        ChannelsConfig(transcription_language="EN")       # uppercase
    with pytest.raises(ValidationError):
        ChannelsConfig(transcription_language="english")   # full word
    with pytest.raises(ValidationError):
        ChannelsConfig(transcription_language="en-US")     # BCP 47 tag


# ---------------------------------------------------------------------------
# _send_with_retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_with_retry_succeeds_first_try():
    """_send_with_retry should succeed on first try and not retry."""
    call_count = 0

    class _FailingChannel(BaseChannel):
        name = "failing"
        display_name = "Failing"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            nonlocal call_count
            call_count += 1
            # Succeeds on first try

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=3),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"failing": _FailingChannel(fake_config, mgr.bus)}
    mgr._dispatch_task = None

    msg = OutboundMessage(channel="failing", chat_id="123", content="test")
    await mgr._send_with_retry(mgr.channels["failing"], msg)

    assert call_count == 1


@pytest.mark.asyncio
async def test_send_with_retry_retries_on_failure():
    """_send_with_retry should retry on failure up to max_retries times."""
    call_count = 0

    class _FailingChannel(BaseChannel):
        name = "failing"
        display_name = "Failing"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated failure")

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=3),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"failing": _FailingChannel(fake_config, mgr.bus)}
    mgr._dispatch_task = None

    msg = OutboundMessage(channel="failing", chat_id="123", content="test")

    # Patch asyncio.sleep to avoid actual delays
    with patch("nanobot.channels.manager.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await mgr._send_with_retry(mgr.channels["failing"], msg)

    assert call_count == 3  # 3 total attempts (initial + 2 retries)
    assert mock_sleep.call_count == 2  # 2 sleeps between retries


@pytest.mark.asyncio
async def test_send_with_retry_no_retry_when_max_is_zero():
    """_send_with_retry should not retry when send_max_retries is 0."""
    call_count = 0

    class _FailingChannel(BaseChannel):
        name = "failing"
        display_name = "Failing"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated failure")

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=0),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"failing": _FailingChannel(fake_config, mgr.bus)}
    mgr._dispatch_task = None

    msg = OutboundMessage(channel="failing", chat_id="123", content="test")

    with patch("nanobot.channels.manager.asyncio.sleep", new_callable=AsyncMock):
        await mgr._send_with_retry(mgr.channels["failing"], msg)

    assert call_count == 1  # Called once but no retry (max(0, 1) = 1)


@pytest.mark.asyncio
async def test_send_with_retry_calls_send_delta():
    """_send_with_retry should call send_delta for stream delta events."""
    calls: list[tuple[str, str, str | None, bool, bool, bool]] = []

    class _StreamingChannel(BaseChannel):
        name = "streaming"
        display_name = "Streaming"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            pass  # Should not be called

        async def send_delta(
            self,
            chat_id: str,
            delta: str,
            metadata: dict | None = None,
            *,
            stream_id: str | None = None,
            stream_end: bool = False,
            resuming: bool = False,
            merge_next: bool = False,
        ) -> None:
            calls.append((chat_id, delta, stream_id, stream_end, resuming, merge_next))

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=3),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"streaming": _StreamingChannel(fake_config, mgr.bus)}
    mgr._dispatch_task = None

    msg = outbound_message_for_event(
        channel="streaming",
        chat_id="123",
        event=StreamDeltaEvent(content="test delta", stream_id="s1"),
    )
    await mgr._send_with_retry(mgr.channels["streaming"], msg)
    end = outbound_message_for_event(
        channel="streaming",
        chat_id="123",
        event=StreamEndEvent(
            content="",
            stream_id="s1",
            resuming=True,
            merge_next=True,
        ),
    )
    await mgr._send_with_retry(mgr.channels["streaming"], end)

    assert calls == [
        ("123", "test delta", "s1", False, False, False),
        ("123", "", "s1", True, True, True),
    ]


@pytest.mark.asyncio
async def test_send_with_retry_skips_send_when_streamed():
    """_send_with_retry should not call send for streamed response events."""
    send_called = False
    send_delta_called = False

    class _StreamedChannel(BaseChannel):
        name = "streamed"
        display_name = "Streamed"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            nonlocal send_called
            send_called = True

        async def send_delta(
            self,
            chat_id: str,
            delta: str,
            metadata: dict | None = None,
            *,
            stream_id: str | None = None,
            stream_end: bool = False,
            resuming: bool = False,
        ) -> None:
            nonlocal send_delta_called
            send_delta_called = True

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=3),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"streamed": _StreamedChannel(fake_config, mgr.bus)}
    mgr._dispatch_task = None

    msg = outbound_message_for_event(
        channel="streamed",
        chat_id="123",
        event=StreamedResponseEvent(),
        content="test",
    )
    await mgr._send_with_retry(mgr.channels["streamed"], msg)

    assert send_called is False
    assert send_delta_called is False


def test_outbound_duplicate_suppression_is_scoped_to_origin_message() -> None:
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=3),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {}
    mgr._dispatch_task = None
    mgr._origin_reply_fingerprints = {}

    first = OutboundMessage(
        channel="feishu",
        chat_id="chat123",
        content="Done",
        metadata={"message_id": "msg-1"},
    )
    duplicate = OutboundMessage(
        channel="feishu",
        chat_id="chat123",
        content="  Done  ",
        metadata={"origin_message_id": "msg-1"},
    )
    separate_turn = OutboundMessage(
        channel="feishu",
        chat_id="chat123",
        content="Done",
        metadata={"message_id": "msg-2"},
    )
    new_origin_content = OutboundMessage(
        channel="feishu",
        chat_id="chat123",
        content="Done with extra details",
        metadata={"origin_message_id": "msg-1"},
    )

    assert mgr._should_suppress_outbound(first) is False
    assert mgr._should_suppress_outbound(duplicate) is True
    assert mgr._should_suppress_outbound(separate_turn) is False
    assert mgr._should_suppress_outbound(new_origin_content) is False


@pytest.mark.asyncio
async def test_send_with_retry_propagates_cancelled_error():
    """_send_with_retry should re-raise CancelledError for graceful shutdown."""
    class _CancellingChannel(BaseChannel):
        name = "cancelling"
        display_name = "Cancelling"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            raise asyncio.CancelledError("simulated cancellation")

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=3),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"cancelling": _CancellingChannel(fake_config, mgr.bus)}
    mgr._dispatch_task = None

    msg = OutboundMessage(channel="cancelling", chat_id="123", content="test")

    with pytest.raises(asyncio.CancelledError):
        await mgr._send_with_retry(mgr.channels["cancelling"], msg)


@pytest.mark.asyncio
async def test_send_with_retry_propagates_cancelled_error_during_sleep():
    """_send_with_retry should re-raise CancelledError during sleep."""
    call_count = 0

    class _FailingChannel(BaseChannel):
        name = "failing"
        display_name = "Failing"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated failure")

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=3),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"failing": _FailingChannel(fake_config, mgr.bus)}
    mgr._dispatch_task = None

    msg = OutboundMessage(channel="failing", chat_id="123", content="test")

    # Mock sleep to raise CancelledError
    async def cancel_during_sleep(_):
        raise asyncio.CancelledError("cancelled during sleep")

    with patch("nanobot.channels.manager.asyncio.sleep", side_effect=cancel_during_sleep):
        with pytest.raises(asyncio.CancelledError):
            await mgr._send_with_retry(mgr.channels["failing"], msg)

    # Should have attempted once before sleep was cancelled
    assert call_count == 1


# ---------------------------------------------------------------------------
# ChannelManager - lifecycle and getters
# ---------------------------------------------------------------------------

class _ChannelWithAllowFrom(BaseChannel):
    """Channel with configurable allow_from."""
    name = "withallow"
    display_name = "With Allow"

    def __init__(self, config, bus, allow_from):
        super().__init__(config, bus)
        if isinstance(self.config, dict):
            self.config["allow_from"] = allow_from
        else:
            self.config.allow_from = allow_from

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        pass


class _StartableChannel(BaseChannel):
    """Channel that tracks start/stop calls."""
    name = "startable"
    display_name = "Startable"

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send(self, msg: OutboundMessage) -> None:
        pass


@pytest.mark.asyncio
async def test_validate_allow_from_allows_empty_list():
    """Empty allow_from is valid now — pairing store handles unapproved senders."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.channels = {"test": _ChannelWithAllowFrom(fake_config, None, [])}
    mgr._dispatch_task = None

    # Should not raise — empty list defers to pairing store
    mgr._validate_allow_from()
    assert list(mgr.channels) == ["test"]
    assert mgr.channels["test"].config.allow_from == []


@pytest.mark.asyncio
async def test_validate_allow_from_passes_with_asterisk():
    """_validate_allow_from should not raise when allow_from contains '*'."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.channels = {"test": _ChannelWithAllowFrom(fake_config, None, ["*"])}
    mgr._dispatch_task = None

    # Should not raise
    mgr._validate_allow_from()
    assert list(mgr.channels) == ["test"]
    assert mgr.channels["test"].config.allow_from == ["*"]


@pytest.mark.asyncio
async def test_validate_allow_from_allows_empty_dict_allow_from():
    """Empty dict-backed allow_from is valid — pairing store handles approval."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.channels = {"test": _ChannelWithAllowFrom({"enabled": True}, None, [])}
    mgr._dispatch_task = None

    mgr._validate_allow_from()
    assert list(mgr.channels) == ["test"]
    assert mgr.channels["test"].config["allow_from"] == []


@pytest.mark.asyncio
async def test_validate_allow_from_allows_missing_allow_from():
    """Omitted allowFrom is valid — channel operates in pairing-only mode."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    class _NoAllowFromChannel(BaseChannel):
        name = "noallow"
        display_name = "No Allow"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            pass

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.channels = {"test": _NoAllowFromChannel({"enabled": True}, None)}
    mgr._dispatch_task = None

    # Should not raise — pairing-only mode
    mgr._validate_allow_from()
    assert list(mgr.channels) == ["test"]
    assert "allow_from" not in mgr.channels["test"].config


@pytest.mark.asyncio
async def test_get_channel_returns_channel_if_exists():
    """get_channel should return the channel if it exists."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"telegram": _StartableChannel(fake_config, mgr.bus)}
    mgr._dispatch_task = None

    assert mgr.get_channel("telegram") is not None
    assert mgr.get_channel("nonexistent") is None


@pytest.mark.asyncio
async def test_get_status_returns_running_state():
    """get_status should return enabled and running state for each channel."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    ch = _StartableChannel(fake_config, mgr.bus)
    mgr.channels = {"startable": ch}
    mgr._dispatch_task = None

    status = mgr.get_status()

    assert status["startable"]["enabled"] is True
    assert status["startable"]["running"] is False  # Not started yet


@pytest.mark.asyncio
async def test_enabled_channels_returns_channel_names():
    """enabled_channels should return list of enabled channel names."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {
        "telegram": _StartableChannel(fake_config, mgr.bus),
        "slack": _StartableChannel(fake_config, mgr.bus),
    }
    mgr._dispatch_task = None

    enabled = mgr.enabled_channels

    assert "telegram" in enabled
    assert "slack" in enabled
    assert len(enabled) == 2


@pytest.mark.asyncio
async def test_stop_all_cancels_dispatcher_and_stops_channels():
    """stop_all should cancel the dispatch task and stop all channels."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()

    ch = _StartableChannel(fake_config, mgr.bus)
    mgr.channels = {"startable": ch}
    mgr._channel_tasks = {}

    # Create a real cancelled task
    async def dummy_task():
        while True:
            await asyncio.sleep(1)

    dispatch_task = asyncio.create_task(dummy_task())
    mgr._dispatch_task = dispatch_task

    await mgr.stop_all()

    # Task should be cancelled
    assert dispatch_task.cancelled()
    # Channel should be stopped
    assert ch.stopped is True


@pytest.mark.asyncio
async def test_start_channel_logs_error_on_failure():
    """_start_channel should log error when channel start fails."""
    class _FailingChannel(BaseChannel):
        name = "failing"
        display_name = "Failing"

        async def start(self) -> None:
            raise RuntimeError("connection failed")

        async def stop(self) -> None:
            pass

        async def send(self, msg: OutboundMessage) -> None:
            pass

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {}
    mgr._dispatch_task = None

    ch = _FailingChannel(fake_config, mgr.bus)

    # Should not raise, just log error
    await mgr._start_channel("failing", ch)
    assert mgr.channels == {}
    assert mgr._dispatch_task is None


@pytest.mark.asyncio
async def test_stop_all_handles_channel_exception():
    """stop_all should handle exceptions when stopping channels gracefully."""
    class _StopFailingChannel(BaseChannel):
        name = "stopfailing"
        display_name = "Stop Failing"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            raise RuntimeError("stop failed")

        async def send(self, msg: OutboundMessage) -> None:
            pass

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {"stopfailing": _StopFailingChannel(fake_config, mgr.bus)}
    mgr._channel_tasks = {}
    mgr._dispatch_task = None

    # Should not raise even if channel.stop() raises
    await mgr.stop_all()
    assert list(mgr.channels) == ["stopfailing"]
    assert mgr._dispatch_task is None


@pytest.mark.asyncio
async def test_stop_all_handles_channel_stop_cancelled_task():
    """stop_all should treat a channel's already-cancelled internals as stopped."""

    class _StopCancelledChannel(BaseChannel):
        name = "stopcancelled"
        display_name = "Stop Cancelled"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            raise asyncio.CancelledError("server task cancelled")

        async def send(self, msg: OutboundMessage) -> None:
            pass

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    next_channel = _StartableChannel(fake_config, mgr.bus)
    mgr.channels = {
        "stopcancelled": _StopCancelledChannel(fake_config, mgr.bus),
        "next": next_channel,
    }
    mgr._channel_tasks = {}
    mgr._dispatch_task = None

    await mgr.stop_all()

    assert next_channel.stopped is True


@pytest.mark.asyncio
async def test_start_all_no_channels_logs_warning():
    """start_all should log warning when no channels are enabled."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    mgr.channels = {}  # No channels
    mgr._dispatch_task = None

    # Should return early without creating dispatch task
    await mgr.start_all()

    assert mgr._dispatch_task is None


@pytest.mark.asyncio
async def test_start_all_creates_dispatch_task():
    """start_all should create the dispatch task when channels exist."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()

    ch = _StartableChannel(fake_config, mgr.bus)
    mgr.channels = {"startable": ch}
    mgr._channel_tasks = {}
    mgr._dispatch_task = None

    # Cancel immediately after start to avoid running forever
    async def cancel_after_start():
        await asyncio.sleep(0.01)
        if mgr._dispatch_task:
            mgr._dispatch_task.cancel()

    cancel_task = asyncio.create_task(cancel_after_start())

    try:
        await mgr.start_all()
    except asyncio.CancelledError:
        pass
    finally:
        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass

    # Dispatch task should have been created
    assert mgr._dispatch_task is not None


@pytest.mark.asyncio
async def test_notify_restart_done_waits_until_channel_starts():
    """Restart notice should not be sent before the target channel starts."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    channel = _StartableChannel(fake_config, mgr.bus)
    mgr.channels = {"feishu": channel}
    mgr._dispatch_task = None
    mgr._send_with_retry = AsyncMock()

    notice = RestartNotice(channel="feishu", chat_id="oc_123", started_at_raw="100.0")
    with patch("nanobot.channels.manager.consume_restart_notice_from_env", return_value=notice):
        task = mgr._notify_restart_done_if_needed()

    await asyncio.sleep(0)
    mgr._send_with_retry.assert_not_awaited()

    channel._running = True
    assert task is not None
    await asyncio.wait_for(task, timeout=1.0)

    mgr._send_with_retry.assert_awaited_once()
    sent_channel, sent_msg = mgr._send_with_retry.await_args.args
    assert sent_channel is channel
    assert sent_msg.channel == "feishu"
    assert sent_msg.chat_id == "oc_123"
    assert sent_msg.content.startswith("Restart completed")


@pytest.mark.asyncio
async def test_websocket_restart_notice_does_not_overwrite_recovery_state():
    """WebSocket attach/recovery events already own reconnect state."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )
    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    channel = _StartableChannel(fake_config, mgr.bus)
    channel._running = True
    mgr.channels = {"websocket": channel}
    mgr._send_with_retry = AsyncMock()

    notice = RestartNotice(channel="websocket", chat_id="chat", started_at_raw="100.0")
    await mgr._send_restart_notice_when_started(notice)

    mgr._send_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_notice_retries_until_running_channel_accepts_delivery():
    """A running flag must not make an early transport failure final."""

    class _EventuallyDeliverableChannel(_StartableChannel):
        def __init__(self, config, bus):
            super().__init__(config, bus)
            self.attempts = 0
            self.sent: OutboundMessage | None = None

        async def send(self, msg: OutboundMessage) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("transport not ready")
            self.sent = msg

    fake_config = SimpleNamespace(
        channels=ChannelsConfig(send_max_retries=1),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )
    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.bus = MessageBus()
    channel = _EventuallyDeliverableChannel(fake_config, mgr.bus)
    channel._running = True
    mgr.channels = {"discord": channel}

    notice = RestartNotice(channel="discord", chat_id="123", started_at_raw="")
    with patch("nanobot.channels.manager._SEND_RETRY_DELAYS", (0,)):
        await mgr._send_restart_notice_when_started(notice, timeout_s=0.1, poll_s=0.01)

    assert channel.attempts == 2
    assert channel.sent is not None
    assert channel.sent.content == "Restart completed."
