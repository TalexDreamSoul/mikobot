#!/usr/bin/env python3
"""Backfill the per-turn session access record for already-persisted sessions.

`nanobot/agent/loop.py` stamps `session_access_*` on every turn that resolved a
scope. Sessions written before that change carry no record, so the shared
`session_access_allowed` decision treats them as unmarked and refuses
cross-session access. This operator tool resolves each persisted session's scope
through the same collaboration store the gateway uses and writes the record.

Host-private keys (`heartbeat`, `heartbeat:*`, `cron:*`, `dream:*`,
`websocket:*`, `cli:*`) are left exactly as they are: the tool never writes an
access record there and never takes one away, because the decision reads a missing
record in that namespace as the owner's own. A session whose scope cannot be
resolved is marked isolated, which the decision refuses in both directions.

Only the first JSONL line (session metadata) is rewritten; every later line is
byte-identical and verified by hash. The whole plan is verified before the first
write, each file is replaced atomically, and the report is written before any byte
moves, so a failed or interrupted run always leaves a store that `--revert` can
restore exactly. Run it with the gateway stopped.

Scope resolution reads a private copy of the collaboration document: the store
migrates a document whose `schemaVersion` trails this code and locks a file beside
it, so a read must never point at the live document.

Usage:
    python -m scripts.backfill_session_access_scope --dry-run --report /tmp/report.json
    python -m scripts.backfill_session_access_scope --sessions-root <dir> --apply --report /tmp/report.json
    python -m scripts.backfill_session_access_scope --sessions-root <dir> --revert --report /tmp/report.json

`--apply` and `--revert` require an explicit `--sessions-root`: the tool refuses to
mutate a default store it was not pointed at.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
import tempfile
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from nanobot.collaboration.pairing import runtime_channel_key
from nanobot.collaboration.store import CollaborationStore, CollaborationStoreError
from nanobot.session.keys import is_host_private_session_key
from nanobot.session.manager import JsonlSessionStore
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


def iter_session_files(sessions_root: Path) -> Iterator[SessionFile]:
    """Yield every canonical session file under a sessions root, key-first."""
    for path in sorted(sessions_root.rglob("*.jsonl")):
        key = JsonlSessionStore.session_key_from_path(path)
        if key is None:
            print(f"skip (not a canonical session filename): {path}", file=sys.stderr)
            continue
        payload = path.read_bytes()
        first_line_bytes, _, rest = payload.partition(b"\n")
        try:
            first_line = first_line_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SystemExit(f"{path}: the session metadata line is not valid UTF-8") from exc
        yield SessionFile(path=path, key=key, first_line=first_line, rest=rest)


def read_metadata(session: SessionFile) -> dict[str, Any]:
    """Return the session metadata mapping, or an empty mapping when unreadable."""
    try:
        record = json.loads(session.first_line)
    except json.JSONDecodeError:
        return {}
    if not isinstance(record, dict):
        return {}
    metadata = cast("dict[str, Any]", record).get("metadata")
    return dict(cast("dict[str, Any]", metadata)) if isinstance(metadata, dict) else {}


def current_record(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return the access keys already present in *metadata*."""
    return {key: metadata.get(key) for key in ACCESS_KEYS if key in metadata}


