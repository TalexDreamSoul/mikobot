"""Private vault, persona, sharing, and personal-task PostgreSQL operations."""
from __future__ import annotations

from typing import Any

from ..models import (
    Persona,
    PersonalTask,
    ShareGrant,
    SharePermission,
    TaskReviewState,
    TaskStatus,
    User,
    Vault,
    VaultKind,
)
from ..store import (
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    CollaborationStoreFormatError,
)
from .base import PostgresRepositoryBase
from .rows import (
    decode_persona_row,
    decode_personal_task_row,
    decode_share_grant_row,
    decode_user_row,
    decode_vault_row,
)

_MAX_STRING = 256
_MAX_TEXT = 65_536


class PostgresPrivateMixin(PostgresRepositoryBase):
    """Normalized private collaboration aggregates with explicit share checks."""

    async def create_vault(
        self, user_id: str, name: str, *, kind: VaultKind = VaultKind.PRIVATE
    ) -> Vault:
        user_id = _id(user_id, "user_id")
        name = _string(name, "name")
        kind = _vault_kind(kind)
        now = self._now_ms()
        async with self._actor_transaction(user_id) as connection:
            await self._require_private_user(connection, user_id)
            organization_id = await self._owner_organization(connection, user_id)
            row = await self._fetch_one(
                connection,
                """
                INSERT INTO nanobot_collaboration.collaboration_vaults
                    (id, organization_id, owner_user_id, name, kind, created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, owner_user_id, name, kind, created_at_ms, updated_at_ms
                """,
                (self._new_id(), organization_id, user_id, name, kind.value, now, now),
            )
        assert row is not None
        return decode_vault_row(row)

    async def list_vaults(self, user_id: str) -> list[Vault]:
        user_id = _id(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            await self._require_private_user(connection, user_id)
            rows = await self._fetch_all(
                connection,
                """
                SELECT id, owner_user_id, name, kind, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_vaults
                WHERE owner_user_id = %s
                ORDER BY created_at_ms, id
                """,
                (user_id,),
            )
        return [decode_vault_row(row) for row in rows]

    async def get_vault(self, actor_user_id: str, vault_id: str) -> Vault | None:
        actor_user_id, vault_id = _id(actor_user_id, "actor_user_id"), _id(vault_id, "vault_id")
        async with self._actor_transaction(actor_user_id) as connection:
            await self._require_private_user(connection, actor_user_id)
            row = await self._fetch_one(
                connection,
                """
                SELECT id, owner_user_id, name, kind, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_vaults AS vault
                WHERE vault.id = %s
                  AND (
                      vault.owner_user_id = %s
                      OR EXISTS (
                          SELECT 1
                          FROM nanobot_collaboration.collaboration_share_grants AS share_grant
                          WHERE share_grant.vault_id = vault.id
                            AND share_grant.grantee_user_id = %s
                            AND share_grant.resource_type = 'vault'
                            AND share_grant.resource_id IS NULL
                            AND share_grant.revoked_at_ms IS NULL
                            AND (share_grant.expires_at_ms IS NULL OR share_grant.expires_at_ms > %s)
                            AND (share_grant.permission = 'collaborate' OR share_grant.permission = %s)
                      )
                  )
                """,
                (vault_id, actor_user_id, actor_user_id, self._now_ms(), SharePermission.READ.value),
            )
        return decode_vault_row(row) if row is not None else None

    async def update_user_default_vault(self, user_id: str, vault_id: str) -> User:
        user_id, vault_id = _id(user_id, "user_id"), _id(vault_id, "vault_id")
        now = self._now_ms()
        async with self._actor_transaction(user_id) as connection:
            await self._require_private_user(connection, user_id)
            owned = await self._fetch_one(
                connection,
                """
                SELECT 1 AS present
                FROM nanobot_collaboration.collaboration_vaults
                WHERE id = %s AND owner_user_id = %s
                """,
                (vault_id, user_id),
            )
            if owned is None:
                raise CollaborationPermissionError("only the owner may select a default vault")
            row = await self._fetch_one(
                connection,
                """
                UPDATE nanobot_collaboration.collaboration_users
                SET default_vault_id = %s, updated_at_ms = %s
                WHERE id = %s
                RETURNING id, display_name, default_project_id, created_at_ms, updated_at_ms,
                          default_vault_id, default_persona_id
                """,
                (vault_id, now, user_id),
            )
        assert row is not None
        return decode_user_row(row)

    async def create_persona(
        self, user_id: str, name: str, default_vault_id: str, *, instructions: str = ""
    ) -> Persona:
        user_id, default_vault_id = _id(user_id, "user_id"), _id(default_vault_id, "default_vault_id")
        name, instructions = _string(name, "name"), _string(instructions, "instructions", limit=32_768, empty=True)
        now = self._now_ms()
        async with self._actor_transaction(user_id) as connection:
            user = await self._require_private_user(connection, user_id)
            organization_id = await self._owner_organization(connection, user_id)
            owned = await self._fetch_one(
                connection,
                """
                SELECT 1 AS present
                FROM nanobot_collaboration.collaboration_vaults
                WHERE id = %s AND organization_id = %s AND owner_user_id = %s
                """,
                (default_vault_id, organization_id, user_id),
            )
            if owned is None:
                raise CollaborationPermissionError("persona vault must be user-owned")
            row = await self._fetch_one(
                connection,
                """
                INSERT INTO nanobot_collaboration.collaboration_personas
                    (id, organization_id, owner_user_id, name, default_vault_id, instructions,
                     created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, owner_user_id, name, default_vault_id, instructions,
                          created_at_ms, updated_at_ms
                """,
                (self._new_id(), organization_id, user_id, name, default_vault_id, instructions, now, now),
            )
            if row is None:
                raise RuntimeError("persona insert did not return a row")
            persona = decode_persona_row(row)
            if user.default_persona_id is None:
                await self._fetch_one(
                    connection,
                    """
                    UPDATE nanobot_collaboration.collaboration_users
                    SET default_persona_id = %s, updated_at_ms = %s
                    WHERE id = %s
                    RETURNING id
                    """,
                    (persona.id, now, user_id),
                )
        return persona

    async def list_personas(self, user_id: str) -> list[Persona]:
        user_id = _id(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            await self._require_private_user(connection, user_id)
            rows = await self._fetch_all(
                connection,
                """
                SELECT id, owner_user_id, name, default_vault_id, instructions, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_personas
                WHERE owner_user_id = %s
                ORDER BY created_at_ms, id
                """,
                (user_id,),
            )
        return [decode_persona_row(row) for row in rows]

    async def update_user_default_persona(self, user_id: str, persona_id: str | None) -> User:
        user_id = _id(user_id, "user_id")
        persona_id = _id(persona_id, "persona_id") if persona_id is not None else None
        now = self._now_ms()
        async with self._actor_transaction(user_id) as connection:
            await self._require_private_user(connection, user_id)
            if persona_id is not None:
                owned = await self._fetch_one(
                    connection,
                    """
                    SELECT 1 AS present
                    FROM nanobot_collaboration.collaboration_personas
                    WHERE id = %s AND owner_user_id = %s
                    """,
                    (persona_id, user_id),
                )
                if owned is None:
                    raise CollaborationPermissionError("persona is not user-owned")
            row = await self._fetch_one(
                connection,
                """
                UPDATE nanobot_collaboration.collaboration_users
                SET default_persona_id = %s, updated_at_ms = %s
                WHERE id = %s
                RETURNING id, display_name, default_project_id, created_at_ms, updated_at_ms,
                          default_vault_id, default_persona_id
                """,
                (persona_id, now, user_id),
            )
        assert row is not None
        return decode_user_row(row)

    async def create_share_grant(
        self,
        owner_user_id: str,
        vault_id: str,
        grantee_user_id: str,
        *,
        resource_type: str,
        resource_id: str | None = None,
        permission: SharePermission = SharePermission.READ,
        expires_at_ms: int | None = None,
    ) -> ShareGrant:
        owner_user_id, vault_id, grantee_user_id = (
            _id(owner_user_id, "owner_user_id"), _id(vault_id, "vault_id"), _id(grantee_user_id, "grantee_user_id"),
        )
        resource_type = _string(resource_type, "resource_type", limit=128)
        resource_id = _id(resource_id, "resource_id") if resource_id is not None else None
        permission = _share_permission(permission)
        expires_at_ms = _optional_timestamp(expires_at_ms)
        now = self._now_ms()
        async with self._actor_transaction(owner_user_id) as connection:
            await self._require_private_user(connection, owner_user_id)
            vault = await self._fetch_one(
                connection,
                """
                SELECT organization_id
                FROM nanobot_collaboration.collaboration_vaults
                WHERE id = %s AND owner_user_id = %s
                """,
                (vault_id, owner_user_id),
            )
            if vault is None:
                raise CollaborationPermissionError("only the vault owner may share data")
            grantee = await self._fetch_one(
                connection,
                """
                SELECT 1 AS present
                FROM nanobot_collaboration.collaboration_organization_memberships
                WHERE organization_id = %s AND user_id = %s
                """,
                (vault["organization_id"], grantee_user_id),
            )
            if grantee is None:
                raise CollaborationPermissionError("share grantee is not an organization member")
            row = await self._fetch_one(
                connection,
                """
                INSERT INTO nanobot_collaboration.collaboration_share_grants
                    (id, organization_id, vault_id, grantee_user_id, resource_type, resource_id,
                     permission, expires_at_ms, created_at_ms, revoked_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
                RETURNING id, vault_id, grantee_user_id, resource_type, resource_id, permission,
                          expires_at_ms, created_at_ms, revoked_at_ms
                """,
                (self._new_id(), vault["organization_id"], vault_id, grantee_user_id, resource_type,
                 resource_id, permission.value, expires_at_ms, now),
            )
        assert row is not None
        return decode_share_grant_row(row)

    async def revoke_share_grant(self, owner_user_id: str, grant_id: str) -> ShareGrant:
        owner_user_id, grant_id = _id(owner_user_id, "owner_user_id"), _id(grant_id, "grant_id")
        now = self._now_ms()
        async with self._actor_transaction(owner_user_id) as connection:
            await self._require_private_user(connection, owner_user_id)
            grant = await self._fetch_one(
                connection,
                """
                SELECT share_grant.id
                FROM nanobot_collaboration.collaboration_share_grants AS share_grant
                JOIN nanobot_collaboration.collaboration_vaults AS vault ON vault.id = share_grant.vault_id
                WHERE share_grant.id = %s
                  AND vault.owner_user_id = %s
                """,
                (grant_id, owner_user_id),
            )
            if grant is None:
                existing = await self._fetch_one(
                    connection,
                    """
                    SELECT id
                    FROM nanobot_collaboration.collaboration_share_grants
                    WHERE id = %s
                    """,
                    (grant_id,),
                )
                if existing is None:
                    raise CollaborationNotFoundError("share grant was not found")
                raise CollaborationPermissionError("only the vault owner may revoke a share")
            row = await self._fetch_one(
                connection,
                """
                UPDATE nanobot_collaboration.collaboration_share_grants
                SET revoked_at_ms = %s
                WHERE id = %s
                RETURNING id, vault_id, grantee_user_id, resource_type, resource_id, permission,
                          expires_at_ms, created_at_ms, revoked_at_ms
                """,
                (now, grant_id),
            )
        assert row is not None
        return decode_share_grant_row(row)

    async def authorize_vault(
        self, user_id: str, vault_id: str, permission: SharePermission = SharePermission.READ
    ) -> Vault:
        user_id, vault_id = _id(user_id, "user_id"), _id(vault_id, "vault_id")
        permission = _share_permission(permission)
        async with self._actor_transaction(user_id) as connection:
            await self._require_private_user(connection, user_id)
            row = await self._authorized_vault(connection, user_id, vault_id, permission)
            if row is None:
                raise CollaborationPermissionError("vault access is not authorized")
        return decode_vault_row(row)

    async def create_personal_task(
        self,
        user_id: str,
        vault_id: str,
        title: str,
        *,
        note: str = "",
        status: TaskStatus = TaskStatus.TODO,
        priority: int = 0,
        due_at_ms: int | None = None,
        timezone: str | None = None,
        recurrence_rule: str | None = None,
        source_type: str = "user",
        source_ref: str | None = None,
        review_state: TaskReviewState = TaskReviewState.CONFIRMED,
    ) -> PersonalTask:
        user_id, vault_id = _id(user_id, "user_id"), _id(vault_id, "vault_id")
        title, note = _string(title, "title", limit=512), _string(note, "note", limit=_MAX_TEXT, empty=True)
        status, review_state = _task_status(status), _review_state(review_state)
        priority = _priority(priority)
        due_at_ms = _optional_timestamp(due_at_ms)
        timezone = _optional_text(timezone, "timezone", limit=128)
        recurrence_rule = _optional_text(recurrence_rule, "recurrence_rule", limit=2_048)
        source_type = _string(source_type, "source_type", limit=128)
        source_ref = _optional_text(source_ref, "source_ref", limit=2_048)
        now = self._now_ms()
        async with self._actor_transaction(user_id) as connection:
            vault = await self._owner_vault(connection, user_id, vault_id)
            if vault is None:
                raise CollaborationPermissionError("only the vault owner may create personal tasks")
            row = await self._fetch_one(
                connection,
                """
                INSERT INTO nanobot_collaboration.collaboration_personal_tasks
                    (id, organization_id, owner_user_id, vault_id, title, note, status, priority,
                     due_at_ms, timezone, recurrence_rule, source_type, source_ref, external_provider,
                     external_id, external_version, review_state, created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL, %s, %s, %s)
                RETURNING id, owner_user_id, vault_id, title, note, status, priority, due_at_ms, timezone,
                          recurrence_rule, source_type, source_ref, external_provider, external_id,
                          external_version, review_state, created_at_ms, updated_at_ms
                """,
                (self._new_id(), vault["organization_id"], user_id, vault_id, title, note, status.value,
                 priority, due_at_ms, timezone, recurrence_rule, source_type, source_ref,
                 review_state.value, now, now),
            )
        assert row is not None
        return decode_personal_task_row(row)

    async def list_personal_tasks(self, user_id: str, vault_id: str) -> list[PersonalTask]:
        user_id, vault_id = _id(user_id, "user_id"), _id(vault_id, "vault_id")
        async with self._actor_transaction(user_id) as connection:
            await self._require_private_user(connection, user_id)
            if await self._authorized_vault(connection, user_id, vault_id, SharePermission.READ) is None:
                raise CollaborationPermissionError("vault task list is not authorized")
            rows = await self._fetch_all(
                connection,
                """
                SELECT id, owner_user_id, vault_id, title, note, status, priority, due_at_ms, timezone,
                       recurrence_rule, source_type, source_ref, external_provider, external_id,
                       external_version, review_state, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_personal_tasks
                WHERE vault_id = %s
                ORDER BY due_at_ms IS NULL, due_at_ms, priority DESC, id
                """,
                (vault_id,),
            )
        return [decode_personal_task_row(row) for row in rows]

    async def get_personal_task(self, user_id: str, task_id: str) -> PersonalTask | None:
        user_id, task_id = _id(user_id, "user_id"), _id(task_id, "task_id")
        async with self._actor_transaction(user_id) as connection:
            await self._require_private_user(connection, user_id)
            row = await self._authorized_personal_task(connection, user_id, task_id, SharePermission.READ)
        return decode_personal_task_row(row) if row is not None else None

    async def update_personal_task(
        self,
        user_id: str,
        task_id: str,
        *,
        title: str | None = None,
        note: str | None = None,
        status: TaskStatus | None = None,
        priority: int | None = None,
        due_at_ms: int | None = None,
        review_state: TaskReviewState | None = None,
        external_provider: str | None = None,
        external_id: str | None = None,
        external_version: str | None = None,
    ) -> PersonalTask:
        user_id, task_id = _id(user_id, "user_id"), _id(task_id, "task_id")
        title = _string(title, "title", limit=512) if title is not None else None
        note = _string(note, "note", limit=_MAX_TEXT, empty=True) if note is not None else None
        status = _task_status(status) if status is not None else None
        priority = _priority(priority) if priority is not None else None
        due_at_ms = _timestamp(due_at_ms) if due_at_ms is not None else None
        review_state = _review_state(review_state) if review_state is not None else None
        external_provider = _optional_text(external_provider, "external_provider", limit=128) if external_provider is not None else None
        external_id = _optional_text(external_id, "external_id", limit=512) if external_id is not None else None
        external_version = _optional_text(external_version, "external_version", limit=512) if external_version is not None else None
        now = self._now_ms()
        async with self._actor_transaction(user_id) as connection:
            current = await self._authorized_personal_task(
                connection, user_id, task_id, SharePermission.COLLABORATE
            )
            if current is None:
                existing = await self._fetch_one(
                    connection,
                    """
                    SELECT id
                    FROM nanobot_collaboration.collaboration_personal_tasks
                    WHERE id = %s
                    """,
                    (task_id,),
                )
                if existing is None:
                    raise CollaborationNotFoundError("personal task was not found")
                raise CollaborationPermissionError("personal task update is not authorized")
            provider = external_provider if external_provider is not None else current["external_provider"]
            external = external_id if external_id is not None else current["external_id"]
            if provider is not None and external is not None:
                conflict = await self._fetch_one(
                    connection,
                    """
                    SELECT 1 AS present
                    FROM nanobot_collaboration.collaboration_personal_tasks
                    WHERE organization_id = %s
                      AND external_provider = %s
                      AND external_id = %s
                      AND id <> %s
                    """,
                    (current["organization_id"], provider, external, task_id),
                )
                if conflict is not None:
                    raise CollaborationConflictError("external personal task identity already exists")
            row = await self._fetch_one(
                connection,
                """
                UPDATE nanobot_collaboration.collaboration_personal_tasks
                SET title = COALESCE(%s, title),
                    note = COALESCE(%s, note),
                    status = COALESCE(%s, status),
                    priority = COALESCE(%s, priority),
                    due_at_ms = COALESCE(%s, due_at_ms),
                    review_state = COALESCE(%s, review_state),
                    external_provider = COALESCE(%s, external_provider),
                    external_id = COALESCE(%s, external_id),
                    external_version = COALESCE(%s, external_version),
                    updated_at_ms = %s
                WHERE id = %s
                RETURNING id, owner_user_id, vault_id, title, note, status, priority, due_at_ms, timezone,
                          recurrence_rule, source_type, source_ref, external_provider, external_id,
                          external_version, review_state, created_at_ms, updated_at_ms
                """,
                (title, note, status.value if status is not None else None, priority, due_at_ms,
                 review_state.value if review_state is not None else None, external_provider, external_id,
                 external_version, now, task_id),
            )
        assert row is not None
        return decode_personal_task_row(row)

    async def delete_personal_task(self, user_id: str, task_id: str) -> bool:
        user_id, task_id = _id(user_id, "user_id"), _id(task_id, "task_id")
        async with self._actor_transaction(user_id) as connection:
            current = await self._authorized_personal_task(
                connection, user_id, task_id, SharePermission.COLLABORATE
            )
            if current is None:
                existing = await self._fetch_one(
                    connection,
                    """
                    SELECT id
                    FROM nanobot_collaboration.collaboration_personal_tasks
                    WHERE id = %s
                    """,
                    (task_id,),
                )
                if existing is None:
                    return False
                raise CollaborationPermissionError("personal task delete is not authorized")
            deleted = await self._fetch_one(
                connection,
                """
                DELETE FROM nanobot_collaboration.collaboration_personal_tasks
                WHERE id = %s
                RETURNING id
                """,
                (task_id,),
            )
        return deleted is not None

    async def _require_private_user(self, connection: Any, user_id: str) -> User:
        row = await self._fetch_one(
            connection,
            """
            SELECT id, display_name, default_project_id, created_at_ms, updated_at_ms,
                   default_vault_id, default_persona_id
            FROM nanobot_collaboration.collaboration_users
            WHERE id = %s
            """,
            (user_id,),
        )
        if row is None:
            raise CollaborationNotFoundError("user was not found")
        return decode_user_row(row)

    async def _owner_organization(self, connection: Any, user_id: str) -> str:
        row = await self._fetch_one(
            connection,
            """
            SELECT organization.id
            FROM nanobot_collaboration.collaboration_organizations AS organization
            JOIN nanobot_collaboration.collaboration_organization_memberships AS membership
              ON membership.organization_id = organization.id
            WHERE organization.created_by_user_id = %s
              AND membership.user_id = %s
              AND membership.role = 'owner'
            ORDER BY organization.created_at_ms, organization.id
            LIMIT 1
            """,
            (user_id, user_id),
        )
        if row is None:
            raise CollaborationStoreFormatError("user personal organization is missing")
        return _id(row["id"], "organization_id")

    async def _owner_vault(self, connection: Any, user_id: str, vault_id: str) -> dict[str, Any] | None:
        return await self._fetch_one(
            connection,
            """
            SELECT organization_id
            FROM nanobot_collaboration.collaboration_vaults
            WHERE id = %s AND owner_user_id = %s
            """,
            (vault_id, user_id),
        )

    async def _authorized_vault(
        self, connection: Any, user_id: str, vault_id: str, permission: SharePermission
    ) -> dict[str, Any] | None:
        return await self._fetch_one(
            connection,
            """
            SELECT id, owner_user_id, name, kind, created_at_ms, updated_at_ms
            FROM nanobot_collaboration.collaboration_vaults AS vault
            WHERE vault.id = %s
              AND (
                  vault.owner_user_id = %s
                  OR EXISTS (
                      SELECT 1
                      FROM nanobot_collaboration.collaboration_share_grants AS share_grant
                      WHERE share_grant.vault_id = vault.id
                        AND share_grant.grantee_user_id = %s
                        AND share_grant.resource_type = 'vault'
                        AND share_grant.resource_id IS NULL
                        AND share_grant.revoked_at_ms IS NULL
                        AND (share_grant.expires_at_ms IS NULL OR share_grant.expires_at_ms > %s)
                        AND (share_grant.permission = 'collaborate' OR share_grant.permission = %s)
                  )
              )
            """,
            (vault_id, user_id, user_id, self._now_ms(), permission.value),
        )

    async def _authorized_personal_task(
        self, connection: Any, user_id: str, task_id: str, permission: SharePermission
    ) -> dict[str, Any] | None:
        return await self._fetch_one(
            connection,
            """
            SELECT task.id, task.organization_id, task.owner_user_id, task.vault_id, task.title, task.note,
                   task.status, task.priority, task.due_at_ms, task.timezone, task.recurrence_rule,
                   task.source_type, task.source_ref, task.external_provider, task.external_id,
                   task.external_version, task.review_state, task.created_at_ms, task.updated_at_ms
            FROM nanobot_collaboration.collaboration_personal_tasks AS task
            WHERE task.id = %s
              AND (
                  task.owner_user_id = %s
                  OR EXISTS (
                      SELECT 1
                      FROM nanobot_collaboration.collaboration_share_grants AS share_grant
                      WHERE share_grant.vault_id = task.vault_id
                        AND share_grant.grantee_user_id = %s
                        AND share_grant.revoked_at_ms IS NULL
                        AND (share_grant.expires_at_ms IS NULL OR share_grant.expires_at_ms > %s)
                        AND (share_grant.permission = 'collaborate' OR share_grant.permission = %s)
                        AND (
                            (share_grant.resource_type = 'vault' AND share_grant.resource_id IS NULL)
                            OR (share_grant.resource_type = 'personal_task' AND share_grant.resource_id = task.id)
                        )
                  )
              )
            """,
            (task_id, user_id, user_id, self._now_ms(), permission.value),
        )


def _string(value: object, field: str, *, limit: int = _MAX_STRING, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError(f"{field} must be a string")
    value = value.strip()
    if (not value and not empty) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise CollaborationStoreFormatError(f"invalid {field}")
    return value


def _id(value: object, field: str) -> str:
    return _string(value, field, limit=128)


def _optional_text(value: object, field: str, *, limit: int = _MAX_TEXT) -> str | None:
    return _string(value, field, limit=limit, empty=True) if value is not None else None


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CollaborationStoreFormatError("invalid timestamp")
    return value


def _optional_timestamp(value: object | None) -> int | None:
    return _timestamp(value) if value is not None else None


def _priority(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
        raise CollaborationStoreFormatError("invalid personal task priority")
    return value


def _vault_kind(value: object) -> VaultKind:
    try:
        return VaultKind(value)
    except (TypeError, ValueError) as exc:
        raise CollaborationStoreFormatError("invalid vault kind") from exc


def _share_permission(value: object) -> SharePermission:
    try:
        return SharePermission(value)
    except (TypeError, ValueError) as exc:
        raise CollaborationStoreFormatError("invalid share permission") from exc


def _task_status(value: object) -> TaskStatus:
    try:
        return TaskStatus(value)
    except (TypeError, ValueError) as exc:
        raise CollaborationStoreFormatError("invalid task status") from exc


def _review_state(value: object) -> TaskReviewState:
    try:
        return TaskReviewState(value)
    except (TypeError, ValueError) as exc:
        raise CollaborationStoreFormatError("invalid personal task state") from exc
