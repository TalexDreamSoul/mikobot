"""Behavioral composition coverage for channel extension services."""

from pathlib import Path

import pytest

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.channels.plugin import ChannelPlugin
from nanobot.config import loader
from nanobot.config.schema import Config, _resolve_tool_config_refs
from nanobot.extensions.adapters import channels as channel_adapters
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
)
from nanobot.extensions.management import build_management_extension_registry
from nanobot.extensions.registry import ExtensionRegistryError
from nanobot.extensions.runtime import build_core_extension_registry

_resolve_tool_config_refs()


def _plugin() -> ChannelPlugin:
    return ChannelPlugin(
        name="demo",
        display_name="Demo",
        runtime="missing.demo.runtime:Channel",
    )


@pytest.mark.asyncio
async def test_core_registry_without_channel_services_refuses_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-only core inventory cannot disable a configured channel."""
    monkeypatch.setattr(channel_adapters, "discover_plugins", lambda _names=None: {"demo": _plugin()})
    config = Config.model_validate({
        "agents": {"defaults": {"workspace": str(tmp_path)}},
        "channels": {"demo": {"enabled": True, "token": "keep-secret"}},
    })
    registry = build_core_extension_registry(config, tools=ToolRegistry())
    component = next(p for p in registry.snapshot().packages if p.name == "demo").components[0]
    with pytest.raises(ExtensionRegistryError) as rejected:
        await registry.execute(ExtensionActionRequest(
            context=ExtensionActionContext(actor_id="owner", is_system_admin=True),
            target_id=component.id,
            action=ExtensionAction.DISABLE,
            expected_revision=component.revision,
        ))
    assert rejected.value.code == "action_not_supported"
    assert config.channels.demo == {"enabled": True, "token": "keep-secret"}


@pytest.mark.asyncio
async def test_management_registry_mutates_only_selected_config_and_rejects_stale_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disable persists in the selected instance; stale actions preserve intervening edits."""
    monkeypatch.setattr(loader, "_current_config_path", loader._current_config_path)
    monkeypatch.setattr(channel_adapters, "discover_plugins", lambda _names=None: {"demo": _plugin()})
    selected = tmp_path / "selected" / "config.json"
    unrelated = tmp_path / "other" / "config.json"
    config = Config.model_validate({
        "agents": {"defaults": {"workspace": str(tmp_path)}},
        "channels": {
            "demo": {"enabled": True, "token": "keep-secret"},
            "sibling": {"enabled": True, "marker": "保留🤖"},
        },
    })
    loader.save_config(config, selected)
    loader.save_config(config, unrelated)
    unrelated_before = unrelated.read_bytes()
    registry = build_management_extension_registry(selected)
    component = next(p for p in registry.snapshot().packages if p.name == "demo").components[0]
    request = ExtensionActionRequest(
        context=ExtensionActionContext(actor_id="owner", is_system_admin=True),
        target_id=component.id,
        action=ExtensionAction.DISABLE,
        expected_revision=component.revision,
    )
    result = await registry.execute(request)
    assert result.ok is True
    saved = loader.load_config(selected)
    assert saved.channels.demo["enabled"] is False
    assert saved.channels.demo["token"] == "keep-secret"
    assert saved.channels.sibling == {"enabled": True, "marker": "保留🤖"}
    assert unrelated.read_bytes() == unrelated_before

    saved.channels.demo["token"] = "replaced-secret"
    loader.save_config(saved, selected)
    before_stale = selected.read_bytes()
    with pytest.raises(ExtensionRegistryError) as rejected:
        await registry.execute(request)
    assert rejected.value.code == "stale_revision"
    assert selected.read_bytes() == before_stale
    assert unrelated.read_bytes() == unrelated_before
