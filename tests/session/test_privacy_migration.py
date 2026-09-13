from __future__ import annotations

from pathlib import Path

from nanobot.session.manager import SessionManager
from nanobot.session.privacy import (
    SessionPrivacyScope,
    same_privacy_scope,
    session_project_scope,
    user_scoped_session_key,
)


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


def test_project_scoped_sessions_require_the_same_member_and_project() -> None:
    """Cross-session access remains inside one member's current project, while host history stays legacy-compatible."""
    alpha_chat = "user:alice:project:alpha:telegram:one"
    alpha_unified = "unified:alice:project:alpha"
    beta_chat = "user:alice:project:beta:telegram:one"
    bob_alpha = "user:bob:project:alpha:telegram:one"

    assert session_project_scope(alpha_chat) == SessionPrivacyScope("alice", "alpha")
    assert session_project_scope(alpha_unified) == SessionPrivacyScope("alice", "alpha")
    assert same_privacy_scope(alpha_chat, alpha_unified)
    assert not same_privacy_scope(alpha_chat, beta_chat)
    assert not same_privacy_scope(alpha_chat, bob_alpha)
    assert not same_privacy_scope(alpha_chat, "user:alice:telegram:one")
    assert same_privacy_scope("websocket:host-chat", "cli:host-chat")


def test_existing_user_scoped_history_is_not_adopted_by_a_project_scope(tmp_path: Path) -> None:
    """A project-qualified session starts empty rather than replaying ambiguous legacy member history."""
    workspace = tmp_path / "workspace"
    root = tmp_path / "sessions"
    manager = SessionManager(workspace, sessions_root=root)
    legacy_key = "user:alice:telegram:one"
    legacy = manager.get_or_create(legacy_key)
    legacy.add_message("user", "LEGACY_MEMBER_SECRET")
    manager.save(legacy, fsync=True)

    reopened = SessionManager(workspace, sessions_root=root)
    project_session = reopened.get_or_create("user:alice:project:alpha:telegram:one")

    assert project_session.messages == []
    assert [message["content"] for message in reopened.get_or_create(legacy_key).messages] == [
        "LEGACY_MEMBER_SECRET"
    ]


def test_restart_preserves_each_unified_project_history_for_one_member(tmp_path: Path) -> None:
    """Migration never collapses current unified project keys into the retired user-only namespace."""
    workspace = tmp_path / "workspace"
    root = tmp_path / "sessions"
    manager = SessionManager(workspace, sessions_root=root)
    alpha_key = "unified:alice:project:alpha"
    beta_key = "unified:alice:project:beta"
    alpha = manager.get_or_create(alpha_key)
    alpha.add_message("user", "ALPHA_UNIFIED_SECRET")
    manager.save(alpha, fsync=True)
    beta = manager.get_or_create(beta_key)
    beta.add_message("user", "BETA_UNIFIED_SECRET")
    manager.save(beta, fsync=True)

    reopened = SessionManager(workspace, sessions_root=root)

    assert [message["content"] for message in reopened.get_or_create(alpha_key).messages] == [
        "ALPHA_UNIFIED_SECRET"
    ]
    assert [message["content"] for message in reopened.get_or_create(beta_key).messages] == [
        "BETA_UNIFIED_SECRET"
    ]
    assert reopened.read_session_metadata("unified:alice") is None
