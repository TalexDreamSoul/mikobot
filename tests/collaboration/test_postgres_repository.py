from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection
from psycopg.errors import InsufficientPrivilege

from nanobot.collaboration import (
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    ContextSourceKind,
    MembershipRole,
    OrganizationRole,
    SharePermission,
    TaskStatus,
)
from nanobot.collaboration.postgres.migrations import migrate
from nanobot.collaboration.postgres.repository import PostgresCollaborationRepository
from nanobot.collaboration.postgres.session import PostgresSession
from nanobot.config.schema import CollaborationConfig


@pytest.fixture
def postgres_test_dsns() -> tuple[str, str]:
    """Require separate restricted-runtime and migration-admin PostgreSQL roles."""
    runtime_dsn = os.getenv("NANOBOT_TEST_POSTGRES_DSN")
    migration_dsn = os.getenv("NANOBOT_TEST_POSTGRES_MIGRATION_DSN")
    if not runtime_dsn or not migration_dsn:
        pytest.skip(
            "requires NANOBOT_TEST_POSTGRES_DSN and "
            "NANOBOT_TEST_POSTGRES_MIGRATION_DSN"
        )
    return runtime_dsn, migration_dsn


@dataclass(frozen=True, slots=True)
class PostgresRepositoryTestContext:
    repository: PostgresCollaborationRepository
    session: PostgresSession
    runtime_dsn: str
    migration_dsn: str


@pytest_asyncio.fixture
async def postgres_repository(
    postgres_test_dsns: tuple[str, str],
) -> AsyncIterator[PostgresRepositoryTestContext]:
    """Migrate as the admin role, then exercise repositories as the restricted role."""
    runtime_dsn, migration_dsn = postgres_test_dsns

    config = CollaborationConfig(
        backend="postgres",
        postgres_dsn=runtime_dsn,
        postgres_migration_dsn=migration_dsn,
        auto_migrate=True,
        postgres_min_pool_size=1,
        postgres_max_pool_size=8,
    )
    session = PostgresSession(config)
    repository = PostgresCollaborationRepository(session)
    await repository.initialize()
    try:
        yield PostgresRepositoryTestContext(repository, session, runtime_dsn, migration_dsn)
    finally:
        await repository.aclose()


def _name(prefix: str) -> str:
    return f"postgres-{prefix}-{uuid4().hex}"

async def _runtime_role(runtime_dsn: str) -> str:
    async with await AsyncConnection.connect(runtime_dsn) as connection:
        row = await (await connection.execute("SELECT current_user")).fetchone()
    assert row is not None
    return str(row[0])


async def test_postgres_rejects_migration_dsn_for_runtime_role(
    postgres_test_dsns: tuple[str, str],
) -> None:
    """A textually distinct migration DSN is rejected when it authenticates as the runtime role."""
    runtime_dsn, _migration_dsn = postgres_test_dsns
    same_role_migration_dsn = (
        f"{runtime_dsn}{'&' if '?' in runtime_dsn else '?'}"
        f"application_name={_name('same-role-migration')}"
    )
    session = PostgresSession(
        CollaborationConfig(
            backend="postgres",
            postgres_dsn=runtime_dsn,
            postgres_migration_dsn=same_role_migration_dsn,
            auto_migrate=True,
        )
    )
    repository = PostgresCollaborationRepository(session)
    try:
        with pytest.raises(RuntimeError):
            await repository.initialize()
        assert session._pool is None
    finally:
        await repository.aclose()
        await repository.aclose()
    assert session._pool is None


async def _shared_project(
    repository: PostgresCollaborationRepository,
) -> tuple[object, object, object, object]:
    owner = await repository.create_user(_name("owner"))
    member = await repository.create_user(_name("member"))
    organization = await repository.create_organization(owner.id, _name("organization"))
    await repository.add_organization_member(
        organization.id, owner.id, member.id, OrganizationRole.MEMBER
    )
    project = await repository.create_project(
        owner.id, _name("project"), Path("/tmp") / _name("workspace"), organization_id=organization.id
    )
    await repository.add_member(project.id, owner.id, member.id, MembershipRole.MEMBER)
    return owner, member, organization, project


