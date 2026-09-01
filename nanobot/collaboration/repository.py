"""Asynchronous boundary for collaboration persistence backends."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from nanobot.config.schema import CollaborationConfig

from .local_repository import AsyncLocalCollaborationRepository
from .models import (
    ContextSource,
    ContextSourceKind,
    ConversationBinding,
    ConversationScope,
    ExtensionProfile,
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    Persona,
    PersonalTask,
    Project,
    ProjectMembership,
    ShareGrant,
    SharePermission,
    Task,
    TaskList,
    TaskReviewState,
    TaskStatus,
    User,
    UserIdentity,
    Vault,
    VaultKind,
)
from .store import CollaborationStore


@runtime_checkable
class CollaborationRepository(Protocol):
    """Async persistence operations for the collaboration domain."""

    async def initialize(self) -> None: ...

    async def aclose(self) -> None: ...

    async def ensure_local_owner(
        self, default_workspace: str | Path
    ) -> tuple[User, Project]: ...

    async def create_user(self, display_name: str) -> User: ...

    async def update_user_display_name(self, user_id: str, display_name: str) -> User: ...

    async def get_user(self, user_id: str) -> User | None: ...

    async def list_users(self) -> list[User]: ...

    async def update_user_default_project(
        self, user_id: str, project_id: str | None
    ) -> User: ...

    async def create_vault(
        self, user_id: str, name: str, *, kind: VaultKind = VaultKind.PRIVATE
    ) -> Vault: ...

    async def list_vaults(self, user_id: str) -> list[Vault]: ...

    async def get_vault(self, actor_user_id: str, vault_id: str) -> Vault | None: ...

    async def update_user_default_vault(self, user_id: str, vault_id: str) -> User: ...

    async def create_persona(
        self, user_id: str, name: str, default_vault_id: str, *, instructions: str = ""
    ) -> Persona: ...

    async def list_personas(self, user_id: str) -> list[Persona]: ...

    async def update_user_default_persona(
        self, user_id: str, persona_id: str | None
    ) -> User: ...

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
    ) -> ShareGrant: ...

    async def revoke_share_grant(self, owner_user_id: str, grant_id: str) -> ShareGrant: ...

    async def authorize_vault(
        self,
        user_id: str,
        vault_id: str,
        permission: SharePermission = SharePermission.READ,
    ) -> Vault: ...

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
    ) -> PersonalTask: ...

    async def list_personal_tasks(self, user_id: str, vault_id: str) -> list[PersonalTask]: ...

    async def get_personal_task(self, user_id: str, task_id: str) -> PersonalTask | None: ...

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
    ) -> PersonalTask: ...

    async def delete_personal_task(self, user_id: str, task_id: str) -> bool: ...

    async def bind_identity(self, channel: str, sender_id: str, user_id: str) -> UserIdentity: ...

    async def rebind_identity(self, channel: str, sender_id: str, user_id: str) -> UserIdentity: ...

    async def resolve_identity(self, channel: str, sender_id: str) -> User | None: ...

    async def ensure_identity_user(
        self,
        channel: str,
        sender_id: str,
        default_workspace: str | Path,
        *,
        local_owner: bool = False,
    ) -> tuple[User, Project]: ...

    async def create_organization(self, owner_user_id: str, name: str) -> Organization: ...

    async def get_organization(
        self, user_id: str, organization_id: str
    ) -> Organization | None: ...

    async def list_organizations(self, user_id: str) -> list[Organization]: ...

    async def update_organization(
        self, organization_id: str, actor_user_id: str, *, name: str
    ) -> Organization: ...

    async def delete_organization(self, organization_id: str, actor_user_id: str) -> bool: ...

    async def add_organization_member(
        self,
        organization_id: str,
        actor_user_id: str,
        user_id: str,
        role: OrganizationRole = OrganizationRole.MEMBER,
    ) -> OrganizationMembership: ...

    async def remove_organization_member(
        self, organization_id: str, actor_user_id: str, user_id: str
    ) -> bool: ...

    async def list_organization_members(
        self, organization_id: str, user_id: str
    ) -> list[OrganizationMembership]: ...

    async def create_project(
        self,
        owner_user_id: str,
        name: str,
        workspace_path: str | Path,
        *,
        organization_id: str | None = None,
    ) -> Project: ...

    async def get_project(self, user_id: str, project_id: str) -> Project | None: ...

    async def list_projects(self, user_id: str) -> list[Project]: ...

    async def update_project(
        self,
        project_id: str,
        actor_user_id: str,
        *,
        name: str | None = None,
        workspace_path: str | Path | None = None,
    ) -> Project: ...

    async def delete_project(self, project_id: str, actor_user_id: str) -> bool: ...

    async def add_member(
        self,
        project_id: str,
        actor_user_id: str,
        user_id: str,
        role: MembershipRole = MembershipRole.MEMBER,
    ) -> ProjectMembership: ...

    async def remove_member(self, project_id: str, actor_user_id: str, user_id: str) -> bool: ...

    async def list_members(self, project_id: str, user_id: str) -> list[ProjectMembership]: ...

    async def create_task_list(
        self, project_id: str, actor_user_id: str, name: str, *, position: int | None = None
    ) -> TaskList: ...

    async def list_task_lists(self, user_id: str, project_id: str) -> list[TaskList]: ...

    async def get_task_list(self, user_id: str, task_list_id: str) -> TaskList | None: ...

    async def update_task_list(
        self,
        task_list_id: str,
        actor_user_id: str,
        *,
        name: str | None = None,
        position: int | None = None,
    ) -> TaskList: ...

    async def delete_task_list(self, task_list_id: str, actor_user_id: str) -> bool: ...

    async def create_task(
        self,
        project_id: str,
        task_list_id: str,
        actor_user_id: str,
        title: str,
        *,
        description: str = "",
        assignee_user_id: str | None = None,
        status: TaskStatus = TaskStatus.TODO,
        position: int | None = None,
    ) -> Task: ...

    async def list_tasks(
        self, user_id: str, project_id: str, *, task_list_id: str | None = None
    ) -> list[Task]: ...

    async def get_task(self, user_id: str, task_id: str) -> Task | None: ...

    async def update_task(
        self,
        task_id: str,
        actor_user_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: TaskStatus | None = None,
        assignee_user_id: str | None | object = ...,
        position: int | None = None,
    ) -> Task: ...

    async def delete_task(self, task_id: str, actor_user_id: str) -> bool: ...

    async def bind_conversation(
        self,
        channel: str,
        conversation_id: str,
        project_id: str,
        user_id: str,
        *,
        thread_id: str | None = None,
    ) -> ConversationBinding: ...

    async def resolve_binding(
        self, channel: str, conversation_id: str, actor_user_id: str, *, thread_id: str | None = None
    ) -> ConversationBinding | None: ...

    async def unbind_conversation(
        self,
        channel: str,
        conversation_id: str,
        actor_user_id: str,
        *,
        thread_id: str | None = None,
    ) -> bool: ...

    async def get_extension_profile(self, user_id: str, project_id: str) -> ExtensionProfile: ...

    async def update_extension_profile(
        self,
        user_id: str,
        project_id: str,
        settings: Mapping[str, object],
        *,
        expected_revision: int | None = None,
    ) -> ExtensionProfile: ...

    async def create_context_source(
        self,
        project_id: str,
        actor_user_id: str,
        name: str,
        kind: ContextSourceKind,
        *,
        config: Mapping[str, object] | None = None,
        enabled: bool = True,
    ) -> ContextSource: ...

    async def list_context_sources(self, user_id: str, project_id: str) -> list[ContextSource]: ...

    async def get_context_source(self, user_id: str, source_id: str) -> ContextSource | None: ...

    async def update_context_source(
        self,
        source_id: str,
        actor_user_id: str,
        *,
        name: str | None = None,
        kind: ContextSourceKind | None = None,
        config: Mapping[str, object] | None = None,
        enabled: bool | None = None,
    ) -> ContextSource: ...

    async def delete_context_source(self, source_id: str, actor_user_id: str) -> bool: ...

    async def resolve_scope(
        self,
        channel: str,
        sender_id: str,
        chat_id: str,
        metadata: Mapping[str, object] | None,
        default_workspace: str | Path,
    ) -> ConversationScope: ...


def build_collaboration_repository(
    config: CollaborationConfig,
    local_store: CollaborationStore | None = None,
) -> CollaborationRepository:
    """Create the configured asynchronous collaboration persistence backend."""
    if config.backend == "local":
        return AsyncLocalCollaborationRepository(local_store)

    from .postgres.repository import PostgresCollaborationRepository
    from .postgres.session import PostgresSession

    return PostgresCollaborationRepository(PostgresSession(config))
