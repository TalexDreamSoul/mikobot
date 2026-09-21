"""Regression coverage for the session-access operator scripts.

`scripts/backfill_session_access_scope.py` stamps per-session access provenance
into a live session store; `scripts/verify_session_access_matrix.py` re-derives
the cross-session allow matrix from the same store.  An operator runs both
against the owner's real `~/.nanobot`, so the contracts pinned here are the ones
a plausible mistake would destroy silently: a dry run that mutates, an apply
that rewrites conversation history, a second apply that re-marks, a missing
backup, a revert that cannot restore the store byte-for-byte, and a deployment
check that passes while the matrix is wrong.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from nanobot.collaboration.store import CollaborationStore
from nanobot.session.manager import SessionManager
from nanobot.session.privacy import (
    SESSION_ACCESS_KIND_METADATA_KEY,
    SESSION_ACCESS_PROJECT_METADATA_KEY,
    SESSION_ACCESS_USER_METADATA_KEY,
)

_ROOT = Path(__file__).resolve().parents[1]

_KIND = SESSION_ACCESS_KIND_METADATA_KEY
_USER = SESSION_ACCESS_USER_METADATA_KEY
_PROJECT = SESSION_ACCESS_PROJECT_METADATA_KEY
_ISOLATED = "isolated"

_DIRECT_CHAT = "oc_direct"
_BOUND_CHAT = "oc_bound"
_DIRECT_KEY = f"feishu:{_DIRECT_CHAT}"
_BOUND_KEY = f"feishu:{_BOUND_CHAT}"
_GROUP_THREAD_KEY = "feishu:oc_room:omt_9"
_UNASSIGNED_KEY = "wecom:oc_unknown"
_STALE_KEY = "telegram:oc_legacy"
_STALE_TITLE = "legacy route"
_HOST_PRIVATE_KEYS = ("heartbeat", "cron:nightly")
_ACCESS_KEYS = (_KIND, _USER, _PROJECT)


def _load_script(name: str) -> ModuleType:
    """Import an operator script by path: this repository has no `scripts` package."""
    path = _ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_operator_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load_script("backfill_session_access_scope")
matrix = _load_script("verify_session_access_matrix")


@dataclass(frozen=True)
class _Store:
    """A synthetic session store plus the collaboration document that scopes it."""

    sessions_root: Path
    collaboration_path: Path
    workspace: Path
    backup_root: Path
    owner_id: str
    alice_id: str
    project_id: str
    originals: dict[str, bytes]

    @property
    def retired_key(self) -> str:
        """A retired user-only key: it names the member but no project."""
        return f"user:{self.alice_id}:telegram:one"

    @property
    def scoped_key(self) -> str:
        """A project-qualified key: it names the member and the project."""
        return f"unified:{self.alice_id}:project:{self.project_id}"

    def session_path(self, key: str) -> Path:
        directory = "host" if key in _HOST_PRIVATE_KEYS else "default"
        return self.sessions_root / directory / f"{SessionManager._storage_key(key)}.jsonl"

    def expected_records(self) -> dict[str, dict[str, Any] | None]:
        """Map every persisted session to the access record the backfill resolves."""
        member: dict[str, Any] = {_USER: self.alice_id, _PROJECT: self.project_id}
        isolated: dict[str, Any] = {_KIND: _ISOLATED, _USER: None, _PROJECT: None}
        return {
            _DIRECT_KEY: {_KIND: "direct", **member},
            _BOUND_KEY: {_KIND: "bound", **member},
            self.retired_key: {_KIND: "direct", **member},
            self.scoped_key: {_KIND: "bound", **member},
            _GROUP_THREAD_KEY: dict(isolated),
            _UNASSIGNED_KEY: dict(isolated),
            _STALE_KEY: dict(isolated),
            "heartbeat": None,
            "cron:nightly": None,
        }

    def initial_metadata(self) -> dict[str, dict[str, Any]]:
        """The metadata every session carries before any backfill runs."""
        return {
            _DIRECT_KEY: {"title": "assigned direct"},
            _BOUND_KEY: {"title": "bound conversation"},
            _GROUP_THREAD_KEY: {"title": "群聊 · 归档"},
            _UNASSIGNED_KEY: {},
            self.retired_key: {"collaboration_project_id": self.project_id},
            self.scoped_key: {"collaboration_binding_id": "col_scoped_key_binding"},
            _STALE_KEY: {
                "title": _STALE_TITLE,
                _KIND: "direct",
                _USER: self.alice_id,
                _PROJECT: self.project_id,
            },
            "heartbeat": {"title": "owner heartbeat"},
            "cron:nightly": {"title": "nightly cron"},
        }

    def original_records(self) -> dict[str, dict[str, Any]]:
        """The access keys each session already carries."""
        return {
            key: {name: fields[name] for name in _ACCESS_KEYS if name in fields}
            for key, fields in self.initial_metadata().items()
        }

    def expected_marked_keys(self) -> set[str]:
        """The sessions a first apply rewrites: those whose record actually moves."""
        records = self.expected_records()
        before = self.original_records()
        return {
            key
            for key, record in records.items()
            if (dict(record) if record is not None else {}) != before[key]
        }

    def expected_summary(self) -> dict[str, int]:
        """The report counters a first apply produces for this store."""
        records = self.expected_records()
        marked = len(self.expected_marked_keys())
        return {
            "total": len(records),
            "marked": marked,
            "unchanged": len(records) - marked,
            "host_private": sum(record is None for record in records.values()),
            "isolated": sum(
                record is not None and record[_KIND] == _ISOLATED for record in records.values()
            ),
        }

    def expected_matrix(self) -> dict[str, list[str]]:
        """The allow matrix the records above imply, as an oracle for the reporter."""
        records = self.expected_records()
        built: dict[str, list[str]] = {}
        for source, record in records.items():
            if record is None:
                # The owner's own host-private turns reach every session this instance persists.
                built[source] = sorted(other for other in records if other != source)
            elif record[_KIND] == _ISOLATED:
                built[source] = []
            else:
                built[source] = sorted(
                    other
                    for other, other_record in records.items()
                    if other != source
                    and other_record is not None
                    and other_record[_KIND] != _ISOLATED
                    and other_record[_USER] == record[_USER]
                    and other_record[_PROJECT] == record[_PROJECT]
                )
        return built

    def files(self) -> dict[str, bytes]:
        """Every session file's bytes, keyed by session key."""
        return {key: self.session_path(key).read_bytes() for key in self.expected_records()}

    def store_bytes(self) -> dict[Path, bytes]:
        """Every byte the scripts may rewrite: the session tree and the collaboration file."""
        paths = [*sorted(self.sessions_root.rglob("*")), self.collaboration_path]
        return {path: path.read_bytes() for path in paths if path.is_file()}


