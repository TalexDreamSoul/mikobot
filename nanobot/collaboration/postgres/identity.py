"""PostgreSQL user, identity, and organization repository mixin."""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from nanobot.collaboration.models import (
    Organization,
    OrganizationMembership,
    OrganizationRole,
    Project,
    User,
    UserIdentity,
)
from nanobot.collaboration.postgres.base import PostgresRepositoryBase
from nanobot.collaboration.postgres.rows import (
    decode_identity_row,
    decode_organization_membership_row,
    decode_organization_row,
    decode_project_row,
    decode_user_row,
)
from nanobot.collaboration.store import (
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    CollaborationStoreFormatError,
)


class PostgresIdentityMixin(PostgresRepositoryBase):
    """User identities and organization membership operations for PostgreSQL."""

    async def ensure_local_owner(self, default_workspace: str | Path) -> tuple[User, Project]:
        return await self.ensure_identity_user("local", "owner", default_workspace, local_owner=True)

    async def create_user(self, display_name: str) -> User:
        display_name = _string(display_name, "display_name", limit=256)
        user_id, now = self._new_id(), self._now_ms()
        async with self._actor_transaction(user_id) as connection:
            _organization_id, vault_id = await self._create_private_state(connection, user_id, display_name, now)
            row = await self._fetch_one(connection, "UPDATE nanobot_collaboration.collaboration_users SET default_vault_id = %s, updated_at_ms = %s WHERE id = %s RETURNING id, display_name, default_project_id, created_at_ms, updated_at_ms, default_vault_id, default_persona_id", (vault_id, now, user_id))
        if row is None:
            raise CollaborationNotFoundError("user not found")
        return decode_user_row(row)

    async def update_user_display_name(self, user_id: str, display_name: str) -> User:
        user_id = _identifier(user_id, "user_id")
        display_name = _string(display_name, "display_name", limit=256)
        async with self._actor_transaction(user_id) as connection:
            row = await self._fetch_one(connection, "UPDATE nanobot_collaboration.collaboration_users SET display_name = %s, updated_at_ms = %s WHERE id = %s RETURNING id, display_name, default_project_id, created_at_ms, updated_at_ms, default_vault_id, default_persona_id", (display_name, self._now_ms(), user_id))
        if row is None:
            raise CollaborationNotFoundError("user not found")
        return decode_user_row(row)

    async def get_user(self, user_id: str) -> User | None:
        user_id = _identifier(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            row = await self._user_row(connection, user_id)
        return decode_user_row(row) if row else None

    async def list_users(self) -> list[User]:
        async with self._actor_transaction("") as connection:
            rows = await self._fetch_all(connection, "SELECT id, display_name, default_project_id, created_at_ms, updated_at_ms, default_vault_id, default_persona_id FROM nanobot_collaboration.collaboration_users ORDER BY created_at_ms, id")
        return [decode_user_row(row) for row in rows]

    async def update_user_default_project(self, user_id: str, project_id: str | None) -> User:
        user_id = _identifier(user_id, "user_id")
        if project_id is not None:
            project_id = _identifier(project_id, "project_id")
        async with self._actor_transaction(user_id) as connection:
            if project_id is not None and await self._fetch_one(connection, "SELECT 1 FROM nanobot_collaboration.collaboration_project_memberships WHERE project_id = %s AND user_id = %s", (project_id, user_id)) is None:
                raise CollaborationPermissionError("project membership is required")
            row = await self._fetch_one(connection, "UPDATE nanobot_collaboration.collaboration_users SET default_project_id = %s, updated_at_ms = %s WHERE id = %s RETURNING id, display_name, default_project_id, created_at_ms, updated_at_ms, default_vault_id, default_persona_id", (project_id, self._now_ms(), user_id))
        if row is None:
            raise CollaborationNotFoundError("user not found")
        return decode_user_row(row)

    async def bind_identity(self, channel: str, sender_id: str, user_id: str) -> UserIdentity:
        channel, sender_id, user_id = _string(channel, "channel", limit=128), _string(sender_id, "sender_id", limit=512), _identifier(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            organization_id = await self._personal_organization_id(connection, user_id)
            if organization_id is None:
                raise CollaborationNotFoundError("user personal organization not found")
            row = await self._fetch_one(connection, "INSERT INTO nanobot_collaboration.collaboration_identities (organization_id, user_id, channel, sender_id, created_at_ms) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (channel, sender_id) DO NOTHING RETURNING user_id, channel, sender_id, created_at_ms", (organization_id, user_id, channel, sender_id, self._now_ms()))
            if row is None:
                row = await self._identity_row(connection, channel, sender_id)
                if row is None or decode_identity_row(row).user_id != user_id:
                    raise CollaborationConflictError("channel sender identity is already bound")
        return decode_identity_row(row)

    async def rebind_identity(self, channel: str, sender_id: str, user_id: str) -> UserIdentity:
        channel, sender_id, user_id = _string(channel, "channel", limit=128), _string(sender_id, "sender_id", limit=512), _identifier(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            organization_id = await self._personal_organization_id(connection, user_id)
            if organization_id is None:
                raise CollaborationNotFoundError("user personal organization not found")
            row = await self._fetch_one(connection, "INSERT INTO nanobot_collaboration.collaboration_identities (organization_id, user_id, channel, sender_id, created_at_ms) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (channel, sender_id) DO UPDATE SET organization_id = EXCLUDED.organization_id, user_id = EXCLUDED.user_id, created_at_ms = EXCLUDED.created_at_ms RETURNING user_id, channel, sender_id, created_at_ms", (organization_id, user_id, channel, sender_id, self._now_ms()))
        if row is None:
            raise CollaborationNotFoundError("user not found")
        return decode_identity_row(row)

    async def resolve_identity(self, channel: str, sender_id: str) -> User | None:
        channel, sender_id = _string(channel, "channel", limit=128), _string(sender_id, "sender_id", limit=512)
        async with self._identity_transaction(channel, sender_id) as connection:
            identity = await self._identity_row(connection, channel, sender_id)
            if identity is None:
                return None
            await _set_transaction_user(connection, str(identity["user_id"]))
            row = await self._user_row(connection, str(identity["user_id"]))
        return decode_user_row(row) if row else None

    async def ensure_identity_user(self, channel: str, sender_id: str, default_workspace: str | Path, *, local_owner: bool = False) -> tuple[User, Project]:
        channel, sender_id, workspace = _string(channel, "channel", limit=128), _string(sender_id, "sender_id", limit=512), _workspace(default_workspace)
        use_local_owner = local_owner and channel == "local" and sender_id == "owner"
        async with self._identity_transaction(channel, sender_id) as connection:
            await self._fetch_one(connection, "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{channel}\\x00{sender_id}",))
            identity = await self._identity_row(connection, channel, sender_id)
            if identity is not None:
                user_id = str(identity["user_id"])
                await _set_transaction_user(connection, user_id)
                return await self._default_project(connection, user_id)
            user_id, now = self._new_id(), self._now_ms()
            await _set_transaction_user(connection, user_id)
            organization_id, vault_id = await self._create_private_state(connection, user_id, "Local owner" if use_local_owner else f"{channel} user", now)
            project_id = self._new_id()
            project_workspace = workspace if use_local_owner else str(Path(workspace) / "workspaces" / user_id / "default")
            project_name = "Local project" if use_local_owner else "Personal project"
            await _execute(connection, "INSERT INTO nanobot_collaboration.collaboration_projects (id, organization_id, name, workspace_path, created_by_user_id, created_at_ms, updated_at_ms) VALUES (%s, %s, %s, %s, %s, %s, %s)", (project_id, organization_id, project_name, project_workspace, user_id, now, now))
            await _execute(connection, "INSERT INTO nanobot_collaboration.collaboration_project_memberships (organization_id, project_id, user_id, role, created_at_ms) VALUES (%s, %s, %s, 'owner', %s)", (organization_id, project_id, user_id, now))
            user_row = await self._fetch_one(connection, "UPDATE nanobot_collaboration.collaboration_users SET default_project_id = %s, default_vault_id = %s, updated_at_ms = %s WHERE id = %s RETURNING id, display_name, default_project_id, created_at_ms, updated_at_ms, default_vault_id, default_persona_id", (project_id, vault_id, now, user_id))
            if await self._fetch_one(connection, "INSERT INTO nanobot_collaboration.collaboration_identities (organization_id, user_id, channel, sender_id, created_at_ms) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (channel, sender_id) DO NOTHING RETURNING user_id", (organization_id, user_id, channel, sender_id, now)) is None:
                raise CollaborationConflictError("channel sender identity is already bound")
            project_row = await self._project_row(connection, project_id)
        if user_row is None or project_row is None:
            raise CollaborationNotFoundError("provisioned user state not found")
        return decode_user_row(user_row), decode_project_row(project_row)

    async def create_organization(self, owner_user_id: str, name: str) -> Organization:
        owner_user_id, name, now = _identifier(owner_user_id, "owner_user_id"), _string(name, "name", limit=256), self._now_ms()
        organization_id = self._new_id()
        async with self._actor_transaction(owner_user_id) as connection:
            await _execute(connection, "INSERT INTO nanobot_collaboration.collaboration_organizations (id, name, created_by_user_id, is_personal, created_at_ms, updated_at_ms) VALUES (%s, %s, %s, false, %s, %s)", (organization_id, name, owner_user_id, now, now))
            await _execute(connection, "INSERT INTO nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id, role, created_at_ms) VALUES (%s, %s, 'owner', %s)", (organization_id, owner_user_id, now))
            row = await self._fetch_one(connection, "SELECT id, name, created_by_user_id, is_personal, created_at_ms, updated_at_ms FROM nanobot_collaboration.collaboration_organizations WHERE id = %s", (organization_id,))
        if row is None:
            raise CollaborationNotFoundError("owner user not found")
        return decode_organization_row(row)

    async def get_organization(self, user_id: str, organization_id: str) -> Organization | None:
        user_id, organization_id = _identifier(user_id, "user_id"), _identifier(organization_id, "organization_id")
        async with self._actor_transaction(user_id) as connection:
            row = await self._fetch_one(connection, "SELECT organization.id, organization.name, organization.created_by_user_id, organization.is_personal, organization.created_at_ms, organization.updated_at_ms FROM nanobot_collaboration.collaboration_organizations AS organization JOIN nanobot_collaboration.collaboration_organization_memberships AS membership ON membership.organization_id = organization.id WHERE organization.id = %s AND membership.user_id = %s", (organization_id, user_id))
        return decode_organization_row(row) if row else None

    async def list_organizations(self, user_id: str) -> list[Organization]:
        user_id = _identifier(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            rows = await self._fetch_all(connection, "SELECT organization.id, organization.name, organization.created_by_user_id, organization.is_personal, organization.created_at_ms, organization.updated_at_ms FROM nanobot_collaboration.collaboration_organizations AS organization JOIN nanobot_collaboration.collaboration_organization_memberships AS membership ON membership.organization_id = organization.id WHERE membership.user_id = %s ORDER BY organization.updated_at_ms DESC, organization.id DESC", (user_id,))
        return [decode_organization_row(row) for row in rows]

    async def update_organization(self, organization_id: str, actor_user_id: str, *, name: str) -> Organization:
        organization_id, actor_user_id, name = _identifier(organization_id, "organization_id"), _identifier(actor_user_id, "actor_user_id"), _string(name, "name", limit=256)
        async with self._actor_transaction(actor_user_id) as connection:
            await self._require_role(connection, organization_id, actor_user_id, {OrganizationRole.OWNER, OrganizationRole.ADMIN})
            row = await self._fetch_one(connection, "UPDATE nanobot_collaboration.collaboration_organizations SET name = %s, updated_at_ms = %s WHERE id = %s RETURNING id, name, created_by_user_id, is_personal, created_at_ms, updated_at_ms", (name, self._now_ms(), organization_id))
        if row is None:
            raise CollaborationNotFoundError("organization not found")
        return decode_organization_row(row)

    async def delete_organization(self, organization_id: str, actor_user_id: str) -> bool:
        organization_id, actor_user_id = _identifier(organization_id, "organization_id"), _identifier(actor_user_id, "actor_user_id")
        async with self._actor_transaction(actor_user_id) as connection:
            await self._require_role(connection, organization_id, actor_user_id, {OrganizationRole.OWNER})
            organization = await self._fetch_one(connection, "SELECT is_personal FROM nanobot_collaboration.collaboration_organizations WHERE id = %s", (organization_id,))
            if organization is None:
                raise CollaborationNotFoundError("organization not found")
            if cast(bool, organization["is_personal"]):
                raise CollaborationConflictError("a personal organization cannot be deleted")
            if await self._fetch_one(connection, "SELECT 1 FROM nanobot_collaboration.collaboration_projects WHERE organization_id = %s", (organization_id,)) is not None:
                raise CollaborationConflictError("an organization with projects cannot be deleted")
            row = await self._fetch_one(connection, "DELETE FROM nanobot_collaboration.collaboration_organizations WHERE id = %s RETURNING id", (organization_id,))
        return row is not None

    async def add_organization_member(self, organization_id: str, actor_user_id: str, user_id: str, role: OrganizationRole = OrganizationRole.MEMBER) -> OrganizationMembership:
        organization_id, actor_user_id, user_id, role = _identifier(organization_id, "organization_id"), _identifier(actor_user_id, "actor_user_id"), _identifier(user_id, "user_id"), _role(role)
        async with self._actor_transaction(actor_user_id) as connection:
            actor_role = await self._require_role(connection, organization_id, actor_user_id, {OrganizationRole.OWNER, OrganizationRole.ADMIN})
            old = await self._membership(connection, organization_id, user_id)
            if role is not OrganizationRole.MEMBER and actor_role is not OrganizationRole.OWNER:
                raise CollaborationPermissionError("organization owner role is required")
            if old and old.role is OrganizationRole.OWNER and role is not OrganizationRole.OWNER:
                if actor_role is not OrganizationRole.OWNER:
                    raise CollaborationPermissionError("organization owner role is required")
                if await self._owner_count(connection, organization_id) == 1:
                    raise CollaborationConflictError("an organization must retain an owner")
            row = await self._fetch_one(connection, "INSERT INTO nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id, role, created_at_ms) VALUES (%s, %s, %s, %s) ON CONFLICT (organization_id, user_id) DO UPDATE SET role = EXCLUDED.role RETURNING organization_id, user_id, role, created_at_ms", (organization_id, user_id, role.value, self._now_ms()))
        if row is None:
            raise CollaborationNotFoundError("organization or user not found")
        return decode_organization_membership_row(row)

    async def remove_organization_member(self, organization_id: str, actor_user_id: str, user_id: str) -> bool:
        organization_id, actor_user_id, user_id = _identifier(organization_id, "organization_id"), _identifier(actor_user_id, "actor_user_id"), _identifier(user_id, "user_id")
        async with self._actor_transaction(actor_user_id) as connection:
            actor_role = await self._require_role(connection, organization_id, actor_user_id, {OrganizationRole.OWNER, OrganizationRole.ADMIN})
            old = await self._membership(connection, organization_id, user_id)
            if old is None:
                return False
            if old.role is OrganizationRole.OWNER:
                if actor_role is not OrganizationRole.OWNER:
                    raise CollaborationPermissionError("organization owner role is required")
                if await self._owner_count(connection, organization_id) == 1:
                    raise CollaborationConflictError("an organization must retain an owner")
            row = await self._fetch_one(connection, "DELETE FROM nanobot_collaboration.collaboration_organization_memberships WHERE organization_id = %s AND user_id = %s RETURNING organization_id", (organization_id, user_id))
        return row is not None

    async def list_organization_members(self, organization_id: str, user_id: str) -> list[OrganizationMembership]:
        organization_id, user_id = _identifier(organization_id, "organization_id"), _identifier(user_id, "user_id")
        async with self._actor_transaction(user_id) as connection:
            await self._require_role(connection, organization_id, user_id, set(OrganizationRole))
            rows = await self._fetch_all(connection, "SELECT organization_id, user_id, role, created_at_ms FROM nanobot_collaboration.collaboration_organization_memberships WHERE organization_id = %s ORDER BY created_at_ms, user_id", (organization_id,))
        return [decode_organization_membership_row(row) for row in rows]

    async def _create_private_state(self, connection: Any, user_id: str, display_name: str, now: int) -> tuple[str, str]:
        organization_id, vault_id = self._new_id(), self._new_id()
        await _execute(connection, "INSERT INTO nanobot_collaboration.collaboration_users (id, display_name, default_project_id, default_vault_id, default_persona_id, created_at_ms, updated_at_ms) VALUES (%s, %s, NULL, NULL, NULL, %s, %s)", (user_id, display_name, now, now))
        await _execute(connection, "INSERT INTO nanobot_collaboration.collaboration_organizations (id, name, created_by_user_id, is_personal, created_at_ms, updated_at_ms) VALUES (%s, 'Personal', %s, true, %s, %s)", (organization_id, user_id, now, now))
        await _execute(connection, "INSERT INTO nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id, role, created_at_ms) VALUES (%s, %s, 'owner', %s)", (organization_id, user_id, now))
        await _execute(connection, "INSERT INTO nanobot_collaboration.collaboration_vaults (id, organization_id, owner_user_id, name, kind, created_at_ms, updated_at_ms) VALUES (%s, %s, %s, 'Private', 'private', %s, %s)", (vault_id, organization_id, user_id, now, now))
        return organization_id, vault_id

    async def _user_row(self, connection: Any, user_id: str):
        return await self._fetch_one(connection, "SELECT id, display_name, default_project_id, created_at_ms, updated_at_ms, default_vault_id, default_persona_id FROM nanobot_collaboration.collaboration_users WHERE id = %s", (user_id,))

    async def _project_row(self, connection: Any, project_id: str):
        return await self._fetch_one(connection, "SELECT id, name, workspace_path, created_by_user_id, created_at_ms, updated_at_ms, organization_id FROM nanobot_collaboration.collaboration_projects WHERE id = %s", (project_id,))

    async def _identity_row(self, connection: Any, channel: str, sender_id: str):
        return await self._fetch_one(connection, "SELECT user_id, channel, sender_id, created_at_ms FROM nanobot_collaboration.collaboration_identities WHERE channel = %s AND sender_id = %s", (channel, sender_id))

    async def _default_project(self, connection: Any, user_id: str) -> tuple[User, Project]:
        row = await self._user_row(connection, user_id)
        if row is None:
            raise CollaborationNotFoundError("identity user not found")
        user = decode_user_row(row)
        if user.default_project_id is None:
            raise CollaborationConflictError("channel identity has no default project")
        project_row = await self._project_row(connection, user.default_project_id)
        if project_row is None:
            raise CollaborationConflictError("channel identity default project is missing")
        return user, decode_project_row(project_row)

    async def _personal_organization_id(self, connection: Any, user_id: str) -> str | None:
        row = await self._fetch_one(connection, "SELECT organization.id, organization.is_personal FROM nanobot_collaboration.collaboration_organizations AS organization JOIN nanobot_collaboration.collaboration_organization_memberships AS membership ON membership.organization_id = organization.id WHERE organization.created_by_user_id = %s AND organization.is_personal AND membership.user_id = %s AND membership.role = 'owner' ORDER BY organization.created_at_ms, organization.id LIMIT 1", (user_id, user_id))
        return str(row["id"]) if row else None

    async def _membership(self, connection: Any, organization_id: str, user_id: str) -> OrganizationMembership | None:
        row = await self._fetch_one(connection, "SELECT organization_id, user_id, role, created_at_ms FROM nanobot_collaboration.collaboration_organization_memberships WHERE organization_id = %s AND user_id = %s", (organization_id, user_id))
        return decode_organization_membership_row(row) if row else None

    async def _require_role(self, connection: Any, organization_id: str, user_id: str, roles: set[OrganizationRole]) -> OrganizationRole:
        membership = await self._membership(connection, organization_id, user_id)
        if membership is None:
            raise CollaborationPermissionError("organization membership is required")
        if membership.role not in roles:
            raise CollaborationPermissionError("organization owner role is required" if OrganizationRole.OWNER in roles else "organization admin role is required")
        return membership.role

    async def _owner_count(self, connection: Any, organization_id: str) -> int:
        row = await self._fetch_one(connection, "SELECT count(*) AS owner_count FROM nanobot_collaboration.collaboration_organization_memberships WHERE organization_id = %s AND role = 'owner'", (organization_id,))
        return int(cast(int, row["owner_count"])) if row else 0


async def _execute(connection: Any, query: str, params: tuple[object, ...]) -> None:
    await connection.execute(query, params)


async def _set_transaction_user(connection: Any, user_id: str) -> None:
    await connection.execute("SELECT set_config(%s, %s, true)", ("nanobot.user_id", user_id))


def _string(value: object, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > limit or any(ord(char) < 32 for char in normalized):
        raise CollaborationStoreFormatError(f"invalid {field}")
    return normalized


def _identifier(value: object, field: str) -> str:
    return _string(value, field, limit=128)


def _workspace(value: str | Path) -> str:
    text = str(value).strip()
    if not text or len(text) > 16_000 or any(ord(char) < 32 for char in text):
        raise CollaborationStoreFormatError("invalid workspace_path")
    return str(Path(text).expanduser().resolve(strict=False))


def _role(value: OrganizationRole | str) -> OrganizationRole:
    try:
        return OrganizationRole(value)
    except (TypeError, ValueError) as exc:
        raise CollaborationStoreFormatError("invalid organization membership role") from exc
