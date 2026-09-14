"""Immutable collaboration domain entities.

The collaboration subsystem models users, projects, channel-instance
assignments, and conversation bindings.  Persistence and authorization live in
:mod:`nanobot.collaboration.store`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

COLLABORATION_USER_METADATA_KEY = "collaboration_user_id"
COLLABORATION_PROJECT_METADATA_KEY = "collaboration_project_id"
COLLABORATION_BINDING_METADATA_KEY = "collaboration_binding_id"
COLLABORATION_ASSIGNMENT_METADATA_KEY = "collaboration_assignment_required"


class MembershipRole(StrEnum):
    """The two supported project roles."""

    OWNER = "owner"
    MEMBER = "member"


class TaskStatus(StrEnum):
    """The board columns of a project's tasks."""

    TODO = "todo"
    DOING = "doing"
    DONE = "done"


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

    ``is_builtin`` marks the instance's default project. Classified work that
    belongs to nobody else — the host's own conversations, their tasks, and their
    heartbeat and Dream resources — lives there, so it cannot be deleted.

    ``app_grants`` records the apps the project approved: for each one, the exact
    package revision at approval time and the capabilities it granted. The host
    revokes those capabilities when the installed revision differs from the
    approved one, so an app update can never silently change what a project runs —
    and because the granted capabilities are recorded, the revocation still works
    after the app itself is removed.
    """

    id: str
    name: str
    workspace_path: str
    created_by_user_id: str
    created_at_ms: int
    updated_at_ms: int
    allowed_skills: tuple[str, ...] | None = None
    allowed_mcp_servers: tuple[str, ...] | None = None
    is_builtin: bool = False
    app_grants: tuple[ProjectAppGrant, ...] = ()
    description: str = ""

    def app_grant(self, name: str) -> ProjectAppGrant | None:
        """Return the project's approval of one app, if it has one."""
        for grant in self.app_grants:
            if grant.name == name:
                return grant
        return None


@dataclass(frozen=True, slots=True)
class ProjectAppGrant:
    """One app a project approved, and the capabilities it granted.

    ``revision`` is the app package's immutable content fingerprint at approval
    time. The capability names are what the project was given, so the host can
    always take them back, even after the app is uninstalled or disabled.
    """

    name: str
    revision: str
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.skills and not self.mcp_servers


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    project_id: str
    user_id: str
    role: MembershipRole
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ProjectTask:
    """One card on a project's board.

    Tasks belong to the project rather than to the person who wrote them: every
    member sees the same board, and the task disappears with its project.
    """

    id: str
    project_id: str
    title: str
    created_by_user_id: str
    created_at_ms: int
    updated_at_ms: int
    status: TaskStatus = TaskStatus.TODO
    detail: str = ""


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


def private_memory_root_for_scope(
    scope: ConversationScope | None,
    *,
    project_path: Path,
) -> Path | None:
    """Return the profile and memory root a turn on this scope may read.

    ``None`` keeps the host's own store, which belongs to the instance's home: a
    turn with no collaboration scope, and the built-in project — whose workspace
    *is* the host's own workspace. Every other project keeps each user's profile,
    memory, and journal inside that project, for its owner as much as for its
    members, so a channel instance assigned to a project never exposes the host's
    private data. A scope that resolved without both ids falls back to the
    authorized project directory rather than the host store.
    """
    if scope is None:
        return None
    if scope.is_local_owner and (scope.project is None or scope.project.is_builtin):
        return None
    if scope.user_id is not None and scope.project_id is not None:
        from nanobot.security.private_media import user_private_memory_root

        return user_private_memory_root(scope.user_id, project_id=scope.project_id)
    return project_path
