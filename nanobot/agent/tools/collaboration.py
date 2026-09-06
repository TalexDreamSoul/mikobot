"""Project and conversation-binding tools."""
# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Literal

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import RequestContext, ToolContext, current_request_context
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema
from nanobot.collaboration import (
    CollaborationRepository,
    CollaborationStoreError,
    ConversationScope,
    Project,
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


__all__ = ["ProjectsTool"]
