from __future__ import annotations

from nanobot.channels.contracts import channel_coerce_config_value
from nanobot.config.schema import Config
from nanobot.webui.settings_system import (
    system_settings_payload,
    update_agent_system_settings,
)


def test_system_domain_owns_runtime_dto_and_agent_updates(tmp_path) -> None:
    config = Config()

    changed, restart_required = update_agent_system_settings(
        config,
        {
            "timezone": ["Asia/Shanghai"],
            "tool_hint_max_length": ["120"],
        },
    )
    payload = system_settings_payload(
        config,
        config_path=tmp_path / "config.json",
        version="0.3.0",
    )

    assert changed is True
    assert restart_required is True
    assert config.agents.defaults.timezone == "Asia/Shanghai"
    assert config.agents.defaults.timezone_mode == "manual"
    assert config.agents.defaults.tool_hint_max_length == 120
    assert payload["runtime"]["config_path"] == str(tmp_path / "config.json")
    assert payload["version"] == {"current": "0.3.0"}
    assert payload["docs"]["version"] == "0.3.0"
    assert set(payload) == {"runtime", "usage", "advanced", "version", "docs"}


def test_channel_domain_coerces_settings_values_and_skips_blank_secrets() -> None:
    cases = (
        ("list", "allow_from", "alice, bob", "list", ["alice", "bob"]),
        ("bool", "enabled", "yes", "bool", True),
        ("int", "port", "8765", "int", 8765),
    )

    for _, raw_key, raw_value, value_type, expected in cases:
        save, value = channel_coerce_config_value(raw_key, raw_value, value_type)
        assert save is True
        assert value == expected

    save, _ = channel_coerce_config_value("token", "", "secret")
    assert save is False
