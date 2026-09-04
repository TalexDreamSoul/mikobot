"""Immutable collaboration domain entities.

The collaboration subsystem models tenant, project, bot, and channel-instance
boundaries needed to partition work.  Persistence and authorization live in
:mod:`nanobot.collaboration.store`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias, cast, overload

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonObject: TypeAlias = Mapping[str, "JsonValue"]
JsonValue: TypeAlias = JsonPrimitive | tuple["JsonValue", ...] | JsonObject
MutableJsonValue: TypeAlias = JsonPrimitive | list["MutableJsonValue"] | dict[str, "MutableJsonValue"]
COLLABORATION_USER_METADATA_KEY = "collaboration_user_id"
COLLABORATION_PROJECT_METADATA_KEY = "collaboration_project_id"
COLLABORATION_BINDING_METADATA_KEY = "collaboration_binding_id"
COLLABORATION_VAULT_METADATA_KEY = "collaboration_vault_id"
COLLABORATION_BOT_METADATA_KEY = "collaboration_bot_id"




class OrganizationRole(StrEnum):
    """Roles governing an organization and its member roster."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class MembershipRole(StrEnum):
    """The two supported project roles."""

    OWNER = "owner"
    MEMBER = "member"


class TaskStatus(StrEnum):
    """The intentionally small task lifecycle."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskReviewState(StrEnum):
    """Whether an assistant-proposed task is actionable yet."""

    CONFIRMED = "confirmed"
    PROPOSED = "proposed"
    DISMISSED = "dismissed"


class ConversationScopeKind(StrEnum):
    """How a request acquired its collaboration scope."""

    DIRECT = "direct"
    BOUND = "bound"
    ISOLATED = "isolated"


class ContextSourceKind(StrEnum):
    """Kinds of project-owned context descriptors."""

    SKILL = "skill"
    MCP = "mcp"
    PLUGIN = "plugin"
    DOCUMENT = "document"
    CUSTOM = "custom"


class BotState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class PairingPurpose(StrEnum):
    CLAIM_CHANNEL = "claim_channel"
    ASSIGN_BOT_PROJECT = "assign_bot_project"


@dataclass(frozen=True, slots=True)
class User:
    id: str
    display_name: str
    default_project_id: str | None
    created_at_ms: int
    updated_at_ms: int
    # Optional only for backward-compatible decoding of the original single-user store.
    default_vault_id: str | None = None
    default_persona_id: str | None = None
    default_organization_id: str | None = None
    default_bot_id: str | None = None


@dataclass(frozen=True, slots=True)
class UserIdentity:
    """A channel-local external sender identifier bound to an internal user."""

    user_id: str
    channel: str
    sender_id: str
    created_at_ms: int


class VaultKind(StrEnum):
    """The purpose of a private user-owned data vault."""

    PRIVATE = "private"
    WORK = "work"
    LIFE = "life"


class SharePermission(StrEnum):
    """The only permissions granted across user boundaries."""

    READ = "read"
    COLLABORATE = "collaborate"


@dataclass(frozen=True, slots=True)
class Vault:
    """A user-owned security boundary for private durable data."""

    id: str
    owner_user_id: str
    name: str
    kind: VaultKind
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class Persona:
    """A user-facing assistant identity routed to one default vault."""

    id: str
    owner_user_id: str
    name: str
    default_vault_id: str
    instructions: str
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class Bot:
    id: str
    organization_id: str
    owner_user_id: str
    name: str
    avatar_url: str | None
    persona_id: str | None
    state: BotState
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class BotProjectAssignment:
    bot_id: str
    project_id: str
    assigned_by_user_id: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class BotChannelAssignment:
    bot_id: str
    channel_type: str
    instance_id: str
    claimed_by_user_id: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ChannelProvision:
    """The member who provisioned one channel instance through self-service connect.

    Ownership is recorded when the instance is created, never inferred afterwards.
    An instance with no record predates this provenance and stays administrator-only.
    """

    channel_type: str
    instance_id: str
    organization_id: str
    created_by_user_id: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class BotProjectChannel:
    bot_id: str
    project_id: str
    channel_type: str
    instance_id: str
    enabled: bool
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class BotCapabilityProfile:
    bot_id: str
    project_id: str | None
    revision: int
    settings: JsonObject
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class PairingChallenge:
    id: str
    code_digest: str
    requested_by_user_id: str
    purpose: PairingPurpose
    organization_id: str
    bot_id: str
    project_id: str | None
    channel_type: str
    instance_id: str
    channel_revision: str
    expires_at_ms: int
    verified_at_ms: int | None
    verified_sender_id: str | None
    consumed_at_ms: int | None
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ShareGrant:
    """An explicit, revocable grant; private data has no implicit sharing."""

    id: str
    vault_id: str
    grantee_user_id: str
    resource_type: str
    resource_id: str | None
    permission: SharePermission
    expires_at_ms: int | None
    created_at_ms: int
    revoked_at_ms: int | None


@dataclass(frozen=True, slots=True)
class Organization:
    id: str
    name: str
    created_by_user_id: str
    created_at_ms: int
    updated_at_ms: int
    is_personal: bool = False


@dataclass(frozen=True, slots=True)
class OrganizationMembership:
    organization_id: str
    user_id: str
    role: OrganizationRole
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str
    workspace_path: str
    created_by_user_id: str
    created_at_ms: int
    updated_at_ms: int
    # Optional only while decoding a legacy store predating organizations.
    organization_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    project_id: str
    user_id: str
    role: MembershipRole
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class TaskList:
    id: str
    project_id: str
    name: str
    position: int
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    project_id: str
    task_list_id: str
    title: str
    status: TaskStatus
    description: str
    assignee_user_id: str | None
    position: int
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class PersonalTask:
    """A vault-owned personal task with sync-safe provenance."""

    id: str
    owner_user_id: str
    vault_id: str
    title: str
    note: str
    status: TaskStatus
    priority: int
    due_at_ms: int | None
    timezone: str | None
    recurrence_rule: str | None
    source_type: str
    source_ref: str | None
    external_provider: str | None
    external_id: str | None
    external_version: str | None
    review_state: TaskReviewState
    created_at_ms: int
    updated_at_ms: int


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
class ExtensionProfile:
    """Per-project, per-member extension settings and optimistic revision."""

    project_id: str
    user_id: str
    revision: int
    settings: Mapping[str, JsonValue]
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class ContextSource:
    """A bounded descriptor for context available to one project."""

    id: str
    project_id: str
    name: str
    kind: ContextSourceKind
    enabled: bool
    config: Mapping[str, JsonValue]
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
    profile: ExtensionProfile | None
    profile_revision: int
    workspace_path: str | None
    session_suffix: str
    vault_id: str | None = None
    persona_id: str | None = None
    bot_id: str | None = None
    bot: Bot | None = None
    route_denied: bool = False

    @property
    def is_isolated(self) -> bool:
        return self.kind is ConversationScopeKind.ISOLATED


@overload
def freeze_json(value: Mapping[str, object]) -> JsonObject: ...


@overload
def freeze_json(value: object) -> JsonValue: ...


def freeze_json(value: object) -> JsonValue:
    """Return an immutable JSON-compatible copy of *value*.

    Store methods use this at their boundary so frozen records cannot expose a
    mutable mapping retained by persistence code or supplied by a caller.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in cast(Mapping[object, object], value).items():
            frozen[str(key)] = freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        items = cast(Sequence[object], value)
        return tuple(freeze_json(item) for item in items)
    raise TypeError(f"not JSON-compatible: {type(value).__name__}")


def thaw_json(value: JsonValue) -> MutableJsonValue:
    """Return a fresh mutable JSON-compatible copy for serialization."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value
