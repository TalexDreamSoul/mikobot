"""Privacy-scope helpers for durable session identifiers.

Two different questions live here:

* **Key-encoded scope** — a session key may name the member and project it
  belongs to (``user:<uid>:project:<pid>:…``), which survives any metadata loss.
* **Cross-session access** — whether one persisted session may read or message
  another. Members answer with their capability provenance; host turns answer
  with an access record written per turn, so recording access never changes the
  host's prompt, memory, media, or Dream routing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nanobot.session.keys import is_host_private_session_key

SESSION_ACCESS_KIND_METADATA_KEY = "session_access_kind"
SESSION_ACCESS_USER_METADATA_KEY = "session_access_user_id"
SESSION_ACCESS_PROJECT_METADATA_KEY = "session_access_project_id"

_ISOLATED_SCOPE_KIND = "isolated"
_COLLABORATION_USER_METADATA_KEY = "collaboration_user_id"
_COLLABORATION_PROJECT_METADATA_KEY = "collaboration_project_id"


@dataclass(frozen=True, slots=True)
class SessionPrivacyScope:
    """Durable owner and project provenance for a collaboration session."""

    user_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class SessionAccessScope:
    """Where one persisted session sits for cross-session access decisions."""

    user_id: str | None
    project_id: str | None
    isolated: bool = False


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
    """Allow cross-session access only between equally project-qualified keys.

    A key that names no project carries no cross-session authority, so a
    project-qualified key never matches an unscoped one.
    """
    source = session_project_scope(source_session_key)
    target = session_project_scope(target_session_key)
    if source is None or target is None:
        return False
    return source == target


def same_project_owner(
    source_metadata: Mapping[str, Any] | None,
    target_metadata: Mapping[str, Any] | None,
) -> bool:
    """Require explicit, identical collaboration owner and project metadata."""
    if not isinstance(source_metadata, Mapping) or not isinstance(target_metadata, Mapping):
        return False
    source_user = source_metadata.get(_COLLABORATION_USER_METADATA_KEY)
    source_project = source_metadata.get(_COLLABORATION_PROJECT_METADATA_KEY)
    target_user = target_metadata.get(_COLLABORATION_USER_METADATA_KEY)
    target_project = target_metadata.get(_COLLABORATION_PROJECT_METADATA_KEY)
    return (
        isinstance(source_user, str)
        and bool(source_user)
        and isinstance(source_project, str)
        and bool(source_project)
        and source_user == target_user
        and source_project == target_project
    )


def session_access_scope(
    metadata: Mapping[str, Any] | None,
    session_key: str | None,
) -> SessionAccessScope | None:
    """Return the access scope recorded for one session, or ``None`` when unmarked.

    The per-turn record wins; member capability provenance and a
    project-qualified key are the remaining, older ways to mark a session.
    """
    if isinstance(metadata, Mapping):
        kind = metadata.get(SESSION_ACCESS_KIND_METADATA_KEY)
        if isinstance(kind, str) and kind:
            return SessionAccessScope(
                _optional_id(metadata.get(SESSION_ACCESS_USER_METADATA_KEY)),
                _optional_id(metadata.get(SESSION_ACCESS_PROJECT_METADATA_KEY)),
                isolated=kind == _ISOLATED_SCOPE_KIND,
            )
        owner = _optional_id(metadata.get(_COLLABORATION_USER_METADATA_KEY))
        if owner is not None:
            return SessionAccessScope(owner, _optional_id(metadata.get(_COLLABORATION_PROJECT_METADATA_KEY)))
    project_scope = session_project_scope(session_key)
    if project_scope is not None:
        return SessionAccessScope(project_scope.user_id, project_scope.project_id)
    user_id = session_privacy_scope(session_key)
    if user_id is not None:
        return SessionAccessScope(user_id, None)
    return None


def session_access_allowed(
    source_metadata: Mapping[str, Any] | None,
    source_key: str | None,
    target_metadata: Mapping[str, Any] | None,
    target_key: str | None,
) -> bool:
    """Decide whether one persisted session may read or message another.

    A member reaches their own sessions, including the project they run in and
    their retired user-only keys. A channel session reaches only its own
    project. An isolated (unrouted group) session reaches nothing and nothing
    reaches it, not even through the member compatibility path. The owner's own
    host-private turns may read any session this instance persists, and no other
    session may reach them: an access record that is missing never stands in for
    one that is present.
    """
    source = session_access_scope(source_metadata, source_key)
    if source is None:
        return is_host_private_session_key(source_key)
    target = session_access_scope(target_metadata, target_key)
    if source.isolated or (target is not None and target.isolated):
        # Isolation outranks the compatibility path: a conversation that reached
        # no project stays sealed even while its file still carries older member
        # provenance from before it lost its binding.
        return False
    if same_project_owner(source_metadata, target_metadata):
        return True
    if target is None:
        return False
    if source.user_id is None or source.user_id != target.user_id:
        return False
    return source.project_id == target.project_id


def _optional_id(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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
