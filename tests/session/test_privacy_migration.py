from __future__ import annotations

from pathlib import Path

from nanobot.session.manager import SessionManager
from nanobot.session.privacy import user_scoped_session_key


def test_user_scoped_session_key_translates_only_retired_vault_keys() -> None:
    assert user_scoped_session_key("vault:alice:private:telegram:one") == "user:alice:telegram:one"
    assert user_scoped_session_key("unified:alice:private") == "unified:alice"
    assert user_scoped_session_key("user:alice:telegram:one") is None
    assert user_scoped_session_key("unified:default") is None
    assert user_scoped_session_key("telegram:one") is None


def test_session_store_renames_vault_scoped_sessions_on_startup(tmp_path: Path) -> None:
    """Sessions written under the retired vault namespace keep their history."""
    workspace = tmp_path / "workspace"
    root = tmp_path / "sessions"
    manager = SessionManager(workspace, sessions_root=root)
    legacy = manager.get_or_create("vault:alice:private:telegram:one")
    legacy.add_message("user", "keep me")
    manager.save(legacy, fsync=True)
    conflicting = manager.get_or_create("vault:bob:work:telegram:two")
    conflicting.add_message("user", "older")
    manager.save(conflicting, fsync=True)
    current = manager.get_or_create("user:bob:telegram:two")
    current.add_message("user", "newer")
    manager.save(current, fsync=True)

    reopened = SessionManager(workspace, sessions_root=root)

    migrated = reopened.get_or_create("user:alice:telegram:one")
    assert [message["content"] for message in migrated.messages] == ["keep me"]
    assert reopened.read_session_metadata("vault:alice:private:telegram:one") is None
    assert [m["content"] for m in reopened.get_or_create("user:bob:telegram:two").messages] == ["newer"]
    assert reopened.read_session_metadata("vault:bob:work:telegram:two") is not None
