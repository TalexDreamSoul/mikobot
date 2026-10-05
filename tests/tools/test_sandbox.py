"""Observable Bubblewrap policy tests for member command execution."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

from nanobot.agent.tools.sandbox import (
    SandboxUnavailableError,
    _sbpl_quote,
    _seatbelt_is_within,
    wrap_command,
)

_sandbox = import_module("nanobot.agent.tools.sandbox")


def _parse(command: str) -> list[str]:
    return shlex.split(command)


def test_member_bwrap_refuses_unsupported_platform_before_process_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sandbox.sys, "platform", "darwin")

    with pytest.raises(SandboxUnavailableError, match="only on Linux"):
        wrap_command("bwrap", "echo should-not-run", str(tmp_path), str(tmp_path), member=True)


def test_member_bwrap_refuses_missing_backend_before_process_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sandbox.sys, "platform", "linux")
    monkeypatch.setattr(_sandbox.shutil, "which", lambda _name: None)

    with pytest.raises(SandboxUnavailableError, match="unavailable"):
        wrap_command("bwrap", "echo should-not-run", str(tmp_path), str(tmp_path), member=True)


class TestSeatbeltBackend:
    """macOS Seatbelt policy generation.

    These parse the generated ``sandbox-exec`` command line, so they run on any
    platform — the profile is only ever executed on macOS.
    """

    @staticmethod
    def _profile(cmd: str) -> str:
        """Return the SBPL profile from a wrapped command."""
        tokens = _parse(cmd)
        return tokens[tokens.index("-p") + 1]

    @staticmethod
    def _quote(path: object) -> str:
        """Render *path* as expected in the generated SBPL profile."""
        return _sbpl_quote(str(path))

    @staticmethod
    def _metadata_rule(profile: str) -> str:
        return next(
            line
            for line in profile.splitlines()
            if line.startswith("(allow file-read-metadata ")
        )

    def test_basic_structure(self, tmp_path):
        ws = str(tmp_path / "project")
        result = wrap_command("seatbelt", "echo hi", ws, ws)
        tokens = _parse(result)

        assert tokens[0] == "/usr/bin/sandbox-exec"
        assert tokens[1] == "-p"
        assert tokens[-3] == "sh"
        assert tokens[-2] == "-c"
        assert tokens[-1].endswith("echo hi")

    def test_denies_by_default(self, tmp_path):
        profile = self._profile(wrap_command("seatbelt", "ls", str(tmp_path), str(tmp_path)))

        assert "(version 1)" in profile
        assert "(deny default)" in profile

    def test_root_directory_is_readable(self, tmp_path):
        """sh(1) stats `/` at startup; without this rule the shell aborts.

        `(subpath "/usr")` does not cover the root directory itself, so a
        profile listing only system subpaths kills every wrapped command
        before it runs.
        """
        profile = self._profile(wrap_command("seatbelt", "ls", str(tmp_path), str(tmp_path)))

        assert '(allow file-read* (literal "/"))' in profile

    def test_workspace_allowed_read_write(self, tmp_path):
        ws = (tmp_path / "project").resolve()
        profile = self._profile(wrap_command("seatbelt", "ls", str(ws), str(ws)))

        assert f"(allow file-read* file-write* (subpath {self._quote(ws)}))" in profile

    def test_config_dir_denied_before_workspace_allow(self, tmp_path):
        """Last matching rule wins, so the parent deny must come first.

        The parent holds `config.json`; the workspace allow that follows
        re-exposes only the workspace subtree.
        """
        ws = (tmp_path / "project").resolve()
        profile = self._profile(wrap_command("seatbelt", "ls", str(ws), str(ws)))

        parent_deny = profile.index(f"(deny file-read* file-write* (subpath {self._quote(ws.parent)})")
        workspace_allow = profile.index(f"(allow file-read* file-write* (subpath {self._quote(ws)})")
        assert parent_deny < workspace_allow

    def test_no_parent_deny_when_workspace_is_the_root(self):
        """Denying `/` would override the root read rule and break startup."""
        profile = self._profile(wrap_command("seatbelt", "ls", "/", "/"))

        assert "(deny file-read*" not in profile

    def test_workspace_ancestors_stay_searchable(self, tmp_path):
        """Masking the config dir must not break resolution into the workspace.

        Seatbelt checks every path component while resolving, so without
        metadata on the parent the wrapped command dies with ENOTDIR before it
        runs anything.
        """
        ws = (tmp_path / "project").resolve()
        profile = self._profile(wrap_command("seatbelt", "ls", str(ws), str(ws)))

        assert f"(literal {self._quote(ws.parent)})" in self._metadata_rule(profile)

    def test_config_dir_is_searchable_but_not_listable(self, tmp_path):
        """Metadata only: `cd workspace` works, `ls ..` and `cat ../config.json` do not."""
        ws = (tmp_path / "project").resolve()
        profile = self._profile(wrap_command("seatbelt", "ls", str(ws), str(ws)))

        assert f"(literal {self._quote(ws.parent)})" in self._metadata_rule(profile)
        assert f"(allow file-read* (subpath {self._quote(ws.parent)}))" not in profile
        assert f"(allow file-read* file-write* (subpath {self._quote(ws.parent)}))" not in profile

    def test_host_scratch_directories_are_not_shared(self, tmp_path):
        profile = self._profile(wrap_command("seatbelt", "ls", str(tmp_path), str(tmp_path)))

        for path in ("/tmp", "/private/tmp", "/var", "/private/var", "/var/folders",
                     "/private/var/folders", "/Library", "/etc", "/private/etc"):
            assert f'(subpath "{path}")' not in profile

    def test_home_and_tmpdir_use_workspace(self, tmp_path):
        ws = tmp_path.resolve()
        tokens = _parse(wrap_command("seatbelt", "mktemp", str(ws), str(ws)))
        assert tokens[3:6] == ["/usr/bin/env", f"HOME={ws}", f"TMPDIR={ws}"]

    def test_network_matches_bwrap_policy(self, tmp_path):
        profile = self._profile(wrap_command("seatbelt", "ls", str(tmp_path), str(tmp_path)))
        assert "(allow network*)" in profile

    def test_media_dir_read_only(self, tmp_path, monkeypatch):
        fake_media = (tmp_path / "media").resolve()
        fake_media.mkdir()
        monkeypatch.setattr(_sandbox, "get_media_dir", lambda: fake_media)
        ws = (tmp_path / "project").resolve()
        profile = self._profile(wrap_command("seatbelt", "ls", str(ws), str(ws)))

        assert f"(allow file-read* (subpath {self._quote(fake_media)}))" in profile
        assert f"(deny file-write* (subpath {self._quote(fake_media)}))" in profile

    def test_cwd_inside_workspace(self, tmp_path):
        ws = (tmp_path / "project").resolve()
        sub = ws / "src" / "lib"
        result = wrap_command("seatbelt", "pwd", str(ws), str(sub))

        assert _parse(result)[-1] == f"cd {shlex.quote(str(sub))} || exit\npwd"

    def test_cwd_outside_workspace_falls_back(self, tmp_path):
        ws = (tmp_path / "project").resolve()
        outside = tmp_path / "other"
        result = wrap_command("seatbelt", "pwd", str(ws), str(outside))

        assert _parse(result)[-1] == f"cd {shlex.quote(str(ws))} || exit\npwd"

    def test_custom_read_only_binds(self, tmp_path):
        ws = (tmp_path / "project").resolve()
        tool_bin = (tmp_path / "home" / ".local" / "bin").resolve(strict=False)

        profile = self._profile(
            wrap_command(
                "seatbelt", "uv --version", str(ws), str(ws),
                sandbox_ro_binds=[str(tool_bin)],
            )
        )

        assert f"(allow file-read* (subpath {self._quote(tool_bin)}))" in profile
        assert f"(deny file-write* (subpath {self._quote(tool_bin)}))" in profile

    def test_read_only_bind_overrides_workspace_write(self, tmp_path):
        ws = tmp_path.resolve()
        ro = ws / "readonly"
        profile = self._profile(wrap_command(
            "seatbelt", "ls", str(ws), str(ws), sandbox_ro_binds=[str(ro)],
        ))
        assert profile.index(f"(allow file-read* file-write* (subpath {self._quote(ws)}))") < (
            profile.index(f"(deny file-write* (subpath {self._quote(ro)}))")
        )

    @pytest.mark.parametrize("source", ["bind", "media"])
    def test_readonly_ancestors_cannot_be_unlinked(self, tmp_path, monkeypatch, source):
        ws = (tmp_path / "workspace").resolve()
        ro = ws / 'tree with "quotes' / "branch" / "readonly"
        media = ro if source == "media" else tmp_path / "media"
        monkeypatch.setattr(_sandbox, "get_media_dir", lambda: media)
        profile = self._profile(wrap_command(
            "seatbelt", "ls", str(ws), str(ws),
            sandbox_ro_binds=[str(ro)] if source == "bind" else [],
        ))

        rule = profile.splitlines()[-1]
        assert rule.startswith("(deny file-write-unlink ")
        for parent in ro.parents:
            assert f"(literal {self._quote(parent)})" in rule
        assert "subpath" not in rule

    @pytest.mark.parametrize("override", ["root", "parent", "child"])
    def test_rw_override_only_unlocks_fully_covered_roots(self, tmp_path, monkeypatch, override):
        ws = (tmp_path / "workspace").resolve()
        ro = ws / "tree" / "readonly"
        rw = {"root": ro, "parent": ro.parent, "child": ro / "cache"}[override]
        monkeypatch.setattr(_sandbox, "get_media_dir", lambda: ro)
        profile = self._profile(wrap_command(
            "seatbelt", "ls", str(ws), str(ws),
            sandbox_ro_binds=[str(ro)], sandbox_rw_binds=[str(rw)],
        ))

        if override == "child":
            assert profile.splitlines()[-1].startswith("(deny file-write-unlink ")
            assert f"(literal {self._quote(ro.parent)})" in profile.splitlines()[-1]
        else:
            assert "(deny file-write-unlink " not in profile

    @pytest.mark.parametrize("same_directory", [True, False])
    def test_rw_coverage_uses_filesystem_identity(self, tmp_path, monkeypatch, same_directory):
        ro = tmp_path / "Tree" / "ReadOnly"
        # Keep the lexical paths distinct even on Windows; this test controls
        # filesystem identity, while native tests cover volume case sensitivity.
        rw = tmp_path / "aliased-tree"
        monkeypatch.setattr(
            Path, "samefile",
            lambda path, other: same_directory and path == ro.parent and other == rw,
        )
        assert _seatbelt_is_within(ro, rw) is same_directory

    def test_missing_rw_alias_does_not_unlock_readonly_root(self, tmp_path):
        ro = tmp_path / "missing" / "readonly"
        assert not _seatbelt_is_within(ro, tmp_path / "missing-alias")
        assert _seatbelt_is_within(ro, ro.parent)

    def test_custom_read_write_binds(self, tmp_path):
        ws = (tmp_path / "project").resolve()
        cache_dir = (tmp_path / "cache").resolve(strict=False)

        profile = self._profile(
            wrap_command(
                "seatbelt", "touch cache/file", str(ws), str(ws),
                sandbox_rw_binds=[str(cache_dir)],
            )
        )

        assert f"(allow file-read* file-write* (subpath {self._quote(cache_dir)}))" in profile

    def test_custom_workspace_parent_binds_are_ignored(self, tmp_path):
        """A bind must not uncover the masked config directory."""
        ws = tmp_path / "private" / "project"
        parent = ws.parent.resolve(strict=False)

        profile = self._profile(
            wrap_command(
                "seatbelt", "cat ../config.json", str(ws), str(ws),
                sandbox_ro_binds=[str(parent)],
                sandbox_rw_binds=[str(parent)],
            )
        )

        assert f"(allow file-read* (subpath {self._quote(parent)}))" not in profile
        assert f"(allow file-read* file-write* (subpath {self._quote(parent)}))" not in profile

    def test_paths_with_quotes_are_escaped(self, tmp_path):
        """An unescaped quote would terminate the literal early and silently
        widen every rule that follows it."""
        ws = (tmp_path / 'pro"ject').resolve()
        profile = self._profile(wrap_command("seatbelt", "ls", str(ws), str(ws)))

        escaped = str(ws).replace("\\", "\\\\").replace('"', '\\"')
        assert f'(allow file-read* file-write* (subpath "{escaped}"))' in profile

    def test_sbpl_quote_escapes_special_characters(self):
        """Backslashes and double quotes must be escaped with C-style escaping."""
        assert _sbpl_quote(r'path\with"quotes') == r'"path\\with\"quotes"'


class TestUnknownBackend:
    def test_raises_value_error(self, tmp_path):
        ws = str(tmp_path / "project")
        with pytest.raises(ValueError, match="Unknown sandbox backend"):
            wrap_command("nonexistent", "ls", ws, ws)

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
