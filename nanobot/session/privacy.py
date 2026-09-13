"""Privacy-scope helpers for durable session identifiers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SessionPrivacyScope:
    """Durable owner and project provenance for a collaboration session."""

    user_id: str
    project_id: str


def session_privacy_scope(session_key: str | None) -> str | None:
    """Return the owning user id encoded in a user-scoped session key."""
    if not session_key:
        return None
    parts = session_key.split(":", 2)
    if len(parts) == 3 and parts[0] == "user" and parts[1]:
        return parts[1]
    unified = session_key.split(":", 1)
    if len(unified) == 2 and unified[0] == "unified" and unified[1]:
        return unified[1]
    return None


def session_project_scope(session_key: str | None) -> SessionPrivacyScope | None:
    """Return explicit user/project ownership encoded by current scoped keys.

    Retired user-only keys deliberately return ``None``: their historical project
    authority is unknowable and they must not be adopted into a new project.
    """
    if not session_key:
        return None
    user = session_key.split(":", 4)
    if (
        len(user) == 5
        and user[0] == "user"
        and user[1]
        and user[2] == "project"
        and user[3]
        and user[4]
    ):
        return SessionPrivacyScope(user[1], user[3])
    unified = session_key.split(":", 3)
    if (
        len(unified) == 4
        and unified[0] == "unified"
        and unified[1]
        and unified[2] == "project"
        and unified[3]
    ):
        return SessionPrivacyScope(unified[1], unified[3])
    return None


def same_privacy_scope(source_session_key: str | None, target_session_key: str | None) -> bool:
    """Allow cross-session access only inside a stable privacy namespace.

    Legacy unscoped host sessions retain their historical compatibility. Current
    collaboration sessions require both the same user and project; user-only
    legacy member keys never bridge into the project-qualified namespace.
    """
    source = session_project_scope(source_session_key)
    target = session_project_scope(target_session_key)
    if source is not None or target is not None:
        return source is not None and source == target
    source_user = session_privacy_scope(source_session_key)
    target_user = session_privacy_scope(target_session_key)
    if source_user is None and target_user is None:
        return True
    return source_user is not None and source_user == target_user

def same_project_owner(
    source_metadata: Mapping[str, Any] | None,
    target_metadata: Mapping[str, Any] | None,
) -> bool:
    """Require explicit, identical collaboration owner and project metadata."""
    if not isinstance(source_metadata, Mapping) or not isinstance(target_metadata, Mapping):
        return False
    source_user = source_metadata.get("collaboration_user_id")
    source_project = source_metadata.get("collaboration_project_id")
    target_user = target_metadata.get("collaboration_user_id")
    target_project = target_metadata.get("collaboration_project_id")
    return (
        isinstance(source_user, str)
        and bool(source_user)
        and isinstance(source_project, str)
        and bool(source_project)
        and source_user == target_user
        and source_project == target_project
    )


def user_scoped_session_key(session_key: str) -> str | None:
    """Translate a retired vault-scoped key into its user-scoped form, or ``None``.

    Current project-qualified keys must never be collapsed by this legacy migration.
    """
    parts = session_key.split(":", 3)
    if len(parts) == 4 and parts[0] == "vault" and parts[1] and parts[2]:
        return f"user:{parts[1]}:{parts[3]}"
    unified = session_key.split(":")
    if len(unified) == 3 and unified[0] == "unified" and unified[1] and unified[2]:
        return f"unified:{unified[1]}"
    return None
