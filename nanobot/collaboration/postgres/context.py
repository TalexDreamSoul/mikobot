"""Conversation scope and project-context PostgreSQL repository mixin."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from nanobot.collaboration.models import (
    Bot,
    ContextSource,
    ContextSourceKind,
    ConversationBinding,
    ConversationScope,
    ConversationScopeKind,
    ExtensionProfile,
)
from nanobot.collaboration.pairing import BOT_PROJECT_ROUTE_REQUIRED_METADATA_KEY
from nanobot.collaboration.postgres.base import PostgresRepositoryBase
from nanobot.collaboration.postgres.rows import (
    decode_bot_capability_profile_row,
    decode_bot_row,
    decode_context_source_row,
    decode_conversation_binding_row,
    decode_extension_profile_row,
    decode_persona_row,
    decode_project_row,
    decode_user_row,
    thaw_bounded_json,
)
from nanobot.collaboration.store import (
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    CollaborationStoreFormatError,
)

if TYPE_CHECKING:
    from nanobot.collaboration.models import Persona, Project, User


_Connection: TypeAlias = AsyncConnection[tuple[object, ...]]


class PostgresContextMixin(PostgresRepositoryBase):
    """Normalized PostgreSQL implementation of conversation context operations."""

    async def bind_conversation(
        self, channel: str, conversation_id: str, project_id: str, user_id: str, *, thread_id: str | None = None
    ) -> ConversationBinding:
        channel = _key(channel, "channel", limit=128)
        conversation_id = _key(conversation_id, "conversation_id")
        project_id = _identifier(project_id, "project_id")
        user_id = _identifier(user_id, "user_id")
        thread_id = _optional_key(thread_id, "thread_id")
        async with self._actor_transaction(user_id) as connection:
            organization_id = await self._member_organization(connection, project_id, user_id)
            existing = await self._fetch_one(connection, """
                SELECT id, channel, conversation_id, thread_id, project_id, created_by_user_id, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_conversation_bindings
                WHERE organization_id = %s AND channel = %s AND conversation_id = %s AND thread_id = %s
                """, (organization_id, channel, conversation_id, thread_id or ""))
            if existing is not None:
                binding = decode_conversation_binding_row(existing)
                if binding.project_id != project_id or binding.created_by_user_id != user_id:
                    raise CollaborationConflictError("conversation is already bound")
                return binding
            now = self._now_ms()
            try:
                row = await self._fetch_one(connection, """
                    INSERT INTO nanobot_collaboration.collaboration_conversation_bindings (
                        id, organization_id, channel, conversation_id, thread_id, project_id, created_by_user_id, created_at_ms, updated_at_ms
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, channel, conversation_id, thread_id, project_id, created_by_user_id, created_at_ms, updated_at_ms
                    """, (self._new_id(), organization_id, channel, conversation_id, thread_id or "", project_id, user_id, now, now))
            except UniqueViolation as exc:
                raise CollaborationConflictError("conversation is already bound") from exc
            if row is None:
                raise CollaborationStoreFormatError("failed to create conversation binding")
            return decode_conversation_binding_row(row)

    async def resolve_binding(
        self, channel: str, conversation_id: str, actor_user_id: str, *, thread_id: str | None = None
    ) -> ConversationBinding | None:
        channel = _key(channel, "channel", limit=128)
        conversation_id = _key(conversation_id, "conversation_id")
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        thread_id = _optional_key(thread_id, "thread_id")
        async with self._actor_transaction(actor_user_id) as connection:
            rows = await self._fetch_all(connection, """
                SELECT binding.id, binding.channel, binding.conversation_id, binding.thread_id,
                       binding.project_id, binding.created_by_user_id, binding.created_at_ms, binding.updated_at_ms
                FROM nanobot_collaboration.collaboration_conversation_bindings AS binding
                JOIN nanobot_collaboration.collaboration_project_memberships AS membership
                  ON membership.organization_id = binding.organization_id
                 AND membership.project_id = binding.project_id
                 AND membership.user_id = %s
                WHERE binding.channel = %s AND binding.conversation_id = %s AND binding.thread_id = %s
                """, (actor_user_id, channel, conversation_id, thread_id or ""))
        if len(rows) > 1:
            raise CollaborationStoreFormatError("duplicate conversation binding")
        return decode_conversation_binding_row(rows[0]) if rows else None

    async def unbind_conversation(
        self, channel: str, conversation_id: str, actor_user_id: str, *, thread_id: str | None = None
    ) -> bool:
        channel = _key(channel, "channel", limit=128)
        conversation_id = _key(conversation_id, "conversation_id")
        actor_user_id = _identifier(actor_user_id, "actor_user_id")
        thread_id = _optional_key(thread_id, "thread_id")
        async with self._actor_transaction(actor_user_id) as connection:
            rows = await self._fetch_all(connection, """
                SELECT id, organization_id, project_id
                FROM nanobot_collaboration.collaboration_conversation_bindings
                WHERE channel = %s AND conversation_id = %s AND thread_id = %s
                """, (channel, conversation_id, thread_id or ""))
            if len(rows) > 1:
                raise CollaborationStoreFormatError("duplicate conversation binding")
            if not rows:
                return False
            row = rows[0]
            owner = await self._fetch_one(connection, """
                SELECT 1 FROM nanobot_collaboration.collaboration_project_memberships
                WHERE organization_id = %s AND project_id = %s AND user_id = %s AND role = 'owner'
                """, (row["organization_id"], row["project_id"], actor_user_id))
            if owner is None:
                raise CollaborationPermissionError("project owner role is required")
            return await self._fetch_one(connection, """
                DELETE FROM nanobot_collaboration.collaboration_conversation_bindings
                WHERE id = %s AND organization_id = %s RETURNING id
                """, (row["id"], row["organization_id"])) is not None

    async def get_extension_profile(self, user_id: str, project_id: str) -> ExtensionProfile:
        user_id, project_id = _identifier(user_id, "user_id"), _identifier(project_id, "project_id")
        async with self._actor_transaction(user_id) as connection:
            organization_id = await self._member_organization(connection, project_id, user_id)
            row = await self._fetch_one(connection, """
                SELECT project_id, user_id, revision, settings, updated_at_ms
                FROM nanobot_collaboration.collaboration_extension_profiles
                WHERE organization_id = %s AND project_id = %s AND user_id = %s
                """, (organization_id, project_id, user_id))
        return decode_extension_profile_row(row) if row is not None else ExtensionProfile(project_id, user_id, 0, {}, 0)

    async def update_extension_profile(self, user_id: str, project_id: str, settings: Mapping[str, object], *, expected_revision: int | None = None) -> ExtensionProfile:
        user_id, project_id = _identifier(user_id, "user_id"), _identifier(project_id, "project_id")
        if expected_revision is not None and (isinstance(expected_revision, bool) or expected_revision < 0):
            raise ValueError("expected_revision must be non-negative")
        settings_json = _json_parameter(settings, "settings")
        async with self._actor_transaction(user_id) as connection:
            organization_id = await self._member_organization(connection, project_id, user_id)
            row = await self._fetch_one(connection, """
                INSERT INTO nanobot_collaboration.collaboration_extension_profiles AS profile (
                    organization_id, project_id, user_id, revision, settings, updated_at_ms
                ) VALUES (%s, %s, %s, 1, %s, %s)
                ON CONFLICT (organization_id, project_id, user_id) DO UPDATE
                SET revision = profile.revision + 1, settings = EXCLUDED.settings, updated_at_ms = EXCLUDED.updated_at_ms
                WHERE %s OR profile.revision = %s
                RETURNING project_id, user_id, revision, settings, updated_at_ms
                """, (organization_id, project_id, user_id, Jsonb(settings_json), self._now_ms(), expected_revision is None, expected_revision or 0))
            if row is None:
                raise CollaborationConflictError("extension profile revision changed")
            return decode_extension_profile_row(row)

    async def create_context_source(self, project_id: str, actor_user_id: str, name: str, kind: ContextSourceKind, *, config: Mapping[str, object] | None = None, enabled: bool = True) -> ContextSource:
        project_id, actor_user_id = _identifier(project_id, "project_id"), _identifier(actor_user_id, "actor_user_id")
        name, kind = _key(name, "name", limit=256), _context_kind(kind)
        if type(enabled) is not bool:
            raise CollaborationStoreFormatError("enabled must be a boolean")
        config_json = _json_parameter(config or {}, "config")
        async with self._actor_transaction(actor_user_id) as connection:
            organization_id = await self._member_organization(connection, project_id, actor_user_id)
            now = self._now_ms()
            row = await self._fetch_one(connection, """
                INSERT INTO nanobot_collaboration.collaboration_context_sources (
                    id, organization_id, project_id, name, kind, enabled, config, created_at_ms, updated_at_ms
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, project_id, name, kind, enabled, config, created_at_ms, updated_at_ms
                """, (self._new_id(), organization_id, project_id, name, kind.value, enabled, Jsonb(config_json), now, now))
            if row is None:
                raise CollaborationStoreFormatError("failed to create context source")
            return decode_context_source_row(row)

    async def list_context_sources(self, user_id: str, project_id: str) -> list[ContextSource]:
        user_id, project_id = _identifier(user_id, "user_id"), _identifier(project_id, "project_id")
        async with self._actor_transaction(user_id) as connection:
            organization_id = await self._member_organization(connection, project_id, user_id)
            rows = await self._fetch_all(connection, """
                SELECT id, project_id, name, kind, enabled, config, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_context_sources
                WHERE organization_id = %s AND project_id = %s ORDER BY created_at_ms, id
                """, (organization_id, project_id))
        return [decode_context_source_row(row) for row in rows]

    async def get_context_source(self, user_id: str, source_id: str) -> ContextSource | None:
        user_id, source_id = _identifier(user_id, "user_id"), _identifier(source_id, "source_id")
        async with self._actor_transaction(user_id) as connection:
            row = await self._fetch_one(connection, """
                SELECT source.id, source.project_id, source.name, source.kind, source.enabled, source.config, source.created_at_ms, source.updated_at_ms
                FROM nanobot_collaboration.collaboration_context_sources AS source
                JOIN nanobot_collaboration.collaboration_project_memberships AS membership
                  ON membership.organization_id = source.organization_id AND membership.project_id = source.project_id AND membership.user_id = %s
                WHERE source.id = %s
                """, (user_id, source_id))
        return decode_context_source_row(row) if row is not None else None

    async def update_context_source(self, source_id: str, actor_user_id: str, *, name: str | None = None, kind: ContextSourceKind | None = None, config: Mapping[str, object] | None = None, enabled: bool | None = None) -> ContextSource:
        if name is None and kind is None and config is None and enabled is None:
            raise ValueError("provide at least one context source field")
        source_id, actor_user_id = _identifier(source_id, "source_id"), _identifier(actor_user_id, "actor_user_id")
        name, kind = (_key(name, "name", limit=256) if name is not None else None), (_context_kind(kind) if kind is not None else None)
        if enabled is not None and type(enabled) is not bool:
            raise CollaborationStoreFormatError("enabled must be a boolean")
        config_json = _json_parameter(config, "config") if config is not None else None
        async with self._actor_transaction(actor_user_id) as connection:
            source = await self._fetch_one(connection, """
                SELECT id, organization_id, project_id FROM nanobot_collaboration.collaboration_context_sources WHERE id = %s
                """, (source_id,))
            if source is None:
                raise CollaborationNotFoundError("context source not found")
            await self._require_project_member(connection, source["organization_id"], source["project_id"], actor_user_id)
            row = await self._fetch_one(connection, """
                UPDATE nanobot_collaboration.collaboration_context_sources
                SET name = COALESCE(%s, name), kind = COALESCE(%s, kind), config = COALESCE(%s, config), enabled = COALESCE(%s, enabled), updated_at_ms = %s
                WHERE id = %s AND organization_id = %s AND project_id = %s
                RETURNING id, project_id, name, kind, enabled, config, created_at_ms, updated_at_ms
                """, (name, kind.value if kind else None, Jsonb(config_json) if config_json is not None else None, enabled, self._now_ms(), source_id, source["organization_id"], source["project_id"]))
            if row is None:
                raise CollaborationNotFoundError("context source not found")
            return decode_context_source_row(row)

    async def delete_context_source(self, source_id: str, actor_user_id: str) -> bool:
        source_id, actor_user_id = _identifier(source_id, "source_id"), _identifier(actor_user_id, "actor_user_id")
        async with self._actor_transaction(actor_user_id) as connection:
            source = await self._fetch_one(connection, """
                SELECT id, organization_id, project_id FROM nanobot_collaboration.collaboration_context_sources WHERE id = %s
                """, (source_id,))
            if source is None:
                raise CollaborationNotFoundError("context source not found")
            await self._require_project_member(connection, source["organization_id"], source["project_id"], actor_user_id)
            deleted = await self._fetch_one(connection, """
                DELETE FROM nanobot_collaboration.collaboration_context_sources
                WHERE id = %s AND organization_id = %s AND project_id = %s RETURNING id
                """, (source_id, source["organization_id"], source["project_id"]))
            if deleted is None:
                raise CollaborationNotFoundError("context source not found")
            return True

    async def resolve_scope(self, channel: str, sender_id: str, chat_id: str, metadata: Mapping[str, object] | None, default_workspace: str | Path) -> ConversationScope:
        channel, sender_id, chat_id = _key(channel, "channel", limit=128), _key(sender_id, "sender_id"), _key(chat_id, "chat_id")
        metadata = metadata if metadata is not None else {}
        thread_id = _thread_id(metadata)
        require_bot_route = metadata.get(BOT_PROJECT_ROUTE_REQUIRED_METADATA_KEY) is True
        suffix = _scope_suffix(channel, chat_id, thread_id)
        async with self._identity_transaction(channel, sender_id) as connection:
            identity = await self._fetch_one(connection, """
                SELECT user_id FROM nanobot_collaboration.collaboration_identities
                WHERE channel = %s AND sender_id = %s
                """, (channel, sender_id))
        if identity is None:
            return _isolated_scope(None, default_workspace, suffix)
        user_id = _identifier(identity["user_id"], "user_id")
        async with self._actor_transaction(user_id) as connection:
            user_row = await self._fetch_one(connection, """
                SELECT id, display_name, default_project_id, default_vault_id, default_persona_id,
                       default_organization_id, default_bot_id, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_users WHERE id = %s
                """, (user_id,))
            if user_row is None:
                return _isolated_scope(None, default_workspace, suffix)
            user = decode_user_row(user_row)
            binding_rows = await self._fetch_all(connection, """
                SELECT binding.id, binding.organization_id, binding.channel, binding.conversation_id,
                       binding.thread_id, binding.project_id, binding.created_by_user_id,
                       binding.created_at_ms, binding.updated_at_ms
                FROM nanobot_collaboration.collaboration_conversation_bindings AS binding
                JOIN nanobot_collaboration.collaboration_project_memberships AS recipient
                  ON recipient.organization_id = binding.organization_id
                 AND recipient.project_id = binding.project_id
                 AND recipient.user_id = %s
                JOIN nanobot_collaboration.collaboration_project_memberships AS creator
                  ON creator.organization_id = binding.organization_id
                 AND creator.project_id = binding.project_id
                 AND creator.user_id = binding.created_by_user_id
                WHERE binding.channel = %s AND binding.conversation_id = %s AND binding.thread_id = %s
                """, (user.id, channel, chat_id, thread_id or ""))
            if len(binding_rows) > 1:
                raise CollaborationStoreFormatError("duplicate conversation binding")
            if binding_rows:
                binding = decode_conversation_binding_row(binding_rows[0])
                project = await self._project_for_member(connection, binding.project_id, user.id)
                if project is not None:
                    return await self._project_scope(
                        connection, ConversationScopeKind.BOUND, user, project, binding,
                        suffix, channel, default_workspace,
                        require_bot_route=require_bot_route,
                    )
            if _is_direct(chat_id, sender_id, metadata) and user.default_project_id is not None:
                project = await self._project_for_member(connection, user.default_project_id, user.id)
                if project is not None:
                    return await self._project_scope(
                        connection, ConversationScopeKind.DIRECT, user, project, None,
                        suffix, channel, default_workspace,
                        require_bot_route=require_bot_route,
                    )
        return _isolated_scope(user.id, default_workspace, suffix, user)

    async def _member_organization(self, connection: _Connection, project_id: str, user_id: str) -> str:
        row = await self._fetch_one(connection, """
            SELECT project.organization_id
            FROM nanobot_collaboration.collaboration_projects AS project
            JOIN nanobot_collaboration.collaboration_project_memberships AS membership
              ON membership.organization_id = project.organization_id AND membership.project_id = project.id AND membership.user_id = %s
            WHERE project.id = %s
            """, (user_id, project_id))
        if row is None:
            raise CollaborationPermissionError("project membership is required")
        return _identifier(row["organization_id"], "organization_id")

    async def _require_project_member(self, connection: _Connection, organization_id: object, project_id: object, user_id: str) -> None:
        row = await self._fetch_one(connection, """
            SELECT 1 FROM nanobot_collaboration.collaboration_project_memberships
            WHERE organization_id = %s AND project_id = %s AND user_id = %s
            """, (organization_id, project_id, user_id))
        if row is None:
            raise CollaborationPermissionError("project membership is required")

    async def _project_for_member(self, connection: _Connection, project_id: str, user_id: str) -> Project | None:
        row = await self._fetch_one(connection, """
            SELECT project.id, project.organization_id, project.name, project.workspace_path, project.created_by_user_id, project.created_at_ms, project.updated_at_ms
            FROM nanobot_collaboration.collaboration_projects AS project
            JOIN nanobot_collaboration.collaboration_project_memberships AS membership
              ON membership.organization_id = project.organization_id AND membership.project_id = project.id AND membership.user_id = %s
            WHERE project.id = %s
            """, (user_id, project_id))
        return decode_project_row(row) if row is not None else None

    async def _project_scope(
        self,
        connection: _Connection,
        kind: ConversationScopeKind,
        user: User,
        project: Project,
        binding: ConversationBinding | None,
        suffix: str,
        channel: str,
        default_workspace: str | Path,
        *,
        require_bot_route: bool,
    ) -> ConversationScope:
        bot_row = await self._fetch_one(
            connection,
            """
            SELECT bot.id, bot.organization_id, bot.owner_user_id, bot.name,
                   bot.avatar_url, bot.persona_id, bot.state, bot.created_at_ms,
                   bot.updated_at_ms
            FROM nanobot_collaboration.collaboration_bot_channel_assignments AS assignment
            JOIN nanobot_collaboration.collaboration_bots AS bot
              ON bot.organization_id = assignment.organization_id
             AND bot.id = assignment.bot_id
             AND bot.state = 'active'
            JOIN nanobot_collaboration.collaboration_bot_project_assignments AS project_assignment
              ON project_assignment.organization_id = bot.organization_id
             AND project_assignment.bot_id = bot.id
             AND project_assignment.project_id = %s
            JOIN nanobot_collaboration.collaboration_bot_project_channels AS route
              ON route.organization_id = bot.organization_id
             AND route.bot_id = bot.id
             AND route.project_id = project_assignment.project_id
             AND route.channel_type = assignment.channel_type
             AND route.instance_id = assignment.instance_id
             AND route.enabled = TRUE
            WHERE (CASE WHEN assignment.instance_id = 'default' THEN assignment.channel_type
                        ELSE assignment.channel_type || '.' || assignment.instance_id END) = %s
            ORDER BY bot.updated_at_ms DESC, bot.id
            LIMIT 1
            """,
            (project.id, channel),
        )
        if bot_row is None and require_bot_route:
            return _isolated_scope(
                user.id, default_workspace, suffix, user, route_denied=True
            )
        if bot_row is None:
            claimed_row = await self._fetch_one(
                connection,
                """
                SELECT 1
                FROM nanobot_collaboration.collaboration_channel_claim_registry
                WHERE (CASE WHEN instance_id = 'default' THEN channel_type
                            ELSE channel_type || '.' || instance_id END) = %s
                LIMIT 1
                """,
                (channel,),
            )
            if claimed_row is not None:
                return _isolated_scope(
                    user.id, default_workspace, suffix, user, route_denied=True
                )
        if bot_row is None and user.default_bot_id is not None:
            bot_row = await self._fetch_one(
                connection,
                """
                SELECT bot.id, bot.organization_id, bot.owner_user_id, bot.name,
                       bot.avatar_url, bot.persona_id, bot.state, bot.created_at_ms,
                       bot.updated_at_ms
                FROM nanobot_collaboration.collaboration_bots AS bot
                JOIN nanobot_collaboration.collaboration_bot_project_assignments AS project_assignment
                  ON project_assignment.organization_id = bot.organization_id
                 AND project_assignment.bot_id = bot.id
                 AND project_assignment.project_id = %s
                WHERE bot.id = %s AND bot.state = 'active'
                LIMIT 1
                """,
                (project.id, user.default_bot_id),
            )
        bot: Bot | None = decode_bot_row(bot_row) if bot_row is not None else None
        profile = None
        if bot is not None:
            bot_profile_row = await self._fetch_one(
                connection,
                """
                SELECT bot_id, project_id, revision, settings, updated_at_ms
                FROM nanobot_collaboration.collaboration_bot_capability_profiles
                WHERE organization_id = %s AND bot_id = %s
                  AND scope_project_id IN (%s, '')
                ORDER BY CASE WHEN scope_project_id = %s THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (bot.organization_id, bot.id, project.id, project.id),
            )
            if bot_profile_row is not None:
                bot_profile = decode_bot_capability_profile_row(bot_profile_row)
                profile = ExtensionProfile(
                    project.id, user.id, bot_profile.revision,
                    bot_profile.settings, bot_profile.updated_at_ms,
                )
        if profile is None:
            profile_row = await self._fetch_one(connection, """
                SELECT project_id, user_id, revision, settings, updated_at_ms
                FROM nanobot_collaboration.collaboration_extension_profiles
                WHERE organization_id = %s AND project_id = %s AND user_id = %s
                """, (project.organization_id, project.id, user.id))
            profile = decode_extension_profile_row(profile_row) if profile_row is not None else ExtensionProfile(project.id, user.id, 0, {}, 0)
        persona = await self._default_persona(
            connection, user, bot.persona_id if bot is not None else None
        )
        vault_id = persona.default_vault_id if persona is not None else user.default_vault_id
        if vault_id is None:
            raise CollaborationStoreFormatError("authorized user has no default vault")
        vault = await self._fetch_one(connection, """
            SELECT 1 FROM nanobot_collaboration.collaboration_vaults WHERE id = %s
            """, (vault_id,))
        if vault is None:
            raise CollaborationStoreFormatError("authorized user has no default vault")
        return ConversationScope(
            kind, user.id, project.id, user, project, binding, profile, profile.revision,
            project.workspace_path, suffix, vault_id, persona.id if persona else None,
            bot.id if bot is not None else None, bot,
        )

    async def _default_persona(
        self, connection: _Connection, user: User, persona_id: str | None = None
    ) -> Persona | None:
        candidate_ids = [
            candidate_id
            for candidate_id in (persona_id, user.default_persona_id)
            if candidate_id is not None
        ]
        for candidate_id in dict.fromkeys(candidate_ids):
            row = await self._fetch_one(connection, """
                SELECT id, owner_user_id, name, default_vault_id, instructions, created_at_ms, updated_at_ms
                FROM nanobot_collaboration.collaboration_personas WHERE id = %s
                """, (candidate_id,))
            if row is None:
                continue
            persona = decode_persona_row(row)
            if persona.owner_user_id == user.id:
                return persona
        return None


def _identifier(value: object, field: str) -> str:
    return _key(value, field, limit=128)


def _key(value: object, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > limit or any(ord(char) < 32 for char in result):
        raise CollaborationStoreFormatError(f"invalid {field}")
    return result


def _optional_key(value: object, field: str) -> str | None:
    return _key(value, field) if value is not None else None


def _context_kind(value: object) -> ContextSourceKind:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError("invalid context source kind")
    try:
        return ContextSourceKind(value)
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid context source kind") from exc


def _json_parameter(value: object, column: str) -> dict[str, object]:
    try:
        return thaw_bounded_json(value, column)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise CollaborationStoreFormatError(f"{column} must be an object") from exc


def _thread_id(metadata: Mapping[str, object]) -> str | None:
    for key in ("thread_id", "threadId", "message_thread_id", "messageThreadId", "root_id", "rootId"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _key(value, key)
    return None


def _is_direct(chat_id: str, sender_id: str, metadata: Mapping[str, object]) -> bool:
    if metadata.get("chat_type") == "p2p" or metadata.get("chatType") == "p2p":
        return True
    if str(metadata.get("chat_type") or metadata.get("chatType") or "").lower() == "group":
        return False
    if str(metadata.get("direct") or "").lower() in {"1", "true", "yes"}:
        return True
    if metadata.get("is_group") is True or metadata.get("isGroup") is True:
        return False
    if metadata.get("is_direct") is True or metadata.get("isDirect") is True:
        return True
    for key in ("chat_type", "chatType", "conversation_type", "conversationType"):
        value = metadata.get(key)
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_")
            if normalized in {"group", "group_chat", "groupchat", "channel", "room"}:
                return False
            if normalized in {"direct", "dm", "p2p", "private", "one_to_one"}:
                return True
    return chat_id == sender_id


def _scope_suffix(channel: str, chat_id: str, thread_id: str | None) -> str:
    return "collab-" + sha256("\x00".join(("v1", channel, chat_id, thread_id or "")).encode()).hexdigest()[:24]


def _isolated_scope(
    user_id: str | None,
    default_workspace: str | Path,
    suffix: str,
    user: User | None = None,
    *,
    route_denied: bool = False,
) -> ConversationScope:
    workspace = str(default_workspace).strip()
    if not workspace or len(workspace) > 16_000 or any(ord(char) < 32 for char in workspace):
        raise CollaborationStoreFormatError("invalid workspace_path")
    return ConversationScope(
        ConversationScopeKind.ISOLATED,
        user_id,
        None,
        user,
        None,
        None,
        None,
        0,
        None,
        suffix,
        route_denied=route_denied,
    )
