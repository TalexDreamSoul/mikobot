from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanobot.channels.contracts import ChannelInstanceSpec, ChannelManagementSpec
from nanobot.channels.manager import ChannelManager
from nanobot.channels.plugin import ChannelPlugin


def _instance_specs(section, *, enabled_only=True):
    entries = section.get("instances", []) if isinstance(section, dict) else []
    return [
        ChannelInstanceSpec(instance_id=entry["id"], config=entry)
        for entry in entries
        if not enabled_only or entry.get("enabled", False)
    ]


def _update_instance_config(section, values, *, instance_id="default"):
    return {
        **section,
        "instances": [
            {**entry, **values} if entry.get("id") == instance_id else dict(entry)
            for entry in section.get("instances", [])
        ],
    }


def _manager_double(monkeypatch, delegate: AsyncMock) -> ChannelManager:
    plugin = ChannelPlugin(
        name="fixture",
        display_name="Fixture",
        runtime="tests.channels:unused",
        management=ChannelManagementSpec(
            multi_instance=True,
            instance_specs=_instance_specs,
            runtime_name=lambda name, instance_id: (
                name if instance_id == "default" else f"{name}.{instance_id}"
            ),
            update_instance_config=_update_instance_config,
        ),
    )

    def discover_plugins(enabled_names=None):
        if enabled_names is not None and "fixture" not in enabled_names:
            return {}
        return {"fixture": plugin}

    monkeypatch.setattr(
        "nanobot.channels.registry.discover_plugins",
        discover_plugins,
    )
    section = {
        "instances": [
            {"id": "product", "enabled": True},
            {"id": "staging", "enabled": True},
        ]
    }
    manager = ChannelManager.__new__(ChannelManager)
    manager.config = SimpleNamespace(channels=SimpleNamespace(fixture=section))
    manager._channel_section = (
        lambda _name, *, config=None, default_enabled=False: section
    )
    manager._apply_channel_runtime_action = delegate
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_action", "delegated_action", "safe_failure_message"),
    [
        ("enable", "enable", "Channel could not be enabled."),
        ("disable", "disable", "Channel could not be disabled."),
        ("reconnect", "enable", "Channel could not be reconnected."),
        ("pairing", "pairing", "Pairing listener could not be started."),
    ],
)
async def test_instance_action_delegates_once_to_the_requested_instance_without_persisting(
    monkeypatch,
    requested_action,
    delegated_action,
    safe_failure_message,
):
    injected_detail = "fixture-secret /private/runtime/credentials.json"
    delegate = AsyncMock(
        return_value={
            "handled": True,
            "ok": False,
            "requires_restart": True,
            "message": injected_detail,
            "exception": injected_detail,
        }
    )
    manager = _manager_double(monkeypatch, delegate)

    def persistence_must_not_run(*_args, **_kwargs):
        raise AssertionError("the manager port must not persist channel configuration")

    monkeypatch.setattr("nanobot.config.loader.save_config", persistence_must_not_run)

    result = await manager.apply_channel_instance_action(
        requested_action,
        "fixture",
        "product",
    )

    delegate.assert_awaited_once_with(delegated_action, "fixture", "product")
    assert result == {
        "handled": True,
        "ok": False,
        "requires_restart": True,
        "message": safe_failure_message,
    }
    assert injected_detail not in result["message"]
    assert len(result["message"]) <= 1_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "channel_type", "instance_id"),
    [
        ("", "fixture", "product"),
        ("activate", "fixture", "product"),
        ("enable", "", "product"),
        ("enable", "missing", "product"),
        ("enable", "fixture", ""),
        ("enable", "fixture", "unknown"),
    ],
)
async def test_instance_action_rejects_invalid_or_nonexact_targets_before_delegation(
    monkeypatch,
    action,
    channel_type,
    instance_id,
):
    delegate = AsyncMock()
    manager = _manager_double(monkeypatch, delegate)

    result = await manager.apply_channel_instance_action(action, channel_type, instance_id)

    delegate.assert_not_awaited()
    assert set(result) == {"handled", "ok", "requires_restart", "message"}
    assert result["ok"] is False
    assert isinstance(result["handled"], bool)
    assert isinstance(result["requires_restart"], bool)
    assert isinstance(result["message"], str)
    assert 0 < len(result["message"]) <= 1_000
