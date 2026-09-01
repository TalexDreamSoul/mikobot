"""Normalized PostgreSQL project, membership, task-list, and task repository mixin."""
from __future__ import annotations

from pathlib import Path

from nanobot.collaboration.models import (
    MembershipRole,
    Project,
    ProjectMembership,
    Task,
    TaskList,
    TaskStatus,
)
from nanobot.collaboration.postgres.base import PostgresConnection, PostgresRepositoryBase
from nanobot.collaboration.postgres.rows import (
    Row,
    decode_project_membership_row,
    decode_project_row,
    decode_task_list_row,
    decode_task_row,
)
from nanobot.collaboration.store import (
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    CollaborationStoreError,
    CollaborationStoreFormatError,
)

_MAX_ID = 128
_MAX_NAME = 256
_MAX_TITLE = 512
_MAX_DESCRIPTION = 16_000
_MAX_POSITION = 10_000


class PostgresProjectsMixin(PostgresRepositoryBase):
    """Project-domain operations with explicit authorization beyond RLS."""

    async def create_project(self, owner_user_id: str, name: str, workspace_path: str | Path, *, organization_id: str | None = None) -> Project:
        owner_user_id = _id(owner_user_id, "owner_user_id")
        name = _string(name, "name", limit=_MAX_NAME)
        workspace_path = _workspace(workspace_path)
        organization_id = _id(organization_id, "organization_id") if organization_id is not None else None
        now = self._now_ms()
        async with self._actor_transaction(owner_user_id) as connection:
            await self._project_require_user(connection, owner_user_id)
            if organization_id is None:
                row = await self._fetch_one(connection, """
                    SELECT membership.organization_id
                    FROM nanobot_collaboration.collaboration_organization_memberships AS membership
                    JOIN nanobot_collaboration.collaboration_organizations AS organization
                      ON organization.id = membership.organization_id
                    WHERE organization.created_by_user_id = %s AND membership.user_id = %s
                      AND membership.role = 'owner'
                    ORDER BY organization.created_at_ms, organization.id LIMIT 1
                    """, (owner_user_id, owner_user_id))
                if row is None:
                    raise CollaborationStoreFormatError("user personal organization is missing")
                organization_id = _required_string(row, "organization_id")
            await self._project_require_organization_member(connection, organization_id, owner_user_id)
            project_id = self._new_id()
            await connection.execute(
                """
                INSERT INTO nanobot_collaboration.collaboration_projects
                    (id, organization_id, name, workspace_path, created_by_user_id, created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (project_id, organization_id, name, workspace_path, owner_user_id, now, now),
            )
            await connection.execute(
                """
                INSERT INTO nanobot_collaboration.collaboration_project_memberships
                    (organization_id, project_id, user_id, role, created_at_ms)
                VALUES (%s, %s, %s, 'owner', %s)
                """,
                (organization_id, project_id, owner_user_id, now),
            )
            row = await self._fetch_one(connection, """
                SELECT id, organization_id, name, workspace_path, created_by_user_id, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_projects
                WHERE id = %s AND organization_id = %s
                """, (project_id, organization_id))
            if row is None:
                raise CollaborationStoreError("project creation returned no row")
            return decode_project_row(row)

    async def get_project(self, user_id: str, project_id: str) -> Project | None:
        user_id, project_id = _id(user_id, "user_id"), _id(project_id, "project_id")
        async with self._actor_transaction(user_id) as connection:
            row = await self._fetch_one(connection, """
                SELECT project.id, project.organization_id, project.name, project.workspace_path,
                       project.created_by_user_id, project.created_at_ms, project.updated_at_ms
                FROM nanobot_collaboration.collaboration_projects AS project
                JOIN nanobot_collaboration.collaboration_project_memberships AS membership
                  ON membership.organization_id = project.organization_id AND membership.project_id = project.id
                WHERE project.id = %s AND membership.user_id = %s
                """, (project_id, user_id))
            return decode_project_row(row) if row is not None else None

    async def list_projects(self, user_id: str) -> list[Project]:
        user_id = _id(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            rows = await self._fetch_all(connection, """
                SELECT project.id, project.organization_id, project.name, project.workspace_path,
                       project.created_by_user_id, project.created_at_ms, project.updated_at_ms
                FROM nanobot_collaboration.collaboration_projects AS project
                JOIN nanobot_collaboration.collaboration_project_memberships AS membership
                  ON membership.organization_id = project.organization_id AND membership.project_id = project.id
                WHERE membership.user_id = %s ORDER BY project.updated_at_ms DESC, project.id DESC
                """, (user_id,))
            return [decode_project_row(row) for row in rows]

    async def update_project(self, project_id: str, actor_user_id: str, *, name: str | None = None, workspace_path: str | Path | None = None) -> Project:
        if name is None and workspace_path is None:
            raise ValueError("provide name or workspace_path")
        project_id, actor_user_id = _id(project_id, "project_id"), _id(actor_user_id, "actor_user_id")
        name = _string(name, "name", limit=_MAX_NAME) if name is not None else None
        workspace_path = _workspace(workspace_path) if workspace_path is not None else None
        async with self._actor_transaction(actor_user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_owner(connection, project, actor_user_id)
            row = await self._fetch_one(connection, """
                UPDATE nanobot_collaboration.collaboration_projects
                SET name = COALESCE(%s, name), workspace_path = COALESCE(%s, workspace_path), updated_at_ms = %s
                WHERE id = %s AND organization_id = %s
                RETURNING id, organization_id, name, workspace_path, created_by_user_id, created_at_ms, updated_at_ms
                """, (name, workspace_path, self._now_ms(), project.id, project.organization_id))
            if row is None:
                raise CollaborationNotFoundError("project not found")
            return decode_project_row(row)

    async def delete_project(self, project_id: str, actor_user_id: str) -> bool:
        project_id, actor_user_id = _id(project_id, "project_id"), _id(actor_user_id, "actor_user_id")
        async with self._actor_transaction(actor_user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_owner(connection, project, actor_user_id)
            now = self._now_ms()
            await self._fetch_one(connection, """
                UPDATE nanobot_collaboration.collaboration_users
                SET default_project_id = NULL, updated_at_ms = %s WHERE default_project_id = %s RETURNING id
                """, (now, project.id))
            await self._fetch_one(connection, """
                DELETE FROM nanobot_collaboration.collaboration_projects
                WHERE id = %s AND organization_id = %s RETURNING id
                """, (project.id, project.organization_id))
            return True

    async def add_member(self, project_id: str, actor_user_id: str, user_id: str, role: MembershipRole = MembershipRole.MEMBER) -> ProjectMembership:
        project_id, actor_user_id, user_id = _id(project_id, "project_id"), _id(actor_user_id, "actor_user_id"), _id(user_id, "user_id")
        role = _role(role)
        async with self._actor_transaction(actor_user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_owner(connection, project, actor_user_id)
            organization_id = project.organization_id
            if organization_id is None:
                raise CollaborationStoreFormatError("project organization is missing")
            await self._project_require_organization_member(connection, organization_id, user_id)
            row = await self._fetch_one(connection, """
                INSERT INTO nanobot_collaboration.collaboration_project_memberships
                    (organization_id, project_id, user_id, role, created_at_ms)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (organization_id, project_id, user_id) DO UPDATE SET role = EXCLUDED.role
                RETURNING project_id, user_id, role, created_at_ms
                """, (project.organization_id, project.id, user_id, role.value, self._now_ms()))
            if row is None:
                raise CollaborationStoreError("project membership upsert returned no row")
            return decode_project_membership_row(row)

    async def remove_member(self, project_id: str, actor_user_id: str, user_id: str) -> bool:
        project_id, actor_user_id, user_id = _id(project_id, "project_id"), _id(actor_user_id, "actor_user_id"), _id(user_id, "user_id")
        async with self._actor_transaction(actor_user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_owner(connection, project, actor_user_id)
            membership = await self._project_membership_for_user(connection, project, user_id)
            if membership is None:
                return False
            if membership.role is MembershipRole.OWNER:
                row = await self._fetch_one(connection, """
                    SELECT count(*) AS count
                    FROM nanobot_collaboration.collaboration_project_memberships
                    WHERE organization_id = %s AND project_id = %s AND role = 'owner'
                    """, (project.organization_id, project.id))
                if row is None or _required_integer(row, "count") <= 1:
                    raise CollaborationConflictError("a project must retain an owner")
            now = self._now_ms()
            await self._fetch_one(connection, """
                UPDATE nanobot_collaboration.collaboration_project_tasks
                SET assignee_user_id = NULL, updated_at_ms = %s
                WHERE organization_id = %s AND project_id = %s AND assignee_user_id = %s RETURNING id
                """, (now, project.organization_id, project.id, user_id))
            await self._fetch_one(connection, """
                UPDATE nanobot_collaboration.collaboration_users
                SET default_project_id = NULL, updated_at_ms = %s
                WHERE id = %s AND default_project_id = %s RETURNING id
                """, (now, user_id, project.id))
            await self._fetch_one(connection, """
                DELETE FROM nanobot_collaboration.collaboration_project_memberships
                WHERE organization_id = %s AND project_id = %s AND user_id = %s RETURNING project_id
                """, (project.organization_id, project.id, user_id))
            return True

    async def list_members(self, project_id: str, user_id: str) -> list[ProjectMembership]:
        project_id, user_id = _id(project_id, "project_id"), _id(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_member(connection, project, user_id)
            rows = await self._fetch_all(connection, """
                SELECT project_id, user_id, role, created_at_ms
                FROM nanobot_collaboration.collaboration_project_memberships
                WHERE organization_id = %s AND project_id = %s ORDER BY created_at_ms, user_id
                """, (project.organization_id, project.id))
            return [decode_project_membership_row(row) for row in rows]

    async def create_task_list(self, project_id: str, actor_user_id: str, name: str, *, position: int | None = None) -> TaskList:
        project_id, actor_user_id = _id(project_id, "project_id"), _id(actor_user_id, "actor_user_id")
        name = _string(name, "name", limit=_MAX_NAME)
        position = _position(position) if position is not None else None
        now = self._now_ms()
        async with self._actor_transaction(actor_user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_member(connection, project, actor_user_id)
            if position is None:
                row = await self._fetch_one(connection, """
                    SELECT COALESCE(MAX(position) + 1, 0) AS position
                    FROM nanobot_collaboration.collaboration_task_lists
                    WHERE organization_id = %s AND project_id = %s
                    """, (project.organization_id, project.id))
                position = _required_integer(row, "position") if row is not None else 0
            row = await self._fetch_one(connection, """
                INSERT INTO nanobot_collaboration.collaboration_task_lists
                    (id, organization_id, project_id, name, position, created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, project_id, name, position, created_at_ms, updated_at_ms
                """, (self._new_id(), project.organization_id, project.id, name, position, now, now))
            if row is None:
                raise CollaborationStoreError("task list creation returned no row")
            return decode_task_list_row(row)

    async def list_task_lists(self, user_id: str, project_id: str) -> list[TaskList]:
        user_id, project_id = _id(user_id, "user_id"), _id(project_id, "project_id")
        async with self._actor_transaction(user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_member(connection, project, user_id)
            rows = await self._fetch_all(connection, """
                SELECT id, project_id, name, position, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_task_lists
                WHERE organization_id = %s AND project_id = %s ORDER BY position, created_at_ms, id
                """, (project.organization_id, project.id))
            return [decode_task_list_row(row) for row in rows]

    async def get_task_list(self, user_id: str, task_list_id: str) -> TaskList | None:
        user_id, task_list_id = _id(user_id, "user_id"), _id(task_list_id, "task_list_id")
        async with self._actor_transaction(user_id) as connection:
            row = await self._fetch_one(connection, """
                SELECT task_list.id, task_list.project_id, task_list.name, task_list.position,
                       task_list.created_at_ms, task_list.updated_at_ms
                FROM nanobot_collaboration.collaboration_task_lists AS task_list
                JOIN nanobot_collaboration.collaboration_project_memberships AS membership
                  ON membership.organization_id = task_list.organization_id AND membership.project_id = task_list.project_id
                WHERE task_list.id = %s AND membership.user_id = %s
                """, (task_list_id, user_id))
            return decode_task_list_row(row) if row is not None else None

    async def update_task_list(self, task_list_id: str, actor_user_id: str, *, name: str | None = None, position: int | None = None) -> TaskList:
        if name is None and position is None:
            raise ValueError("provide name or position")
        task_list_id, actor_user_id = _id(task_list_id, "task_list_id"), _id(actor_user_id, "actor_user_id")
        name = _string(name, "name", limit=_MAX_NAME) if name is not None else None
        position = _position(position) if position is not None else None
        async with self._actor_transaction(actor_user_id) as connection:
            task_list, organization_id = await self._project_require_task_list(connection, task_list_id)
            project = await self._project_require_project(connection, task_list.project_id)
            if project.organization_id != organization_id:
                raise CollaborationNotFoundError("task list not found")
            await self._project_require_member(connection, project, actor_user_id)
            row = await self._fetch_one(connection, """
                UPDATE nanobot_collaboration.collaboration_task_lists
                SET name = COALESCE(%s, name), position = COALESCE(%s, position), updated_at_ms = %s
                WHERE id = %s AND organization_id = %s AND project_id = %s
                RETURNING id, project_id, name, position, created_at_ms, updated_at_ms
                """, (name, position, self._now_ms(), task_list.id, organization_id, task_list.project_id))
            if row is None:
                raise CollaborationNotFoundError("task list not found")
            return decode_task_list_row(row)

    async def delete_task_list(self, task_list_id: str, actor_user_id: str) -> bool:
        task_list_id, actor_user_id = _id(task_list_id, "task_list_id"), _id(actor_user_id, "actor_user_id")
        async with self._actor_transaction(actor_user_id) as connection:
            task_list, organization_id = await self._project_require_task_list(connection, task_list_id)
            project = await self._project_require_project(connection, task_list.project_id)
            if project.organization_id != organization_id:
                raise CollaborationNotFoundError("task list not found")
            await self._project_require_member(connection, project, actor_user_id)
            await self._fetch_one(connection, """
                DELETE FROM nanobot_collaboration.collaboration_task_lists
                WHERE id = %s AND organization_id = %s AND project_id = %s RETURNING id
                """, (task_list.id, organization_id, task_list.project_id))
            return True

    async def create_task(self, project_id: str, task_list_id: str, actor_user_id: str, title: str, *, description: str = "", assignee_user_id: str | None = None, status: TaskStatus = TaskStatus.TODO, position: int | None = None) -> Task:
        project_id, task_list_id, actor_user_id = _id(project_id, "project_id"), _id(task_list_id, "task_list_id"), _id(actor_user_id, "actor_user_id")
        title = _string(title, "title", limit=_MAX_TITLE)
        description = _string(description, "description", limit=_MAX_DESCRIPTION, empty=True)
        assignee_user_id = _id(assignee_user_id, "assignee_user_id") if assignee_user_id is not None else None
        status, position = _status(status), (_position(position) if position is not None else None)
        async with self._actor_transaction(actor_user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_member(connection, project, actor_user_id)
            task_list, organization_id = await self._project_require_task_list(connection, task_list_id)
            if organization_id != project.organization_id or task_list.project_id != project.id:
                raise ValueError("task list does not belong to project")
            if assignee_user_id is not None:
                await self._project_require_member(connection, project, assignee_user_id)
            if position is None:
                row = await self._fetch_one(connection, """
                    SELECT COALESCE(MAX(position) + 1, 0) AS position
                    FROM nanobot_collaboration.collaboration_project_tasks
                    WHERE organization_id = %s AND project_id = %s AND task_list_id = %s
                    """, (project.organization_id, project.id, task_list.id))
                position = _required_integer(row, "position") if row is not None else 0
            now = self._now_ms()
            row = await self._fetch_one(connection, """
                INSERT INTO nanobot_collaboration.collaboration_project_tasks
                    (id, organization_id, project_id, task_list_id, title, status, description, assignee_user_id, position, created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, project_id, task_list_id, title, status, description, assignee_user_id, position, created_at_ms, updated_at_ms
                """, (self._new_id(), project.organization_id, project.id, task_list.id, title, status.value, description, assignee_user_id, position, now, now))
            if row is None:
                raise CollaborationStoreError("task creation returned no row")
            return decode_task_row(row)

    async def list_tasks(self, user_id: str, project_id: str, *, task_list_id: str | None = None) -> list[Task]:
        user_id, project_id = _id(user_id, "user_id"), _id(project_id, "project_id")
        task_list_id = _id(task_list_id, "task_list_id") if task_list_id is not None else None
        async with self._actor_transaction(user_id) as connection:
            project = await self._project_require_project(connection, project_id)
            await self._project_require_member(connection, project, user_id)
            rows = await self._fetch_all(connection, """
                SELECT id, project_id, task_list_id, title, status, description, assignee_user_id, position, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_project_tasks
                WHERE organization_id = %s AND project_id = %s AND (%s OR task_list_id = %s)
                ORDER BY task_list_id, position, created_at_ms, id
                """, (
                    project.organization_id,
                    project.id,
                    task_list_id is None,
                    task_list_id or "",
                ))
            return [decode_task_row(row) for row in rows]

    async def get_task(self, user_id: str, task_id: str) -> Task | None:
        user_id, task_id = _id(user_id, "user_id"), _id(task_id, "task_id")
        async with self._actor_transaction(user_id) as connection:
            row = await self._fetch_one(connection, """
                SELECT task.id, task.project_id, task.task_list_id, task.title, task.status, task.description,
                       task.assignee_user_id, task.position, task.created_at_ms, task.updated_at_ms
                FROM nanobot_collaboration.collaboration_project_tasks AS task
                JOIN nanobot_collaboration.collaboration_project_memberships AS membership
                  ON membership.organization_id = task.organization_id AND membership.project_id = task.project_id
                WHERE task.id = %s AND membership.user_id = %s
                """, (task_id, user_id))
            return decode_task_row(row) if row is not None else None

    async def update_task(self, task_id: str, actor_user_id: str, *, title: str | None = None, description: str | None = None, status: TaskStatus | None = None, assignee_user_id: str | None | object = ..., position: int | None = None) -> Task:
        if title is None and description is None and status is None and assignee_user_id is ... and position is None:
            raise ValueError("provide at least one task field")
        task_id, actor_user_id = _id(task_id, "task_id"), _id(actor_user_id, "actor_user_id")
        title = _string(title, "title", limit=_MAX_TITLE) if title is not None else None
        description = _string(description, "description", limit=_MAX_DESCRIPTION, empty=True) if description is not None else None
        status = _status(status) if status is not None else None
        if assignee_user_id is not ... and assignee_user_id is not None:
            assignee_user_id = _id(assignee_user_id, "assignee_user_id")
        position = _position(position) if position is not None else None
        async with self._actor_transaction(actor_user_id) as connection:
            task, organization_id = await self._project_require_task(connection, task_id)
            project = await self._project_require_project(connection, task.project_id)
            if project.organization_id != organization_id:
                raise CollaborationNotFoundError("task not found")
            await self._project_require_member(connection, project, actor_user_id)
            if assignee_user_id is not ... and assignee_user_id is not None:
                await self._project_require_member(connection, project, assignee_user_id)
            new_assignee = task.assignee_user_id if assignee_user_id is ... else assignee_user_id
            row = await self._fetch_one(connection, """
                UPDATE nanobot_collaboration.collaboration_project_tasks
                SET title = COALESCE(%s, title), description = COALESCE(%s, description), status = COALESCE(%s, status),
                    assignee_user_id = %s, position = COALESCE(%s, position), updated_at_ms = %s
                WHERE id = %s AND organization_id = %s AND project_id = %s
                RETURNING id, project_id, task_list_id, title, status, description, assignee_user_id, position, created_at_ms, updated_at_ms
                """, (title, description, status.value if status is not None else None, new_assignee, position, self._now_ms(), task.id, organization_id, task.project_id))
            if row is None:
                raise CollaborationNotFoundError("task not found")
            return decode_task_row(row)

    async def delete_task(self, task_id: str, actor_user_id: str) -> bool:
        task_id, actor_user_id = _id(task_id, "task_id"), _id(actor_user_id, "actor_user_id")
        async with self._actor_transaction(actor_user_id) as connection:
            task, organization_id = await self._project_require_task(connection, task_id)
            project = await self._project_require_project(connection, task.project_id)
            if project.organization_id != organization_id:
                raise CollaborationNotFoundError("task not found")
            await self._project_require_member(connection, project, actor_user_id)
            await self._fetch_one(connection, """
                DELETE FROM nanobot_collaboration.collaboration_project_tasks
                WHERE id = %s AND organization_id = %s AND project_id = %s RETURNING id
                """, (task.id, organization_id, task.project_id))
            return True

    async def _project_require_user(self, connection: PostgresConnection, user_id: str) -> None:
        row = await self._fetch_one(connection, "SELECT id FROM nanobot_collaboration.collaboration_users WHERE id = %s", (user_id,))
        if row is None:
            raise CollaborationNotFoundError("user not found")

    async def _project_require_organization_member(self, connection: PostgresConnection, organization_id: str, user_id: str) -> None:
        row = await self._fetch_one(connection, """
            SELECT user_id FROM nanobot_collaboration.collaboration_organization_memberships
            WHERE organization_id = %s AND user_id = %s
            """, (organization_id, user_id))
        if row is None:
            raise CollaborationPermissionError("organization membership is required")

    async def _project_require_project(self, connection: PostgresConnection, project_id: str) -> Project:
        row = await self._fetch_one(connection, """
            SELECT id, organization_id, name, workspace_path, created_by_user_id, created_at_ms, updated_at_ms
            FROM nanobot_collaboration.collaboration_projects WHERE id = %s
            """, (project_id,))
        if row is None:
            raise CollaborationNotFoundError("project not found")
        return decode_project_row(row)

    async def _project_membership_for_user(self, connection: PostgresConnection, project: Project, user_id: str) -> ProjectMembership | None:
        row = await self._fetch_one(connection, """
            SELECT project_id, user_id, role, created_at_ms
            FROM nanobot_collaboration.collaboration_project_memberships
            WHERE organization_id = %s AND project_id = %s AND user_id = %s
            """, (project.organization_id, project.id, user_id))
        return decode_project_membership_row(row) if row is not None else None

    async def _project_require_member(self, connection: PostgresConnection, project: Project, user_id: str) -> ProjectMembership:
        membership = await self._project_membership_for_user(connection, project, user_id)
        if membership is None:
            raise CollaborationPermissionError("project membership is required")
        return membership

    async def _project_require_owner(self, connection: PostgresConnection, project: Project, user_id: str) -> ProjectMembership:
        membership = await self._project_require_member(connection, project, user_id)
        if membership.role is not MembershipRole.OWNER:
            raise CollaborationPermissionError("project owner role is required")
        return membership

    async def _project_require_task_list(self, connection: PostgresConnection, task_list_id: str) -> tuple[TaskList, str]:
        row = await self._fetch_one(connection, """
            SELECT id, organization_id, project_id, name, position, created_at_ms, updated_at_ms
            FROM nanobot_collaboration.collaboration_task_lists WHERE id = %s
            """, (task_list_id,))
        if row is None:
            raise CollaborationNotFoundError("task list not found")
        return decode_task_list_row(row), _required_string(row, "organization_id")

    async def _project_require_task(self, connection: PostgresConnection, task_id: str) -> tuple[Task, str]:
        row = await self._fetch_one(connection, """
            SELECT id, organization_id, project_id, task_list_id, title, status, description,
                   assignee_user_id, position, created_at_ms, updated_at_ms
            FROM nanobot_collaboration.collaboration_project_tasks WHERE id = %s
            """, (task_id,))
        if row is None:
            raise CollaborationNotFoundError("task not found")
        return decode_task_row(row), _required_string(row, "organization_id")


def _id(value: object, field: str) -> str:
    return _string(value, field, limit=_MAX_ID)


def _string(value: object, field: str, *, limit: int, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError(f"{field} must be a string")
    result = value.strip()
    if (not result and not empty) or len(result) > limit or any(ord(character) < 32 for character in result):
        raise CollaborationStoreFormatError(f"invalid {field}")
    return result


def _workspace(value: object) -> str:
    if not isinstance(value, (str, Path)):
        raise CollaborationStoreFormatError("workspace_path must be a path")
    result = str(value).strip()
    if not result or len(result) > 4_096 or any(ord(character) < 32 for character in result):
        raise CollaborationStoreFormatError("invalid workspace_path")
    return str(Path(result).expanduser().resolve(strict=False))


def _position(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_POSITION:
        raise CollaborationStoreFormatError("invalid position")
    return value


def _role(value: object) -> MembershipRole:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError("invalid membership role")
    try:
        return MembershipRole(value)
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid membership role") from exc


def _status(value: object) -> TaskStatus:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError("invalid task status")
    try:
        return TaskStatus(value)
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid task status") from exc


def _required_string(row: Row, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise CollaborationStoreFormatError(f"invalid database {field}")
    return value


def _required_integer(row: Row, field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CollaborationStoreFormatError(f"invalid database {field}")
    return value