def _session_bytes(key: str, metadata: dict[str, Any], messages: list[tuple[str, str]]) -> bytes:
    """Serialize a session the way the JSONL store does, so bytes can be compared."""
    lines = [
        json.dumps(
            {
                "_type": "metadata",
                "key": key,
                "created_at": "2026-09-21T09:30:00",
                "updated_at": "2026-09-21T09:31:00",
                "metadata": metadata,
                "last_consolidated": 0,
            },
            ensure_ascii=False,
        ),
        *(
            json.dumps({"role": role, "content": content}, ensure_ascii=False)
            for role, content in messages
        ),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _assign_channel(
    collaboration_path: Path,
    *,
    channel_type: str,
    project_id: str,
    assignee_user_id: str,
    created_by_user_id: str,
) -> None:
    """Give one channel instance an enabled assignment, as consuming a Pair Code does.

    The store only writes assignments through its pairing flow, so the fixture
    patches the document directly with the record that flow produces.
    """
    document = json.loads(collaboration_path.read_text())
    document["channelAssignments"][f"{channel_type}\x00default"] = {
        "channelType": channel_type,
        "instanceId": "default",
        "projectId": project_id,
        "assigneeUserId": assignee_user_id,
        "enabled": True,
        "createdByUserId": created_by_user_id,
        "createdAtMs": 1_700_000_000_000,
        "updatedAtMs": 1_700_000_000_000,
    }
    collaboration_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _build_store(tmp_path: Path) -> _Store:
    """Build a synthetic store through the collaboration API plus one JSON patch."""
    sessions_root = tmp_path / "sessions"
    collaboration_path = tmp_path / "collaboration" / "collaboration.json"
    workspace = tmp_path / "workspace"
    store = CollaborationStore(store_path=collaboration_path)
    owner, _builtin = store.ensure_local_owner(workspace)
    alice, _personal = store.ensure_identity_user("feishu", _DIRECT_CHAT, workspace)
    project = store.create_project(alice.id, "Team Alpha", workspace / "team-alpha")
    store.bind_identity("feishu", _BOUND_CHAT, alice.id)
    store.bind_conversation("feishu", _BOUND_CHAT, project.id, alice.id)
    _assign_channel(
        collaboration_path,
        channel_type="feishu",
        project_id=project.id,
        assignee_user_id=alice.id,
        created_by_user_id=owner.id,
    )

    fixture = _Store(
        sessions_root=sessions_root,
        collaboration_path=collaboration_path,
        workspace=workspace,
        backup_root=tmp_path / "backups",
        owner_id=owner.id,
        alice_id=alice.id,
        project_id=project.id,
        originals={},
    )
    metadata = fixture.initial_metadata()
    messages = [("user", "保留的历史消息"), ("assistant", "kept history line")]
    for key, fields in metadata.items():
        path = fixture.session_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_session_bytes(key, fields, messages))
    return replace(fixture, originals=fixture.files())


