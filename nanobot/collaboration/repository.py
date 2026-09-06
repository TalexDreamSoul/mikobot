"""Asynchronous boundary for the collaboration persistence store."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from .local_repository import AsyncLocalCollaborationRepository
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


@runtime_checkable
class CollaborationRepository(Protocol):
    """Async persistence operations for the collaboration domain."""

    async def initialize(self) -> None: ...

    async def aclose(self) -> None: ...

    async def ensure_local_owner(self, default_workspace: str | Path) -> tuple[User, Project]: ...

    async def create_user(self, display_name: str) -> User: ...

    async def update_user_display_name(self, user_id: str, display_name: str) -> User: ...

    async def update_user_admin(self, user_id: str, is_admin: bool) -> User: ...

    async def get_user(self, user_id: str) -> User | None: ...

    async def list_users(self) -> list[User]: ...

    async def update_user_default_project(
        self, user_id: str, project_id: str | None
    ) -> User: ...

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

    async def create_project(
        self, owner_user_id: str, name: str, workspace_path: str | Path
    ) -> Project: ...

    async def get_project(self, user_id: str, project_id: str) -> Project | None: ...

    async def list_projects(self, user_id: str) -> list[Project]: ...

    async def list_all_projects(self, actor_user_id: str) -> list[Project]: ...

    async def update_project(
        self,
        project_id: str,
        actor_user_id: str,
        *,
        name: str | None = None,
        workspace_path: str | Path | None = None,
        allowed_skills: Sequence[str] | None | object = ...,
        allowed_mcp_servers: Sequence[str] | None | object = ...,
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

    async def record_channel_provision(
        self, actor_user_id: str, *, channel_type: str, instance_id: str
    ) -> ChannelProvision: ...

    async def list_claimable_channels(self, actor_user_id: str) -> list[ChannelProvision]: ...

    async def resolve_channel_assignment(
        self, channel_type: str, instance_id: str
    ) -> ChannelAssignment | None: ...

    async def list_channel_assignments(self, actor_user_id: str) -> list[ChannelAssignment]: ...

    async def update_channel_assignment(
        self,
        actor_user_id: str,
        *,
        channel_type: str,
        instance_id: str,
        enabled: bool | None = None,
    ) -> ChannelAssignment: ...

    async def delete_channel_assignment(
        self, actor_user_id: str, *, channel_type: str, instance_id: str
    ) -> bool: ...

    async def create_pairing_challenge(
        self,
        actor_user_id: str,
        *,
        project_id: str,
        channel_type: str,
        instance_id: str,
        assignee_user_id: str | None = None,
        ttl_seconds: int = 600,
    ) -> tuple[PairingChallenge, str]: ...

    async def get_pairing_challenge(
        self, actor_user_id: str, challenge_id: str
    ) -> PairingChallenge | None: ...

    async def verify_pairing_challenge(
        self, code: str, *, channel_type: str, instance_id: str, sender_id: str
    ) -> PairingChallenge: ...

    async def consume_pairing_challenge(
        self, actor_user_id: str, challenge_id: str
    ) -> PairingChallenge: ...

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

    async def resolve_scope(
        self,
        channel: str,
        sender_id: str,
        chat_id: str,
        metadata: Mapping[str, object] | None,
        default_workspace: str | Path,
    ) -> ConversationScope: ...


def build_collaboration_repository(
    local_store: CollaborationStore | None = None,
) -> CollaborationRepository:
    """Create the asynchronous collaboration repository over the local JSON store."""
    return AsyncLocalCollaborationRepository(local_store)
