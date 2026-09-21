#!/usr/bin/env python3
"""Backfill the per-turn session access record for already-persisted sessions.

`nanobot/agent/loop.py` stamps `session_access_*` on every turn that resolved a
scope. Sessions written before that change carry no record, so the shared
`session_access_allowed` decision treats them as unmarked and refuses
cross-session access. This operator tool resolves each persisted session's scope
through the same collaboration store the gateway uses and writes the record.

Host-private keys (`heartbeat`, `heartbeat:*`, `cron:*`, `dream:*`,
`websocket:*`, `cli:*`) stay unmarked: that is how the decision recognizes the
owner's own namespace. A session whose scope cannot be resolved is marked
isolated, which the decision refuses in both directions.

Only the first JSONL line (session metadata) is rewritten; every later line is
byte-identical and verified by hash. Run it with the gateway stopped.

Usage:
    python -m scripts.backfill_session_access_scope --dry-run --report /tmp/report.json
    python -m scripts.backfill_session_access_scope --sessions-root <dir> --apply --report /tmp/report.json
    python -m scripts.backfill_session_access_scope --sessions-root <dir> --revert --report /tmp/report.json

`--apply` and `--revert` require an explicit `--sessions-root`: the tool refuses to
mutate a default store it was not pointed at.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanobot.collaboration.pairing import runtime_channel_key
from nanobot.collaboration.store import CollaborationStore
from nanobot.session.keys import is_host_private_session_key
from nanobot.session.privacy import (
    SESSION_ACCESS_KIND_METADATA_KEY,
    SESSION_ACCESS_PROJECT_METADATA_KEY,
    SESSION_ACCESS_USER_METADATA_KEY,
    session_privacy_scope,
    session_project_scope,
)

ACCESS_KEYS = (
    SESSION_ACCESS_KIND_METADATA_KEY,
    SESSION_ACCESS_USER_METADATA_KEY,
    SESSION_ACCESS_PROJECT_METADATA_KEY,
)
_ISOLATED = "isolated"
_BOUND = "bound"
_DIRECT = "direct"


@dataclass(slots=True)
class SessionFile:
    """One persisted session file split into its metadata line and the rest."""

    path: Path
    key: str
    first_line: str
    rest: bytes


def decode_session_key(stem: str) -> str | None:
    """Decode a canonical base64url session filename stem back to its session key."""
    try:
        raw = base64.urlsafe_b64decode(stem + "=" * (-len(stem) % 4))
    except (ValueError, TypeError):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def iter_session_files(sessions_root: Path) -> Iterator[SessionFile]:
    """Yield every canonical session file under a sessions root, key-first."""
    for path in sorted(sessions_root.rglob("*.jsonl")):
        key = decode_session_key(path.stem)
        if key is None:
            print(f"skip (undecodable filename): {path}", file=sys.stderr)
            continue
        payload = path.read_bytes()
        first_line, _, rest = payload.partition(b"\n")
        yield SessionFile(
            path=path,
            key=key,
            first_line=first_line.decode("utf-8", errors="replace"),
            rest=rest,
        )


def read_metadata(session: SessionFile) -> dict[str, Any]:
    """Return the session metadata mapping, or an empty mapping when unreadable."""
    try:
        record = json.loads(session.first_line)
    except json.JSONDecodeError:
        return {}
    metadata = record.get("metadata") if isinstance(record, dict) else None
    return dict(metadata) if isinstance(metadata, dict) else {}


def current_record(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return the access keys already present in *metadata*."""
    return {key: metadata.get(key) for key in ACCESS_KEYS if key in metadata}


