"""PostgreSQL bot, channel-assignment, capability, and Pair Code operations."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from pathlib import Path
from typing import LiteralString

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from nanobot.collaboration.capabilities import normalize_bot_capability_settings
from nanobot.collaboration.models import (
    Bot,
    BotCapabilityProfile,
    BotChannelAssignment,
    BotProjectAssignment,
    BotProjectChannel,
    BotState,
    PairingChallenge,
    PairingPurpose,
    Project,
    User,
    thaw_json,
)
from nanobot.collaboration.pairing import (
    assignment_code_digest,
    new_assignment_code,
    normalize_assignment_code,
    runtime_channel_key,
)
from nanobot.collaboration.postgres.base import PostgresConnection
from nanobot.collaboration.postgres.identity import PostgresIdentityMixin
from nanobot.collaboration.postgres.rows import (
    decode_bot_capability_profile_row,
    decode_bot_channel_assignment_row,
    decode_bot_project_assignment_row,
    decode_bot_project_channel_row,
    decode_bot_row,
    decode_identity_row,
    decode_pairing_challenge_row,
    decode_user_row,
)
from nanobot.collaboration.store import (
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    CollaborationStoreFormatError,
)

_BOT_COLUMNS: LiteralString = (
    "id, organization_id, owner_user_id, name, avatar_url, persona_id, state, "
    "created_at_ms, updated_at_ms"
)
_BOT_SELECT_COLUMNS: LiteralString = (
    "bot.id AS id, bot.organization_id AS organization_id, "
    "bot.owner_user_id AS owner_user_id, bot.name AS name, "
    "bot.avatar_url AS avatar_url, bot.persona_id AS persona_id, "
    "bot.state AS state, bot.created_at_ms AS created_at_ms, "
    "bot.updated_at_ms AS updated_at_ms"
)
_PAIRING_COLUMNS: LiteralString = (
    "id, code_digest, requested_by_user_id, purpose, organization_id, bot_id, project_id, "
    "channel_type, instance_id, expires_at_ms, verified_at_ms, verified_sender_id, "
    "consumed_at_ms, created_at_ms"
)


class PostgresBotsMixin(PostgresIdentityMixin):
    """Tenant-scoped deployable bots and proof-gated assignment operations."""

    async def create_user(self, display_name: str) -> User:
        user = await super().create_user(display_name)
        return await self._ensure_default_bot(user, None)

    async def ensure_identity_user(
        self,
        channel: str,
        sender_id: str,
        default_workspace: str | Path,
        *,
        local_owner: bool = False,
    ) -> tuple[User, Project]:
        user, project = await super().ensure_identity_user(
            channel, sender_id, default_workspace, local_owner=local_owner
        )
        return await self._ensure_default_bot(user, project), project

    async def _ensure_default_bot(self, user: User, project: Project | None) -> User:
        now = self._now_ms()
        async with self._actor_transaction(user.id) as connection:
            await connection.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 0))",
                (f"default-bot:{user.id}",),
            )
            current_user_row = await self._fetch_one(
                connection,
                """
                SELECT id, display_name, default_project_id, created_at_ms, updated_at_ms,
                       default_vault_id, default_persona_id, default_organization_id,
                       default_bot_id
                FROM nanobot_collaboration.collaboration_users WHERE id = %s
                """,
                (user.id,),
            )
            if current_user_row is None:
                raise CollaborationNotFoundError("user not found")
            user = decode_user_row(current_user_row)
            if user.default_bot_id is not None and user.default_organization_id is not None:
                selected_bot_row = await self._visible_bot_row(
                    connection, user.id, user.default_bot_id
                )
                if selected_bot_row is not None:
                    selected_bot = decode_bot_row(selected_bot_row)
                    if (
                        selected_bot.organization_id == user.default_organization_id
                        and (
                            project is None
                            or project.organization_id == user.default_organization_id
                        )
                    ):
                        return user
            organization = await self._fetch_one(
                connection,
                """
                SELECT organization.id
                FROM nanobot_collaboration.collaboration_organizations AS organization
                JOIN nanobot_collaboration.collaboration_organization_memberships AS membership
                  ON membership.organization_id = organization.id
                 AND membership.user_id = %s
                WHERE organization.created_by_user_id = %s
                  AND organization.is_personal
                ORDER BY organization.created_at_ms, organization.id
                LIMIT 1
                """,
                (user.id, user.id),
            )
            if organization is None:
                raise CollaborationStoreFormatError("user personal organization is missing")
            organization_id = _row_string(organization, "id")
            row = None
            created_bot = False
            if user.default_bot_id:
                row = await self._fetch_one(
                    connection,
                    f"SELECT {_BOT_COLUMNS} FROM nanobot_collaboration.collaboration_bots WHERE id = %s AND organization_id = %s",
                    (user.default_bot_id, organization_id),
                )
            if row is None:
                row = await self._fetch_one(
                    connection,
                    f"""
                    SELECT {_BOT_COLUMNS}
                    FROM nanobot_collaboration.collaboration_bots
                    WHERE organization_id = %s AND owner_user_id = %s
                    ORDER BY created_at_ms, id
                    LIMIT 1
                    """,
                    (organization_id, user.id),
                )
            if row is None:
                created_bot = True
                row = await self._fetch_one(
                    connection,
                    f"""
                    INSERT INTO nanobot_collaboration.collaboration_bots
                        (id, organization_id, owner_user_id, name, avatar_url, persona_id,
                         state, created_at_ms, updated_at_ms)
                    VALUES (%s, %s, %s, 'Personal bot', NULL, %s, 'active', %s, %s)
                    RETURNING {_BOT_COLUMNS}
                    """,
                    (self._new_id(), organization_id, user.id, user.default_persona_id, now, now),
                )
            if row is None:
                raise CollaborationStoreFormatError("failed to create default bot")
            bot = decode_bot_row(row)
            if (
                project is not None
                and project.organization_id == organization_id
                and created_bot
            ):
                await connection.execute(
                    """
                    INSERT INTO nanobot_collaboration.collaboration_bot_project_assignments
                        (organization_id, bot_id, project_id, assigned_by_user_id, created_at_ms)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (organization_id, bot.id, project.id, user.id, now),
                )
            preserve_shared_defaults = (
                user.default_organization_id is not None
                and user.default_organization_id != organization_id
                and user.default_bot_id is None
                and project is not None
                and project.organization_id == user.default_organization_id
            )
            reset_project_id = (
                project.id
                if project is not None and project.organization_id == organization_id
                else None
            )
            if reset_project_id is None:
                personal_project = await self._fetch_one(
                    connection,
                    """
                    SELECT project.id
                    FROM nanobot_collaboration.collaboration_projects AS project
                    JOIN nanobot_collaboration.collaboration_project_memberships AS membership
                      ON membership.organization_id = project.organization_id
                     AND membership.project_id = project.id
                     AND membership.user_id = %s
                    WHERE project.organization_id = %s
                    ORDER BY project.created_at_ms, project.id
                    LIMIT 1
                    """,
                    (user.id, organization_id),
                )
                if personal_project is not None:
                    reset_project_id = _row_string(personal_project, "id")
            if preserve_shared_defaults or (
                user.default_organization_id == organization_id
                and user.default_bot_id == bot.id
            ):
                user_row = current_user_row
            else:
                user_row = await self._fetch_one(
                    connection,
                    """
                    UPDATE nanobot_collaboration.collaboration_users
                    SET default_project_id = %s, default_organization_id = %s,
                        default_bot_id = %s, updated_at_ms = %s
                    WHERE id = %s
                    RETURNING id, display_name, default_project_id, created_at_ms, updated_at_ms,
                              default_vault_id, default_persona_id, default_organization_id,
                              default_bot_id
                    """,
                    (reset_project_id, organization_id, bot.id, now, user.id),
                )
        if user_row is None:
            raise CollaborationNotFoundError("user not found")
        return decode_user_row(user_row)

    async def create_bot(
        self,
        actor_user_id: str,
        organization_id: str,
        name: str,
        *,
        avatar_url: str | None = None,
        persona_id: str | None = None,
    ) -> Bot:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        organization_id = _identifier(organization_id, "organization_id")
        name = _string(name, "name", limit=256)
        avatar_url = _optional_string(avatar_url, "avatar_url", limit=4096)
        persona_id = _optional_identifier(persona_id, "persona_id")
        async with self._actor_transaction(actor_user_id) as connection:
            await self._require_organization_admin(
                connection, organization_id, actor_user_id
            )
            if persona_id is not None:
                persona = await self._fetch_one(
                    connection,
                    "SELECT id FROM nanobot_collaboration.collaboration_personas WHERE id = %s AND owner_user_id = %s",
                    (persona_id, actor_user_id),
                )
                if persona is None:
                    raise CollaborationPermissionError("bot persona must be user-owned")
            now = self._now_ms()
            row = await self._fetch_one(
                connection,
                f"""
                INSERT INTO nanobot_collaboration.collaboration_bots
                    (id, organization_id, owner_user_id, name, avatar_url, persona_id,
                     state, created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)
                RETURNING {_BOT_COLUMNS}
                """,
                (
                    self._new_id(), organization_id, actor_user_id, name, avatar_url,
                    persona_id, now, now,
                ),
            )
        if row is None:
            raise CollaborationStoreFormatError("failed to create bot")
        return decode_bot_row(row)

    async def get_bot(self, actor_user_id: str, bot_id: str) -> Bot | None:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        bot_id = _identifier(bot_id, "bot_id")
        async with self._actor_transaction(actor_user_id) as connection:
            row = await self._visible_bot_row(connection, actor_user_id, bot_id)
        return decode_bot_row(row) if row is not None else None

    async def list_bots(
        self, actor_user_id: str, *, organization_id: str | None = None
    ) -> list[Bot]:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        organization_id = _optional_identifier(organization_id, "organization_id")
        async with self._actor_transaction(actor_user_id) as connection:
            rows = await self._fetch_all(
                connection,
                f"""
                SELECT DISTINCT {_BOT_SELECT_COLUMNS}
                FROM nanobot_collaboration.collaboration_bots AS bot
                LEFT JOIN nanobot_collaboration.collaboration_organization_memberships AS organization_membership
                  ON organization_membership.organization_id = bot.organization_id
                 AND organization_membership.user_id = %s
                LEFT JOIN nanobot_collaboration.collaboration_bot_project_assignments AS assignment
                  ON assignment.organization_id = bot.organization_id AND assignment.bot_id = bot.id
                LEFT JOIN nanobot_collaboration.collaboration_project_memberships AS project_membership
                  ON project_membership.organization_id = assignment.organization_id
                 AND project_membership.project_id = assignment.project_id
                 AND project_membership.user_id = %s
                WHERE (%s::varchar IS NULL OR bot.organization_id = %s)
                  AND (
                    bot.owner_user_id = %s
                    OR organization_membership.role IN ('owner', 'admin')
                    OR project_membership.user_id IS NOT NULL
                  )
                ORDER BY bot.updated_at_ms DESC, bot.id DESC
                """,
                (
                    actor_user_id, actor_user_id, organization_id, organization_id,
                    actor_user_id,
                ),
            )
        return [decode_bot_row(row) for row in rows]

    async def update_bot(
        self,
        bot_id: str,
        actor_user_id: str,
        *,
        name: str | None = None,
        avatar_url: str | None = None,
        persona_id: str | None = None,
        state_value: BotState | None = None,
    ) -> Bot:
        bot_id = _identifier(bot_id, "bot_id")
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        name = _string(name, "name", limit=256) if name is not None else None
        avatar_url = _optional_string(avatar_url, "avatar_url", limit=4096)
        persona_id = _optional_identifier(persona_id, "persona_id")
        async with self._actor_transaction(actor_user_id) as connection:
            bot = await self._require_bot_manager(connection, actor_user_id, bot_id)
            if persona_id is not None:
                persona = await self._fetch_one(
                    connection,
                    "SELECT id FROM nanobot_collaboration.collaboration_personas WHERE id = %s AND owner_user_id = %s",
                    (persona_id, bot.owner_user_id),
                )
                if persona is None:
                    raise CollaborationPermissionError("bot persona must be owner-owned")
            row = await self._fetch_one(
                connection,
                f"""
                UPDATE nanobot_collaboration.collaboration_bots
                SET name = COALESCE(%s, name),
                    avatar_url = COALESCE(%s, avatar_url),
                    persona_id = COALESCE(%s, persona_id),
                    state = COALESCE(%s, state),
                    updated_at_ms = %s
                WHERE id = %s AND organization_id = %s
                RETURNING {_BOT_COLUMNS}
                """,
                (
                    name, avatar_url, persona_id,
                    state_value.value if state_value is not None else None,
                    self._now_ms(), bot.id, bot.organization_id,
                ),
            )
        if row is None:
            raise CollaborationNotFoundError("bot not found")
        return decode_bot_row(row)

    async def delete_bot(self, bot_id: str, actor_user_id: str) -> bool:
        bot_id = _identifier(bot_id, "bot_id")
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        async with self._actor_transaction(actor_user_id) as connection:
            bot = await self._require_bot_manager(connection, actor_user_id, bot_id)
            default_user = await self._fetch_one(
                connection,
                "SELECT id FROM nanobot_collaboration.collaboration_users WHERE default_bot_id = %s LIMIT 1",
                (bot_id,),
            )
            if default_user is not None:
                raise CollaborationConflictError("a default bot cannot be deleted")
            cursor = await connection.execute(
                "DELETE FROM nanobot_collaboration.collaboration_bots WHERE id = %s AND organization_id = %s",
                (bot.id, bot.organization_id),
            )
            return cursor.rowcount > 0

    async def update_user_defaults(
        self,
        user_id: str,
        *,
        organization_id: str,
        bot_id: str,
        project_id: str | None = None,
    ) -> User:
        user_id = _identifier(user_id, "user_id")
        organization_id = _identifier(organization_id, "organization_id")
        bot_id = _identifier(bot_id, "bot_id")
        project_id = _optional_identifier(project_id, "project_id")
        async with self._actor_transaction(user_id) as connection:
            bot = await self._visible_bot_row(connection, user_id, bot_id)
            if bot is None or _row_string(bot, "organization_id") != organization_id:
                raise CollaborationPermissionError("bot is unavailable in this organization")
            if project_id is not None:
                assignment = await self._fetch_one(
                    connection,
                    """
                    SELECT 1 AS present
                    FROM nanobot_collaboration.collaboration_bot_project_assignments AS assignment
                    JOIN nanobot_collaboration.collaboration_project_memberships AS membership
                      ON membership.organization_id = assignment.organization_id
                     AND membership.project_id = assignment.project_id
                     AND membership.user_id = %s
                    WHERE assignment.organization_id = %s AND assignment.bot_id = %s
                      AND assignment.project_id = %s
                    """,
                    (user_id, organization_id, bot_id, project_id),
                )
                if assignment is None:
                    raise CollaborationPermissionError("bot is not assigned to project")
            row = await self._fetch_one(
                connection,
                """
                UPDATE nanobot_collaboration.collaboration_users
                SET default_organization_id = %s, default_bot_id = %s,
                    default_project_id = %s, updated_at_ms = %s
                WHERE id = %s
                RETURNING id, display_name, default_project_id, created_at_ms, updated_at_ms,
                          default_vault_id, default_persona_id, default_organization_id,
                          default_bot_id
                """,
                (organization_id, bot_id, project_id, self._now_ms(), user_id),
            )
        if row is None:
            raise CollaborationNotFoundError("user not found")
        return decode_user_row(row)

    async def list_bot_projects(
        self, actor_user_id: str, bot_id: str
    ) -> list[BotProjectAssignment]:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        bot_id = _identifier(bot_id, "bot_id")
        async with self._actor_transaction(actor_user_id) as connection:
            if await self._visible_bot_row(connection, actor_user_id, bot_id) is None:
                raise CollaborationPermissionError("bot is unavailable")
            rows = await self._fetch_all(
                connection,
                """
                SELECT assignment.bot_id, assignment.project_id,
                       assignment.assigned_by_user_id, assignment.created_at_ms
                FROM nanobot_collaboration.collaboration_bot_project_assignments AS assignment
                WHERE assignment.bot_id = %s
                ORDER BY assignment.created_at_ms, assignment.project_id
                """,
                (bot_id,),
            )
        return [decode_bot_project_assignment_row(row) for row in rows]

    async def list_bot_channels(
        self, actor_user_id: str, bot_id: str
    ) -> list[BotChannelAssignment]:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        bot_id = _identifier(bot_id, "bot_id")
        async with self._actor_transaction(actor_user_id) as connection:
            if await self._visible_bot_row(connection, actor_user_id, bot_id) is None:
                raise CollaborationPermissionError("bot is unavailable")
            rows = await self._fetch_all(
                connection,
                """
                SELECT bot_id, channel_type, instance_id, claimed_by_user_id, created_at_ms
                FROM nanobot_collaboration.collaboration_bot_channel_assignments
                WHERE bot_id = %s
                ORDER BY channel_type, instance_id
                """,
                (bot_id,),
            )
        return [decode_bot_channel_assignment_row(row) for row in rows]

    async def list_bot_project_channels(
        self, actor_user_id: str, bot_id: str, project_id: str
    ) -> list[BotProjectChannel]:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        bot_id = _identifier(bot_id, "bot_id")
        project_id = _identifier(project_id, "project_id")
        async with self._actor_transaction(actor_user_id) as connection:
            rows = await self._fetch_all(
                connection,
                """
                SELECT bot_id, project_id, channel_type, instance_id, enabled, updated_at_ms
                FROM nanobot_collaboration.collaboration_bot_project_channels
                WHERE bot_id = %s AND project_id = %s
                ORDER BY channel_type, instance_id
                """,
                (bot_id, project_id),
            )
        return [decode_bot_project_channel_row(row) for row in rows]

    async def get_bot_capability_profile(
        self, actor_user_id: str, bot_id: str, *, project_id: str | None = None
    ) -> BotCapabilityProfile:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        bot_id = _identifier(bot_id, "bot_id")
        project_id = _optional_identifier(project_id, "project_id")
        async with self._actor_transaction(actor_user_id) as connection:
            if await self._visible_bot_row(connection, actor_user_id, bot_id) is None:
                raise CollaborationPermissionError("bot is unavailable")
            row = await self._fetch_one(
                connection,
                """
                SELECT bot_id, project_id, revision, settings, updated_at_ms
                FROM nanobot_collaboration.collaboration_bot_capability_profiles
                WHERE bot_id = %s AND scope_project_id = %s
                """,
                (bot_id, project_id or ""),
            )
        return (
            decode_bot_capability_profile_row(row)
            if row is not None
            else BotCapabilityProfile(bot_id, project_id, 0, normalize_bot_capability_settings({}), 0)
        )

    async def update_bot_capability_profile(
        self,
        actor_user_id: str,
        bot_id: str,
        settings: Mapping[str, object],
        *,
        project_id: str | None = None,
        expected_revision: int | None = None,
    ) -> BotCapabilityProfile:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        bot_id = _identifier(bot_id, "bot_id")
        project_id = _optional_identifier(project_id, "project_id")
        normalized = normalize_bot_capability_settings(settings)
        if expected_revision is not None and (
            isinstance(expected_revision, bool) or expected_revision < 0
        ):
            raise ValueError("expected_revision must be non-negative")
        async with self._actor_transaction(actor_user_id) as connection:
            bot = await self._require_bot_manager(connection, actor_user_id, bot_id)
            if project_id is not None:
                await self._require_project_owner(connection, actor_user_id, project_id)
                assigned = await self._fetch_one(
                    connection,
                    "SELECT 1 AS present FROM nanobot_collaboration.collaboration_bot_project_assignments WHERE organization_id = %s AND bot_id = %s AND project_id = %s",
                    (bot.organization_id, bot.id, project_id),
                )
                if assigned is None:
                    raise CollaborationPermissionError("bot is not assigned to project")
            now = self._now_ms()
            row = await self._fetch_one(
                connection,
                """
                INSERT INTO nanobot_collaboration.collaboration_bot_capability_profiles
                    (organization_id, bot_id, project_id, scope_project_id,
                     revision, settings, updated_at_ms)
                VALUES (%s, %s, %s, %s, 1, %s, %s)
                ON CONFLICT (organization_id, bot_id, scope_project_id) DO UPDATE
                SET revision = collaboration_bot_capability_profiles.revision + 1,
                    settings = EXCLUDED.settings,
                    updated_at_ms = EXCLUDED.updated_at_ms
                WHERE %s OR collaboration_bot_capability_profiles.revision = %s
                RETURNING bot_id, project_id, revision, settings, updated_at_ms
                """,
                (
                    bot.organization_id, bot.id, project_id, project_id or "",
                    Jsonb(thaw_json(normalized)), now,
                    expected_revision is None, expected_revision or 0,
                ),
            )
        if row is None:
            raise CollaborationConflictError("bot capability revision changed")
        return decode_bot_capability_profile_row(row)

    async def create_pairing_challenge(
        self,
        actor_user_id: str,
        *,
        purpose: PairingPurpose,
        organization_id: str,
        bot_id: str,
        channel_type: str,
        instance_id: str,
        project_id: str | None = None,
        ttl_seconds: int = 600,
    ) -> tuple[PairingChallenge, str]:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        organization_id = _identifier(organization_id, "organization_id")
        bot_id = _identifier(bot_id, "bot_id")
        channel_type = _string(channel_type, "channel_type", limit=128)
        instance_id = _string(instance_id, "instance_id", limit=128)
        project_id = _optional_identifier(project_id, "project_id")
        if isinstance(ttl_seconds, bool) or not 60 <= ttl_seconds <= 900:
            raise ValueError("pairing challenge TTL must be between 60 and 900 seconds")
        async with self._actor_transaction(actor_user_id) as connection:
            bot = await self._require_bot_manager(connection, actor_user_id, bot_id)
            if bot.organization_id != organization_id:
                raise CollaborationPermissionError("bot is outside the organization")
            if purpose is PairingPurpose.CLAIM_CHANNEL:
                if project_id is not None:
                    raise ValueError("channel claim must not include project_id")
            else:
                if project_id is None:
                    raise ValueError("bot project assignment requires project_id")
                await self._require_project_owner(connection, actor_user_id, project_id)
                channel = await self._fetch_one(
                    connection,
                    "SELECT bot_id FROM nanobot_collaboration.collaboration_bot_channel_assignments WHERE channel_type = %s AND instance_id = %s",
                    (channel_type, instance_id),
                )
                if channel is None or _row_string(channel, "bot_id") != bot_id:
                    raise CollaborationPermissionError("channel is not claimed by this bot")
            now = self._now_ms()
            await connection.execute(
                "DELETE FROM nanobot_collaboration.collaboration_pairing_challenges WHERE requested_by_user_id = %s AND (expires_at_ms < %s OR consumed_at_ms IS NOT NULL)",
                (actor_user_id, now),
            )
            count = await self._fetch_one(
                connection,
                "SELECT pg_catalog.count(*) AS count FROM nanobot_collaboration.collaboration_pairing_challenges WHERE requested_by_user_id = %s",
                (actor_user_id,),
            )
            if count is not None and _row_int(count, "count") >= 32:
                raise CollaborationConflictError("too many active pairing challenges")
            code = new_assignment_code()
            row = await self._fetch_one(
                connection,
                f"""
                INSERT INTO nanobot_collaboration.collaboration_pairing_challenges
                    (id, code_digest, requested_by_user_id, purpose, organization_id,
                     bot_id, project_id, channel_type, instance_id, expires_at_ms,
                     verified_at_ms, verified_sender_id, consumed_at_ms, created_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL, %s)
                RETURNING {_PAIRING_COLUMNS}
                """,
                (
                    self._new_id(), assignment_code_digest(code), actor_user_id,
                    purpose.value, organization_id, bot_id, project_id, channel_type,
                    instance_id, now + ttl_seconds * 1000, now,
                ),
            )
        if row is None:
            raise CollaborationStoreFormatError("failed to create pairing challenge")
        return decode_pairing_challenge_row(row), code

    async def get_pairing_challenge(
        self, actor_user_id: str, challenge_id: str
    ) -> PairingChallenge | None:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        challenge_id = _identifier(challenge_id, "challenge_id")
        async with self._actor_transaction(actor_user_id) as connection:
            row = await self._fetch_one(
                connection,
                f"SELECT {_PAIRING_COLUMNS} FROM nanobot_collaboration.collaboration_pairing_challenges WHERE id = %s AND requested_by_user_id = %s",
                (challenge_id, actor_user_id),
            )
        return decode_pairing_challenge_row(row) if row is not None else None

    async def verify_pairing_challenge(
        self,
        code: str,
        *,
        channel_type: str,
        instance_id: str,
        sender_id: str,
    ) -> PairingChallenge:
        digest = assignment_code_digest(normalize_assignment_code(code))
        channel_type = _string(channel_type, "channel_type", limit=128)
        instance_id = _string(instance_id, "instance_id", limit=128)
        sender_id = _string(sender_id, "sender_id", limit=512)
        channel = runtime_channel_key(channel_type, instance_id)
        async with self._identity_transaction(channel, sender_id) as connection:
            row = await self._fetch_one(
                connection,
                f"""
                SELECT {_PAIRING_COLUMNS}
                FROM nanobot_collaboration.collaboration_pairing_challenges
                WHERE code_digest = %s AND channel_type = %s AND instance_id = %s
                  AND consumed_at_ms IS NULL
                  AND expires_at_ms >= pg_catalog.floor(pg_catalog.date_part('epoch', pg_catalog.clock_timestamp()) * 1000)::bigint
                LIMIT 1
                """,
                (digest, channel_type, instance_id),
            )
        if row is None:
            raise CollaborationNotFoundError("pairing challenge not found or expired")
        challenge = decode_pairing_challenge_row(row)
        if not hmac.compare_digest(challenge.code_digest, digest):
            raise CollaborationNotFoundError("pairing challenge not found or expired")
        if challenge.verified_at_ms is not None:
            if challenge.verified_sender_id != sender_id:
                raise CollaborationConflictError("pairing challenge was verified by another sender")
            return challenge
        now = self._now_ms()
        async with self._session.transaction(
            challenge.requested_by_user_id,
            identity_channel=channel,
            identity_sender_id=sender_id,
        ) as connection:
            locked = await self._fetch_one(
                connection,
                f"SELECT {_PAIRING_COLUMNS} FROM nanobot_collaboration.collaboration_pairing_challenges WHERE id = %s FOR UPDATE",
                (challenge.id,),
            )
            if locked is None:
                raise CollaborationNotFoundError("pairing challenge not found")
            current = decode_pairing_challenge_row(locked)
            if current.consumed_at_ms is not None or current.expires_at_ms < now:
                raise CollaborationConflictError("pairing challenge expired")
            if current.verified_at_ms is not None:
                if current.verified_sender_id != sender_id:
                    raise CollaborationConflictError(
                        "pairing challenge was verified by another sender"
                    )
                return current
            identity = await self._fetch_one(
                connection,
                "SELECT user_id, channel, sender_id, created_at_ms FROM nanobot_collaboration.collaboration_identities WHERE channel = %s AND sender_id = %s",
                (channel, sender_id),
            )
            if identity is not None:
                resolved = decode_identity_row(identity)
                if resolved.user_id != current.requested_by_user_id:
                    raise CollaborationConflictError("channel sender belongs to another user")
            else:
                identity = await self._fetch_one(
                    connection,
                    """
                    INSERT INTO nanobot_collaboration.collaboration_identities
                        (organization_id, user_id, channel, sender_id, created_at_ms)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING user_id, channel, sender_id, created_at_ms
                    """,
                    (
                        current.organization_id, current.requested_by_user_id,
                        channel, sender_id, now,
                    ),
                )
                if identity is None:
                    raise CollaborationStoreFormatError("failed to bind pairing identity")
            updated = await self._fetch_one(
                connection,
                f"""
                UPDATE nanobot_collaboration.collaboration_pairing_challenges
                SET verified_at_ms = COALESCE(verified_at_ms, %s),
                    verified_sender_id = COALESCE(verified_sender_id, %s)
                WHERE id = %s
                  AND (verified_sender_id IS NULL OR verified_sender_id = %s)
                RETURNING {_PAIRING_COLUMNS}
                """,
                (now, sender_id, current.id, sender_id),
            )
            if updated is None:
                raise CollaborationConflictError("pairing challenge verification changed")
        return decode_pairing_challenge_row(updated)

    async def consume_pairing_challenge(
        self, actor_user_id: str, challenge_id: str
    ) -> PairingChallenge:
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        challenge_id = _identifier(challenge_id, "challenge_id")
        now = self._now_ms()
        async with self._actor_transaction(actor_user_id) as connection:
            row = await self._fetch_one(
                connection,
                f"SELECT {_PAIRING_COLUMNS} FROM nanobot_collaboration.collaboration_pairing_challenges WHERE id = %s FOR UPDATE",
                (challenge_id,),
            )
            if row is None:
                raise CollaborationNotFoundError("pairing challenge not found")
            challenge = decode_pairing_challenge_row(row)
            if challenge.requested_by_user_id != actor_user_id:
                raise CollaborationPermissionError("pairing challenge belongs to another user")
            if challenge.expires_at_ms < now:
                raise CollaborationConflictError("pairing challenge expired")
            if challenge.verified_at_ms is None or challenge.verified_sender_id is None:
                raise CollaborationConflictError("pairing challenge is not verified")
            if challenge.consumed_at_ms is not None:
                raise CollaborationConflictError("pairing challenge was already consumed")
            bot = await self._require_bot_manager(connection, actor_user_id, challenge.bot_id)
            if challenge.purpose is PairingPurpose.CLAIM_CHANNEL:
                try:
                    await connection.execute(
                        """
                        INSERT INTO nanobot_collaboration.collaboration_bot_channel_assignments
                            (organization_id, bot_id, channel_type, instance_id,
                             claimed_by_user_id, created_at_ms)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (channel_type, instance_id) DO UPDATE
                        SET bot_id = EXCLUDED.bot_id
                        WHERE collaboration_bot_channel_assignments.bot_id = EXCLUDED.bot_id
                        """,
                        (
                            bot.organization_id, bot.id, challenge.channel_type,
                            challenge.instance_id, actor_user_id, now,
                        ),
                    )
                except UniqueViolation as exc:
                    raise CollaborationConflictError("channel instance is already claimed") from exc
                claimed = await self._fetch_one(
                    connection,
                    "SELECT bot_id FROM nanobot_collaboration.collaboration_bot_channel_assignments WHERE channel_type = %s AND instance_id = %s",
                    (challenge.channel_type, challenge.instance_id),
                )
                if claimed is None or _row_string(claimed, "bot_id") != bot.id:
                    raise CollaborationConflictError("channel instance is already claimed")
            else:
                project_id = challenge.project_id
                if project_id is None:
                    raise CollaborationStoreFormatError("assignment challenge lacks project")
                project = await self._require_project_owner(connection, actor_user_id, project_id)
                if _row_string(project, "organization_id") != bot.organization_id:
                    raise CollaborationPermissionError("bot and project organizations differ")
                channel = await self._fetch_one(
                    connection,
                    "SELECT bot_id FROM nanobot_collaboration.collaboration_bot_channel_assignments WHERE channel_type = %s AND instance_id = %s",
                    (challenge.channel_type, challenge.instance_id),
                )
                if channel is None or _row_string(channel, "bot_id") != bot.id:
                    raise CollaborationPermissionError("channel is not claimed by this bot")
                await connection.execute(
                    """
                    INSERT INTO nanobot_collaboration.collaboration_bot_project_assignments
                        (organization_id, bot_id, project_id, assigned_by_user_id, created_at_ms)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (bot.organization_id, bot.id, project_id, actor_user_id, now),
                )
                await connection.execute(
                    """
                    INSERT INTO nanobot_collaboration.collaboration_bot_project_channels
                        (organization_id, bot_id, project_id, channel_type,
                         instance_id, enabled, updated_at_ms)
                    VALUES (%s, %s, %s, %s, %s, true, %s)
                    ON CONFLICT (organization_id, bot_id, project_id, channel_type, instance_id)
                    DO UPDATE SET enabled = true, updated_at_ms = EXCLUDED.updated_at_ms
                    """,
                    (
                        bot.organization_id, bot.id, project_id, challenge.channel_type,
                        challenge.instance_id, now,
                    ),
                )
                await connection.execute(
                    """
                    UPDATE nanobot_collaboration.collaboration_users
                    SET default_project_id = %s, default_organization_id = %s,
                        default_bot_id = %s, updated_at_ms = %s
                    WHERE id = %s
                    """,
                    (project_id, bot.organization_id, bot.id, now, actor_user_id),
                )
            updated = await self._fetch_one(
                connection,
                f"""
                UPDATE nanobot_collaboration.collaboration_pairing_challenges
                SET consumed_at_ms = %s
                WHERE id = %s AND consumed_at_ms IS NULL
                RETURNING {_PAIRING_COLUMNS}
                """,
                (now, challenge.id),
            )
        if updated is None:
            raise CollaborationConflictError("pairing challenge was already consumed")
        return decode_pairing_challenge_row(updated)

    async def _visible_bot_row(
        self, connection: PostgresConnection, actor_user_id: str, bot_id: str
    ) -> dict[str, object] | None:
        return await self._fetch_one(
            connection,
            f"""
            SELECT DISTINCT {_BOT_SELECT_COLUMNS}
            FROM nanobot_collaboration.collaboration_bots AS bot
            LEFT JOIN nanobot_collaboration.collaboration_organization_memberships AS organization_membership
              ON organization_membership.organization_id = bot.organization_id
             AND organization_membership.user_id = %s
            LEFT JOIN nanobot_collaboration.collaboration_bot_project_assignments AS assignment
              ON assignment.organization_id = bot.organization_id AND assignment.bot_id = bot.id
            LEFT JOIN nanobot_collaboration.collaboration_project_memberships AS project_membership
              ON project_membership.organization_id = assignment.organization_id
             AND project_membership.project_id = assignment.project_id
             AND project_membership.user_id = %s
            WHERE bot.id = %s
              AND (
                bot.owner_user_id = %s
                OR organization_membership.role IN ('owner', 'admin')
                OR project_membership.user_id IS NOT NULL
              )
            LIMIT 1
            """,
            (actor_user_id, actor_user_id, bot_id, actor_user_id),
        )

    async def _require_bot_manager(
        self, connection: PostgresConnection, actor_user_id: str, bot_id: str
    ) -> Bot:
        row = await self._fetch_one(
            connection,
            f"""
            SELECT {_BOT_SELECT_COLUMNS}
            FROM nanobot_collaboration.collaboration_bots AS bot
            LEFT JOIN nanobot_collaboration.collaboration_organization_memberships AS membership
              ON membership.organization_id = bot.organization_id
             AND membership.user_id = %s
            WHERE bot.id = %s
              AND (bot.owner_user_id = %s OR membership.role IN ('owner', 'admin'))
            """,
            (actor_user_id, bot_id, actor_user_id),
        )
        if row is None:
            raise CollaborationPermissionError("bot manager role is required")
        return decode_bot_row(row)

    async def _require_organization_admin(
        self, connection: PostgresConnection, organization_id: str, actor_user_id: str
    ) -> None:
        row = await self._fetch_one(
            connection,
            "SELECT role FROM nanobot_collaboration.collaboration_organization_memberships WHERE organization_id = %s AND user_id = %s",
            (organization_id, actor_user_id),
        )
        if row is None or _row_string(row, "role") not in {"owner", "admin"}:
            raise CollaborationPermissionError("organization admin role is required")

    async def _require_project_owner(
        self, connection: PostgresConnection, actor_user_id: str, project_id: str
    ) -> dict[str, object]:
        row = await self._fetch_one(
            connection,
            """
            SELECT project.organization_id, project.id
            FROM nanobot_collaboration.collaboration_projects AS project
            JOIN nanobot_collaboration.collaboration_project_memberships AS membership
              ON membership.organization_id = project.organization_id
             AND membership.project_id = project.id
             AND membership.user_id = %s
             AND membership.role = 'owner'
            WHERE project.id = %s
            """,
            (actor_user_id, project_id),
        )
        if row is None:
            raise CollaborationPermissionError("project owner role is required")
        return row


def _identifier(value: str, field: str) -> str:
    return _string(value, field, limit=128)


def _optional_identifier(value: str | None, field: str) -> str | None:
    return _identifier(value, field) if value is not None else None


def _optional_string(value: str | None, field: str, *, limit: int) -> str | None:
    return _string(value, field, limit=limit, empty=True) if value is not None else None


def _string(value: object, field: str, *, limit: int = 512, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if (not normalized and not empty) or len(normalized) > limit or any(
        ord(character) < 32 for character in normalized
    ):
        raise ValueError(f"invalid {field}")
    return normalized


def _row_string(row: Mapping[str, object], column: str) -> str:
    value = row.get(column)
    if not isinstance(value, str):
        raise CollaborationStoreFormatError(f"{column} must be a string")
    return value


def _row_int(row: Mapping[str, object], column: str) -> int:
    value = row.get(column)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CollaborationStoreFormatError(f"{column} must be an integer")
    return value