@pytest.fixture
def store(tmp_path: Path) -> _Store:
    return _build_store(tmp_path)


def _apply(fixture: _Store, *, report: Path, backup_root: Path | None = None) -> int:
    argv = [
        "--apply",
        "--sessions-root",
        str(fixture.sessions_root),
        "--collaboration",
        str(fixture.collaboration_path),
        "--workspace",
        str(fixture.workspace),
        "--report",
        str(report),
    ]
    if backup_root is not None:
        argv += ["--backup-root", str(backup_root)]
    return backfill.main(argv)


def _applied(fixture: _Store) -> Path:
    """Apply the backfill once and return the report it wrote."""
    report = fixture.sessions_root.parent / "apply-report.json"
    assert _apply(fixture, report=report, backup_root=fixture.backup_root) == 0
    return report


def _backup_files(backup: Path, fixture: _Store) -> dict[str, bytes]:
    """Every session file as the backup captured it."""
    root = backup / fixture.sessions_root.name
    return {
        key: (root / fixture.session_path(key).relative_to(fixture.sessions_root)).read_bytes()
        for key in fixture.expected_records()
    }


def _revert(fixture: _Store, report: Path) -> int:
    return backfill.main(
        ["--revert", "--report", str(report), "--sessions-root", str(fixture.sessions_root)]
    )


@pytest.mark.parametrize("mode_args", [[], ["--dry-run"]], ids=["default-mode", "explicit-flag"])
def test_a_dry_run_reports_every_session_without_writing_anything(
    store: _Store, tmp_path: Path, mode_args: list[str]
) -> None:
    """The default mode is a pure read that still resolves provenance for every session."""
    before = store.store_bytes()
    report_path = tmp_path / "dry-run.json"

    exit_code = backfill.main(
        [
            *mode_args,
            "--sessions-root",
            str(store.sessions_root),
            "--collaboration",
            str(store.collaboration_path),
            "--workspace",
            str(store.workspace),
            "--report",
            str(report_path),
        ]
    )

    assert exit_code == 0
    assert store.store_bytes() == before
    assert not store.backup_root.exists()

    report = json.loads(report_path.read_text())
    assert report["mode"] == "dry-run"
    assert report["sessions_root"] == str(store.sessions_root)
    summary = report["summary"]
    assert {name: summary[name] for name in store.expected_summary()} == store.expected_summary()
    records = store.expected_records()
    previous = store.original_records()
    entries = {entry["key"]: entry for entry in report["entries"]}
    assert set(entries) == store.expected_marked_keys()
    for key, entry in entries.items():
        assert entry["record_after"] == records[key]
        assert entry["record_before"] == previous[key]


