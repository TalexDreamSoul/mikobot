"""Async adapter for the local JSON collaboration store."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

from .models import (
    ChannelAssignment,
    ChannelProvision,
    ConversationBinding,
    ConversationScope,
    MembershipRole,
    PairingChallenge,
    Project,
    ProjectMembership,
    User,
    UserIdentity,
)
from .store import CollaborationStore


class AsyncLocalCollaborationRepository:
    """Run each complete local-store operation outside the event loop."""

    def __init__(self, store: CollaborationStore | None = None) -> None:
        self._store = store or CollaborationStore()

    @property
    def local_store(self) -> CollaborationStore:
        """Return the synchronous store for callers that already run off-loop."""
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

    async def update_user_admin(self, user_id: str, is_admin: bool) -> User:
        return await asyncio.to_thread(self._store.update_user_admin, user_id, is_admin)

    async def get_user(self, user_id: str) -> User | None:
        return await asyncio.to_thread(self._store.get_user, user_id)

    async def list_users(self) -> list[User]:
        return await asyncio.to_thread(self._store.list_users)

    async def update_user_default_project(self, user_id: str, project_id: str | None) -> User:
        return await asyncio.to_thread(self._store.update_user_default_project, user_id, project_id)

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

    async def create_project(
        self, owner_user_id: str, name: str, workspace_path: str | Path
    ) -> Project:
        return await asyncio.to_thread(
            self._store.create_project, owner_user_id, name, workspace_path
        )

    async def get_project(self, user_id: str, project_id: str) -> Project | None:
        return await asyncio.to_thread(self._store.get_project, user_id, project_id)

    async def list_projects(self, user_id: str) -> list[Project]:
        return await asyncio.to_thread(self._store.list_projects, user_id)

    async def list_all_projects(self, actor_user_id: str) -> list[Project]:
        return await asyncio.to_thread(self._store.list_all_projects, actor_user_id)

    async def manageable_project_ids(self, actor_user_id: str) -> list[str]:
        return await asyncio.to_thread(self._store.manageable_project_ids, actor_user_id)

    async def update_project(
        self,
        project_id: str,
        actor_user_id: str,
        *,
        name: str | None = None,
        workspace_path: str | Path | None = None,
        allowed_skills: Sequence[str] | None | object = ...,
        allowed_mcp_servers: Sequence[str] | None | object = ...,
    ) -> Project:
        return await asyncio.to_thread(
            self._store.update_project, project_id, actor_user_id, name=name,
            workspace_path=workspace_path, allowed_skills=allowed_skills,
            allowed_mcp_servers=allowed_mcp_servers,
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

    async def record_channel_provision(
        self, actor_user_id: str, *, channel_type: str, instance_id: str
    ) -> ChannelProvision:
        return await asyncio.to_thread(
            self._store.record_channel_provision, actor_user_id,
            channel_type=channel_type, instance_id=instance_id,
        )

    async def list_claimable_channels(self, actor_user_id: str) -> list[ChannelProvision]:
        return await asyncio.to_thread(self._store.list_claimable_channels, actor_user_id)

    async def resolve_channel_assignment(
        self, channel_type: str, instance_id: str
    ) -> ChannelAssignment | None:
        return await asyncio.to_thread(
            self._store.resolve_channel_assignment, channel_type, instance_id
        )

    async def list_channel_assignments(self, actor_user_id: str) -> list[ChannelAssignment]:
        return await asyncio.to_thread(self._store.list_channel_assignments, actor_user_id)

    async def update_channel_assignment(
        self, actor_user_id: str, *, channel_type: str, instance_id: str,
        enabled: bool | None = None, project_id: str | None = None,
    ) -> ChannelAssignment:
        return await asyncio.to_thread(
            self._store.update_channel_assignment, actor_user_id,
            channel_type=channel_type, instance_id=instance_id, enabled=enabled,
            project_id=project_id,
        )

    async def delete_channel_assignment(
        self, actor_user_id: str, *, channel_type: str, instance_id: str
    ) -> bool:
        return await asyncio.to_thread(
            self._store.delete_channel_assignment, actor_user_id,
            channel_type=channel_type, instance_id=instance_id,
        )

    async def create_pairing_challenge(
        self, actor_user_id: str, *, project_id: str, channel_type: str, instance_id: str,
        assignee_user_id: str | None = None, ttl_seconds: int = 600,
    ) -> tuple[PairingChallenge, str]:
        return await asyncio.to_thread(
            self._store.create_pairing_challenge, actor_user_id, project_id=project_id,
            channel_type=channel_type, instance_id=instance_id,
            assignee_user_id=assignee_user_id, ttl_seconds=ttl_seconds,
        )

    async def get_pairing_challenge(
        self, actor_user_id: str, challenge_id: str
    ) -> PairingChallenge | None:
        return await asyncio.to_thread(self._store.get_pairing_challenge, actor_user_id, challenge_id)

    async def verify_pairing_challenge(
        self, code: str, *, channel_type: str, instance_id: str, sender_id: str
    ) -> PairingChallenge:
        return await asyncio.to_thread(
            self._store.verify_pairing_challenge, code, channel_type=channel_type,
            instance_id=instance_id, sender_id=sender_id,
        )

    async def consume_pairing_challenge(
        self, actor_user_id: str, challenge_id: str
    ) -> PairingChallenge:
        return await asyncio.to_thread(
            self._store.consume_pairing_challenge, actor_user_id, challenge_id
        )

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
        project = await asyncio.to_thread(self._store.get_project, actor_user_id, binding.project_id)
        return binding if project is not None else None

    async def unbind_conversation(
        self, channel: str, conversation_id: str, actor_user_id: str,
        *, thread_id: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._store.unbind_conversation, channel, conversation_id, actor_user_id,
            thread_id=thread_id,
        )

    async def resolve_scope(
        self, channel: str, sender_id: str, chat_id: str, metadata: Mapping[str, object] | None,
        default_workspace: str | Path,
    ) -> ConversationScope:
        return await asyncio.to_thread(
            self._store.resolve_scope, channel, sender_id, chat_id, metadata, default_workspace
        )

    async def resolve_session_scope(
        self, user_id: str, project_id: str, *, channel: str, chat_id: str,
        thread_id: str | None = None,
    ) -> ConversationScope | None:
        return await asyncio.to_thread(
            self._store.resolve_session_scope, user_id, project_id,
            channel=channel, chat_id=chat_id, thread_id=thread_id,
        )
