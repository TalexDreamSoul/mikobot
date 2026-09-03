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