def assigned_channel_names(collaboration_path: Path) -> set[str]:
    """Return the runtime channel names that carry a channel assignment."""
    try:
        raw = json.loads(collaboration_path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(raw, dict):
        return set()
    assignments = cast("dict[str, Any]", raw).get("channelAssignments")
    if not isinstance(assignments, dict):
        return set()
    names: set[str] = set()
    for value in cast("dict[str, Any]", assignments).values():
        if not isinstance(value, dict):
            continue
        entry = cast("dict[str, Any]", value)
        channel_type, instance_id = entry.get("channelType"), entry.get("instanceId")
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
        return {
            SESSION_ACCESS_KIND_METADATA_KEY: _BOUND if _stored_binding(metadata) else _DIRECT,
            SESSION_ACCESS_USER_METADATA_KEY: user_only,
            SESSION_ACCESS_PROJECT_METADATA_KEY: _stored_project(store, user_only, metadata),
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
    if thread:
        # A threaded route is a group topic: the product never resolves one as a
        # direct chat, and the member behind an old topic is not recoverable from
        # its key — so the sender cannot stand in for the conversation.
        message_metadata["chat_type"] = "group"
    elif route_channel in assigned:
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


def _stored_project(
    store: CollaborationStore,
    user_id: str,
    metadata: dict[str, Any],
) -> str | None:
    """Return the metadata project while the store still authorizes *user_id* for it.

    A retired user-only key names no project of its own, so its project authority
    has to be re-confirmed by the collaboration store instead of re-adopted from
    whatever the session remembered: a project that has since been deleted or that
    the member no longer belongs to must not come back with the record.
    """
    candidate = metadata.get("collaboration_project_id")
    if not isinstance(candidate, str) or not candidate:
        return None
    try:
        project = store.get_project(user_id, candidate)
    except CollaborationStoreError:
        return None
    return project.id if project is not None else None


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


@contextmanager
def _resolution_store(
    collaboration_path: Path,
) -> Generator[tuple[CollaborationStore, Path], None, None]:
    """Resolve scopes against a private copy of the collaboration document.

    ``CollaborationStore`` rewrites a document whose ``schemaVersion`` trails this
    code (keeping a ``.v<schema>.bak`` beside it) and locks a file next to the store,
    so resolving against the live path would let a read-only pass modify production
    collaboration state. The copy carries the same bytes, so every decision is
    identical.
    """
    with tempfile.TemporaryDirectory(prefix="nanobot-session-access-") as directory:
        copy = Path(directory) / collaboration_path.name
        if collaboration_path.is_file():
            shutil.copy2(collaboration_path, copy)
        elif collaboration_path.exists():
            raise SystemExit(f"{collaboration_path} is not a file")
        yield CollaborationStore(store_path=copy), copy


def run(
    *,
    sessions_root: Path,
    collaboration_path: Path,
    workspace: str,
    mode: str,
    report_path: Path | None,
    backup_root: Path | None,
) -> int:
    summary = {"total": 0, "marked": 0, "unchanged": 0, "host_private": 0, "isolated": 0}
    entries: list[dict[str, Any]] = []
    planned: list[tuple[SessionFile, bytes]] = []
    if mode == "apply" and backup_root is not None:
        print(f"backup: {backup_store(sessions_root, collaboration_path, backup_root)}")
    with _resolution_store(collaboration_path) as (store, resolution_copy):
        assigned = assigned_channel_names(resolution_copy)
        for session in iter_session_files(sessions_root):
            summary["total"] += 1
            metadata = read_metadata(session)
            before = current_record(metadata)
            record = resolve_record(session, metadata, store, workspace, assigned)
            if record is None:
                # A host-private key keeps exactly what it carries. Removing a record
                # here would hand the owner's namespace reach to a `websocket:`/`cli:`
                # session that the decision had sealed, so the tool never writes one
                # and never takes one away.
                summary["host_private"] += 1
                if before:
                    print(
                        f"note: {session.path} is host-private and keeps its record",
                        file=sys.stderr,
                    )
                summary["unchanged"] += 1
                continue
            after = dict(record)
            if record[SESSION_ACCESS_KIND_METADATA_KEY] == _ISOLATED:
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
                "first_line_sha256_after": _sha(new_line),
                "rest_sha256": _sha(session.rest),
            })
            planned.append((session, new_line))
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "sessions_root": str(sessions_root),
        "collaboration": str(collaboration_path),
        "summary": summary,
        "entries": entries,
    }
    if mode == "apply":
        _verify_planned(planned)
        # The report is the revert contract: it has to be on disk before the first
        # byte moves, so an interrupted run still leaves an exact record to restore.
        _write_report(report_path, report)
        for session, new_line in planned:
            _write_first_line_atomic(session, new_line)
        report["applied"] = len(planned)
    _write_report(report_path, report)
    if report_path is not None:
        print(f"report: {report_path}")
    print(
        "mode={mode} total={total} changed={marked} unchanged={unchanged} "
        "host_private={host_private} isolated={isolated}".format(mode=mode, **summary)
    )
    return 0


