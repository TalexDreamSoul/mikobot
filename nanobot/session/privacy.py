"""Privacy-scope helpers for durable session identifiers."""

from __future__ import annotations


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


def same_privacy_scope(source_session_key: str | None, target_session_key: str | None) -> bool:
    """Allow cross-session access only inside one user's own sessions."""
    source = session_privacy_scope(source_session_key)
    target = session_privacy_scope(target_session_key)
    # Legacy single-user sessions predate user-scoped keys. They remain mutually
    # compatible for migration; a scoped session never crosses into them.
    if source is None and target is None:
        return True
    return source is not None and source == target


def user_scoped_session_key(session_key: str) -> str | None:
    """Translate a retired vault-scoped key into its user-scoped form, or ``None``.

    The vault layer was removed; only the owning user remains meaningful.
    """
    parts = session_key.split(":", 3)
    if len(parts) == 4 and parts[0] == "vault" and parts[1] and parts[2]:
        return f"user:{parts[1]}:{parts[3]}"
    unified = session_key.split(":", 2)
    if len(unified) == 3 and unified[0] == "unified" and unified[1] and unified[2]:
        return f"unified:{unified[1]}"
    return None