def test_apply_stamps_the_resolved_record_and_leaves_history_alone(
    store: _Store, tmp_path: Path
) -> None:
    """Only the metadata line moves: the route decides the record and every later byte survives."""
    report_path = tmp_path / "apply.json"

    assert _apply(store, report=report_path, backup_root=store.backup_root) == 0

    records = store.expected_records()
    for key, record in records.items():
        original = store.originals[key]
        first_line, _, rest = store.session_path(key).read_bytes().partition(b"\n")
        assert rest == original.partition(b"\n")[2]
        if record is None:
            assert store.session_path(key).read_bytes() == original
            continue
        line = json.loads(first_line)
        assert {name: line["metadata"][name] for name in _ACCESS_KEYS} == record
        assert line["metadata"] == {**json.loads(original.partition(b"\n")[0])["metadata"], **record}

    report = json.loads(report_path.read_text())
    summary = report["summary"]
    assert {name: summary[name] for name in store.expected_summary()} == store.expected_summary()
    entries = {entry["key"]: entry for entry in report["entries"]}
    assert set(entries) == store.expected_marked_keys()
    assert entries[_STALE_KEY]["record_before"] == {
        _KIND: "direct",
        _USER: store.alice_id,
        _PROJECT: store.project_id,
    }
    assert entries[_STALE_KEY]["record_after"] == records[_STALE_KEY]


def test_a_second_apply_is_a_no_op(store: _Store, tmp_path: Path) -> None:
    """Re-running the backfill re-marks nothing and rewrites no byte."""
    _applied(store)
    stamped = store.store_bytes()
    report_path = tmp_path / "second.json"

    assert _apply(store, report=report_path, backup_root=store.backup_root) == 0

    assert store.store_bytes() == stamped
    summary = json.loads(report_path.read_text())["summary"]
    assert summary["marked"] == 0
    assert summary["unchanged"] == len(store.expected_records())


def test_apply_backs_up_the_session_and_collaboration_store(
    store: _Store, tmp_path: Path
) -> None:
    """The store as it stood before the run is copied aside before the first write."""
    collaboration_before = store.collaboration_path.read_bytes()

    _applied(store)

    backups = [path for path in store.backup_root.iterdir() if path.is_dir()]
    assert len(backups) == 1
    assert (backups[0] / store.collaboration_path.name).read_bytes() == collaboration_before
    assert _backup_files(backups[0], store) == store.originals


class _FrozenDatetime(datetime):
    """A clock that never advances, so two backups land on the same second."""

    @classmethod
    def now(cls, tz: Any = None) -> _FrozenDatetime:
        return cls(2026, 9, 21, 9, 30, 0, tzinfo=tz)


