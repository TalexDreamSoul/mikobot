"""Transactional, advisory-lock protected PostgreSQL schema migrations."""
from __future__ import annotations

from typing import LiteralString, cast

from psycopg import AsyncConnection, sql

from nanobot.collaboration.postgres.ddl import MIGRATIONS, MIGRATIONS_TABLE_DDL

_MIGRATION_LOCK_NAME = "nanobot.collaboration.schema"
_POLICY_OWNER = "nanobot_collaboration_policy_owner"
_POLICY_OWNER_DDL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'nanobot_collaboration_policy_owner'
    ) THEN
        CREATE ROLE nanobot_collaboration_policy_owner NOLOGIN;
    END IF;
END
$$;
"""
_RUNTIME_BUSINESS_GRANTS = sql.SQL("""
GRANT USAGE ON SCHEMA nanobot_collaboration TO {role};
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    nanobot_collaboration.collaboration_users,
    nanobot_collaboration.collaboration_organizations,
    nanobot_collaboration.collaboration_organization_memberships,
    nanobot_collaboration.collaboration_identities,
    nanobot_collaboration.collaboration_vaults,
    nanobot_collaboration.collaboration_personas,
    nanobot_collaboration.collaboration_share_grants,
    nanobot_collaboration.collaboration_projects,
    nanobot_collaboration.collaboration_project_memberships,
    nanobot_collaboration.collaboration_task_lists,
    nanobot_collaboration.collaboration_project_tasks,
    nanobot_collaboration.collaboration_personal_tasks,
    nanobot_collaboration.collaboration_conversation_bindings,
    nanobot_collaboration.collaboration_extension_profiles,
    nanobot_collaboration.collaboration_context_sources
TO {role};
""")
_RUNTIME_HELPER_GRANTS = sql.SQL("""
GRANT EXECUTE ON FUNCTION
    nanobot_collaboration.nanobot_current_user_id(),
    nanobot_collaboration.nanobot_has_organization_role(varchar, varchar[]),
    nanobot_collaboration.nanobot_has_project_role(varchar, varchar, varchar[]),
    nanobot_collaboration.nanobot_is_organization_creator(varchar),
    nanobot_collaboration.nanobot_is_project_creator(varchar, varchar),
    nanobot_collaboration.nanobot_is_vault_owner(varchar, varchar),
    nanobot_collaboration.nanobot_can_access_vault_resource(varchar, varchar, varchar, varchar, boolean)
TO {role};
""")


async def migrate(
    connection: AsyncConnection[tuple[object, ...]], runtime_role: str
) -> None:
    """Apply schema changes and grant only runtime-safe privileges to ``runtime_role``."""
    async with connection.transaction():
        await connection.execute("SET LOCAL search_path TO pg_catalog")
        await connection.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 0))",
            (_MIGRATION_LOCK_NAME,),
        )
        cursor = await connection.execute("SELECT current_user")
        row = await cursor.fetchone()
        assert row is not None
        migration_role = cast(str, row[0])
        if migration_role == runtime_role:
            raise RuntimeError("PostgreSQL migration and runtime roles must differ")
        await connection.execute(cast(LiteralString, _POLICY_OWNER_DDL))
        runtime_cursor = await connection.execute(
            """
            SELECT role.rolsuper,
                   role.rolbypassrls,
                   pg_catalog.pg_has_role(%s, %s, 'MEMBER')
            FROM pg_catalog.pg_roles AS role
            WHERE role.rolname = %s
            """,
            (runtime_role, _POLICY_OWNER, runtime_role),
        )
        runtime_row = await runtime_cursor.fetchone()
        if (
            runtime_row is None
            or runtime_row[0] is not False
            or runtime_row[1] is not False
            or runtime_row[2] is not False
        ):
            raise RuntimeError("PostgreSQL runtime role cannot bypass collaboration RLS")
        membership_cursor = await connection.execute(
            "SELECT pg_catalog.pg_has_role(current_user, %s, 'MEMBER')",
            (_POLICY_OWNER,),
        )
        membership_row = await membership_cursor.fetchone()
        if membership_row is None or membership_row[0] is not True:
            await connection.execute(
                sql.SQL("GRANT nanobot_collaboration_policy_owner TO {migration_role}").format(
                    migration_role=sql.Identifier(migration_role)
                )
            )
        await connection.execute(cast(LiteralString, MIGRATIONS_TABLE_DDL))
        rows = await connection.execute(
            "SELECT version FROM nanobot_collaboration.collaboration_schema_migrations ORDER BY version"
        )
        applied = {cast(int, row[0]) for row in await rows.fetchall()}
        for version, ddl in MIGRATIONS:
            if version in applied:
                continue
            await connection.execute(cast(LiteralString, ddl))
            await connection.execute(
                "INSERT INTO nanobot_collaboration.collaboration_schema_migrations (version) VALUES (%s)",
                (version,),
            )
            applied.add(version)
        await connection.execute(
            _RUNTIME_BUSINESS_GRANTS.format(role=sql.Identifier(runtime_role))
        )
        await connection.execute(
            sql.SQL("SET LOCAL ROLE {policy_owner}").format(
                policy_owner=sql.Identifier(_POLICY_OWNER)
            )
        )
        await connection.execute(
            _RUNTIME_HELPER_GRANTS.format(role=sql.Identifier(runtime_role))
        )
        await connection.execute("RESET ROLE")
