"""Private per-user media relocation for inbound channel attachments."""

from __future__ import annotations

import os
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

from nanobot.config.paths import get_media_dir, get_runtime_subdir


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_private_path_id(value: object, *, label: str) -> str:
    raw = value.strip() if isinstance(value, str) else ""
    relative = Path(raw)
    if (
        not raw
        or len(raw) > 128
        or relative.is_absolute()
        or len(relative.parts) != 1
        or relative.parts[0] in {".", ".."}
        or "/" in raw or "\\" in raw
        or any(ord(char) < 32 for char in raw)
    ):
        raise ValueError(f"invalid private media {label}")
    return raw


def user_private_root(owner_user_id: str) -> Path:
    """Return the managed private-media root for one validated user id."""
    raw_user_id = _safe_private_path_id(owner_user_id, label="owner id")
    users_root = get_runtime_subdir("users").resolve(strict=False)
    candidate = (users_root / raw_user_id).resolve(strict=False)
    if not _is_under(candidate, users_root):
        raise PermissionError("private media owner root escapes managed users directory")
    return candidate


def user_private_media_root(owner_user_id: str, *, project_id: str | None = None) -> Path:
    """Return a non-symlinked private media root, optionally scoped to one project."""
    private_root = user_private_root(owner_user_id)
    suffix = ["media"]
    if project_id is not None:
        suffix.append(_safe_private_path_id(project_id, label="project id"))
    logical_root = private_root.joinpath(*suffix)
    resolved_root = logical_root.resolve(strict=False)
    if logical_root != resolved_root or not _is_under(resolved_root, private_root):
        raise PermissionError("private media root is invalid")
    return logical_root


def user_private_memory_root(owner_user_id: str, *, project_id: str) -> Path:
    """Return one member's private profile and journal root, outside shared project files."""
    private_root = user_private_root(owner_user_id)
    project = _safe_private_path_id(project_id, label="project id")
    logical_root = private_root / "projects" / project
    resolved_root = logical_root.resolve(strict=False)
    if logical_root != resolved_root or not _is_under(resolved_root, private_root):
        raise PermissionError("private memory root is invalid")
    return logical_root


def resolve_user_private_media_file(
    path: str | Path,
    *,
    owner_user_id: str,
    project_id: str | None = None,
) -> Path:
    """Resolve one exact private attachment without links or cross-project roots."""
    candidate = Path(path).expanduser()
    try:
        logical = Path(os.path.abspath(candidate))
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PermissionError("authorized attachment is unavailable") from exc
    media_root = user_private_media_root(owner_user_id, project_id=project_id)
    if logical != resolved or not _is_under(resolved, media_root) or not resolved.is_file():
        raise PermissionError("attachment is outside the member private media root")
    return resolved


def relocate_media_to_user(
    paths: list[str],
    *,
    owner_user_id: str,
    project_id: str | None = None,
) -> list[str]:
    """Move managed attachments into the owner's optional project-private media root.

    Only files created below Nanobot's managed media root are eligible. Caller
    supplied paths are ignored rather than becoming a read primitive.
    """
    media_root = get_media_dir().resolve(strict=False)
    destination_root = user_private_media_root(owner_user_id, project_id=project_id)
    destination_root.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        destination_root.chmod(0o700)

    relocated: list[str] = []
    for raw_path in paths:
        source = Path(raw_path)
        try:
            resolved = source.resolve(strict=True)
        except OSError:
            continue
        if not _is_under(resolved, media_root) or not resolved.is_file():
            continue
        filename = f"{uuid.uuid4().hex}-{resolved.name}"
        destination = destination_root / filename
        try:
            shutil.move(str(resolved), destination)
            os.chmod(destination, 0o600)
        except OSError:
            continue
        relocated.append(str(destination))
    return relocated
