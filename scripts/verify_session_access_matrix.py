#!/usr/bin/env python3
"""Verify cross-session access decisions over a persisted session store.

Read-only companion to `scripts/backfill_session_access_scope.py`: it loads the
same session metadata and answers, for every session, which other sessions the
shared `session_access_allowed` decision lets it read.

Without `--expect` it prints a per-source visibility summary plus the full
allow matrix as JSON. With `--expect` (a JSON file of `{"assertions": [{"source",
"target", "allowed"}], "visible": [{"source", "count"}]}`) it fails when the
decision disagrees, which is what a deployment check needs.

Usage:
    python -m scripts.verify_session_access_matrix --json-out /tmp/matrix.json
    python -m scripts.verify_session_access_matrix --expect /tmp/matrix-expect.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from nanobot.session.keys import is_host_private_session_key
from nanobot.session.privacy import session_access_allowed

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_session_access_scope import (  # noqa: E402
    iter_session_files,
    read_metadata,
)


def load_sessions(sessions_root: Path) -> dict[str, dict[str, Any]]:
    """Return every persisted session's metadata keyed by session key."""
    return {session.key: read_metadata(session) for session in iter_session_files(sessions_root)}


def visible_targets(
    key: str,
    metadata: dict[str, dict[str, Any]],
) -> list[str]:
    """Return the sessions *key* may read, sorted."""
    source = metadata.get(key) or {}
    return sorted(
        other
        for other, other_metadata in metadata.items()
        if other != key
        and session_access_allowed(source, key, other_metadata, other)
    )


def iter_assertions(payload: dict[str, Any]) -> Iterator[tuple[str, str, bool]]:
    for item in payload.get("assertions") or []:
        yield str(item["source"]), str(item["target"]), bool(item["allowed"])


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions-root", type=Path, default=Path.home() / ".nanobot" / "sessions")
    parser.add_argument("--expect", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    metadata = load_sessions(args.sessions_root)
    matrix = {key: visible_targets(key, metadata) for key in sorted(metadata)}
    channel_sources = [key for key in matrix if not is_host_private_session_key(key)]

    print(f"sessions: {len(metadata)} (channel sources: {len(channel_sources)})")
    for key in channel_sources:
        targets = matrix[key]
        print(f"  {key[:64]:<64} sees {len(targets):>3}")

    payload: dict[str, Any] = {
        "sessions": len(metadata),
        "visibility": {key: len(targets) for key, targets in matrix.items()},
        "matrix": matrix,
    }
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"matrix: {args.json_out}")

    if args.expect is None:
        return 0

    expected = json.loads(args.expect.read_text())
    failures: list[str] = []
    for source, target, allowed in iter_assertions(expected):
        actual = target in matrix.get(source, [])
        if actual is not allowed:
            failures.append(
                f"{source} -> {target}: expected allowed={allowed} got {actual}"
            )
    for item in expected.get("visible") or []:
        source, count = str(item["source"]), int(item["count"])
        actual = len(matrix.get(source, []))
        if actual != count:
            failures.append(f"{source} visible count: expected {count} got {actual}")
    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    print("assertions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
