"""Trusted collaboration scope helpers for tool-boundary authorization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from nanobot.agent.tools.context import current_request_context
from nanobot.collaboration.models import ConversationScope
from nanobot.security.private_media import resolve_user_private_media_file, user_private_memory_root


@dataclass(frozen=True)
class MemberToolScope:
    """The project capability attached to one externally authorized turn."""

    scope: ConversationScope
    project_root: Path


def current_member_scope() -> ConversationScope | None:
    """Return the trusted non-host collaboration scope for this tool call."""
    request = current_request_context()
    if request is None:
        return None
    candidate = request.attributes.get("collaboration_scope")
    if not isinstance(candidate, ConversationScope):
        return None
    if candidate.is_local_owner or candidate.user_id is None:
        return None
    return candidate


def current_member_tool_scope() -> MemberToolScope | None:
    """Resolve the current member's project root or deny incomplete authority."""
    scope = current_member_scope()
    if scope is None:
        return None
    if scope.route_denied or scope.project_id is None or scope.project is None:
        raise PermissionError("member authorization has no active project")
    workspace_path = scope.workspace_path
    if not isinstance(workspace_path, str) or not workspace_path.strip():
        raise PermissionError("member authorization has no project workspace")
    project_root = Path(workspace_path).expanduser().resolve(strict=False)
    if project_root != Path(scope.project.workspace_path).expanduser().resolve(strict=False):
        raise PermissionError("member authorization project workspace is inconsistent")
    return MemberToolScope(scope=scope, project_root=project_root)


def current_member_attachment_files() -> tuple[Path, ...]:
    """Return only exact private attachments authorized for this request."""
    scope = current_member_tool_scope()
    if scope is None:
        return ()
    request = current_request_context()
    assert request is not None
    user_id = scope.scope.user_id
    project_id = scope.scope.project_id
    if user_id is None or project_id is None:
        return ()
    raw_paths = request.attributes.get("authorized_attachment_paths", ())
    if not isinstance(raw_paths, (tuple, list)):
        return ()

    files: list[Path] = []
    seen: set[Path] = set()
    for raw_path in cast(list[object] | tuple[object, ...], raw_paths):
        if not isinstance(raw_path, str):
            continue
        try:
            path = resolve_user_private_media_file(
                raw_path,
                owner_user_id=user_id,
                project_id=project_id,
            )
        except (OSError, PermissionError, ValueError):
            continue
        if path not in seen:
            seen.add(path)
            files.append(path)
    return tuple(files)


def current_member_memory_files(*, writable: bool = False) -> tuple[Path, ...]:
    """Grant exact own-profile files, never a directory or another member's journal."""
    member = current_member_tool_scope()
    if member is None or member.scope.user_id is None or member.scope.project_id is None:
        return ()
    root = user_private_memory_root(member.scope.user_id, project_id=member.scope.project_id)
    names = ("SOUL.md", "USER.md", "memory/MEMORY.md")
    if not writable:
        names += ("memory/history.jsonl",)
    files = tuple(root / name for name in names)
    if any(path.resolve(strict=False) != path for path in files):
        raise PermissionError("private memory file is invalid")
    return files
