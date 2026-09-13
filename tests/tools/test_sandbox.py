"""Observable Bubblewrap policy tests for member command execution."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nanobot.agent.tools.sandbox import SandboxUnavailableError, wrap_command


def test_member_bwrap_refuses_unsupported_platform_before_process_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nanobot.agent.tools.sandbox.sys.platform", "darwin")

    with pytest.raises(SandboxUnavailableError, match="only on Linux"):
        wrap_command("bwrap", "echo should-not-run", str(tmp_path), str(tmp_path), member=True)


def test_member_bwrap_refuses_missing_backend_before_process_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nanobot.agent.tools.sandbox.sys.platform", "linux")
    monkeypatch.setattr("nanobot.agent.tools.sandbox.shutil.which", lambda _name: None)

    with pytest.raises(SandboxUnavailableError, match="unavailable"):
        wrap_command("bwrap", "echo should-not-run", str(tmp_path), str(tmp_path), member=True)


@pytest.mark.skipif(sys.platform != "linux", reason="Bubblewrap member isolation is Linux-only")
def test_member_bwrap_isolates_namespace_network_environment_and_files(
    tmp_path: Path,
) -> None:
    """A member process can use only its project and exact attachment capability."""
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is not installed")

    project = tmp_path / "project"
    attachment = tmp_path / "runtime" / "users" / "member" / "media" / "receipt.txt"
    other_attachment = tmp_path / "runtime" / "users" / "other" / "media" / "other-secret.txt"
    host_history = tmp_path / "host" / "memory" / "history.jsonl"
    global_media = tmp_path / "global-media" / "host-image.txt"
    project.mkdir()
    attachment.parent.mkdir(parents=True)
    other_attachment.parent.mkdir(parents=True)
    host_history.parent.mkdir(parents=True)
    global_media.parent.mkdir()
    (project / "member-note.txt").write_text("project data", encoding="utf-8")
    attachment.write_text("member attachment", encoding="utf-8")
    other_attachment.write_text("other member secret", encoding="utf-8")
    host_history.write_text("host history", encoding="utf-8")
    global_media.write_text("global media", encoding="utf-8")

    checks = " && ".join([
        "test -r member-note.txt",
        f"test -r {shlex.quote(str(attachment))}",
        f"test ! -e {shlex.quote(str(other_attachment))}",
        f"test ! -e {shlex.quote(str(host_history))}",
        f"test ! -e {shlex.quote(str(global_media))}",
        'test -z "$HOST_SECRET"',
        'test "$HOME" = "$PWD"',
        "test ! -s /proc/net/route",
    ])
    command = wrap_command(
        "bwrap",
        checks,
        str(project),
        str(project),
        member=True,
        member_ro_files=[attachment],
    )
    completed = subprocess.run(
        shlex.split(command),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOST_SECRET": "host-only-token"},
    )
    if completed.returncode and any(
        marker in (completed.stderr or "").lower()
        for marker in ("operation not permitted", "permission denied", "creating new namespace failed")
    ):
        pytest.skip("Bubblewrap is installed but this runtime forbids unprivileged namespaces")
    assert completed.returncode == 0, completed.stderr or completed.stdout
