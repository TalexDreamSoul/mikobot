"""Immutable collaboration domain entities.

The collaboration subsystem models users, projects, channel-instance
assignments, and conversation bindings.  Persistence and authorization live in
:mod:`nanobot.collaboration.store`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

COLLABORATION_USER_METADATA_KEY = "collaboration_user_id"
COLLABORATION_PROJECT_METADATA_KEY = "collaboration_project_id"
COLLABORATION_BINDING_METADATA_KEY = "collaboration_binding_id"


class MembershipRole(StrEnum):
    """The two supported project roles."""

    OWNER = "owner"
    MEMBER = "member"


class ConversationScopeKind(StrEnum):
    """How a request acquired its collaboration scope."""

    DIRECT = "direct"
    BOUND = "bound"
    ISOLATED = "isolated"


@dataclass(frozen=True, slots=True)
class User:
    id: str
    display_name: str
    default_project_id: str | None
    created_at_ms: int
    updated_at_ms: int
    # System administrators manage every project and assignment. The local
    # owner is always an administrator; other users are flagged by the gateway
    # from its authenticated principal, never from a payload.
    is_admin: bool = False


@dataclass(frozen=True, slots=True)
class UserIdentity:
    """A channel-local external sender identifier bound to an internal user."""

    user_id: str
    channel: str
    sender_id: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class Project:
    """A workspace shared by its members.

    ``allowed_skills`` and ``allowed_mcp_servers`` restrict what conversations
    scoped to the project may use; ``None`` leaves the capability unrestricted.
    """

    id: str
    name: str
    workspace_path: str
    created_by_user_id: str
    created_at_ms: int
    updated_at_ms: int
    allowed_skills: tuple[str, ...] | None = None
    allowed_mcp_servers: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    project_id: str
    user_id: str
    role: MembershipRole
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ChannelProvision:
    """The member who connected one channel instance through self-service connect.

    Ownership is recorded when the instance is created, never inferred afterwards.
    An instance with no record predates this provenance and stays administrator-only.
    """

    channel_type: str
    instance_id: str
    created_by_user_id: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ChannelAssignment:
    """One channel instance handed to one project on behalf of one member.

    Messages arriving on the instance are routed to ``project_id``. The
    ``assignee_user_id`` is the member the instance was handed to; the sender
    who verified the Pair Code is bound to that user.
    """

    channel_type: str
    instance_id: str
    project_id: str
    assignee_user_id: str
    enabled: bool
    created_by_user_id: str
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class PairingChallenge:
    """A one-time Pair Code that assigns a channel instance to a project."""

    id: str
    code_digest: str
    requested_by_user_id: str
    assignee_user_id: str
    project_id: str
    channel_type: str
    instance_id: str
    expires_at_ms: int
    verified_at_ms: int | None
    verified_sender_id: str | None
    consumed_at_ms: int | None
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationBinding:
    """An explicit channel conversation/thread to project assignment."""

    id: str
    channel: str
    conversation_id: str
    thread_id: str | None
    project_id: str
    created_by_user_id: str
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class ConversationScope:
    """Resolved, authorization-checked context for one inbound conversation."""

    kind: ConversationScopeKind
    user_id: str | None
    project_id: str | None
    user: User | None
    project: Project | None
    binding: ConversationBinding | None
    assignment: ChannelAssignment | None
    workspace_path: str | None
    session_suffix: str
    route_denied: bool = False
    # True when the resolved user is the host's own owner. They are one person,
    # not a tenant, so their conversations keep their original session keys.
    is_local_owner: bool = False

    @property
    def is_isolated(self) -> bool:
        return self.kind is ConversationScopeKind.ISOLATED

    @property
    def allowed_skills(self) -> tuple[str, ...] | None:
        return self.project.allowed_skills if self.project is not None else None

    @property
    def allowed_mcp_servers(self) -> tuple[str, ...] | None:
        return self.project.allowed_mcp_servers if self.project is not None else None