def test_backups_in_the_same_second_get_their_own_directory(
    store: _Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Back-to-back applies both succeed, each with the store it was about to mutate."""
    monkeypatch.setattr(backfill, "datetime", _FrozenDatetime)
    _applied(store)
    after_first = store.files()

    assert _apply(store, report=tmp_path / "second.json", backup_root=store.backup_root) == 0

    backups = sorted(path for path in store.backup_root.iterdir() if path.is_dir())
    assert len(backups) == 2
    assert _backup_files(backups[0], store) == store.originals
    assert _backup_files(backups[1], store) == after_first


def test_revert_restores_the_original_bytes(store: _Store) -> None:
    """Revert puts back the exact metadata line the backfill replaced."""
    report = _applied(store)

    assert _revert(store, report) == 0

    assert store.files() == store.originals


def test_revert_refuses_when_a_session_moved_on(store: _Store) -> None:
    """A conversation that gained messages after the report must not be rolled back."""
    report = _applied(store)
    records = store.expected_records()
    for key, record in records.items():
        if record is None:
            continue
        path = store.session_path(key)
        payload = path.read_bytes()
        path.write_bytes(payload + json.dumps({"role": "user", "content": "after"}).encode() + b"\n")
    moved_on = store.files()

    with pytest.raises(SystemExit):
        _revert(store, report)

    assert store.files() == moved_on


@pytest.mark.parametrize("mode", ["--apply", "--revert"])
def test_apply_and_revert_refuse_the_implicit_home_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A mutating mode without an explicit store fails instead of touching `~/.nanobot`."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    with pytest.raises(SystemExit):
        backfill.main([mode, "--report", str(tmp_path / "report.json")])

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


def test_apply_writes_nothing_when_a_metadata_line_is_unusable(
    store: _Store, tmp_path: Path
) -> None:
    """One unreadable session aborts the run before any file is rewritten."""
    path = store.session_path(_DIRECT_KEY)
    path.write_bytes(b"not json\n" + store.originals[_DIRECT_KEY].partition(b"\n")[2])
    corrupt = store.store_bytes()

    with pytest.raises(ValueError):
        _apply(store, report=tmp_path / "apply.json", backup_root=store.backup_root)

    assert store.store_bytes() == corrupt


def _expect_payload(store: _Store) -> dict[str, Any]:
    """The assertion set a correct deployment check writes for this store."""
    expected = store.expected_matrix()
    return {
        "assertions": [
            {"source": _DIRECT_KEY, "target": _BOUND_KEY, "allowed": True},
            {"source": _DIRECT_KEY, "target": store.retired_key, "allowed": True},
            {"source": _DIRECT_KEY, "target": store.scoped_key, "allowed": True},
            {"source": _DIRECT_KEY, "target": _GROUP_THREAD_KEY, "allowed": False},
            {"source": _GROUP_THREAD_KEY, "target": _DIRECT_KEY, "allowed": False},
            {"source": _UNASSIGNED_KEY, "target": _DIRECT_KEY, "allowed": False},
            {"source": _DIRECT_KEY, "target": "heartbeat", "allowed": False},
            {"source": "heartbeat", "target": _DIRECT_KEY, "allowed": True},
        ],
        "visible": [
            {"source": _DIRECT_KEY, "count": len(expected[_DIRECT_KEY])},
            {"source": _GROUP_THREAD_KEY, "count": 0},
            {"source": "heartbeat", "count": len(expected["heartbeat"])},
        ],
    }


def test_verify_writes_the_documented_matrix_and_accepts_true_assertions(
    store: _Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The deployment check passes a correct assertion set and reports what it computed."""
    _applied(store)
    matrix_out = tmp_path / "matrix.json"
    expect = tmp_path / "expect.json"
    expect.write_text(json.dumps(_expect_payload(store)), encoding="utf-8")

    exit_code = matrix.main(
        [
            "--sessions-root",
            str(store.sessions_root),
            "--json-out",
            str(matrix_out),
            "--expect",
            str(expect),
        ]
    )

    assert exit_code == 0
    expected = store.expected_matrix()
    payload = json.loads(matrix_out.read_text())
    assert set(payload) == {"sessions", "visibility", "matrix"}
    assert payload["sessions"] == len(expected)
    assert payload["matrix"] == expected
    assert payload["visibility"] == {key: len(targets) for key, targets in expected.items()}
    output = capsys.readouterr()
    channel_sources = len(expected) - len(_HOST_PRIVATE_KEYS)
    assert f"sessions: {len(expected)} (channel sources: {channel_sources})" in output.out
    assert output.err == ""


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"assertions": [{"source": _GROUP_THREAD_KEY, "target": _DIRECT_KEY, "allowed": True}]},
            id="inverted-assertion",
        ),
        pytest.param(
            {"assertions": [], "visible": [{"source": _DIRECT_KEY, "count": 0}]},
            id="wrong-visible-count",
        ),
    ],
)
def test_verify_fails_the_deployment_check_on_a_false_expectation(
    store: _Store, tmp_path: Path, payload: dict[str, Any]
) -> None:
    """A wrong allow/deny or a wrong visible count has to fail the check."""
    _applied(store)
    expect = tmp_path / "expect.json"
    expect.write_text(json.dumps(payload), encoding="utf-8")

    assert matrix.main(["--sessions-root", str(store.sessions_root), "--expect", str(expect)]) == 1
