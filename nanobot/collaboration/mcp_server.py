"""Low-overhead stdio MCP surface for local Codex and other MCP clients."""
# pyright: reportUnusedFunction=false

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.collaboration import (
    CollaborationRepository,
    ContextSource,
    ContextSourceKind,
    Task,
    TaskStatus,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_MAX_CONTEXT_CHARS = 8_000


def _task_payload(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "task_list_id": task.task_list_id,
        "title": task.title,
        "status": task.status.value,
        "description": task.description,
        "position": task.position,
    }


def _source_content(project_root: Path, source: ContextSource) -> str | None:
    config = source.config
    if source.kind is ContextSourceKind.CUSTOM:
        value = config.get("content")
        return value[:_MAX_CONTEXT_CHARS] if isinstance(value, str) else None
    if source.kind is not ContextSourceKind.DOCUMENT:
        return None
    raw_path = config.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not resolved.is_relative_to(project_root):
        return None
    try:
        with open(resolved, encoding="utf-8", errors="replace") as handle:
            return handle.read(_MAX_CONTEXT_CHARS + 1)[:_MAX_CONTEXT_CHARS]
    except OSError:
        return None


async def build_collaboration_mcp_server(
    collaboration: CollaborationRepository,
    *,
    user_id: str,
    project_id: str,
) -> FastMCP[object]:
    """Build one project-pinned MCP server without starting background services."""
    from mcp.server.fastmcp import FastMCP

    project = await collaboration.get_project(user_id, project_id)
    if project is None:
        raise ValueError("project is not available to this user")
    mcp: FastMCP[object] = FastMCP("nanobot-project")

    @mcp.tool()
    async def nanobot_projects() -> dict[str, object]:
        """List projects available to the pinned local nanobot user."""
        return {
            "projects": [
                {"id": item.id, "name": item.name, "workspace_path": item.workspace_path}
                for item in await collaboration.list_projects(user_id)
            ],
            "active_project_id": project_id,
        }

    @mcp.tool()
    async def nanobot_task_lists() -> dict[str, object]:
        """List task lists in the active nanobot project."""
        return {
            "task_lists": [
                {"id": item.id, "name": item.name, "position": item.position}
                for item in await collaboration.list_task_lists(user_id, project_id)
            ]
        }

    @mcp.tool()
    async def nanobot_tasks(task_list_id: str | None = None) -> dict[str, object]:
        """List durable tasks in the active nanobot project."""
        return {
            "tasks": [
                _task_payload(item)
                for item in await collaboration.list_tasks(
                    user_id, project_id, task_list_id=task_list_id
                )
            ]
        }

    @mcp.tool()
    async def nanobot_task_create(
        title: str,
        description: str = "",
        task_list_id: str | None = None,
    ) -> dict[str, object]:
        """Create a task in the active project; the MCP client should request approval."""
        if not title.strip():
            raise ValueError("title is required")
        target = task_list_id
        if target is None:
            lists = await collaboration.list_task_lists(user_id, project_id)
            target = lists[0].id if lists else (
                await collaboration.create_task_list(project_id, user_id, "Tasks")
            ).id
        task = await collaboration.create_task(
            project_id,
            target,
            user_id,
            title.strip(),
            description=description.strip(),
        )
        return {"task": _task_payload(task)}

    @mcp.tool()
    async def nanobot_task_update(
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict[str, object]:
        """Update one task in the active project; the MCP client should request approval."""
        current = await collaboration.get_task(user_id, task_id)
        if current is None or current.project_id != project_id:
            raise ValueError("task is not in the active project")
        task = await collaboration.update_task(
            task_id,
            user_id,
            title=title,
            description=description,
            status=TaskStatus(status) if status is not None else None,
        )
        return {"task": _task_payload(task)}

    @mcp.tool()
    async def nanobot_context_sources() -> dict[str, object]:
        """List enabled context sources configured for the active project."""
        return {
            "sources": [
                {"id": item.id, "name": item.name, "kind": item.kind.value}
                for item in await collaboration.list_context_sources(user_id, project_id)
                if item.enabled
            ]
        }

    @mcp.tool()
    async def nanobot_context_read(source_id: str) -> dict[str, object]:
        """Read one bounded, authorized context source from the active project."""
        source = await collaboration.get_context_source(user_id, source_id)
        if source is None or source.project_id != project_id or not source.enabled:
            raise ValueError("context source is not available in the active project")
        content = await asyncio.to_thread(
            _source_content, Path(project.workspace_path).resolve(), source
        )
        if content is None:
            raise ValueError("context source has no readable bounded content")
        return {
            "source": {"id": source.id, "name": source.name, "kind": source.kind.value},
            "content": content,
            "truncated": len(content) >= _MAX_CONTEXT_CHARS,
        }

    return mcp


async def run_collaboration_mcp(
    collaboration: CollaborationRepository,
    *,
    workspace_path: str | Path,
    project_id: str | None = None,
) -> None:
    """Run the local-owner project surface over line-delimited stdio MCP."""
    owner, default_project = await collaboration.ensure_local_owner(workspace_path)
    selected_project_id = project_id or owner.default_project_id or default_project.id
    server = await build_collaboration_mcp_server(
        collaboration,
        user_id=owner.id,
        project_id=selected_project_id,
    )
    await server.run_stdio_async()


__all__ = ["build_collaboration_mcp_server", "run_collaboration_mcp"]
