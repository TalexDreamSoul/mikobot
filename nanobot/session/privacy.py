"""Privacy-scope helpers for durable session identifiers."""

from __future__ import annotations


def session_privacy_scope(session_key: str | None) -> tuple[str, str] | None:
    """Return the user/vault pair encoded in an authorized session key."""
    if not session_key:
        return None
    parts = session_key.split(":", 3)
    if len(parts) == 4 and parts[0] == "vault" and parts[1] and parts[2]:
        return (parts[1], parts[2])
    unified = session_key.split(":", 2)
    if len(unified) == 3 and unified[0] == "unified" and unified[1] and unified[2]:
        return (unified[1], unified[2])
    return None


def same_privacy_scope(source_session_key: str | None, target_session_key: str | None) -> bool:
    """Allow cross-session access only inside one explicitly scoped vault."""
    source = session_privacy_scope(source_session_key)
    target = session_privacy_scope(target_session_key)
    # Legacy single-user sessions predate vault keys. They remain mutually
    # compatible for migration; a scoped session never crosses into them.
    if source is None and target is None:
        return True
    return source is not None and source == target
