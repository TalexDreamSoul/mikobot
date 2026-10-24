"""Workspace-scoped source preview payloads for the WebUI."""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from nanobot.config.paths import get_media_dir
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.security.workspace_policy import (
    WorkspaceBoundaryError,
    is_path_within,
    resolve_allowed_path,
    resolve_path,
)

MAX_FILE_PREVIEW_BYTES = 384 * 1024
MAX_IMAGE_PREVIEW_BYTES = 8 * 1024 * 1024


def _image_mime(prefix: bytes) -> str | None:
    # Only inert raster formats; SVG/HTML continue to be shown as source.
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    return None


class WebUIFilePreviewError(ValueError):
    """Raised when a file cannot be previewed through the WebUI."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class PreviewMedia:
    """Media a member may preview beyond the project workspace.

    Host owners pass ``None`` instead and keep the whole shared media directory.
    """

    roots: tuple[Path, ...] = ()
    # Stored messages of the previewed session; read only when a shared-media path is asked for.
    session_messages: Callable[[], Iterable[object] | None] | None = None


def _shared_media_references(messages: Iterable[object]) -> list[Path]:
    """Shared-media files that a session's own records point at.

    Only fields the runtime writes qualify: ``media`` lists and the artifact paths
    ``generate_image`` reported. Message text never does, because a member can type
    or coax any path into a conversation.
    """
    media_root = get_media_dir()
    files: dict[Path, None] = {}
    for item in messages:
        if not isinstance(item, dict):
            continue
        message = cast(dict[str, object], item)
        candidates: list[object] = []
        media = message.get("media")
        if isinstance(media, list):
            candidates.extend(cast(list[object], media))
        if message.get("role") == "tool" and message.get("name") == "generate_image":
            candidates.extend(_artifact_paths(message.get("content")))
        for candidate in candidates:
            if (
                isinstance(candidate, str)
                and os.path.isabs(candidate)
                and is_path_within(candidate, media_root)
            ):
                files[Path(candidate)] = None
    return list(files)


def _artifact_paths(content: object) -> list[object]:
    try:
        payload = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return []
    artifacts = cast(dict[str, object], payload).get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list):
        return []
    return [
        cast(dict[str, object], artifact).get("path")
        for artifact in cast(list[object], artifacts)
        if isinstance(artifact, dict)
    ]


def file_reference_payload(
    raw_path: str | None, *, scope: WorkspaceScope, media: PreviewMedia | None = None,
) -> dict[str, str | None]:
    """Resolve copyable paths through the preview policy without reading file contents."""
    resolved = resolve_file_path(raw_path, scope=scope, media=media)
    try:
        relative = resolved.relative_to(scope.project_path).as_posix()
    except ValueError:
        relative = None
    return {"path": str(resolved), "relative_path": relative}


def file_preview_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    max_bytes: int = MAX_FILE_PREVIEW_BYTES,
    media: PreviewMedia | None = None,
) -> dict[str, Any]:
    """Return a bounded source or raster preview within the session workspace scope."""

    resolved = resolve_file_path(raw_path, scope=scope, media=media)

    try:
        with open(resolved, "rb") as f:
            prefix = f.read(12)
            mime = _image_mime(prefix)
            limit = MAX_IMAGE_PREVIEW_BYTES if mime else max_bytes
            raw = prefix + f.read(max(0, limit + 1 - len(prefix)))
        size = resolved.stat().st_size
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e

    metadata = {
        "path": str(resolved),
        "display_path": _display_path(resolved, scope.project_path),
        "project_path": str(scope.project_path),
        "size": size,
    }
    if mime:
        if len(raw) > MAX_IMAGE_PREVIEW_BYTES:
            raise WebUIFilePreviewError(413, "image is too large to preview (maximum 8 MiB)")
        return {
            **metadata,
            "kind": "image",
            "mime_type": mime,
            "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
        }

    if b"\0" in raw[:4096]:
        raise WebUIFilePreviewError(415, "binary files cannot be previewed")

    truncated = len(raw) > max_bytes
    preview_bytes = raw[:max_bytes]
    try:
        content = preview_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = preview_bytes.decode("utf-8", errors="replace")

    return {
        **metadata,
        "kind": "text",
        "language": _language_for_path(resolved),
        "content": content,
        "truncated": truncated,
    }


def file_preview_availability_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    media: PreviewMedia | None = None,
) -> dict[str, bool]:
    """Probe a readable source or raster candidate without loading it fully."""

    resolved = resolve_file_path(raw_path, scope=scope, media=media)
    try:
        with open(resolved, "rb") as f:
            prefix = f.read(4096)
        size = resolved.stat().st_size
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e
    if _image_mime(prefix):
        if size > MAX_IMAGE_PREVIEW_BYTES:
            raise WebUIFilePreviewError(413, "image is too large to preview (maximum 8 MiB)")
    elif b"\0" in prefix:
        raise WebUIFilePreviewError(415, "binary files cannot be previewed")
    return {"available": True}


def resolve_file_path(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    literal: bool = False,
    media: PreviewMedia | None = None,
) -> Path:
    path = (raw_path or "").strip() if literal else _clean_preview_path(raw_path)
    if not path:
        raise WebUIFilePreviewError(400, "missing path")
    if len(path) > 4096:
        raise WebUIFilePreviewError(400, "path is too long")

    try:
        extra_roots = None
        if scope.restrict_to_workspace:
            extra_roots = [get_media_dir()] if media is None else list(media.roots)
        try:
            resolved = _resolve_in_scope(path, scope, extra_roots)
        except WorkspaceBoundaryError:
            recorded = _recorded_media(path, scope, media)
            if not recorded:
                raise
            resolved = _resolve_in_scope(path, scope, extra_roots, recorded)
    except FileNotFoundError as e:
        raise WebUIFilePreviewError(404, "file not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(403, "file is outside the current workspace") from e
    except PermissionError as e:
        raise WebUIFilePreviewError(403, "file is not readable") from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e

    if not resolved.is_file():
        raise WebUIFilePreviewError(404, "file not found")
    return resolved


def _resolve_in_scope(
    path: str,
    scope: WorkspaceScope,
    extra_roots: list[Path] | None,
    extra_files: list[Path] | None = None,
) -> Path:
    return resolve_allowed_path(
        path,
        workspace=scope.project_path,
        allowed_root=scope.project_path if scope.restrict_to_workspace else None,
        extra_allowed_roots=extra_roots,
        extra_allowed_files=extra_files,
        strict=True,
    )


def _recorded_media(path: str, scope: WorkspaceScope, media: PreviewMedia | None) -> list[Path]:
    """Exact shared-media files a member's session recorded, read only for shared-media paths."""
    if media is None or media.session_messages is None:
        return []
    if not is_path_within(resolve_path(path, scope.project_path), get_media_dir()):
        return []
    return _shared_media_references(media.session_messages() or ())


def _clean_preview_path(raw_path: str | None) -> str:
    if raw_path is None:
        return ""
    value = raw_path.strip()
    if not value:
        return ""
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:[\\/]", value):
            value = value[1:]
    else:
        value = unquote(value)
    value = value.split("?", 1)[0].split("#", 1)[0].strip()
    if not re.match(r"^[A-Za-z]:[\\/]", value):
        value = re.sub(r":\d+(?::\d+)?$", "", value)
    return value


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _language_for_path(path: Path) -> str:
    name = path.name.lower()
    ext = path.suffix.lower().lstrip(".")
    if name == "dockerfile":
        return "dockerfile"
    return {
        "cjs": "javascript",
        "css": "css",
        "cts": "typescript",
        "html": "html",
        "js": "javascript",
        "json": "json",
        "jsonl": "json",
        "jsx": "jsx",
        "md": "markdown",
        "mdx": "markdown",
        "mjs": "javascript",
        "mts": "typescript",
        "py": "python",
        "pyi": "python",
        "scss": "scss",
        "sh": "bash",
        "toml": "toml",
        "ts": "typescript",
        "tsx": "tsx",
        "yaml": "yaml",
        "yml": "yaml",
    }.get(ext, ext or "text")
