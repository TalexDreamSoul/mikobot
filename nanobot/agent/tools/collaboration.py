"""Project, conversation-binding, and task tools."""
# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Literal

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import RequestContext, ToolContext, current_request_context
from nanobot.agent.tools.schema import ObjectSchema, StringSchema, tool_parameters_schema
from nanobot.collaboration import (
    CollaborationRepository,
    CollaborationStoreError,
    ConversationScope,
    Project,
    Task,
    TaskStatus,
)
from nanobot.collaboration.conversation import canonical_conversation_id
from nanobot.collaboration.links import IdentityLinkError, IdentityLinkStore
from nanobot.config.paths import get_runtime_subdir


def _scope(request: RequestContext | None) -> ConversationScope | None:
    if request is None:
        return None
    value = request.attributes.get("collaboration_scope")
    return value if isinstance(value, ConversationScope) else None


def _thread_id(request: RequestContext) -> str | None:
    for key in ("thread_id", "threadId", "root_id", "rootId"):
        value = request.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _project_payload(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "name": project.name,
        "workspace_path": project.workspace_path,
    }


def _task_payload(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "task_list_id": task.task_list_id,
        "title": task.title,
        "status": task.status.value,
        "description": task.description,
        "assignee_user_id": task.assignee_user_id,
        "position": task.position,
    }


@tool_parameters(
    tool_parameters_schema(
        required=["action"],
        action=StringSchema(
            "Action: list, create, bind_current, set_default, create_link, or consume_link.",
            enum=("list", "create", "bind_current", "set_default", "create_link", "consume_link"),
        ),
        project_id=StringSchema("Project id for bind_current or set_default.", nullable=True),
        name=StringSchema("Project name for create.", nullable=True),
        link_code=StringSchema("Eight-character identity link code for consume_link.", nullable=True),
    )
)
class ProjectsTool(Tool):
    """Manage the current user's lightweight projects and conversation binding."""

    def __init__(self, repository: CollaborationRepository) -> None:
        self._repository = repository

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.collaboration_repository is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.collaboration_repository is None:
            raise RuntimeError("collaboration repository is unavailable")
        return cls(ctx.collaboration_repository)

    @property
    def name(self) -> str:
        return "projects"

    @property
    def description(self) -> str:
        return (
            "List or create the current user's projects, bind the current channel conversation "
            "to one project, or select a default project. Bind only when the user explicitly asks."
        )

    async def execute(
        self,
        action: Literal[
            "list", "create", "bind_current", "set_default", "create_link", "consume_link"
        ],
        project_id: str | None = None,
        name: str | None = None,
        link_code: str | None = None,
    ) -> str | ToolResult:
        request = current_request_context()
        scope = _scope(request)
        if request is None or scope is None or scope.user_id is None:
            return ToolResult.error("Error: projects requires an authenticated user request.")
        user_id = scope.user_id
        assert user_id is not None
        try:
            if action == "create_link":
                code = await IdentityLinkStore(self._repository).create(user_id)
                return json.dumps({"link_code": code, "expires_in_seconds": 600})
            if action == "consume_link":
                if not request.sender_id:
                    return ToolResult.error("Error: current channel sender identity is unavailable.")
                if not link_code:
                    return ToolResult.error("Error: consume_link requires link_code.")
                linked_user_id = await IdentityLinkStore(self._repository).consume(
                    link_code, channel=request.channel, sender_id=request.sender_id
                )
                return json.dumps({"linked_user_id": linked_user_id, "applies_from": "next_message"})
            if action == "list":
                projects = await self._repository.list_projects(user_id)
                return json.dumps({"projects": [_project_payload(project) for project in projects]}, ensure_ascii=False)
            if action == "create":
                if not name or not name.strip():
                    return ToolResult.error("Error: create requires a project name.")
                workspace = await asyncio.to_thread(
                    lambda: get_runtime_subdir("collaboration")
                    / "workspaces"
                    / user_id
                    / f"project-{secrets.token_hex(6)}"
                )
                await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=False)
                project = await self._repository.create_project(user_id, name.strip(), workspace)
                return json.dumps({"project": _project_payload(project)}, ensure_ascii=False)
            if not project_id:
                return ToolResult.error(f"Error: {action} requires project_id.")
            if action == "set_default":
                user = await self._repository.update_user_default_project(user_id, project_id)
                return json.dumps({"default_project_id": user.default_project_id})
            binding = await self._repository.bind_conversation(
                request.channel, canonical_conversation_id(request.chat_id, request.metadata), project_id,
                user_id, thread_id=_thread_id(request),
            )
            return json.dumps({"binding_id": binding.id, "project_id": binding.project_id, "applies_from": "next_message"})
        except (CollaborationStoreError, IdentityLinkError, OSError, ValueError) as exc:
            return ToolResult.error(f"Error managing projects: {exc}")


