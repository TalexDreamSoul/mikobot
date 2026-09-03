"""Async adapter for the local JSON collaboration store."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from .models import (
    Bot,
    BotCapabilityProfile,
    BotChannelAssignment,
    BotProjectAssignment,
    BotProjectChannel,
    BotState,
    ContextSource,
    ContextSourceKind,
    ConversationBinding,
    ConversationScope,
    ExtensionProfile,
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    PairingChallenge,
    PairingPurpose,
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


class AsyncLocalCollaborationRepository:
    """Run each complete local-store operation outside the event loop."""

    def __init__(self, store: CollaborationStore | None = None) -> None:
        self._store = store or CollaborationStore()

    @property
    def local_store(self) -> CollaborationStore:
        """Return the local store for the stdio MCP compatibility boundary."""
        return self._store

    async def initialize(self) -> None:
        """The local store initializes lazily within its blocking operations."""

    async def aclose(self) -> None:
        """The local store owns no persistent process-local resources."""

    async def ensure_local_owner(self, default_workspace: str | Path) -> tuple[User, Project]:
        return await asyncio.to_thread(self._store.ensure_local_owner, default_workspace)

    async def create_user(self, display_name: str) -> User:
        return await asyncio.to_thread(self._store.create_user, display_name)

    async def update_user_display_name(self, user_id: str, display_name: str) -> User:
        return await asyncio.to_thread(self._store.update_user_display_name, user_id, display_name)

    async def get_user(self, user_id: str) -> User | None:
        return await asyncio.to_thread(self._store.get_user, user_id)

    async def list_users(self) -> list[User]:
        return await asyncio.to_thread(self._store.list_users)

    async def update_user_default_project(self, user_id: str, project_id: str | None) -> User:
        return await asyncio.to_thread(self._store.update_user_default_project, user_id, project_id)

    async def create_vault(self, user_id: str, name: str, *, kind: VaultKind = VaultKind.PRIVATE) -> Vault:
        return await asyncio.to_thread(self._store.create_vault, user_id, name, kind=kind)

    async def list_vaults(self, user_id: str) -> list[Vault]:
        return await asyncio.to_thread(self._store.list_vaults, user_id)

    async def get_vault(self, actor_user_id: str, vault_id: str) -> Vault | None:
        return await asyncio.to_thread(self._store.get_vault, actor_user_id, vault_id)

    async def update_user_default_vault(self, user_id: str, vault_id: str) -> User:
        return await asyncio.to_thread(self._store.update_user_default_vault, user_id, vault_id)

    async def create_persona(
        self, user_id: str, name: str, default_vault_id: str, *, instructions: str = ""
    ) -> Persona:
        return await asyncio.to_thread(
            self._store.create_persona, user_id, name, default_vault_id, instructions=instructions
        )

    async def list_personas(self, user_id: str) -> list[Persona]:
        return await asyncio.to_thread(self._store.list_personas, user_id)

    async def update_user_default_persona(self, user_id: str, persona_id: str | None) -> User:
        return await asyncio.to_thread(self._store.update_user_default_persona, user_id, persona_id)

    async def create_share_grant(
        self, owner_user_id: str, vault_id: str, grantee_user_id: str, *, resource_type: str,
        resource_id: str | None = None, permission: SharePermission = SharePermission.READ,
        expires_at_ms: int | None = None,
    ) -> ShareGrant:
        return await asyncio.to_thread(
            self._store.create_share_grant, owner_user_id, vault_id, grantee_user_id,
            resource_type=resource_type, resource_id=resource_id, permission=permission,
            expires_at_ms=expires_at_ms,
        )

    async def revoke_share_grant(self, owner_user_id: str, grant_id: str) -> ShareGrant:
        return await asyncio.to_thread(self._store.revoke_share_grant, owner_user_id, grant_id)

    async def authorize_vault(
        self, user_id: str, vault_id: str, permission: SharePermission = SharePermission.READ
    ) -> Vault:
        return await asyncio.to_thread(self._store.authorize_vault, user_id, vault_id, permission)

    async def create_personal_task(
        self, user_id: str, vault_id: str, title: str, *, note: str = "",
        status: TaskStatus = TaskStatus.TODO, priority: int = 0, due_at_ms: int | None = None,
        timezone: str | None = None, recurrence_rule: str | None = None,
        source_type: str = "user", source_ref: str | None = None,
        review_state: TaskReviewState = TaskReviewState.CONFIRMED,
    ) -> PersonalTask:
        return await asyncio.to_thread(
            self._store.create_personal_task, user_id, vault_id, title, note=note, status=status,
            priority=priority, due_at_ms=due_at_ms, timezone=timezone,
            recurrence_rule=recurrence_rule, source_type=source_type, source_ref=source_ref,
            review_state=review_state,
        )

    async def list_personal_tasks(self, user_id: str, vault_id: str) -> list[PersonalTask]:
        return await asyncio.to_thread(self._store.list_personal_tasks, user_id, vault_id)

    async def get_personal_task(self, user_id: str, task_id: str) -> PersonalTask | None:
        return await asyncio.to_thread(self._store.get_personal_task, user_id, task_id)

    async def update_personal_task(
        self, user_id: str, task_id: str, *, title: str | None = None, note: str | None = None,
        status: TaskStatus | None = None, priority: int | None = None, due_at_ms: int | None = None,
        review_state: TaskReviewState | None = None, external_provider: str | None = None,
        external_id: str | None = None, external_version: str | None = None,
    ) -> PersonalTask:
        return await asyncio.to_thread(
            self._store.update_personal_task, user_id, task_id, title=title, note=note, status=status,
            priority=priority, due_at_ms=due_at_ms, review_state=review_state,
            external_provider=external_provider, external_id=external_id,
            external_version=external_version,
        )

    async def delete_personal_task(self, user_id: str, task_id: str) -> bool:
        return await asyncio.to_thread(self._store.delete_personal_task, user_id, task_id)

    async def bind_identity(self, channel: str, sender_id: str, user_id: str) -> UserIdentity:
        return await asyncio.to_thread(self._store.bind_identity, channel, sender_id, user_id)

    async def rebind_identity(self, channel: str, sender_id: str, user_id: str) -> UserIdentity:
        return await asyncio.to_thread(self._store.rebind_identity, channel, sender_id, user_id)

    async def resolve_identity(self, channel: str, sender_id: str) -> User | None:
        return await asyncio.to_thread(self._store.resolve_identity, channel, sender_id)

    async def ensure_identity_user(
        self, channel: str, sender_id: str, default_workspace: str | Path, *, local_owner: bool = False
    ) -> tuple[User, Project]:
        return await asyncio.to_thread(
            self._store.ensure_identity_user, channel, sender_id, default_workspace,
            local_owner=local_owner,
        )

    async def create_organization(self, owner_user_id: str, name: str) -> Organization:
        return await asyncio.to_thread(self._store.create_organization, owner_user_id, name)

    async def get_organization(self, user_id: str, organization_id: str) -> Organization | None:
        return await asyncio.to_thread(self._store.get_organization, user_id, organization_id)

    async def list_organizations(self, user_id: str) -> list[Organization]:
        return await asyncio.to_thread(self._store.list_organizations, user_id)

    async def update_organization(
        self, organization_id: str, actor_user_id: str, *, name: str
    ) -> Organization:
        return await asyncio.to_thread(
            self._store.update_organization, organization_id, actor_user_id, name=name
        )

    async def delete_organization(self, organization_id: str, actor_user_id: str) -> bool:
        return await asyncio.to_thread(self._store.delete_organization, organization_id, actor_user_id)

    async def add_organization_member(
        self, organization_id: str, actor_user_id: str, user_id: str,
        role: OrganizationRole = OrganizationRole.MEMBER,
    ) -> OrganizationMembership:
        return await asyncio.to_thread(
            self._store.add_organization_member, organization_id, actor_user_id, user_id, role
        )

    async def remove_organization_member(
        self, organization_id: str, actor_user_id: str, user_id: str
    ) -> bool:
        return await asyncio.to_thread(
            self._store.remove_organization_member, organization_id, actor_user_id, user_id
        )

    async def list_organization_members(
        self, organization_id: str, user_id: str
    ) -> list[OrganizationMembership]:
        return await asyncio.to_thread(self._store.list_organization_members, organization_id, user_id)


    async def create_bot(
        self, actor_user_id: str, organization_id: str, name: str, *,
        avatar_url: str | None = None, persona_id: str | None = None,
    ) -> Bot:
        return await asyncio.to_thread(
            self._store.create_bot, actor_user_id, organization_id, name,
            avatar_url=avatar_url, persona_id=persona_id,
        )

    async def get_bot(self, actor_user_id: str, bot_id: str) -> Bot | None:
        return await asyncio.to_thread(self._store.get_bot, actor_user_id, bot_id)

    async def list_bots(
        self, actor_user_id: str, *, organization_id: str | None = None
    ) -> list[Bot]:
        return await asyncio.to_thread(
            self._store.list_bots, actor_user_id, organization_id=organization_id
        )

    async def update_bot(
        self, bot_id: str, actor_user_id: str, *, name: str | None = None,
        avatar_url: str | None = None, persona_id: str | None = None,
        state_value: BotState | None = None,
    ) -> Bot:
        return await asyncio.to_thread(
            self._store.update_bot, bot_id, actor_user_id, name=name,
            avatar_url=avatar_url, persona_id=persona_id, state_value=state_value,
        )

    async def delete_bot(self, bot_id: str, actor_user_id: str) -> bool:
        return await asyncio.to_thread(self._store.delete_bot, bot_id, actor_user_id)

    async def update_user_defaults(
        self, user_id: str, *, organization_id: str, bot_id: str,
        project_id: str | None = None,
    ) -> User:
        return await asyncio.to_thread(
            self._store.update_user_defaults, user_id, organization_id=organization_id,
            bot_id=bot_id, project_id=project_id,
        )

    async def list_bot_projects(
        self, actor_user_id: str, bot_id: str
    ) -> list[BotProjectAssignment]:
        return await asyncio.to_thread(self._store.list_bot_projects, actor_user_id, bot_id)

    async def list_bot_channels(
        self, actor_user_id: str, bot_id: str
    ) -> list[BotChannelAssignment]:
        return await asyncio.to_thread(self._store.list_bot_channels, actor_user_id, bot_id)

    async def list_bot_project_channels(
        self, actor_user_id: str, bot_id: str, project_id: str
    ) -> list[BotProjectChannel]:
        return await asyncio.to_thread(
            self._store.list_bot_project_channels, actor_user_id, bot_id, project_id
        )

    async def get_bot_capability_profile(
        self, actor_user_id: str, bot_id: str, *, project_id: str | None = None
    ) -> BotCapabilityProfile:
        return await asyncio.to_thread(
            self._store.get_bot_capability_profile, actor_user_id, bot_id,
            project_id=project_id,
        )

    async def update_bot_capability_profile(
        self, actor_user_id: str, bot_id: str, settings: Mapping[str, object], *,
        project_id: str | None = None, expected_revision: int | None = None,
    ) -> BotCapabilityProfile:
        return await asyncio.to_thread(
            self._store.update_bot_capability_profile, actor_user_id, bot_id, settings,
            project_id=project_id, expected_revision=expected_revision,
        )

    async def create_pairing_challenge(
        self, actor_user_id: str, *, purpose: PairingPurpose, organization_id: str,
        bot_id: str, channel_type: str, instance_id: str, channel_revision: str,
        project_id: str | None = None, ttl_seconds: int = 600,
    ) -> tuple[PairingChallenge, str]:
        return await asyncio.to_thread(
            self._store.create_pairing_challenge, actor_user_id, purpose=purpose,
            organization_id=organization_id, bot_id=bot_id, channel_type=channel_type,
            instance_id=instance_id, channel_revision=channel_revision,
            project_id=project_id, ttl_seconds=ttl_seconds,
        )

    async def get_pairing_challenge(
        self, actor_user_id: str, challenge_id: str
    ) -> PairingChallenge | None:
        return await asyncio.to_thread(
            self._store.get_pairing_challenge, actor_user_id, challenge_id
        )

    async def verify_pairing_challenge(
        self, code: str, *, channel_type: str, instance_id: str,
        channel_revision: str, sender_id: str,
    ) -> PairingChallenge:
        return await asyncio.to_thread(
            self._store.verify_pairing_challenge, code, channel_type=channel_type,
            instance_id=instance_id, channel_revision=channel_revision, sender_id=sender_id,
        )

    async def consume_pairing_challenge(
        self, actor_user_id: str, challenge_id: str, *, channel_revision: str
    ) -> PairingChallenge:
        return await asyncio.to_thread(
            self._store.consume_pairing_challenge, actor_user_id, challenge_id,
            channel_revision=channel_revision,
        )

    async def create_project(
        self,
        owner_user_id: str,
        name: str,
        workspace_path: str | Path,
        *,
        organization_id: str | None = None,
    ) -> Project:
        return await asyncio.to_thread(
            self._store.create_project,
            owner_user_id,
            name,
            workspace_path,
            organization_id=organization_id,
        )

    async def get_project(self, user_id: str, project_id: str) -> Project | None:
        return await asyncio.to_thread(self._store.get_project, user_id, project_id)

    async def list_projects(self, user_id: str) -> list[Project]:
        return await asyncio.to_thread(self._store.list_projects, user_id)

    async def update_project(
        self, project_id: str, actor_user_id: str, *, name: str | None = None,
        workspace_path: str | Path | None = None,
    ) -> Project:
        return await asyncio.to_thread(
            self._store.update_project, project_id, actor_user_id, name=name,
            workspace_path=workspace_path,
        )

    async def delete_project(self, project_id: str, actor_user_id: str) -> bool:
        return await asyncio.to_thread(self._store.delete_project, project_id, actor_user_id)

    async def add_member(
        self, project_id: str, actor_user_id: str, user_id: str,
        role: MembershipRole = MembershipRole.MEMBER,
    ) -> ProjectMembership:
        return await asyncio.to_thread(self._store.add_member, project_id, actor_user_id, user_id, role)

    async def remove_member(self, project_id: str, actor_user_id: str, user_id: str) -> bool:
        return await asyncio.to_thread(self._store.remove_member, project_id, actor_user_id, user_id)

    async def list_members(self, project_id: str, user_id: str) -> list[ProjectMembership]:
        return await asyncio.to_thread(self._store.list_members, project_id, user_id)

    async def create_task_list(
        self, project_id: str, actor_user_id: str, name: str, *, position: int | None = None
    ) -> TaskList:
        return await asyncio.to_thread(
            self._store.create_task_list, project_id, actor_user_id, name, position=position
        )

    async def list_task_lists(self, user_id: str, project_id: str) -> list[TaskList]:
        return await asyncio.to_thread(self._store.list_task_lists, user_id, project_id)

    async def get_task_list(self, user_id: str, task_list_id: str) -> TaskList | None:
        return await asyncio.to_thread(self._store.get_task_list, user_id, task_list_id)

    async def update_task_list(
        self, task_list_id: str, actor_user_id: str, *, name: str | None = None,
        position: int | None = None,
    ) -> TaskList:
        return await asyncio.to_thread(
            self._store.update_task_list, task_list_id, actor_user_id, name=name, position=position
        )

    async def delete_task_list(self, task_list_id: str, actor_user_id: str) -> bool:
        return await asyncio.to_thread(self._store.delete_task_list, task_list_id, actor_user_id)

    async def create_task(
        self, project_id: str, task_list_id: str, actor_user_id: str, title: str, *,
        description: str = "", assignee_user_id: str | None = None,
        status: TaskStatus = TaskStatus.TODO, position: int | None = None,
    ) -> Task:
        return await asyncio.to_thread(
            self._store.create_task, project_id, task_list_id, actor_user_id, title,
            description=description, assignee_user_id=assignee_user_id, status=status,
            position=position,
        )

    async def list_tasks(
        self, user_id: str, project_id: str, *, task_list_id: str | None = None
    ) -> list[Task]:
        return await asyncio.to_thread(self._store.list_tasks, user_id, project_id, task_list_id=task_list_id)

    async def get_task(self, user_id: str, task_id: str) -> Task | None:
        return await asyncio.to_thread(self._store.get_task, user_id, task_id)

    async def update_task(
        self, task_id: str, actor_user_id: str, *, title: str | None = None,
        description: str | None = None, status: TaskStatus | None = None,
        assignee_user_id: str | None | object = ..., position: int | None = None,
    ) -> Task:
        return await asyncio.to_thread(
            self._store.update_task, task_id, actor_user_id, title=title, description=description,
            status=status, assignee_user_id=assignee_user_id, position=position,
        )

    async def delete_task(self, task_id: str, actor_user_id: str) -> bool:
        return await asyncio.to_thread(self._store.delete_task, task_id, actor_user_id)

    async def bind_conversation(
        self, channel: str, conversation_id: str, project_id: str, user_id: str,
        *, thread_id: str | None = None,
    ) -> ConversationBinding:
        return await asyncio.to_thread(
            self._store.bind_conversation, channel, conversation_id, project_id, user_id,
            thread_id=thread_id,
        )

    async def resolve_binding(
        self, channel: str, conversation_id: str, actor_user_id: str, *, thread_id: str | None = None
    ) -> ConversationBinding | None:
        binding = await asyncio.to_thread(
            self._store.resolve_binding, channel, conversation_id, thread_id=thread_id
        )
        if binding is None:
            return None
        project = await asyncio.to_thread(
            self._store.get_project, actor_user_id, binding.project_id
        )
        return binding if project is not None else None

    async def unbind_conversation(
        self, channel: str, conversation_id: str, actor_user_id: str,
        *, thread_id: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._store.unbind_conversation, channel, conversation_id, actor_user_id,
            thread_id=thread_id,
        )

    async def get_extension_profile(self, user_id: str, project_id: str) -> ExtensionProfile:
        return await asyncio.to_thread(self._store.get_extension_profile, user_id, project_id)

    async def update_extension_profile(
        self, user_id: str, project_id: str, settings: Mapping[str, object], *,
        expected_revision: int | None = None,
    ) -> ExtensionProfile:
        return await asyncio.to_thread(
            self._store.update_extension_profile, user_id, project_id, settings,
            expected_revision=expected_revision,
        )

    async def create_context_source(
        self, project_id: str, actor_user_id: str, name: str, kind: ContextSourceKind, *,
        config: Mapping[str, object] | None = None, enabled: bool = True,
    ) -> ContextSource:
        return await asyncio.to_thread(
            self._store.create_context_source, project_id, actor_user_id, name, kind,
            config=config, enabled=enabled,
        )

    async def list_context_sources(self, user_id: str, project_id: str) -> list[ContextSource]:
        return await asyncio.to_thread(self._store.list_context_sources, user_id, project_id)

    async def get_context_source(self, user_id: str, source_id: str) -> ContextSource | None:
        return await asyncio.to_thread(self._store.get_context_source, user_id, source_id)

    async def update_context_source(
        self, source_id: str, actor_user_id: str, *, name: str | None = None,
        kind: ContextSourceKind | None = None, config: Mapping[str, object] | None = None,
        enabled: bool | None = None,
    ) -> ContextSource:
        return await asyncio.to_thread(
            self._store.update_context_source, source_id, actor_user_id, name=name, kind=kind,
            config=config, enabled=enabled,
        )

    async def delete_context_source(self, source_id: str, actor_user_id: str) -> bool:
        return await asyncio.to_thread(self._store.delete_context_source, source_id, actor_user_id)

    async def resolve_scope(
        self, channel: str, sender_id: str, chat_id: str, metadata: Mapping[str, object] | None,
        default_workspace: str | Path,
    ) -> ConversationScope:
        return await asyncio.to_thread(
            self._store.resolve_scope, channel, sender_id, chat_id, metadata, default_workspace
        )
