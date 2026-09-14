"""Read-only project workspace materials for the authenticated WebUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote

from nanobot.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path

MAX_PROJECT_MATERIALS = 256
MAX_PROJECT_MATERIAL_PREVIEW_BYTES = 384 * 1024
_HIDDEN_PARTS = {".git", ".gitignore", ".gitmodules"}
_PRIVATE_MATERIALS = {"SOUL.md", "USER.md", "memory/MEMORY.md", "memory/history.jsonl", "memory/.dream_cursor", "memory/.cursor"}


class ProjectMaterialsError(ValueError):
    """Raised when a project material cannot be listed or previewed safely."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def project_materials_payload(
    project_path: str | Path,
    *,
    raw_path: str | None = None,
) -> dict[str, Any]:
    """Return relative project files or one bounded text preview.

    Project members may inspect shared project files, but this projection never
    returns the workspace's absolute path. Symlinks, hidden control files, raw
    history, and files outside the project root are excluded.
    """
    root = Path(project_path).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise ProjectMaterialsError(404, "project workspace not found")
    if raw_path is None:
        return {"files": _list_files(root)}
    relative = _relative_material_path(raw_path)
    resolved = _resolve_material(root, relative)
    return _preview_file(resolved, relative)
def project_memory_payload(project_path: str | Path) -> dict[str, object]:
    """Return only the shared project's Dream-maintained MEMORY.md."""
    root = Path(project_path).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise ProjectMaterialsError(404, "project workspace not found")
    path = root / "memory" / "MEMORY.md"
    if not path.exists():
        return {
            "path": "memory/MEMORY.md",
            "size": 0,
            "previewable": False,
            "truncated": False,
            "content": "",
        }
    return _preview_file(
        _resolve_material(root, "memory/MEMORY.md", allow_shared_memory=True),
        "memory/MEMORY.md",
    )

def _list_files(root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    try:
        candidates = sorted(root.rglob("*"), key=lambda path: path.as_posix())
    except OSError as exc:
        raise ProjectMaterialsError(500, "failed to list project materials") from exc
    for candidate in candidates:
        if len(files) >= MAX_PROJECT_MATERIALS:
            break
        try:
            relative = candidate.relative_to(root).as_posix()
            if not candidate.is_file() or candidate.is_symlink() or _is_hidden(relative):
                continue
            if relative in _PRIVATE_MATERIALS:
                continue
            stat = candidate.stat()
            files.append({
                "path": relative,
                "size": stat.st_size,
                "modified_at_ms": int(stat.st_mtime * 1000),
                "previewable": _is_text_file(candidate),
            })
        except (OSError, ValueError):
            continue
    return files


def _preview_file(path: Path, relative: str) -> dict[str, object]:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_PROJECT_MATERIAL_PREVIEW_BYTES + 1)
        size = path.stat().st_size
    except OSError as exc:
        raise ProjectMaterialsError(500, "failed to read project material") from exc
    if b"\0" in raw[:4096]:
        return {
            "path": relative,
            "size": size,
            "previewable": False,
            "truncated": False,
            "content": "",
        }
    try:
        content = raw[:MAX_PROJECT_MATERIAL_PREVIEW_BYTES].decode("utf-8")
    except UnicodeDecodeError:
        content = raw[:MAX_PROJECT_MATERIAL_PREVIEW_BYTES].decode("utf-8", errors="replace")
    return {
        "path": relative,
        "size": size,
        "previewable": True,
        "truncated": len(raw) > MAX_PROJECT_MATERIAL_PREVIEW_BYTES,
        "content": content,
    }


def _resolve_material(root: Path, relative: str, *, allow_shared_memory: bool = False) -> Path:
    logical = root / relative
    cursor = root
    for part in Path(relative).parts:
        cursor /= part
        try:
            if cursor.is_symlink():
                raise ProjectMaterialsError(403, "symlinked project materials are not available")
        except OSError as exc:
            raise ProjectMaterialsError(404, "project material not found") from exc
    try:
        resolved = resolve_allowed_path(
            logical,
            workspace=root,
            allowed_root=root,
            strict=True,
        )
    except FileNotFoundError as exc:
        raise ProjectMaterialsError(404, "project material not found") from exc
    except WorkspaceBoundaryError as exc:
        raise ProjectMaterialsError(403, "project material is outside the project workspace") from exc
    except OSError as exc:
        raise ProjectMaterialsError(400, "invalid project material path") from exc
    if not resolved.is_file() or (relative in _PRIVATE_MATERIALS and not allow_shared_memory) or _is_hidden(relative):
        raise ProjectMaterialsError(404, "project material not found")
    return resolved


def _relative_material_path(raw_path: str) -> str:
    value = unquote(raw_path).strip().replace("\\", "/")
    if not value or value.startswith("/") or ":" in value.split("/", 1)[0]:
        raise ProjectMaterialsError(400, "project material path must be relative")
    parts = tuple(part for part in value.split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        raise ProjectMaterialsError(400, "invalid project material path")
    relative = "/".join(parts)
    if len(relative) > 4096 or _is_hidden(relative) or relative in _PRIVATE_MATERIALS:
        raise ProjectMaterialsError(404, "project material not found")
    return relative


def _is_hidden(relative: str) -> bool:
    return any(part.startswith(".") or part in _HIDDEN_PARTS for part in Path(relative).parts)


def _is_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return b"\0" not in handle.read(4096)
    except OSError:
        return False