def assigned_channel_names(collaboration_path: Path) -> set[str]:
    """Return the runtime channel names that carry an enabled channel assignment."""
    try:
        state = json.loads(collaboration_path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    assignments = state.get("channelAssignments")
    if not isinstance(assignments, dict):
        return set()
    names: set[str] = set()
    for value in assignments.values():
        if not isinstance(value, dict):
            continue
        channel_type, instance_id = value.get("channelType"), value.get("instanceId")
        if isinstance(channel_type, str) and isinstance(instance_id, str):
            names.add(runtime_channel_key(channel_type, instance_id))
    return names


def split_route(key: str) -> tuple[str, str, str | None]:
    """Split `<channel>:<chat>[:<thread>]` into its parts."""
    parts = key.split(":")
    channel = parts[0]
    chat = parts[1] if len(parts) > 1 else ""
    thread = parts[2] if len(parts) > 2 and parts[2] else None
    return channel, chat, thread


def resolve_record(
    session: SessionFile,
    metadata: dict[str, Any],
    store: CollaborationStore,
    workspace: str,
    assigned: set[str],
) -> dict[str, Any] | None:
    """Return the access record for one session, or ``None`` to leave it unmarked."""
    if is_host_private_session_key(session.key):
        return None
    project_scope = session_project_scope(session.key)
    if project_scope is not None:
        return {
            SESSION_ACCESS_KIND_METADATA_KEY: _BOUND
            if _stored_binding(metadata)
            else _DIRECT,
            SESSION_ACCESS_USER_METADATA_KEY: project_scope.user_id,
            SESSION_ACCESS_PROJECT_METADATA_KEY: project_scope.project_id,
        }
    user_only = session_privacy_scope(session.key)
    if user_only is not None:
        stored_project = metadata.get("collaboration_project_id")
        return {
            SESSION_ACCESS_KIND_METADATA_KEY: _BOUND if _stored_binding(metadata) else _DIRECT,
            SESSION_ACCESS_USER_METADATA_KEY: user_only,
            SESSION_ACCESS_PROJECT_METADATA_KEY: (
                stored_project if isinstance(stored_project, str) and stored_project else None
            ),
        }
    channel, chat, thread = split_route(session.key)
    if not chat:
        return {
            SESSION_ACCESS_KIND_METADATA_KEY: _ISOLATED,
            SESSION_ACCESS_USER_METADATA_KEY: None,
            SESSION_ACCESS_PROJECT_METADATA_KEY: None,
        }
    stored_channel = metadata.get("collaboration_channel")
    stored_chat = metadata.get("collaboration_chat_id")
    route_channel = stored_channel if isinstance(stored_channel, str) and stored_channel else channel
    route_chat = stored_chat if isinstance(stored_chat, str) and stored_chat else chat
    message_metadata: dict[str, Any] = {"thread_id": thread} if thread else {}
    if not thread and route_channel in assigned:
        message_metadata["chat_type"] = "p2p"
    scope = store.resolve_scope(
        route_channel,
        route_chat,
        route_chat,
        message_metadata,
        workspace,
    )
    if scope.project_id is None:
        return {
            SESSION_ACCESS_KIND_METADATA_KEY: _ISOLATED,
            SESSION_ACCESS_USER_METADATA_KEY: scope.user_id,
            SESSION_ACCESS_PROJECT_METADATA_KEY: None,
        }
    return {
        SESSION_ACCESS_KIND_METADATA_KEY: _BOUND if scope.binding is not None else _DIRECT,
        SESSION_ACCESS_USER_METADATA_KEY: scope.user_id,
        SESSION_ACCESS_PROJECT_METADATA_KEY: scope.project_id,
    }


def _stored_binding(metadata: dict[str, Any]) -> bool:
    return isinstance(metadata.get("collaboration_binding_id"), str)


def render_first_line(session: SessionFile, metadata: dict[str, Any]) -> bytes:
    """Serialize the metadata line with the current record, preserving other fields."""
    record = json.loads(session.first_line)
    if not isinstance(record, dict):
        raise ValueError(f"session metadata line is not an object: {session.path}")
    record["metadata"] = metadata
    return json.dumps(record, ensure_ascii=False).encode("utf-8")


def apply_entry(session: SessionFile, record: dict[str, Any] | None) -> bytes:
    """Return the new first line for *session* with *record* applied or removed."""
    metadata = read_metadata(session)
    for key in ACCESS_KEYS:
        metadata.pop(key, None)
    if record is not None:
        metadata.update(record)
    return render_first_line(session, metadata)


def backup_store(sessions_root: Path, collaboration_path: Path, backup_root: Path) -> Path:
    """Copy the session store and collaboration store aside before mutating."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_root / stamp
    suffix = 1
    while target.exists():
        suffix += 1
        target = backup_root / f"{stamp}-{suffix}"
    target.mkdir(parents=True)
    shutil.copytree(sessions_root, target / sessions_root.name)
    if collaboration_path.is_file():
        shutil.copy2(collaboration_path, target / collaboration_path.name)
    return target


def run(
    *,
    sessions_root: Path,
    collaboration_path: Path,
    workspace: str,
    mode: str,
    report_path: Path | None,
    backup_root: Path | None,
) -> int:
    store = CollaborationStore(store_path=collaboration_path)
    assigned = assigned_channel_names(collaboration_path)
    entries: list[dict[str, Any]] = []
    summary = {"total": 0, "marked": 0, "unchanged": 0, "host_private": 0, "isolated": 0, "unresolved": 0}
    if mode == "apply" and backup_root is not None:
        target = backup_store(sessions_root, collaboration_path, backup_root)
        print(f"backup: {target}")
    planned: list[tuple[SessionFile, dict[str, Any] | None, bytes]] = []
    for session in iter_session_files(sessions_root):
        summary["total"] += 1
        metadata = read_metadata(session)
        record = resolve_record(session, metadata, store, workspace, assigned)
        before = current_record(metadata)
        after = dict(record) if record is not None else {}
        if record is None:
            summary["host_private"] += 1
        elif record[SESSION_ACCESS_KIND_METADATA_KEY] == _ISOLATED:
            summary["isolated"] += 1
        if before == after:
            summary["unchanged"] += 1
            continue
        summary["marked"] += 1
        new_line = apply_entry(session, record)
        entries.append({
            "key": session.key,
            "file": str(session.path),
            "record_before": before,
            "record_after": after,
            "first_line_sha256_before": _sha(session.first_line.encode("utf-8")),
            "rest_sha256": _sha(session.rest),
        })
        planned.append((session, record, new_line))
    if mode == "apply":
        for session, _record, new_line in planned:
            _write_first_line(session, new_line)
            entry = next(item for item in entries if item["file"] == str(session.path))
            entry["first_line_sha256_after"] = _sha(new_line)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "sessions_root": str(sessions_root),
        "collaboration": str(collaboration_path),
        "summary": summary,
        "entries": entries,
    }
    if report_path is not None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"report: {report_path}")
    print(
        "mode={mode} total={total} changed={marked} unchanged={unchanged} "
        "host_private={host_private} isolated={isolated}".format(mode=mode, **summary)
    )
    return 0


def revert(
    *,
    report_path: Path,
    sessions_root: Path,
    summary_holder: dict[str, int] | None = None,
) -> int:
    report = json.loads(report_path.read_text())
    reverted = 0
    for entry in report.get("entries", []):
        path = Path(entry["file"])
        if not path.is_file() or not str(path).startswith(str(sessions_root)):
            continue
        key = entry.get("key")
        if not isinstance(key, str):
            continue
        payload = path.read_bytes()
        first_line, _, rest = payload.partition(b"\n")
        if _sha(rest) != entry.get("rest_sha256"):
            raise SystemExit(f"refusing to revert {path}: trailing content changed")
        session = SessionFile(path=path, key=key, first_line=first_line.decode("utf-8"), rest=rest)
        record = entry.get("record_before") or None
        path.write_bytes(apply_entry(session, record) + b"\n" + rest)
        reverted += 1
    if summary_holder is not None:
        summary_holder["reverted"] = reverted
    print(f"mode=revert reverted={reverted}")
    return 0


def _write_first_line(session: SessionFile, new_line: bytes) -> None:
    payload = session.path.read_bytes()
    _first, separator, rest = payload.partition(b"\n")
    if _sha(rest) != _sha(session.rest):
        raise SystemExit(f"refusing to write {session.path}: trailing content changed")
    session.path.write_bytes(new_line + separator + rest)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    home = Path.home() / ".nanobot"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions-root", type=Path, default=None)
    parser.add_argument("--collaboration", type=Path, default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--report", type=Path, default=Path("/tmp/backfill-session-access.json"))
    parser.add_argument("--backup-root", type=Path, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_const", const="dry-run", dest="mode")
    mode.add_argument("--apply", action="store_const", const="apply", dest="mode")
    mode.add_argument("--revert", action="store_const", const="revert", dest="mode")
    parser.set_defaults(mode="dry-run", home=home)
    return parser


def _resolve_paths(args: argparse.Namespace) -> None:
    """Fill in the home defaults, but never mutate a store the caller did not name."""
    if args.sessions_root is None:
        if args.mode != "dry-run":
            raise SystemExit(
                "--sessions-root is required for --apply/--revert; refusing to use a default store"
            )
        args.sessions_root = args.home / "sessions"
    if args.collaboration is None:
        args.collaboration = args.home / "collaboration" / "collaboration.json"
    if args.workspace is None:
        args.workspace = str(args.home / "workspace")


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    _resolve_paths(args)
    if args.mode == "revert":
        return revert(report_path=args.report, sessions_root=args.sessions_root)
    return run(
        sessions_root=args.sessions_root,
        collaboration_path=args.collaboration,
        workspace=args.workspace,
        mode=args.mode,
        report_path=args.report,
        backup_root=args.backup_root or args.sessions_root.parent / "backups",
    )


if __name__ == "__main__":
    raise SystemExit(main())
