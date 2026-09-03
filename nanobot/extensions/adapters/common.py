"""Shared adapter helpers for canonical, display-safe extension identities."""

from __future__ import annotations

import hashlib
import re

from nanobot.extensions.contracts import (
    ExtensionSource,
    extension_package_id,
    safe_extension_message,
)

_MAX_EXTENSION_NAME = 128
_MAX_EXTENSION_LABEL = 256
_NON_IDENTIFIER = re.compile(r"[^a-z0-9._-]+")


def canonical_extension_name(value: str, *, fallback: str = "extension") -> str:
    """Return a stable canonical segment without merging distinct raw names."""
    try:
        extension_package_id(ExtensionSource.CONFIGURED, value)
    except ValueError:
        digest = hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()[:32]
        prefix = _NON_IDENTIFIER.sub("-", value.lower()).strip("-._")
        if not prefix:
            prefix = fallback
        prefix = prefix[: _MAX_EXTENSION_NAME - len(digest) - 1].rstrip("-._") or fallback
        return f"{prefix}-{digest}"
    return value


def safe_extension_label(value: object, *, fallback: str) -> str:
    """Return bounded display text without exposing an obvious host path."""
    label = safe_extension_message(value)[:_MAX_EXTENSION_LABEL].strip()
    return fallback if not label or label == "<path>" else label


__all__ = ["canonical_extension_name", "safe_extension_label"]