async def test_postgres_identity_provisioning_is_concurrent_and_idempotent(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """The same external identity concurrently resolves to one personal owner/project/vault."""
    repository = postgres_repository.repository
    channel, sender = _name("channel"), _name("sender")
    pairs = await asyncio.gather(
        *(
            repository.ensure_identity_user(channel, sender, Path("/tmp") / _name("identity"))
            for _ in range(8)
        )
    )
    users = {user.id for user, _project in pairs}
    projects = {project.id for _user, project in pairs}
    assert len(users) == 1
    assert len(projects) == 1

    user, project = pairs[0]
    assert user.default_project_id == project.id
    assert user.default_vault_id is not None
    assert await repository.resolve_identity(channel, sender) == user
    organizations = await repository.list_organizations(user.id)
    assert [(membership.user_id, membership.role) for membership in await repository.list_organization_members(organizations[0].id, user.id)] == [
        (user.id, OrganizationRole.OWNER)
    ]
    assert [vault.id for vault in await repository.list_vaults(user.id)] == [user.default_vault_id]


async def test_postgres_organization_roles_and_last_owner_are_enforced(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """Only owners/admins manage a roster, and a shared organization always retains an owner."""
    repository = postgres_repository.repository
    owner = await repository.create_user(_name("owner"))
    admin = await repository.create_user(_name("admin"))
    member = await repository.create_user(_name("member"))
    successor = await repository.create_user(_name("successor"))
    organization = await repository.create_organization(owner.id, _name("organization"))

    assert (await repository.add_organization_member(organization.id, owner.id, admin.id, OrganizationRole.ADMIN)).role is OrganizationRole.ADMIN
    assert (await repository.add_organization_member(organization.id, admin.id, member.id)).role is OrganizationRole.MEMBER
    with pytest.raises(CollaborationPermissionError, match="owner role"):
        await repository.add_organization_member(organization.id, member.id, successor.id)
    with pytest.raises(CollaborationPermissionError, match="owner role"):
        await repository.add_organization_member(
            organization.id, admin.id, successor.id, OrganizationRole.ADMIN
        )
    with pytest.raises(CollaborationConflictError, match="retain an owner"):
        await repository.remove_organization_member(organization.id, owner.id, owner.id)

    assert (await repository.add_organization_member(organization.id, owner.id, successor.id, OrganizationRole.OWNER)).role is OrganizationRole.OWNER
    assert await repository.remove_organization_member(organization.id, owner.id, owner.id)
    assert {entry.user_id for entry in await repository.list_organization_members(organization.id, successor.id)} == {
        admin.id,
        member.id,
        successor.id,
    }


async def test_postgres_concurrent_owner_changes_retain_one_organization_and_project_owner(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """Concurrent owner demotions and removals serialize so each aggregate keeps exactly one owner."""
    repository = postgres_repository.repository

    def winning_index(outcomes: list[object]) -> int:
        successful = [
            index for index, outcome in enumerate(outcomes) if not isinstance(outcome, BaseException)
        ]
        assert len(successful) == 1
        return successful[0]

    first_owner = await repository.create_user(_name("organization-owner"))
    second_owner = await repository.create_user(_name("organization-owner"))
    organization = await repository.create_organization(first_owner.id, _name("organization"))
    await repository.add_organization_member(
        organization.id, first_owner.id, second_owner.id, OrganizationRole.OWNER
    )
    organization_demotions = list(
        await asyncio.gather(
            repository.add_organization_member(
                organization.id, first_owner.id, second_owner.id, OrganizationRole.MEMBER
            ),
            repository.add_organization_member(
                organization.id, second_owner.id, first_owner.id, OrganizationRole.MEMBER
            ),
            return_exceptions=True,
        )
    )
    organization_winner = (first_owner, second_owner)[winning_index(organization_demotions)]
    organization_loser = second_owner if organization_winner is first_owner else first_owner
    assert sum(
        membership.role is OrganizationRole.OWNER
        for membership in await repository.list_organization_members(
            organization.id, organization_winner.id
        )
    ) == 1
    await repository.add_organization_member(
        organization.id, organization_winner.id, organization_loser.id, OrganizationRole.OWNER
    )
    organization_removals = list(
        await asyncio.gather(
            repository.remove_organization_member(
                organization.id, first_owner.id, second_owner.id
            ),
            repository.remove_organization_member(
                organization.id, second_owner.id, first_owner.id
            ),
            return_exceptions=True,
        )
    )
    organization_winner = (first_owner, second_owner)[winning_index(organization_removals)]
    assert sum(
        membership.role is OrganizationRole.OWNER
        for membership in await repository.list_organization_members(
            organization.id, organization_winner.id
        )
    ) == 1

    project_owner = await repository.create_user(_name("project-owner"))
    project_peer = await repository.create_user(_name("project-owner"))
    project_organization = await repository.create_organization(
        project_owner.id, _name("project-organization")
    )
    await repository.add_organization_member(
        project_organization.id, project_owner.id, project_peer.id, OrganizationRole.OWNER
    )
    project = await repository.create_project(
        project_owner.id,
        _name("project"),
        Path("/tmp") / _name("project-workspace"),
        organization_id=project_organization.id,
    )
    await repository.add_member(project.id, project_owner.id, project_peer.id, MembershipRole.OWNER)
    project_demotions = list(
        await asyncio.gather(
            repository.add_member(
                project.id, project_owner.id, project_peer.id, MembershipRole.MEMBER
            ),
            repository.add_member(
                project.id, project_peer.id, project_owner.id, MembershipRole.MEMBER
            ),
            return_exceptions=True,
        )
    )
    project_winner = (project_owner, project_peer)[winning_index(project_demotions)]
    project_loser = project_peer if project_winner is project_owner else project_owner
    assert sum(
        membership.role is MembershipRole.OWNER
        for membership in await repository.list_members(project.id, project_winner.id)
    ) == 1
    await repository.add_member(project.id, project_winner.id, project_loser.id, MembershipRole.OWNER)
    project_removals = list(
        await asyncio.gather(
            repository.remove_member(project.id, project_owner.id, project_peer.id),
            repository.remove_member(project.id, project_peer.id, project_owner.id),
            return_exceptions=True,
        )
    )
    project_winner = (project_owner, project_peer)[winning_index(project_removals)]
    assert sum(
        membership.role is MembershipRole.OWNER
        for membership in await repository.list_members(project.id, project_winner.id)
    ) == 1


async def test_postgres_project_task_context_binding_and_profile_crud(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """Exact project members can persist and mutate all project-scoped collaboration aggregates."""
    repository = postgres_repository.repository
    owner, member, organization, project = await _shared_project(repository)

    renamed_name = _name("renamed")
    renamed = await repository.update_project(project.id, owner.id, name=renamed_name)
    assert renamed.name == renamed_name
    task_list = await repository.create_task_list(project.id, member.id, _name("list"))
    updated_list_name = _name("list-update")
    assert (await repository.update_task_list(task_list.id, owner.id, name=updated_list_name)).name == updated_list_name
    task = await repository.create_task(project.id, task_list.id, member.id, _name("task"))
    updated_task_title = _name("task-update")
    updated_task = await repository.update_task(
        task.id, owner.id, title=updated_task_title, status=TaskStatus.DONE
    )
    assert (updated_task.title, updated_task.status) == (updated_task_title, TaskStatus.DONE)
    assert await repository.delete_task(task.id, member.id)
    assert await repository.delete_task_list(task_list.id, owner.id)

    profile = await repository.update_extension_profile(member.id, project.id, {"tool": "enabled"})
    assert (await repository.get_extension_profile(member.id, project.id)).settings == {"tool": "enabled"}
    assert (await repository.update_extension_profile(member.id, project.id, {"tool": "disabled"}, expected_revision=profile.revision)).settings == {"tool": "disabled"}

    source = await repository.create_context_source(
        project.id, member.id, _name("source"), ContextSourceKind.SKILL, config={"path": "skill.py"}
    )
    assert (await repository.update_context_source(source.id, owner.id, enabled=False)).enabled is False
    assert await repository.get_context_source(member.id, source.id) is not None
    assert await repository.delete_context_source(source.id, member.id)

    binding = await repository.bind_conversation(
        _name("channel"), _name("conversation"), project.id, member.id, thread_id="thread"
    )
    foreign_owner = await repository.create_user(_name("binding-foreign-owner"))
    foreign_project = await repository.create_project(
        foreign_owner.id,
        _name("binding-foreign-project"),
        Path("/tmp") / _name("binding-foreign-workspace"),
    )
    with pytest.raises(CollaborationConflictError):
        await repository.bind_conversation(
            binding.channel,
            binding.conversation_id,
            foreign_project.id,
            foreign_owner.id,
            thread_id="thread",
        )
    nonmember = await repository.create_user(_name("binding-nonmember"))
    await repository.add_organization_member(
        organization.id, owner.id, nonmember.id, OrganizationRole.MEMBER
    )
    assert await repository.resolve_binding(
        binding.channel, binding.conversation_id, member.id, thread_id="thread"
    ) == binding
    assert await repository.resolve_binding(
        binding.channel, binding.conversation_id, owner.id, thread_id="thread"
    ) == binding
    assert await repository.resolve_binding(
        binding.channel, binding.conversation_id, nonmember.id, thread_id="thread"
    ) is None
    assert await repository.unbind_conversation(
        binding.channel, binding.conversation_id, owner.id, thread_id="thread"
    )
    assert await repository.resolve_binding(
        binding.channel, binding.conversation_id, member.id, thread_id="thread"
    ) is None

    assert await repository.delete_project(project.id, owner.id)
    assert await repository.get_project(owner.id, project.id) is None


async def test_postgres_same_organization_nonmember_cannot_access_project_or_profile(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """Organization membership never substitutes for exact project membership."""
    repository = postgres_repository.repository
    owner, member, organization, project = await _shared_project(repository)
    nonmember = await repository.create_user(_name("nonmember"))
    await repository.add_organization_member(
        organization.id, owner.id, nonmember.id, OrganizationRole.MEMBER
    )
    task_list = await repository.create_task_list(project.id, member.id, _name("list"))
    task = await repository.create_task(project.id, task_list.id, member.id, _name("task"))

    assert await repository.get_project(nonmember.id, project.id) is None
    assert await repository.get_task(nonmember.id, task.id) is None
    with pytest.raises(CollaborationNotFoundError, match="project not found"):
        await repository.list_tasks(nonmember.id, project.id)
    with pytest.raises(CollaborationPermissionError, match="project membership"):
        await repository.get_extension_profile(nonmember.id, project.id)


async def test_postgres_private_task_grants_are_exact_read_only_expiring_and_revocable(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """A personal-task grant authorizes only its resource, only until expiry or revocation, and only at its permission level."""
    repository = postgres_repository.repository
    owner = await repository.create_user(_name("owner"))
    reader = await repository.create_user(_name("reader"))
    assert owner.default_vault_id is not None
    owner_organization = (await repository.list_organizations(owner.id))[0]
    await repository.add_organization_member(
        owner_organization.id, owner.id, reader.id, OrganizationRole.MEMBER
    )
    shared = await repository.create_personal_task(owner.id, owner.default_vault_id, _name("shared"))
    private = await repository.create_personal_task(owner.id, owner.default_vault_id, _name("private"))

    assert await repository.get_personal_task(reader.id, shared.id) is None
    read_grant = await repository.create_share_grant(
        owner.id,
        owner.default_vault_id,
        reader.id,
        resource_type="personal_task",
        resource_id=shared.id,
        permission=SharePermission.READ,
    )
    assert (await repository.get_personal_task(reader.id, shared.id)).id == shared.id
    assert await repository.get_personal_task(reader.id, private.id) is None
    with pytest.raises(CollaborationPermissionError, match="not authorized"):
        await repository.update_personal_task(reader.id, shared.id, title=_name("forbidden"))

    await repository.revoke_share_grant(owner.id, read_grant.id)
    assert await repository.get_personal_task(reader.id, shared.id) is None
    await repository.create_share_grant(
        owner.id,
        owner.default_vault_id,
        reader.id,
        resource_type="personal_task",
        resource_id=shared.id,
        permission=SharePermission.READ,
        expires_at_ms=0,
    )
    assert await repository.get_personal_task(reader.id, shared.id) is None

    collaborate_grant = await repository.create_share_grant(
        owner.id,
        owner.default_vault_id,
        reader.id,
        resource_type="personal_task",
        resource_id=shared.id,
        permission=SharePermission.COLLABORATE,
    )
    collaborated_title = _name("collaborated")
    assert (await repository.update_personal_task(reader.id, shared.id, title=collaborated_title)).title == collaborated_title
    await repository.revoke_share_grant(owner.id, collaborate_grant.id)
    assert await repository.get_personal_task(reader.id, shared.id) is None


async def test_postgres_extension_profiles_are_isolated_except_for_exact_project_owners(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """Exact project owners administer member profiles, while ordinary members see and change only their own."""
    repository = postgres_repository.repository
    owner, member, organization, project = await _shared_project(repository)
    other_member = await repository.create_user(_name("other-member"))
    await repository.add_organization_member(
        organization.id, owner.id, other_member.id, OrganizationRole.MEMBER
    )
    await repository.add_member(project.id, owner.id, other_member.id, MembershipRole.MEMBER)
    await repository.update_extension_profile(owner.id, project.id, {"visibility": "owner"})
    await repository.update_extension_profile(member.id, project.id, {"visibility": "member"})
    await repository.update_extension_profile(other_member.id, project.id, {"visibility": "other"})

    async with postgres_repository.session.transaction(owner.id) as connection:
        cursor = await connection.execute(
            "SELECT user_id FROM nanobot_collaboration.collaboration_extension_profiles "
            "WHERE project_id = %s AND user_id = %s",
            (project.id, member.id),
        )
        assert await cursor.fetchall() == [(member.id,)]
        cursor = await connection.execute(
            "UPDATE nanobot_collaboration.collaboration_extension_profiles "
            "SET settings = '{\"visibility\": \"owner-managed\"}'::jsonb "
            "WHERE project_id = %s AND user_id = %s RETURNING user_id",
            (project.id, member.id),
        )
        assert await cursor.fetchall() == [(member.id,)]

    async with postgres_repository.session.transaction(member.id) as connection:
        cursor = await connection.execute(
            "SELECT user_id FROM nanobot_collaboration.collaboration_extension_profiles "
            "WHERE project_id = %s AND user_id IN (%s, %s)",
            (project.id, owner.id, other_member.id),
        )
        assert await cursor.fetchall() == []
        cursor = await connection.execute(
            "UPDATE nanobot_collaboration.collaboration_extension_profiles "
            "SET settings = '{\"visibility\": \"forged\"}'::jsonb "
            "WHERE project_id = %s AND user_id IN (%s, %s) RETURNING user_id",
            (project.id, owner.id, other_member.id),
        )
        assert await cursor.fetchall() == []

    assert (await repository.get_extension_profile(owner.id, project.id)).settings == {"visibility": "owner"}
    assert (await repository.get_extension_profile(member.id, project.id)).settings == {
        "visibility": "owner-managed"
    }
    assert (await repository.get_extension_profile(other_member.id, project.id)).settings == {
        "visibility": "other"
    }


async def test_postgres_runtime_rls_hides_foreign_rows_and_denies_internal_edge_writes(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """The runtime role sees no foreign project rows and cannot alter authorization projections."""
    repository = postgres_repository.repository
    owner, _member, _organization, project = await _shared_project(repository)
    foreign_owner = await repository.create_user(_name("foreign-owner"))
    foreign_project = await repository.create_project(
        foreign_owner.id, _name("foreign-project"), Path("/tmp") / _name("foreign-workspace")
    )

    async with postgres_repository.session.transaction(owner.id) as connection:
        cursor = await connection.execute(
            "SELECT id FROM nanobot_collaboration.collaboration_projects WHERE id = %s",
            (foreign_project.id,),
        )
        assert await cursor.fetchall() == []
        cursor = await connection.execute(
            "SELECT id FROM nanobot_collaboration.collaboration_projects WHERE id = %s",
            (project.id,),
        )
        assert len(await cursor.fetchall()) == 1

    async with postgres_repository.session.transaction(owner.id) as connection:
        with pytest.raises(InsufficientPrivilege):
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO nanobot_collaboration.organization_role_edges "
                    "(organization_id, user_id, role) VALUES (%s, %s, 'owner')",
                    (_name("forged-org"), owner.id),
                )
        with pytest.raises(InsufficientPrivilege):
            async with connection.transaction():
                await connection.execute(
                    "UPDATE nanobot_collaboration.project_role_edges SET role = 'owner' "
                    "WHERE organization_id = %s",
                    (_name("forged-org"),),
                )


async def test_postgres_catalog_separates_runtime_owner_and_nologin_policy_owner(
    postgres_repository: PostgresRepositoryTestContext,
) -> None:
    """RLS objects belong to an administrative NOLOGIN policy owner, not the runtime role."""
    runtime_dsn = postgres_repository.runtime_dsn
    async with await AsyncConnection.connect(runtime_dsn) as runtime_connection:
        runtime_role = (await (await runtime_connection.execute("SELECT current_user")).fetchone())[0]
    async with await AsyncConnection.connect(postgres_repository.migration_dsn) as migration_connection:
        table_owners = await (
            await migration_connection.execute(
                "SELECT DISTINCT pg_catalog.pg_get_userbyid(class.relowner) "
                "FROM pg_catalog.pg_class AS class "
                "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = class.relnamespace "
                "WHERE namespace.nspname = 'nanobot_collaboration' AND class.relkind = 'r'"
            )
        ).fetchall()
        policy_owners = await (
            await migration_connection.execute(
                "SELECT DISTINCT role.rolname, role.rolcanlogin "
                "FROM pg_catalog.pg_proc AS procedure "
                "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace "
                "JOIN pg_catalog.pg_roles AS role ON role.oid = procedure.proowner "
                "WHERE namespace.nspname = 'nanobot_collaboration' "
                "AND procedure.proname LIKE 'nanobot_%'"
            )
        ).fetchall()

    assert table_owners and {row[0] for row in table_owners}.isdisjoint({runtime_role})
    assert policy_owners and all(not row[1] for row in policy_owners)


async def test_postgres_migrations_are_idempotent(postgres_repository: PostgresRepositoryTestContext) -> None:
    """Concurrent migration callers serialize under the advisory lock and leave one row per version."""
    async def apply_migrations() -> None:
        async with await AsyncConnection.connect(postgres_repository.migration_dsn) as connection:
            await migrate(connection, await _runtime_role(postgres_repository.runtime_dsn))

    await asyncio.gather(*(apply_migrations() for _ in range(3)))
    async with await AsyncConnection.connect(postgres_repository.migration_dsn) as connection:
        versions = await (
            await connection.execute(
                "SELECT version, count(*) FROM nanobot_collaboration.collaboration_schema_migrations "
                "GROUP BY version ORDER BY version"
            )
        ).fetchall()
    assert versions and all(count == 1 for _version, count in versions)
