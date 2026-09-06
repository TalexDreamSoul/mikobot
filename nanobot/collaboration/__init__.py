"""Local collaboration domain models and durable state store."""

from nanobot.collaboration.local_repository import AsyncLocalCollaborationRepository
from nanobot.collaboration.models import (
    COLLABORATION_BINDING_METADATA_KEY,
    COLLABORATION_PROJECT_METADATA_KEY,
    COLLABORATION_USER_METADATA_KEY,
    ChannelAssignment,
    ChannelProvision,
    ConversationBinding,
    ConversationScope,
    ConversationScopeKind,
    MembershipRole,
    PairingChallenge,
    Project,
    ProjectMembership,
    User,
    UserIdentity,
)
from nanobot.collaboration.repository import (
    CollaborationRepository,
    build_collaboration_repository,
)
from nanobot.collaboration.store import (
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationPermissionError,
    CollaborationStore,
    CollaborationStoreError,
    CollaborationStoreFormatError,
)

__all__ = [
    "CollaborationConflictError",
    "CollaborationNotFoundError",
    "CollaborationPermissionError",
    "CollaborationStore",
    "CollaborationStoreError",
    "CollaborationStoreFormatError",
    "CollaborationRepository",
    "build_collaboration_repository",
    "AsyncLocalCollaborationRepository",
    "COLLABORATION_BINDING_METADATA_KEY",
    "COLLABORATION_PROJECT_METADATA_KEY",
    "COLLABORATION_USER_METADATA_KEY",
    "ChannelAssignment",
    "ChannelProvision",
    "ConversationBinding",
    "ConversationScope",
    "ConversationScopeKind",
    "MembershipRole",
    "PairingChallenge",
    "Project",
    "ProjectMembership",
    "User",
    "UserIdentity",
]