def revert(*, report_path: Path, sessions_root: Path) -> int:
    """Restore the bytes one apply recorded, refusing a store that moved on."""
    report = json.loads(report_path.read_text())
    recorded_root = report.get("sessions_root")
    if not isinstance(recorded_root, str):
        raise SystemExit(f"refusing to revert: {report_path} records no sessions root")
    if Path(recorded_root).resolve() != sessions_root.resolve():
        raise SystemExit(
            f"refusing to revert: {report_path} was written for {recorded_root}, not {sessions_root}"
        )
    restored: list[tuple[Path, bytes]] = []
    root = sessions_root.resolve()
    for entry in report.get("entries", []):
        path = Path(entry["file"])
        if not path.resolve().is_relative_to(root):
            raise SystemExit(f"refusing to revert {path}: outside {sessions_root}")
        if not path.is_file():
            raise SystemExit(f"refusing to revert {path}: the session file is gone")
        first, separator, rest = path.read_bytes().partition(b"\n")
        _require_recorded_state(entry, path, first, rest)
        session = SessionFile(
            path=path,
            key=entry["key"],
            first_line=first.decode("utf-8"),
            rest=rest,
        )
        new_line = apply_entry(session, entry.get("record_before") or None)
        if _sha(new_line) != entry.get("first_line_sha256_before"):
            raise SystemExit(
                f"refusing to revert {path}: the recorded metadata line cannot be "
                "reproduced byte-for-byte; restore this session from the backup instead"
            )
        restored.append((path, new_line + separator + rest))
    for path, payload in restored:
        _replace_atomically(path, payload)
    if not restored:
        print("mode=revert reverted=0 (the report records no change)")
    else:
        print(f"mode=revert reverted={len(restored)}")
    return 0


def _require_recorded_state(
    entry: dict[str, Any],
    path: Path,
    first: bytes,
    rest: bytes,
) -> None:
    """Refuse a session that is neither the recorded before- nor after-state."""
    if _sha(rest) != entry.get("rest_sha256"):
        raise SystemExit(f"refusing to revert {path}: the conversation grew after the report")
    recorded = {entry.get("first_line_sha256_before"), entry.get("first_line_sha256_after")}
    if _sha(first) not in recorded:
        raise SystemExit(f"refusing to revert {path}: the metadata line changed after the report")


def _scan_payload(session: SessionFile) -> tuple[bytes, bytes]:
    """Return the on-disk separator and tail, refusing a session that moved on."""
    first, separator, rest = session.path.read_bytes().partition(b"\n")
    if rest != session.rest or first != session.first_line.encode("utf-8"):
        raise SystemExit(
            f"refusing to write {session.path}: the session changed after the scan"
        )
    return separator, rest


def _verify_planned(planned: list[tuple[SessionFile, bytes]]) -> None:
    """Re-check every planned session before the first write, so a plan never half-lands."""
    for session, _new_line in planned:
        _scan_payload(session)


def _write_first_line_atomic(session: SessionFile, new_line: bytes) -> None:
    separator, rest = _scan_payload(session)
    _replace_atomically(session.path, new_line + separator + rest)


def _replace_atomically(path: Path, payload: bytes) -> None:
    """Replace *path*'s bytes in one step, keeping its mode.

    A plain in-place write truncates first, so a full disk or a killed process can
    leave a session file cut short. The temporary file is fsynced before the
    rename, so the target is either the old bytes or the new ones.
    """
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with open(temporary, "xb") as handle:
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    with suppress(PermissionError, NotImplementedError):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            try:
                os.fsync(descriptor)
            except OSError as exc:
                if exc.errno != errno.EINVAL:
                    raise
        finally:
            os.close(descriptor)


def _write_report(report_path: Path | None, report: dict[str, Any]) -> None:
    """Write the report, refusing the run when the report cannot be recorded."""
    if report_path is None:
        return
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    except OSError as exc:
        raise SystemExit(f"refusing to continue: cannot write {report_path}: {exc}") from exc


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    home = Path.home() / ".nanobot"
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
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
    # Absolute and tilde-free: the report records these paths verbatim, and
    # `--revert` addresses the same files through them from any working directory.
    args.sessions_root = Path(args.sessions_root).expanduser().absolute()
    args.collaboration = Path(args.collaboration).expanduser().absolute()


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