@tool_parameters(
    ObjectSchema(
        {
            "action": StringSchema(
                "Action: list, create, update, delete, list_lists, or create_list.",
                enum=("list", "create", "update", "delete", "list_lists", "create_list"),
            ),
            "task_id": StringSchema("Task id for update or delete.", nullable=True),
            "task_list_id": StringSchema(
                "Task-list id for create or list filtering.", nullable=True
            ),
            "title": StringSchema("Task title for create or update.", nullable=True),
            "description": StringSchema("Optional task description.", nullable=True),
            "status": StringSchema(
                "Task status.",
                enum=("todo", "in_progress", "done", "cancelled"),
                nullable=True,
            ),
            "list_name": StringSchema("Task-list name for create_list.", nullable=True),
        },
        required=["action"],
        additional_properties=False,
    ).to_json_schema()
)
class ProjectTasksTool(Tool):
    """Manage task lists and tasks inside the authorized current project."""

    def __init__(self, repository: CollaborationRepository) -> None:
        self._repository = repository

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.collaboration_repository is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.collaboration_repository is None:
            raise RuntimeError("collaboration repository is unavailable")
        return cls(ctx.collaboration_repository)

    @property
    def name(self) -> str:
        return "project_tasks"

    @property
    def description(self) -> str:
        return (
            "List and maintain task lists for the authorized current project. Use this for durable "
            "project work items; it never reads or changes another project."
        )

    async def execute(
        self,
        action: Literal["list", "create", "update", "delete", "list_lists", "create_list"],
        task_id: str | None = None,
        task_list_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: Literal["todo", "in_progress", "done", "cancelled"] | None = None,
        list_name: str | None = None,
    ) -> str | ToolResult:
        scope = _scope(current_request_context())
        if scope is None or scope.user_id is None or scope.project_id is None:
            return ToolResult.error("Error: bind this conversation to a project before using project tasks.")
        try:
            if action == "list_lists":
                lists = await self._repository.list_task_lists(scope.user_id, scope.project_id)
                return json.dumps({"task_lists": [{"id": item.id, "name": item.name, "position": item.position} for item in lists]}, ensure_ascii=False)
            if action == "create_list":
                if not list_name or not list_name.strip():
                    return ToolResult.error("Error: create_list requires list_name.")
                item = await self._repository.create_task_list(scope.project_id, scope.user_id, list_name.strip())
                return json.dumps({"task_list": {"id": item.id, "name": item.name}})
            if action == "list":
                tasks = await self._repository.list_tasks(scope.user_id, scope.project_id, task_list_id=task_list_id)
                return json.dumps({"tasks": [_task_payload(task) for task in tasks]}, ensure_ascii=False)
            if action == "create":
                if not title or not title.strip():
                    return ToolResult.error("Error: create requires a task title.")
                target_list_id = task_list_id
                if not target_list_id:
                    lists = await self._repository.list_task_lists(scope.user_id, scope.project_id)
                    target_list_id = lists[0].id if lists else (await self._repository.create_task_list(scope.project_id, scope.user_id, "Tasks")).id
                task = await self._repository.create_task(
                    scope.project_id, target_list_id, scope.user_id, title.strip(),
                    description=(description or "").strip(), status=TaskStatus(status or "todo"),
                )
                return json.dumps({"task": _task_payload(task)}, ensure_ascii=False)
            if not task_id:
                return ToolResult.error(f"Error: {action} requires task_id.")
            current = await self._repository.get_task(scope.user_id, task_id)
            if current is None or current.project_id != scope.project_id:
                return ToolResult.error("Error: task is not in the current project.")
            if action == "delete":
                return json.dumps({"deleted": await self._repository.delete_task(task_id, scope.user_id)})
            task = await self._repository.update_task(
                task_id, scope.user_id, title=title.strip() if title is not None else None,
                description=description.strip() if description is not None else None,
                status=TaskStatus(status) if status is not None else None,
            )
            return json.dumps({"task": _task_payload(task)}, ensure_ascii=False)
        except (CollaborationStoreError, ValueError) as exc:
            return ToolResult.error(f"Error managing project tasks: {exc}")


__all__ = ["ProjectTasksTool", "ProjectsTool"]
