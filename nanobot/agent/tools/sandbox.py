"""Sandbox backends for shell command execution."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Iterable

from nanobot.config.paths import get_media_dir


class SandboxUnavailableError(RuntimeError):
    """Raised when a requested process isolation backend cannot run."""


def ensure_sandbox_available(sandbox: str) -> None:
    """Fail before command spawn when the selected sandbox is unavailable."""
    if sandbox not in _BACKENDS:
        raise ValueError(f"Unknown sandbox backend {sandbox!r}. Available: {list(_BACKENDS)}")
    if sandbox == "bwrap":
        if sys.platform != "linux":
            raise SandboxUnavailableError("bwrap sandboxing is supported only on Linux")
        if shutil.which("bwrap") is None:
            raise SandboxUnavailableError("bwrap sandbox is unavailable; install bubblewrap or disable execution")


def _normalize_bind_paths(
    paths: Iterable[str] | None,
    *,
    workspace: Path | None = None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        value = str(raw).strip()
        if not value:
            continue
        path = Path(os.path.expandvars(value)).expanduser()
        if not path.is_absolute():
            continue
        resolved_path = path.resolve(strict=False)
        if workspace is not None:
            try:
                workspace.relative_to(resolved_path)
            except ValueError:
                pass
            else:
                # A later bind of the workspace or one of its parents could
                # cover the tmpfs that hides the config directory.
                continue
        resolved = str(resolved_path)
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _bind_file_parent_dirs(args: list[str], path: Path, seen: set[Path]) -> None:
    """Create only empty destination parents for an exact-file bind."""
    parents: list[Path] = []
    parent = path.parent
    while parent != parent.parent:
        parents.append(parent)
        parent = parent.parent
    for directory in reversed(parents):
        if directory not in seen:
            args.extend(["--dir", str(directory)])
            seen.add(directory)


def _member_files(files: Iterable[str | Path] | None) -> list[Path]:
    """Normalize already-authorized attachment paths for exact bwrap binds."""
    result: list[Path] = []
    seen: set[Path] = set()
    for raw in files or []:
        candidate = Path(raw)
        try:
            logical = Path(os.path.abspath(candidate))
            path = candidate.resolve(strict=True)
        except OSError:
            continue
        if logical != path or not path.is_file() or path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _bwrap(
    command: str,
    workspace: str,
    cwd: str,
    *,
    sandbox_ro_binds: Iterable[str] | None = None,
    sandbox_rw_binds: Iterable[str] | None = None,
    member: bool = False,
    member_ro_files: Iterable[str | Path] | None = None,
) -> str:
    """Wrap a command in Bubblewrap; member calls get a closed filesystem policy."""
    ws = Path(workspace).resolve()
    if ws == ws.parent:
        raise ValueError("sandbox workspace cannot be the filesystem root")

    try:
        sandbox_cwd = str(ws / Path(cwd).resolve().relative_to(ws))
    except ValueError:
        sandbox_cwd = str(ws)

    required = ["/usr"]
    optional = [
        "/bin",
        "/lib",
        "/lib64",
        "/etc/alternatives",
        "/etc/ssl/certs",
        "/etc/pki/tls/certs",
        "/etc/pki/ca-trust",
        "/etc/crypto-policies",
        "/etc/resolv.conf",
        "/etc/ld.so.cache",
    ]

    # PID/IPC namespaces keep any bwrap mode from using host /proc entries to
    # bypass its mount policy. Network isolation stays member-only so existing
    # deliberate host bwrap workflows retain their connectivity.
    args = [
        "bwrap", "--new-session", "--die-with-parent",
        "--unshare-pid", "--unshare-ipc",
    ]
    if member:
        args.append("--unshare-net")
        args.extend([
            "--clearenv",
            "--setenv", "HOME", str(ws),
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "LANG", "C.UTF-8",
            "--setenv", "TERM", "dumb",
            "--setenv", "PYTHONUNBUFFERED", "1",
        ])
    else:
        args.extend(["--setenv", "HOME", str(ws)])
    for p in required:
        args += ["--ro-bind", p, p]
    for p in optional:
        args += ["--ro-bind-try", p, p]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    if ws.parent != Path("/tmp"):
        args += ["--tmpfs", str(ws.parent)]
    args += [
        "--dir", str(ws),
        "--bind", str(ws), str(ws),
    ]
    if member:
        destination_dirs = {
            ws,
            ws.parent,
            Path("/tmp"),
            Path("/proc"),
            Path("/dev"),
            *(Path(path) for path in [*required, *optional]),
        }
        for file_path in _member_files(member_ro_files):
            _bind_file_parent_dirs(args, file_path, destination_dirs)
            args += ["--ro-bind", str(file_path), str(file_path)]
    else:
        media = get_media_dir().resolve()
        args += ["--ro-bind-try", str(media), str(media)]
        for p in _normalize_bind_paths(sandbox_ro_binds, workspace=ws):
            args += ["--ro-bind-try", p, p]
        for p in _normalize_bind_paths(sandbox_rw_binds, workspace=ws):
            args += ["--bind-try", p, p]
    args += ["--chdir", sandbox_cwd, "--", "sh", "-c", command]
    return shlex.join(args)


_BACKENDS = {"bwrap": _bwrap}


def wrap_command(
    sandbox: str,
    command: str,
    workspace: str,
    cwd: str,
    *,
    sandbox_ro_binds: Iterable[str] | None = None,
    sandbox_rw_binds: Iterable[str] | None = None,
    member: bool = False,
    member_ro_files: Iterable[str | Path] | None = None,
) -> str:
    """Wrap *command* using the named sandbox backend after availability checks."""
    ensure_sandbox_available(sandbox)
    backend = _BACKENDS[sandbox]
    return backend(
        command,
        workspace,
        cwd,
        sandbox_ro_binds=sandbox_ro_binds,
        sandbox_rw_binds=sandbox_rw_binds,
        member=member,
        member_ro_files=member_ro_files,
    )
