from __future__ import annotations

import pytest

from nanobot.channels.contracts import (
    channel_coerce_config_value,
    channel_configure_instance,
    channel_instance_specs,
)
from nanobot.channels.manager import ChannelManager
from nanobot.channels.registry import load_channel_plugin
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.webui.settings_system import (
    system_settings_payload,
    update_agent_system_settings,
)


def test_channel_domain_coerces_settings_values_and_skips_blank_secrets() -> None:
    cases = (
        ("list", "allow_from", "alice, bob", "list", ["alice", "bob"]),
        ("bool", "enabled", "yes", "bool", True),
        ("int", "port", "8765", "int", 8765),
        ("float", "delay", "0.6", "float", 0.6),
        ("json", "overrides", '{"123": "open"}', "json", {"123": "open"}),
        ("clear secret", "token", None, "secret", ""),
    )

    for _, raw_key, raw_value, value_type, expected in cases:
        save, value = channel_coerce_config_value(raw_key, raw_value, value_type)
        assert save is True
        assert value == expected

    save, _ = channel_coerce_config_value("token", "", "secret")
    assert save is False


def test_action_errors_are_redacted_and_bounded_before_reaching_a_response() -> None:
    """CLI and MCP action errors carry package-manager output, so they cannot ship raw."""
    from nanobot.webui.settings_system import _MAX_ACTION_ERROR, _safe_action_error

    class _DomainError(Exception):
        def __init__(self, message: str) -> None:
            super().__init__(message)
            self.message = message

    indexed = _safe_action_error(
        _DomainError("pip failed: https://svc:t0k3nvalue@pypi.internal/simple returned 403")
    )
    assert "t0k3nvalue" not in indexed
    assert "<redacted>" in indexed
    assert "pypi.internal" in indexed

    # An exception without ``.message`` used to reach the body as a raw ``str(exc)``.
    unbounded = _safe_action_error(RuntimeError("x" * 12_000))
    assert len(unbounded) == _MAX_ACTION_ERROR

    # Host paths stay: both callers are administrator-only and the path is the diagnosis.
    assert _safe_action_error(_DomainError("npx not found at /usr/local/bin")) == (
        "npx not found at /usr/local/bin"
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
    assert set(payload) == {"runtime", "runtime_config", "usage", "advanced", "version", "docs"}




def test_channel_secret_is_cleared_only_by_explicit_null() -> None:
    config = Config.model_validate({
        "channels": {"matrix": {"password": "saved-password"}},
    })

    updated, saved = channel_configure_instance(
        load_channel_plugin("matrix"),
        config.channels.matrix,
        {"channels.matrix.password": None},
    )

    assert saved == ("channels.matrix.password",)
    assert updated["password"] == ""


@pytest.mark.parametrize("existing_key", ["showCompactionNotices", "show_compaction_notices"])
def test_compaction_notice_webui_contract_and_config_round_trip(existing_key, tmp_path):
    name = "qq"
    plugin = load_channel_plugin(name)
    spec = plugin.setup
    field = next(item for item in spec.to_public_dict(name)["fields"]
                 if item["field"] == "showCompactionNotices")
    assert field["kind"] == "bool"
    assert field["inheritable"] is True
    assert "default_value" not in field
    config = Config.model_validate({
        "channels": {"showCompactionNotices": True, name: {existing_key: False}},
    })
    key = f"channels.{name}.showCompactionNotices"
    manager = ChannelManager.__new__(ChannelManager)
    # The generic settings route must preserve false, true and a return to inheritance.
    for submitted, expected in [("false", False), ("true", True), ("", None), (None, None)]:
        updated, _ = channel_configure_instance(
            plugin, getattr(config.channels, name), {key: submitted},
        )
        setattr(config.channels, name, updated)
        config_path = tmp_path / "config.json"
        save_config(config, config_path)
        config = load_config(config_path)
        assert config.channels.show_compaction_notices is True
        section = getattr(config.channels, name)
        [instance] = channel_instance_specs(plugin, section, enabled_only=False)
        assert instance.config.get("showCompactionNotices") is expected
        assert "show_compaction_notices" not in instance.config
        if expected is None:
            # Inheritance is persisted as absence, also readable by older QQ versions.
            assert "showCompactionNotices" not in instance.config
        assert manager._resolve_bool_override(
            instance.config, "show_compaction_notices", True,
        ) is (True if expected is None else expected)


def test_saving_other_qq_settings_does_not_pin_the_global_notice_policy():
    config = Config.model_validate({"channels": {"showCompactionNotices": True, "qq": {}}})
    updated, _ = channel_configure_instance(
        load_channel_plugin("qq"), config.channels.qq, {"appId": "a", "secret": "s"},
    )
    assert "showCompactionNotices" not in updated
