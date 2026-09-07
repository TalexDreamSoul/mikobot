"""Durable, local JSON storage for collaboration state."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TypeAlias, TypedDict, TypeVar, cast

from filelock import FileLock
from loguru import logger

from nanobot.collaboration.models import (
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
from nanobot.collaboration.pairing import (
    CHANNEL_ASSIGNMENT_REQUIRED_METADATA_KEY,
    assignment_code_digest,
    new_assignment_code,
    normalize_assignment_code,
    runtime_channel_key,
)
from nanobot.config.paths import get_runtime_subdir

_SCHEMA = 9
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_STRING = 512
_MAX_TEXT = 16_000
_MAX_ALLOWLIST = 256
_MAX_CHALLENGES_PER_USER = 32
_MAX_CHALLENGES = 4096

_Record: TypeAlias = dict[str, object]
_Entity = TypeVar("_Entity")
_Allowlist: TypeAlias = "Sequence[str] | None"


class _StoreState(TypedDict):
    schemaVersion: int
    localOwnerId: str | None
    users: dict[str, _Record]
    identities: dict[str, _Record]
    projects: dict[str, _Record]
    memberships: dict[str, _Record]
    channelProvisions: dict[str, _Record]
    channelAssignments: dict[str, _Record]
    pairingChallenges: dict[str, _Record]
    conversationBindings: dict[str, _Record]


class CollaborationStoreError(RuntimeError):
    """Base error for collaboration state."""


class CollaborationStoreFormatError(CollaborationStoreError):
    """Persisted state is corrupt, unsupported, or exceeds a safe bound."""


class CollaborationNotFoundError(CollaborationStoreError):
    """The requested collaboration object does not exist."""


class CollaborationPermissionError(CollaborationStoreError):
    """The requested project operation is not authorized."""


class CollaborationConflictError(CollaborationStoreError):
    """A unique identity or binding is already owned by another object."""


class CollaborationStore:
    """Injectable, process-safe store backed by one small JSON document.

    Production state lives under ``get_runtime_subdir("collaboration")``.  The
    store has no cache: every method reads under a file lock, so independently
    started local CLI, gateway, and WebUI processes observe one coherent state.
    """

    def __init__(self, root: str | Path | None = None, *,
                 store_path: str | Path | None = None) -> None:
        if root is not None and store_path is not None:
            raise ValueError("pass root or store_path, not both")
        if store_path is not None:
            self.path = Path(store_path).expanduser().resolve(strict=False)
            self.root = self.path.parent
        else:
            self.root = (Path(root).expanduser() if root is not None else get_runtime_subdir(
                "collaboration")).resolve(strict=False)
            self.path = self.root / "collaboration.json"
        self._lock = FileLock(
            str(self.path.with_suffix(f"{self.path.suffix}.lock")))

    # -- bootstrap, users, and identities ------------------------------------

    def ensure_local_owner(self, default_workspace: str | Path) -> tuple[User, Project]:
        """Lazily initialize the local owner and its default project."""
        workspace = _workspace(default_workspace)
        with self._state() as state:
            now = _now()
            owner_id = state["localOwnerId"]
            owner_value = state["users"].get(owner_id) if owner_id is not None else None
            owner = _user(owner_value) if owner_value is not None else None
            if owner is None:
                owner = User(_new_id(), "Local owner", None, now, now)
                state["users"][owner.id] = _encode_user(owner)
                state["localOwnerId"] = owner.id
            project_value = (
                state["projects"].get(owner.default_project_id)
                if owner.default_project_id is not None
                else None
            )
            project = _project(project_value) if project_value is not None else None
            if project is None:
                project = Project(_new_id(), "Local project", workspace, owner.id, now, now)
                state["projects"][project.id] = _encode_project(project)
                owner = _replace_user(owner, default_project_id=project.id, updated_at_ms=now)
                state["users"][owner.id] = _encode_user(owner)
            elif project.workspace_path != workspace:
                project = _replace_project(project, workspace_path=workspace, updated_at_ms=now)
                state["projects"][project.id] = _encode_project(project)
            if _member_key(project.id, owner.id) not in state["memberships"]:
                state["memberships"][_member_key(project.id, owner.id)] = _encode_membership(
                    ProjectMembership(project.id, owner.id, MembershipRole.OWNER, now))
            self._save(state)
            return owner, project

    def create_user(self, display_name: str) -> User:
        with self._state() as state:
            now = _now()
            user = User(_new_id(), _string(display_name, "display_name"), None, now, now)
            state["users"][user.id] = _encode_user(user)
            self._save(state)
            return user

    def update_user_display_name(self, user_id: str, display_name: str) -> User:
        """Update the bounded display label supplied by an authenticated identity."""
        user_id = _id(user_id, "user_id")
        display_name = _string(display_name, "display_name", limit=256)
        with self._state() as state:
            user = self._require_user(state, user_id)
            if user.display_name == display_name:
                return user
            updated = _replace_user(user, display_name=display_name, updated_at_ms=_now())
            state["users"][updated.id] = _encode_user(updated)
            self._save(state)
            return updated

    def update_user_admin(self, user_id: str, is_admin: bool) -> User:
        """Record whether the gateway authenticated *user_id* as a system administrator."""
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            user = self._require_user(state, user_id)
            if user.is_admin == is_admin:
                return user
            updated = _replace_user(user, is_admin=is_admin, updated_at_ms=_now())
            state["users"][updated.id] = _encode_user(updated)
            self._save(state)
            return updated

    def get_user(self, user_id: str) -> User | None:
        with self._state() as state:
            value = state["users"].get(_id(user_id, "user_id"))
            return _user(value) if value is not None else None

    def list_users(self) -> list[User]:
        with self._state() as state:
            return sorted((_user(value) for value in state["users"].values()),
                          key=lambda item: (item.created_at_ms, item.id))

    def update_user_default_project(self, user_id: str, project_id: str | None) -> User:
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            user = self._require_user(state, user_id)
            if project_id is not None:
                project_id = _id(project_id, "project_id")
                self._require_member(state, project_id, user_id)
            updated = _replace_user(user, default_project_id=project_id, updated_at_ms=_now())
            state["users"][user.id] = _encode_user(updated)
            self._save(state)
            return updated

    def bind_identity(self, channel: str, sender_id: str, user_id: str) -> UserIdentity:
        channel, sender_id, user_id = (
            _key(channel, "channel"), _key(sender_id, "sender_id"), _id(user_id, "user_id"))
        with self._state() as state:
            self._require_user(state, user_id)
            key = _identity_key(channel, sender_id)
            existing = state["identities"].get(key)
            if existing is not None:
                identity = _identity(existing)
                if identity.user_id != user_id:
                    raise CollaborationConflictError("channel sender identity is already bound")
                return identity
            identity = UserIdentity(user_id, channel, sender_id, _now())
            state["identities"][key] = _encode_identity(identity)
            self._save(state)
            return identity

    def rebind_identity(self, channel: str, sender_id: str, user_id: str) -> UserIdentity:
        """Move one channel identity after an external proof has been verified."""
        channel = _key(channel, "channel")
        sender_id = _key(sender_id, "sender_id")
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            self._require_user(state, user_id)
            identity = UserIdentity(user_id, channel, sender_id, _now())
            state["identities"][_identity_key(channel, sender_id)] = _encode_identity(identity)
            self._save(state)
            return identity

    def resolve_identity(self, channel: str, sender_id: str) -> User | None:
        with self._state() as state:
            identity = state["identities"].get(
                _identity_key(_key(channel, "channel"), _key(sender_id, "sender_id")))
            if identity is None:
                return None
            user = state["users"].get(_identity(identity).user_id)
            return _user(user) if user is not None else None

    def ensure_identity_user(
        self,
        channel: str,
        sender_id: str,
        default_workspace: str | Path,
        *,
        local_owner: bool = False,
    ) -> tuple[User, Project]:
        """Resolve or provision one channel identity and its default project.

        Static-token/local clients intentionally share the local owner. Federated
        and external-channel identities receive private state workspaces so one
        person's files are not placed in another person's project directory.
        """
        channel = _key(channel, "channel")
        sender_id = _key(sender_id, "sender_id")
        existing = self.resolve_identity(channel, sender_id)
        if existing is not None and existing.default_project_id is not None:
            project = self.get_project(existing.id, existing.default_project_id)
            if project is not None:
                return existing, project

        if local_owner:
            user, project = self.ensure_local_owner(default_workspace)
            self.bind_identity(channel, sender_id, user.id)
            return user, project

        with self._state() as state:
            identity_key = _identity_key(channel, sender_id)
            identity_value = state["identities"].get(identity_key)
            if identity_value is not None:
                user = self._require_user(state, _identity(identity_value).user_id)
                if user.default_project_id is None:
                    raise CollaborationConflictError("channel identity has no default project")
                project = self._require_project(state, user.default_project_id)
                return user, project

            now = _now()
            user_id = _new_id()
            workspace = self.root / "workspaces" / user_id / "default"
            workspace.mkdir(parents=True, exist_ok=True)
            with suppress(OSError):
                workspace.chmod(0o700)
            project = Project(
                _new_id(), "Personal project", _workspace(workspace), user_id, now, now)
            user = User(user_id, f"{channel} user", project.id, now, now)
            state["users"][user.id] = _encode_user(user)
            state["projects"][project.id] = _encode_project(project)
            state["memberships"][_member_key(project.id, user.id)] = _encode_membership(
                ProjectMembership(project.id, user.id, MembershipRole.OWNER, now))
            state["identities"][identity_key] = _encode_identity(
                UserIdentity(user.id, channel, sender_id, now))
            self._save(state)
            return user, project

    # -- projects and membership ---------------------------------------------

    def create_project(self, owner_user_id: str, name: str,
                       workspace_path: str | Path) -> Project:
        owner_user_id = _id(owner_user_id, "owner_user_id")
        with self._state() as state:
            self._require_user(state, owner_user_id)
            now = _now()
            project = Project(
                _new_id(), _string(name, "name"), _workspace(workspace_path),
                owner_user_id, now, now)
            state["projects"][project.id] = _encode_project(project)
            state["memberships"][_member_key(project.id, owner_user_id)] = _encode_membership(
                ProjectMembership(project.id, owner_user_id, MembershipRole.OWNER, now))
            self._save(state)
            return project

    def get_project(self, user_id: str, project_id: str) -> Project | None:
        """Return the project when *user_id* is a member or a system administrator."""
        user_id, project_id = _id(user_id, "user_id"), _id(project_id, "project_id")
        with self._state() as state:
            if not self._is_member(state, project_id, user_id) and not self._is_admin(
                    state, user_id):
                return None
            project = state["projects"].get(project_id)
            return _project(project) if project is not None else None

    def list_projects(self, user_id: str) -> list[Project]:
        """Return the projects *user_id* is a member of, newest first."""
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            projects: list[Project] = []
            for membership_value in state["memberships"].values():
                membership = _membership(membership_value)
                if membership.user_id != user_id:
                    continue
                project = state["projects"].get(membership.project_id)
                if project is not None:
                    projects.append(_project(project))
            return sorted(projects, key=lambda item: (item.updated_at_ms, item.id), reverse=True)

    def manageable_project_ids(self, actor_user_id: str) -> list[str]:
        """Return the projects *actor_user_id* may administer.

        A system administrator manages every project; anyone else manages the
        ones they own. The WebUI needs this to decide which channel controls to
        offer, rather than presenting actions the store would refuse.
        """
        actor_user_id = _id(actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_user(state, actor_user_id)
            if self._is_admin(state, actor_user_id):
                return sorted(state["projects"])
            return sorted(
                membership.project_id
                for value in state["memberships"].values()
                if (membership := _membership(value)).user_id == actor_user_id
                and membership.role is MembershipRole.OWNER
                and membership.project_id in state["projects"]
            )

    def list_all_projects(self, actor_user_id: str) -> list[Project]:
        """Return every project; only a system administrator may ask."""
        actor_user_id = _id(actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_admin(state, actor_user_id)
            return sorted((_project(value) for value in state["projects"].values()),
                          key=lambda item: (item.updated_at_ms, item.id), reverse=True)

    def update_project(
        self,
        project_id: str,
        actor_user_id: str,
        *,
        name: str | None = None,
        workspace_path: str | Path | None = None,
        allowed_skills: _Allowlist | object = ...,
        allowed_mcp_servers: _Allowlist | object = ...,
    ) -> Project:
        if (
            name is None and workspace_path is None
            and allowed_skills is ... and allowed_mcp_servers is ...
        ):
            raise ValueError("provide at least one project field")
        project_id = _id(project_id, "project_id")
        actor_user_id = _id(actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_manager(state, project_id, actor_user_id)
            project = self._require_project(state, project_id)
            updated = Project(
                project.id,
                _string(name, "name") if name is not None else project.name,
                _workspace(workspace_path) if workspace_path is not None else project.workspace_path,
                project.created_by_user_id,
                project.created_at_ms,
                _now(),
                (
                    _allowlist(allowed_skills, "allowed_skills")
                    if allowed_skills is not ... else project.allowed_skills
                ),
                (
                    _allowlist(allowed_mcp_servers, "allowed_mcp_servers")
                    if allowed_mcp_servers is not ... else project.allowed_mcp_servers
                ),
            )
            state["projects"][project_id] = _encode_project(updated)
            self._save(state)
            return updated

    def delete_project(self, project_id: str, actor_user_id: str) -> bool:
        project_id = _id(project_id, "project_id")
        actor_user_id = _id(actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_manager(state, project_id, actor_user_id)
            self._require_project(state, project_id)
            del state["projects"][project_id]
            for collection in (
                "memberships", "conversationBindings", "channelAssignments", "pairingChallenges",
            ):
                for key, value in tuple(state[collection].items()):
                    if value["projectId"] == project_id:
                        del state[collection][key]
            now = _now()
            for value in tuple(state["users"].values()):
                user = _user(value)
                if user.default_project_id == project_id:
                    self._reset_default_project(state, user, now)
            self._save(state)
            return True

    def add_member(self, project_id: str, actor_user_id: str, user_id: str,
                   role: MembershipRole = MembershipRole.MEMBER) -> ProjectMembership:
        project_id, actor_user_id, user_id = (
            _id(project_id, "project_id"), _id(actor_user_id, "actor_user_id"),
            _id(user_id, "user_id"))
        role = _role(role)
        with self._state() as state:
            self._require_manager(state, project_id, actor_user_id)
            self._require_user(state, user_id)
            self._require_project(state, project_id)
            key = _member_key(project_id, user_id)
            existing = state["memberships"].get(key)
            if (
                existing is not None
                and _membership(existing).role is MembershipRole.OWNER
                and role is not MembershipRole.OWNER
                and self._owner_count(state, project_id) == 1
            ):
                raise CollaborationConflictError("a project must retain an owner")
            created = _membership(existing).created_at_ms if existing is not None else _now()
            membership = ProjectMembership(project_id, user_id, role, created)
            state["memberships"][key] = _encode_membership(membership)
            self._save(state)
            return membership

    def remove_member(self, project_id: str, actor_user_id: str, user_id: str) -> bool:
        project_id, actor_user_id, user_id = (
            _id(project_id, "project_id"), _id(actor_user_id, "actor_user_id"),
            _id(user_id, "user_id"))
        with self._state() as state:
            self._require_manager(state, project_id, actor_user_id)
            key = _member_key(project_id, user_id)
            value = state["memberships"].get(key)
            if value is None:
                return False
            if (
                _membership(value).role is MembershipRole.OWNER
                and self._owner_count(state, project_id) == 1
            ):
                raise CollaborationConflictError("a project must retain an owner")
            del state["memberships"][key]
            for binding_key, binding in tuple(state["conversationBindings"].items()):
                if binding["projectId"] == project_id and binding["createdByUserId"] == user_id:
                    del state["conversationBindings"][binding_key]
            for assignment_key, assignment in tuple(state["channelAssignments"].items()):
                if assignment["projectId"] == project_id and assignment["assigneeUserId"] == user_id:
                    del state["channelAssignments"][assignment_key]
            for challenge_key, challenge in tuple(state["pairingChallenges"].items()):
                if challenge["projectId"] == project_id and challenge["assigneeUserId"] == user_id:
                    del state["pairingChallenges"][challenge_key]
            user = self._require_user(state, user_id)
            if user.default_project_id == project_id:
                self._reset_default_project(state, user, _now())
            self._save(state)
            return True

    def list_members(self, project_id: str, user_id: str) -> list[ProjectMembership]:
        project_id, user_id = _id(project_id, "project_id"), _id(user_id, "user_id")
        with self._state() as state:
            self._require_member_or_admin(state, project_id, user_id)
            return sorted(
                (_membership(value) for value in state["memberships"].values()
                 if value["projectId"] == project_id),
                key=lambda item: (item.created_at_ms, item.user_id))

    # -- channel provisions and assignments ----------------------------------

    def record_channel_provision(
        self, actor_user_id: str, *, channel_type: str, instance_id: str
    ) -> ChannelProvision:
        """Record that *actor_user_id* provisioned one channel instance.

        The first record wins: re-provisioning an instance another member created
        would turn self-service connect into a takeover path.
        """
        actor_user_id = _id(actor_user_id, "actor_user_id")
        channel_type = _key(channel_type, "channel_type")
        instance_id = _key(instance_id, "instance_id")
        with self._state() as state:
            self._require_user(state, actor_user_id)
            key = _channel_instance_key(channel_type, instance_id)
            existing_value = state["channelProvisions"].get(key)
            if existing_value is not None:
                existing = _channel_provision(existing_value)
                if existing.created_by_user_id != actor_user_id:
                    raise CollaborationConflictError(
                        "channel instance was provisioned by another user")
                return existing
            provision = ChannelProvision(channel_type, instance_id, actor_user_id, _now())
            state["channelProvisions"][key] = _encode_channel_provision(provision)
            self._save(state)
            return provision

    def list_claimable_channels(self, actor_user_id: str) -> list[ChannelProvision]:
        """Return the unassigned instances *actor_user_id* provisioned themselves."""
        actor_user_id = _id(actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_user(state, actor_user_id)
            return sorted(
                (
                    provision
                    for value in state["channelProvisions"].values()
                    if (provision := _channel_provision(value)).created_by_user_id
                    == actor_user_id
                    and _channel_instance_key(provision.channel_type, provision.instance_id)
                    not in state["channelAssignments"]
                ),
                key=lambda provision: (provision.channel_type, provision.instance_id),
            )

    def resolve_channel_assignment(
        self, channel_type: str, instance_id: str
    ) -> ChannelAssignment | None:
        """Return the assignment routing one channel instance, if any.

        Channel runtimes use this without an actor: the instance itself is the
        subject, and the returned record only names a project id and a user id.
        """
        channel_type = _key(channel_type, "channel_type")
        instance_id = _key(instance_id, "instance_id")
        with self._state() as state:
            value = state["channelAssignments"].get(
                _channel_instance_key(channel_type, instance_id))
            return _channel_assignment(value) if value is not None else None

    def list_channel_assignments(self, actor_user_id: str) -> list[ChannelAssignment]:
        """Return the assignments *actor_user_id* may see: their own, their projects', or all."""
        actor_user_id = _id(actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_user(state, actor_user_id)
            admin = self._is_admin(state, actor_user_id)
            return sorted(
                (
                    assignment
                    for value in state["channelAssignments"].values()
                    if (assignment := _channel_assignment(value)).assignee_user_id == actor_user_id
                    or admin
                    or self._is_member(state, assignment.project_id, actor_user_id)
                ),
                key=lambda item: (item.channel_type, item.instance_id),
            )

    def update_channel_assignment(
        self,
        actor_user_id: str,
        *,
        channel_type: str,
        instance_id: str,
        enabled: bool | None = None,
        project_id: str | None = None,
    ) -> ChannelAssignment:
        """Pause an instance, or move it to another project the actor manages.

        Moving an instance requires managing both the project it leaves and the
        one it joins, so a project owner cannot pull a colleague's instance into
        their own project. The assignee follows the instance as a member of the
        target project, exactly as consuming a Pair Code would add them.
        """
        actor_user_id = _id(actor_user_id, "actor_user_id")
        channel_type = _key(channel_type, "channel_type")
        instance_id = _key(instance_id, "instance_id")
        project_id = _id(project_id, "project_id") if project_id is not None else None
        if enabled is None and project_id is None:
            raise ValueError("provide at least one assignment field")
        with self._state() as state:
            key = _channel_instance_key(channel_type, instance_id)
            value = state["channelAssignments"].get(key)
            if value is None:
                raise CollaborationNotFoundError("channel assignment not found")
            assignment = _channel_assignment(value)
            self._require_manager(state, assignment.project_id, actor_user_id)
            now = _now()
            target_project_id = assignment.project_id
            if project_id is not None and project_id != assignment.project_id:
                self._require_project(state, project_id)
                self._require_manager(state, project_id, actor_user_id)
                target_project_id = project_id
                member_key = _member_key(target_project_id, assignment.assignee_user_id)
                if member_key not in state["memberships"]:
                    state["memberships"][member_key] = _encode_membership(
                        ProjectMembership(
                            target_project_id, assignment.assignee_user_id,
                            MembershipRole.MEMBER, now,
                        )
                    )
            updated = ChannelAssignment(
                assignment.channel_type, assignment.instance_id, target_project_id,
                assignment.assignee_user_id,
                enabled if enabled is not None else assignment.enabled,
                assignment.created_by_user_id, assignment.created_at_ms, now,
            )
            state["channelAssignments"][key] = _encode_channel_assignment(updated)
            self._save(state)
            return updated

    def delete_channel_assignment(
        self, actor_user_id: str, *, channel_type: str, instance_id: str
    ) -> bool:
        actor_user_id = _id(actor_user_id, "actor_user_id")
        channel_type = _key(channel_type, "channel_type")
        instance_id = _key(instance_id, "instance_id")
        with self._state() as state:
            key = _channel_instance_key(channel_type, instance_id)
            value = state["channelAssignments"].get(key)
            if value is None:
                return False
            assignment = _channel_assignment(value)
            self._require_manager(state, assignment.project_id, actor_user_id)
            del state["channelAssignments"][key]
            self._save(state)
            return True

    # -- assignment pairing --------------------------------------------------

    def create_pairing_challenge(
        self,
        actor_user_id: str,
        *,
        project_id: str,
        channel_type: str,
        instance_id: str,
        assignee_user_id: str | None = None,
        ttl_seconds: int = 600,
    ) -> tuple[PairingChallenge, str]:
        """Issue a one-time Pair Code that assigns one instance to one project.

        A system administrator may hand any instance to any member of any project.
        Anyone else may only pair an instance they connected themselves, for
        themselves, into a project they belong to.
        """
        actor_user_id = _id(actor_user_id, "actor_user_id")
        project_id = _id(project_id, "project_id")
        channel_type = _key(channel_type, "channel_type")
        instance_id = _key(instance_id, "instance_id")
        assignee_user_id = (
            _id(assignee_user_id, "assignee_user_id")
            if assignee_user_id is not None else actor_user_id
        )
        if isinstance(ttl_seconds, bool) or not 60 <= ttl_seconds <= 900:
            raise ValueError("pairing challenge TTL must be between 60 and 900 seconds")
        with self._state() as state:
            self._require_project(state, project_id)
            self._require_user(state, assignee_user_id)
            if self._is_admin(state, actor_user_id):
                self._require_member_or_admin(state, project_id, assignee_user_id)
            else:
                self._require_member(state, project_id, actor_user_id)
                provision_value = state["channelProvisions"].get(
                    _channel_instance_key(channel_type, instance_id))
                if (
                    provision_value is None
                    or _channel_provision(provision_value).created_by_user_id != actor_user_id
                    or assignee_user_id != actor_user_id
                ):
                    raise CollaborationPermissionError(
                        "only an administrator may pair an instance you did not connect")
            now = _now()
            for key, value in tuple(state["pairingChallenges"].items()):
                challenge = _pairing_challenge(value)
                if challenge.expires_at_ms < now or challenge.consumed_at_ms is not None:
                    del state["pairingChallenges"][key]
            actor_challenges = sum(
                1 for value in state["pairingChallenges"].values()
                if _pairing_challenge(value).requested_by_user_id == actor_user_id)
            if actor_challenges >= _MAX_CHALLENGES_PER_USER:
                raise CollaborationConflictError("too many active pairing challenges for user")
            if len(state["pairingChallenges"]) >= _MAX_CHALLENGES:
                raise CollaborationConflictError("pairing challenge storage is full")
            code = new_assignment_code()
            challenge = PairingChallenge(
                id=_new_id(),
                code_digest=assignment_code_digest(code),
                requested_by_user_id=actor_user_id,
                assignee_user_id=assignee_user_id,
                project_id=project_id,
                channel_type=channel_type,
                instance_id=instance_id,
                expires_at_ms=now + ttl_seconds * 1000,
                verified_at_ms=None,
                verified_sender_id=None,
                consumed_at_ms=None,
                created_at_ms=now,
            )
            state["pairingChallenges"][challenge.id] = _encode_pairing_challenge(challenge)
            self._save(state)
            return challenge, code

    def get_pairing_challenge(
        self, actor_user_id: str, challenge_id: str
    ) -> PairingChallenge | None:
        actor_user_id = _id(actor_user_id, "actor_user_id")
        challenge_id = _id(challenge_id, "challenge_id")
        with self._state() as state:
            value = state["pairingChallenges"].get(challenge_id)
            if value is None:
                return None
            challenge = _pairing_challenge(value)
            return challenge if challenge.requested_by_user_id == actor_user_id else None

    def verify_pairing_challenge(
        self, code: str, *, channel_type: str, instance_id: str, sender_id: str
    ) -> PairingChallenge:
        """Bind the external sender who typed a valid code to the challenge's assignee."""
        digest = assignment_code_digest(normalize_assignment_code(code))
        channel_type = _key(channel_type, "channel_type")
        instance_id = _key(instance_id, "instance_id")
        sender_id = _key(sender_id, "sender_id")
        with self._state() as state:
            now = _now()
            challenge: PairingChallenge | None = None
            for value in state["pairingChallenges"].values():
                candidate = _pairing_challenge(value)
                if (
                    candidate.channel_type == channel_type
                    and candidate.instance_id == instance_id
                    and candidate.consumed_at_ms is None
                    and candidate.expires_at_ms >= now
                    and hmac.compare_digest(candidate.code_digest, digest)
                ):
                    challenge = candidate
                    break
            if challenge is None:
                raise CollaborationNotFoundError("pairing challenge not found or expired")
            if challenge.verified_at_ms is not None:
                if challenge.verified_sender_id != sender_id:
                    raise CollaborationConflictError(
                        "pairing challenge was verified by another sender")
                return challenge
            channel = runtime_channel_key(channel_type, instance_id)
            identity_key = _identity_key(channel, sender_id)
            identity_value = state["identities"].get(identity_key)
            if identity_value is not None:
                if _identity(identity_value).user_id != challenge.assignee_user_id:
                    raise CollaborationConflictError("channel sender belongs to another user")
            else:
                state["identities"][identity_key] = _encode_identity(
                    UserIdentity(challenge.assignee_user_id, channel, sender_id, now))
            verified = _replace_challenge(challenge, verified_at_ms=now, verified_sender_id=sender_id)
            state["pairingChallenges"][verified.id] = _encode_pairing_challenge(verified)
            self._save(state)
            return verified

    def consume_pairing_challenge(self, actor_user_id: str, challenge_id: str) -> PairingChallenge:
        """Turn a verified challenge into the instance's assignment."""
        actor_user_id = _id(actor_user_id, "actor_user_id")
        challenge_id = _id(challenge_id, "challenge_id")
        with self._state() as state:
            raw_challenge = state["pairingChallenges"].get(challenge_id)
            if raw_challenge is None:
                raise CollaborationNotFoundError("pairing challenge not found")
            challenge = _pairing_challenge(raw_challenge)
            now = _now()
            if challenge.requested_by_user_id != actor_user_id:
                raise CollaborationPermissionError("pairing challenge belongs to another user")
            if challenge.expires_at_ms < now:
                raise CollaborationConflictError("pairing challenge expired")
            if challenge.verified_at_ms is None or challenge.verified_sender_id is None:
                raise CollaborationConflictError("pairing challenge is not verified")
            if challenge.consumed_at_ms is not None:
                raise CollaborationConflictError("pairing challenge was already consumed")
            project = self._require_project(state, challenge.project_id)
            assignee = self._require_user(state, challenge.assignee_user_id)
            key = _channel_instance_key(challenge.channel_type, challenge.instance_id)
            existing_value = state["channelAssignments"].get(key)
            if existing_value is not None:
                existing = _channel_assignment(existing_value)
                if existing.project_id != project.id:
                    raise CollaborationConflictError(
                        "channel instance is already assigned to another project")
            created_at = (
                _channel_assignment(existing_value).created_at_ms
                if existing_value is not None else now
            )
            assignment = ChannelAssignment(
                challenge.channel_type, challenge.instance_id, project.id, assignee.id,
                True, actor_user_id, created_at, now,
            )
            state["channelAssignments"][key] = _encode_channel_assignment(assignment)
            member_key = _member_key(project.id, assignee.id)
            if member_key not in state["memberships"]:
                state["memberships"][member_key] = _encode_membership(
                    ProjectMembership(project.id, assignee.id, MembershipRole.MEMBER, now))
            if assignee.default_project_id is None:
                state["users"][assignee.id] = _encode_user(
                    _replace_user(assignee, default_project_id=project.id, updated_at_ms=now))
            consumed = _replace_challenge(challenge, consumed_at_ms=now)
            state["pairingChallenges"][consumed.id] = _encode_pairing_challenge(consumed)
            self._save(state)
            return consumed

    # -- conversation bindings and scope -------------------------------------

    def bind_conversation(self, channel: str, conversation_id: str, project_id: str,
                          user_id: str, *, thread_id: str | None = None) -> ConversationBinding:
        channel, conversation_id, project_id, user_id = (
            _key(channel, "channel"), _key(conversation_id, "conversation_id"),
            _id(project_id, "project_id"), _id(user_id, "user_id"))
        thread_id = _optional_key(thread_id, "thread_id")
        with self._state() as state:
            self._require_member(state, project_id, user_id)
            key = _conversation_key(channel, conversation_id, thread_id)
            existing = state["conversationBindings"].get(key)
            if existing is not None:
                binding = _binding(existing)
                if binding.project_id != project_id or binding.created_by_user_id != user_id:
                    raise CollaborationConflictError("conversation is already bound")
                return binding
            now = _now()
            binding = ConversationBinding(
                _new_id(), channel, conversation_id, thread_id, project_id, user_id, now, now)
            state["conversationBindings"][key] = _encode_binding(binding)
            self._save(state)
            return binding

    def resolve_binding(self, channel: str, conversation_id: str, *,
                        thread_id: str | None = None) -> ConversationBinding | None:
        with self._state() as state:
            value = state["conversationBindings"].get(_conversation_key(
                _key(channel, "channel"), _key(conversation_id, "conversation_id"),
                _optional_key(thread_id, "thread_id")))
            return _binding(value) if value is not None else None

    def unbind_conversation(self, channel: str, conversation_id: str,
                            actor_user_id: str, *, thread_id: str | None = None) -> bool:
        with self._state() as state:
            key = _conversation_key(
                _key(channel, "channel"), _key(conversation_id, "conversation_id"),
                _optional_key(thread_id, "thread_id"))
            value = state["conversationBindings"].get(key)
            if value is None:
                return False
            binding = _binding(value)
            self._require_manager(state, binding.project_id, _id(actor_user_id, "actor_user_id"))
            del state["conversationBindings"][key]
            self._save(state)
            return True

    def resolve_session_scope(
        self, user_id: str, project_id: str, *, channel: str, chat_id: str,
        thread_id: str | None = None,
    ) -> ConversationScope | None:
        """Return the scope a session's recorded owner still holds, or ``None``.

        Runtime-minted turns (cron, triggers, recovery) carry no external sender
        to resolve, so they inherit whatever their target session was already
        authorized for. ``None`` means that authorization no longer holds: the
        project or the user is gone, the membership was revoked, or the channel
        instance has since been reassigned elsewhere.
        """
        user_id = _id(user_id, "user_id")
        project_id = _id(project_id, "project_id")
        channel, chat_id = _key(channel, "channel"), _key(chat_id, "chat_id")
        thread_id = _optional_key(thread_id, "thread_id")
        with self._state() as state:
            user_value = state["users"].get(user_id)
            project_value = state["projects"].get(project_id)
            if user_value is None or project_value is None:
                return None
            if not self._is_member(state, project_id, user_id):
                return None
            assignment = self._assignment_for_channel(state, channel)
            if assignment is not None and (
                not assignment.enabled or assignment.project_id != project_id
            ):
                return None
            return self._project_scope(
                ConversationScopeKind.DIRECT, _user(user_value), _project(project_value),
                None, assignment, _scope_suffix(channel, chat_id, thread_id),
                state["localOwnerId"] == user_id,
            )

    def resolve_scope(self, channel: str, sender_id: str, chat_id: str,
                      metadata: Mapping[str, object] | None,
                      default_workspace: str | Path) -> ConversationScope:
        """Return the current request's only authorized project scope.

        A channel instance that carries an enabled assignment routes its direct
        messages to the assigned project and only admits that project's members.
        An unbound group always remains isolated.  Explicit bindings are accepted
        only while both their creator and the sender are members.
        """
        channel, sender_id, chat_id = (
            _key(channel, "channel"), _key(sender_id, "sender_id"), _key(chat_id, "chat_id"))
        metadata = metadata if metadata is not None else {}
        thread_id = _thread_id(metadata)
        suffix = _scope_suffix(channel, chat_id, thread_id)
        with self._state() as state:
            identity = state["identities"].get(_identity_key(channel, sender_id))
            user = (
                _user(state["users"][_identity(identity).user_id])
                if identity is not None else None
            )
            assignment = self._assignment_for_channel(state, channel)
            active = assignment if assignment is not None and assignment.enabled else None
            gated = assignment is not None or (
                metadata.get(CHANNEL_ASSIGNMENT_REQUIRED_METADATA_KEY) is True)
            denied = ConversationScope(
                ConversationScopeKind.ISOLATED, user.id if user else None, None, user,
                None, None, None, None, suffix, route_denied=True,
            )
            if gated and active is None:
                return denied
            binding_value = state["conversationBindings"].get(
                _conversation_key(channel, chat_id, thread_id))
            binding = _binding(binding_value) if binding_value is not None else None
            if (
                binding is not None and user is not None
                and binding.project_id in state["projects"]
                and self._is_member(state, binding.project_id, binding.created_by_user_id)
                and self._is_member(state, binding.project_id, user.id)
            ):
                if active is not None and active.project_id != binding.project_id:
                    return denied
                return self._project_scope(
                    ConversationScopeKind.BOUND, user,
                    _project(state["projects"][binding.project_id]), binding, active, suffix,
                    state["localOwnerId"] == user.id)
            if _is_direct(chat_id, sender_id, metadata) and user is not None:
                if active is not None:
                    if not self._is_member(state, active.project_id, user.id):
                        return denied
                    return self._project_scope(
                        ConversationScopeKind.DIRECT, user,
                        _project(state["projects"][active.project_id]), None, active, suffix,
                        state["localOwnerId"] == user.id)
                if (
                    user.default_project_id is not None
                    and user.default_project_id in state["projects"]
                    and self._is_member(state, user.default_project_id, user.id)
                ):
                    return self._project_scope(
                        ConversationScopeKind.DIRECT, user,
                        _project(state["projects"][user.default_project_id]), None, None, suffix,
                        state["localOwnerId"] == user.id)
            # validates the injected default without using it for isolation
            _workspace(default_workspace)
            return ConversationScope(
                ConversationScopeKind.ISOLATED, user.id if user else None, None, user,
                None, None, None, None, suffix,
            )

    # -- lock, normalization, and authorization ------------------------------

    @staticmethod
    def _project_scope(
        kind: ConversationScopeKind,
        user: User,
        project: Project,
        binding: ConversationBinding | None,
        assignment: ChannelAssignment | None,
        suffix: str,
        local_owner: bool = False,
    ) -> ConversationScope:
        return ConversationScope(
            kind, user.id, project.id, user, project, binding, assignment,
            project.workspace_path, suffix, is_local_owner=local_owner,
        )

    @staticmethod
    def _assignment_for_channel(state: _StoreState, channel: str) -> ChannelAssignment | None:
        for value in state["channelAssignments"].values():
            assignment = _channel_assignment(value)
            if runtime_channel_key(assignment.channel_type, assignment.instance_id) == channel:
                return assignment
        return None

    def _is_admin(self, state: _StoreState, user_id: str) -> bool:
        if state["localOwnerId"] == user_id:
            return True
        value = state["users"].get(user_id)
        return value is not None and _user(value).is_admin

    def _require_admin(self, state: _StoreState, user_id: str) -> None:
        if not self._is_admin(state, user_id):
            raise CollaborationPermissionError("system administrator role is required")

    def _is_member(self, state: _StoreState, project_id: str, user_id: str) -> bool:
        return _member_key(project_id, user_id) in state["memberships"]

    def _require_member(self, state: _StoreState, project_id: str, user_id: str) -> ProjectMembership:
        value = state["memberships"].get(_member_key(project_id, user_id))
        if value is None:
            raise CollaborationPermissionError("project membership is required")
        return _membership(value)

    def _require_member_or_admin(self, state: _StoreState, project_id: str, user_id: str) -> None:
        if not self._is_member(state, project_id, user_id) and not self._is_admin(state, user_id):
            raise CollaborationPermissionError("project membership is required")

    def _can_manage(self, state: _StoreState, project_id: str, user_id: str) -> bool:
        if self._is_admin(state, user_id):
            return True
        value = state["memberships"].get(_member_key(project_id, user_id))
        return value is not None and _membership(value).role is MembershipRole.OWNER

    def _require_manager(self, state: _StoreState, project_id: str, user_id: str) -> None:
        """Require a project owner or a system administrator."""
        if not self._can_manage(state, project_id, user_id):
            raise CollaborationPermissionError("project owner role is required")

    def _owner_count(self, state: _StoreState, project_id: str) -> int:
        return sum(
            item["projectId"] == project_id and item["role"] == MembershipRole.OWNER.value
            for item in state["memberships"].values())

    def _reset_default_project(self, state: _StoreState, user: User, now: int) -> None:
        """Point a user whose default project vanished at another project they belong to."""
        remaining = sorted(
            (
                _project(state["projects"][membership.project_id])
                for value in state["memberships"].values()
                if (membership := _membership(value)).user_id == user.id
                and membership.project_id in state["projects"]
            ),
            key=lambda project: (project.created_at_ms, project.id),
        )
        replacement = remaining[0].id if remaining else None
        state["users"][user.id] = _encode_user(
            _replace_user(user, default_project_id=replacement, updated_at_ms=now))

    def _require_user(self, state: _StoreState, user_id: str) -> User:
        value = state["users"].get(user_id)
        if value is None:
            raise CollaborationNotFoundError("user not found")
        return _user(value)

    def _require_project(self, state: _StoreState, project_id: str) -> Project:
        value = state["projects"].get(project_id)
        if value is None:
            raise CollaborationNotFoundError("project not found")
        return _project(value)

    class _State:
        def __init__(self, store: "CollaborationStore") -> None:
            self.store = store

        def __enter__(self) -> _StoreState:
            self.store.root.mkdir(parents=True, exist_ok=True)
            self.store._lock.acquire()
            try:
                return self.store._load()
            except BaseException:
                self.store._lock.release()
                raise

        def __exit__(self, *args: object) -> None:
            self.store._lock.release()

    def _state(self) -> "CollaborationStore._State":
        return CollaborationStore._State(self)

    def _load(self) -> _StoreState:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return _empty()
        if len(raw) > _MAX_FILE_BYTES:
            raise CollaborationStoreFormatError("collaboration store exceeds size limit")
        try:
            payload: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollaborationStoreFormatError("collaboration store is not valid JSON") from exc
        version = _mapping(payload, "unsupported collaboration store schema").get("schemaVersion")
        state = _normalize(payload)
        if version != _SCHEMA:
            self._backup_before_migration(raw, version)
            self._save(state)
        return state

    def _backup_before_migration(self, raw: bytes, version: object) -> None:
        """Keep the pre-migration document next to the store, once per source version."""
        label = version if isinstance(version, int) and not isinstance(version, bool) else "unknown"
        backup = self.path.with_name(f"{self.path.stem}.v{label}.bak{self.path.suffix}")
        if backup.exists():
            return
        try:
            with open(backup, "xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            return
        logger.info("collaboration store backup written to {}", backup)

    def _save(self, state: _StoreState) -> None:
        payload = json.dumps(
            _normalize(state), ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode()
        if len(payload) > _MAX_FILE_BYTES:
            raise CollaborationStoreError("collaboration store exceeds size limit")
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_dir(self.path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def _empty() -> _StoreState:
    return {
        "schemaVersion": _SCHEMA,
        "localOwnerId": None,
        "users": {},
        "identities": {},
        "projects": {},
        "memberships": {},
        "channelProvisions": {},
        "channelAssignments": {},
        "pairingChallenges": {},
        "conversationBindings": {},
    }


_DROPPED_LEGACY_COLLECTIONS = (
    "organizations", "organizationMemberships", "bots", "botProjectAssignments",
    "botCapabilityProfiles", "vaults", "personas", "shareGrants", "personalTasks",
    "taskLists", "tasks", "extensionProfiles", "contextSources",
)


def _migrate_legacy(root: Mapping[str, object], version: int) -> dict[str, object]:
    """Collapse a v1–v8 multi-tenant document into the single-admin v9 shape.

    Users, identities, projects, memberships, channel provenance, and conversation
    bindings survive. Bot channel claims become channel assignments routed to the
    project the bot served on that instance; a claim without a route keeps its
    claimant's default project but starts disabled so an administrator must
    switch it on. Organizations, bots, personas, vaults, tasks, sharing grants,
    context sources, extension profiles, and in-flight Pair Codes are dropped and
    counted in the log.
    """
    def collection(name: str) -> _Record:
        raw = root.get(name)
        return _mapping(raw, f"invalid legacy {name}") if raw is not None else {}

    users: dict[str, _Record] = {}
    for user_id, raw_user in collection("users").items():
        user = _mapping(raw_user, "invalid legacy user")
        default_project = user.get("defaultProjectId")
        users[user_id] = {
            "id": user.get("id"),
            "displayName": user.get("displayName"),
            "defaultProjectId": default_project if isinstance(default_project, str) else None,
            "isAdmin": False,
            "createdAtMs": user.get("createdAtMs"),
            "updatedAtMs": user.get("updatedAtMs"),
        }
    memberships = collection("memberships")
    projects: dict[str, _Record] = {}
    for project_id, raw_project in collection("projects").items():
        project = _mapping(raw_project, "invalid legacy project")
        projects[project_id] = {
            "id": project.get("id"),
            "name": project.get("name"),
            "workspacePath": project.get("workspacePath"),
            "createdByUserId": project.get("createdByUserId"),
            "allowedSkills": None,
            "allowedMcpServers": None,
            "createdAtMs": project.get("createdAtMs"),
            "updatedAtMs": project.get("updatedAtMs"),
        }
    provisions: dict[str, _Record] = {}
    for key, raw_provision in collection("channelProvisions").items():
        provision = _mapping(raw_provision, "invalid legacy channel provision")
        provisions[key] = {
            "channelType": provision.get("channelType"),
            "instanceId": provision.get("instanceId"),
            "createdByUserId": provision.get("createdByUserId"),
            "createdAtMs": provision.get("createdAtMs"),
        }

    routes = collection("botProjectChannels")
    profiles = collection("botCapabilityProfiles")
    assignments: dict[str, _Record] = {}
    for key, raw_claim in collection("botChannelAssignments").items():
        claim = _mapping(raw_claim, "invalid legacy bot channel assignment")
        bot_id = claim.get("botId")
        claimant_id = claim.get("claimedByUserId")
        channel_type = claim.get("channelType")
        instance_id = claim.get("instanceId")
        if (
            not isinstance(claimant_id, str)
            or not isinstance(channel_type, str)
            or not isinstance(instance_id, str)
        ):
            continue
        claimant = users.get(claimant_id)
        if claimant is None:
            continue
        enabled_routes = [
            _mapping(raw_route, "invalid legacy bot project channel")
            for raw_route in routes.values()
            if _mapping(raw_route, "invalid legacy bot project channel").get("botId") == bot_id
            and _mapping(raw_route, "invalid legacy bot project channel").get("channelType")
            == channel_type
            and _mapping(raw_route, "invalid legacy bot project channel").get("instanceId")
            == instance_id
            and _mapping(raw_route, "invalid legacy bot project channel").get("enabled") is True
        ]
        route_projects = [
            route.get("projectId") for route in enabled_routes
            if isinstance(route.get("projectId"), str) and route.get("projectId") in projects
        ]
        default_project = claimant.get("defaultProjectId")
        project_id: str | None
        if isinstance(default_project, str) and default_project in route_projects:
            project_id = default_project
        elif route_projects:
            project_id = cast(str, route_projects[0])
        elif isinstance(default_project, str) and default_project in projects:
            project_id = default_project
        else:
            project_id = None
        if project_id is None or _member_key(project_id, claimant_id) not in memberships:
            continue
        created_at = claim.get("createdAtMs")
        assignments[key] = {
            "channelType": channel_type,
            "instanceId": instance_id,
            "projectId": project_id,
            "assigneeUserId": claimant_id,
            "enabled": bool(route_projects),
            "createdByUserId": claimant_id,
            "createdAtMs": created_at,
            "updatedAtMs": created_at,
        }
        for scoped in (
            profiles.get(f"{bot_id}\x00{project_id}"), profiles.get(f"{bot_id}\x00"),
        ):
            if scoped is None:
                continue
            settings = _mapping(scoped, "invalid legacy bot capability profile").get("settings")
            if not isinstance(settings, Mapping):
                continue
            typed_settings = cast(Mapping[str, object], settings)
            target = projects[project_id]
            if target["allowedSkills"] is None and isinstance(typed_settings.get("skills"), list):
                target["allowedSkills"] = list(cast(list[object], typed_settings["skills"]))
            if target["allowedMcpServers"] is None and isinstance(
                    typed_settings.get("mcpServers"), list):
                target["allowedMcpServers"] = list(
                    cast(list[object], typed_settings["mcpServers"]))
            break

    dropped = {
        name: len(collection(name)) for name in _DROPPED_LEGACY_COLLECTIONS
        if root.get(name) is not None
    }
    dropped["pairingChallenges"] = len(collection("pairingChallenges"))
    logger.info(
        "collaboration store migrated from v{} to v{}: kept {} users, {} projects, "
        "{} channel assignments; dropped {}",
        version, _SCHEMA, len(users), len(projects), len(assignments),
        ", ".join(f"{count} {name}" for name, count in sorted(dropped.items()) if count) or "nothing",
    )
    return {
        "schemaVersion": _SCHEMA,
        "localOwnerId": root.get("localOwnerId"),
        "users": users,
        "identities": collection("identities"),
        "projects": projects,
        "memberships": memberships,
        "channelProvisions": provisions,
        "channelAssignments": assignments,
        "pairingChallenges": {},
        "conversationBindings": collection("conversationBindings"),
    }


def _normalize(data: object) -> _StoreState:
    root = _mapping(data, "unsupported collaboration store schema")
    version = root.get("schemaVersion")
    if isinstance(version, int) and not isinstance(version, bool) and 1 <= version < _SCHEMA:
        root = _migrate_legacy(root, version)
    if set(root) != set(_empty()) or root.get("schemaVersion") != _SCHEMA:
        raise CollaborationStoreFormatError("unsupported collaboration store schema")
    result = _empty()
    local_owner = root["localOwnerId"]
    result["localOwnerId"] = _id(local_owner, "localOwnerId") if local_owner is not None else None
    result["users"] = _normalize_collection(
        root["users"], "users", _user, _encode_user, lambda user: user.id)
    result["identities"] = _normalize_collection(
        root["identities"], "identities", _identity, _encode_identity,
        lambda identity: _identity_key(identity.channel, identity.sender_id))
    result["projects"] = _normalize_collection(
        root["projects"], "projects", _project, _encode_project, lambda project: project.id)
    result["memberships"] = _normalize_collection(
        root["memberships"], "memberships", _membership, _encode_membership,
        lambda membership: _member_key(membership.project_id, membership.user_id))
    result["channelProvisions"] = _normalize_collection(
        root["channelProvisions"], "channelProvisions", _channel_provision,
        _encode_channel_provision,
        lambda provision: _channel_instance_key(provision.channel_type, provision.instance_id))
    result["channelAssignments"] = _normalize_collection(
        root["channelAssignments"], "channelAssignments", _channel_assignment,
        _encode_channel_assignment,
        lambda assignment: _channel_instance_key(assignment.channel_type, assignment.instance_id))
    result["pairingChallenges"] = _normalize_collection(
        root["pairingChallenges"], "pairingChallenges", _pairing_challenge,
        _encode_pairing_challenge, lambda challenge: challenge.id)
    result["conversationBindings"] = _normalize_collection(
        root["conversationBindings"], "conversationBindings", _binding, _encode_binding,
        lambda binding: _conversation_key(
            binding.channel, binding.conversation_id, binding.thread_id))
    _references(result)
    return result


def _normalize_collection(
        raw: object, collection: str, decode: Callable[[Mapping[str, object]], _Entity],
        encode: Callable[[_Entity], _Record], record_key: Callable[[_Entity], str]) -> dict[str, _Record]:
    records = _mapping(raw, f"invalid {collection}")
    if len(records) > _MAX_ITEMS:
        raise CollaborationStoreFormatError(f"invalid {collection}")
    normalized: dict[str, _Record] = {}
    for key, value in records.items():
        record = _mapping(value, f"invalid {collection} record")
        entity = decode(record)
        if key != record_key(entity):
            raise CollaborationStoreFormatError(f"invalid {collection} key")
        normalized[key] = encode(entity)
    return normalized


def _references(state: _StoreState) -> None:
    users, projects, memberships = state["users"], state["projects"], state["memberships"]
    if state["localOwnerId"] is not None and state["localOwnerId"] not in users:
        raise CollaborationStoreFormatError("local owner is missing")
    for value in state["identities"].values():
        if _identity(value).user_id not in users:
            raise CollaborationStoreFormatError("identity references missing user")
    for value in projects.values():
        if _project(value).created_by_user_id not in users:
            raise CollaborationStoreFormatError("project owner is missing")
    project_owner_counts: dict[str, int] = {}
    for value in memberships.values():
        membership = _membership(value)
        if membership.project_id not in projects or membership.user_id not in users:
            raise CollaborationStoreFormatError("invalid membership")
        if membership.role is MembershipRole.OWNER:
            project_owner_counts[membership.project_id] = (
                project_owner_counts.get(membership.project_id, 0) + 1)
    if set(project_owner_counts) != set(projects):
        raise CollaborationStoreFormatError("a project must retain an owner")
    for value in users.values():
        user = _user(value)
        if user.default_project_id is not None and _member_key(
                user.default_project_id, user.id) not in memberships:
            raise CollaborationStoreFormatError("invalid default project")
    for value in state["channelProvisions"].values():
        if _channel_provision(value).created_by_user_id not in users:
            raise CollaborationStoreFormatError("channel provisioner is missing")
    for value in state["channelAssignments"].values():
        assignment = _channel_assignment(value)
        if assignment.project_id not in projects or assignment.created_by_user_id not in users:
            raise CollaborationStoreFormatError("invalid channel assignment")
        if _member_key(assignment.project_id, assignment.assignee_user_id) not in memberships:
            raise CollaborationStoreFormatError("channel assignee lacks project membership")
    for value in state["pairingChallenges"].values():
        challenge = _pairing_challenge(value)
        if (
            challenge.requested_by_user_id not in users
            or challenge.assignee_user_id not in users
            or challenge.project_id not in projects
        ):
            raise CollaborationStoreFormatError("invalid pairing challenge references")
        if challenge.expires_at_ms < challenge.created_at_ms:
            raise CollaborationStoreFormatError("invalid pairing challenge lifetime")
    for value in state["conversationBindings"].values():
        binding = _binding(value)
        if _member_key(binding.project_id, binding.created_by_user_id) not in memberships:
            raise CollaborationStoreFormatError("invalid conversation binding")


def _mapping(value: object, error: str) -> _Record:
    if not isinstance(value, Mapping):
        raise CollaborationStoreFormatError(error)
    mapped: _Record = {}
    for key, item in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            raise CollaborationStoreFormatError(error)
        mapped[key] = item
    return mapped


def _shape(data: Mapping[str, object], fields: set[str]) -> None:
    if set(data) != fields:
        raise CollaborationStoreFormatError("record has unsupported shape")


def _user(data: Mapping[str, object]) -> User:
    _shape(data, {"id", "displayName", "defaultProjectId", "isAdmin", "createdAtMs", "updatedAtMs"})
    raw_default_project = data["defaultProjectId"]
    is_admin = data["isAdmin"]
    if not isinstance(is_admin, bool):
        raise CollaborationStoreFormatError("invalid user admin flag")
    return User(
        _id(data["id"], "id"),
        _string(data["displayName"], "displayName"),
        _id(raw_default_project, "defaultProjectId") if raw_default_project is not None else None,
        _time(data["createdAtMs"]),
        _time(data["updatedAtMs"]),
        is_admin,
    )


def _encode_user(x: User) -> _Record:
    return {
        "id": x.id, "displayName": x.display_name, "defaultProjectId": x.default_project_id,
        "isAdmin": x.is_admin, "createdAtMs": x.created_at_ms, "updatedAtMs": x.updated_at_ms,
    }


def _replace_user(
    user: User, *, display_name: str | None = None, default_project_id: str | None | object = ...,
    is_admin: bool | None = None, updated_at_ms: int | None = None,
) -> User:
    return User(
        user.id,
        display_name if display_name is not None else user.display_name,
        cast(str | None, default_project_id) if default_project_id is not ... else user.default_project_id,
        user.created_at_ms,
        updated_at_ms if updated_at_ms is not None else user.updated_at_ms,
        is_admin if is_admin is not None else user.is_admin,
    )


def _identity(data: Mapping[str, object]) -> UserIdentity:
    _shape(data, {"userId", "channel", "senderId", "createdAtMs"})
    return UserIdentity(_id(data["userId"], "userId"), _key(data["channel"], "channel"),
                        _key(data["senderId"], "senderId"), _time(data["createdAtMs"]))


def _encode_identity(x: UserIdentity) -> _Record:
    return {"userId": x.user_id, "channel": x.channel, "senderId": x.sender_id,
            "createdAtMs": x.created_at_ms}


def _project(data: Mapping[str, object]) -> Project:
    _shape(data, {
        "id", "name", "workspacePath", "createdByUserId", "allowedSkills",
        "allowedMcpServers", "createdAtMs", "updatedAtMs",
    })
    return Project(
        _id(data["id"], "id"), _string(data["name"], "name"),
        _workspace(data["workspacePath"]), _id(data["createdByUserId"], "createdByUserId"),
        _time(data["createdAtMs"]), _time(data["updatedAtMs"]),
        _allowlist(data["allowedSkills"], "allowedSkills"),
        _allowlist(data["allowedMcpServers"], "allowedMcpServers"),
    )


def _encode_project(x: Project) -> _Record:
    return {
        "id": x.id, "name": x.name, "workspacePath": x.workspace_path,
        "createdByUserId": x.created_by_user_id,
        "allowedSkills": list(x.allowed_skills) if x.allowed_skills is not None else None,
        "allowedMcpServers": (
            list(x.allowed_mcp_servers) if x.allowed_mcp_servers is not None else None
        ),
        "createdAtMs": x.created_at_ms, "updatedAtMs": x.updated_at_ms,
    }


def _replace_project(
    project: Project, *, workspace_path: str | None = None, updated_at_ms: int | None = None,
) -> Project:
    return Project(
        project.id, project.name,
        workspace_path if workspace_path is not None else project.workspace_path,
        project.created_by_user_id, project.created_at_ms,
        updated_at_ms if updated_at_ms is not None else project.updated_at_ms,
        project.allowed_skills, project.allowed_mcp_servers,
    )


def _membership(data: Mapping[str, object]) -> ProjectMembership:
    _shape(data, {"projectId", "userId", "role", "createdAtMs"})
    return ProjectMembership(_id(data["projectId"], "projectId"), _id(data["userId"], "userId"),
                             _role(data["role"]), _time(data["createdAtMs"]))


def _encode_membership(x: ProjectMembership) -> _Record:
    return {"projectId": x.project_id, "userId": x.user_id, "role": x.role.value,
            "createdAtMs": x.created_at_ms}


def _channel_provision(data: Mapping[str, object]) -> ChannelProvision:
    _shape(data, {"channelType", "instanceId", "createdByUserId", "createdAtMs"})
    return ChannelProvision(
        channel_type=_key(data["channelType"], "channelType"),
        instance_id=_key(data["instanceId"], "instanceId"),
        created_by_user_id=_id(data["createdByUserId"], "createdByUserId"),
        created_at_ms=_time(data["createdAtMs"]),
    )


def _encode_channel_provision(x: ChannelProvision) -> _Record:
    return {"channelType": x.channel_type, "instanceId": x.instance_id,
            "createdByUserId": x.created_by_user_id, "createdAtMs": x.created_at_ms}


def _channel_assignment(data: Mapping[str, object]) -> ChannelAssignment:
    _shape(data, {
        "channelType", "instanceId", "projectId", "assigneeUserId", "enabled",
        "createdByUserId", "createdAtMs", "updatedAtMs",
    })
    enabled = data["enabled"]
    if not isinstance(enabled, bool):
        raise CollaborationStoreFormatError("invalid channel assignment enabled state")
    return ChannelAssignment(
        channel_type=_key(data["channelType"], "channelType"),
        instance_id=_key(data["instanceId"], "instanceId"),
        project_id=_id(data["projectId"], "projectId"),
        assignee_user_id=_id(data["assigneeUserId"], "assigneeUserId"),
        enabled=enabled,
        created_by_user_id=_id(data["createdByUserId"], "createdByUserId"),
        created_at_ms=_time(data["createdAtMs"]),
        updated_at_ms=_time(data["updatedAtMs"]),
    )


def _encode_channel_assignment(x: ChannelAssignment) -> _Record:
    return {
        "channelType": x.channel_type, "instanceId": x.instance_id, "projectId": x.project_id,
        "assigneeUserId": x.assignee_user_id, "enabled": x.enabled,
        "createdByUserId": x.created_by_user_id, "createdAtMs": x.created_at_ms,
        "updatedAtMs": x.updated_at_ms,
    }


def _pairing_challenge(data: Mapping[str, object]) -> PairingChallenge:
    _shape(data, {
        "id", "codeDigest", "requestedByUserId", "assigneeUserId", "projectId", "channelType",
        "instanceId", "expiresAtMs", "verifiedAtMs", "verifiedSenderId", "consumedAtMs",
        "createdAtMs",
    })
    verified_at_ms = data["verifiedAtMs"]
    consumed_at_ms = data["consumedAtMs"]
    return PairingChallenge(
        id=_id(data["id"], "id"),
        code_digest=_key(data["codeDigest"], "codeDigest"),
        requested_by_user_id=_id(data["requestedByUserId"], "requestedByUserId"),
        assignee_user_id=_id(data["assigneeUserId"], "assigneeUserId"),
        project_id=_id(data["projectId"], "projectId"),
        channel_type=_key(data["channelType"], "channelType"),
        instance_id=_key(data["instanceId"], "instanceId"),
        expires_at_ms=_time(data["expiresAtMs"]),
        verified_at_ms=_time(verified_at_ms) if verified_at_ms is not None else None,
        verified_sender_id=_optional_key(data["verifiedSenderId"], "verifiedSenderId"),
        consumed_at_ms=_time(consumed_at_ms) if consumed_at_ms is not None else None,
        created_at_ms=_time(data["createdAtMs"]),
    )


def _encode_pairing_challenge(x: PairingChallenge) -> _Record:
    return {
        "id": x.id, "codeDigest": x.code_digest, "requestedByUserId": x.requested_by_user_id,
        "assigneeUserId": x.assignee_user_id, "projectId": x.project_id,
        "channelType": x.channel_type, "instanceId": x.instance_id,
        "expiresAtMs": x.expires_at_ms, "verifiedAtMs": x.verified_at_ms,
        "verifiedSenderId": x.verified_sender_id, "consumedAtMs": x.consumed_at_ms,
        "createdAtMs": x.created_at_ms,
    }


def _replace_challenge(
    challenge: PairingChallenge, *, verified_at_ms: int | None = None,
    verified_sender_id: str | None = None, consumed_at_ms: int | None = None,
) -> PairingChallenge:
    return PairingChallenge(
        challenge.id, challenge.code_digest, challenge.requested_by_user_id,
        challenge.assignee_user_id, challenge.project_id, challenge.channel_type,
        challenge.instance_id, challenge.expires_at_ms,
        verified_at_ms if verified_at_ms is not None else challenge.verified_at_ms,
        verified_sender_id if verified_sender_id is not None else challenge.verified_sender_id,
        consumed_at_ms if consumed_at_ms is not None else challenge.consumed_at_ms,
        challenge.created_at_ms,
    )


def _binding(data: Mapping[str, object]) -> ConversationBinding:
    _shape(data, {"id", "channel", "conversationId", "threadId", "projectId", "createdByUserId",
                  "createdAtMs", "updatedAtMs"})
    return ConversationBinding(
        _id(data["id"], "id"), _key(data["channel"], "channel"),
        _key(data["conversationId"], "conversationId"), _optional_key(data["threadId"], "threadId"),
        _id(data["projectId"], "projectId"), _id(data["createdByUserId"], "createdByUserId"),
        _time(data["createdAtMs"]), _time(data["updatedAtMs"]))


def _encode_binding(x: ConversationBinding) -> _Record:
    return {"id": x.id, "channel": x.channel, "conversationId": x.conversation_id,
            "threadId": x.thread_id, "projectId": x.project_id,
            "createdByUserId": x.created_by_user_id, "createdAtMs": x.created_at_ms,
            "updatedAtMs": x.updated_at_ms}


def _string(value: object, field: str, *, limit: int = _MAX_STRING, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError(f"{field} must be a string")
    value = value.strip()
    if (not value and not empty) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise CollaborationStoreFormatError(f"invalid {field}")
    return value


def _id(value: object, field: str) -> str:
    return _string(value, field, limit=128)


def _key(value: object, field: str) -> str:
    return _string(value, field)


def _optional_key(value: object, field: str) -> str | None:
    return _key(value, field) if value is not None else None


def _allowlist(value: object, field: str) -> tuple[str, ...] | None:
    """Return a bounded, de-duplicated capability allowlist; ``None`` means unrestricted."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise CollaborationStoreFormatError(f"{field} must be a list or null")
    raw_items = cast(Sequence[object], value)
    if len(raw_items) > _MAX_ALLOWLIST:
        raise CollaborationStoreFormatError(f"{field} has too many entries")
    items: list[str] = []
    for raw_item in raw_items:
        item = _key(raw_item, field)
        if item not in items:
            items.append(item)
    return tuple(items)


def _workspace(value: object) -> str:
    if isinstance(value, str):
        text: str | bytes = value
    elif isinstance(value, os.PathLike):
        path_value = cast(os.PathLike[str] | os.PathLike[bytes], value)
        text = os.fspath(path_value)
    else:
        raise CollaborationStoreFormatError("workspace_path must be a path")
    if not isinstance(text, str):
        raise CollaborationStoreFormatError("workspace_path must be a text path")
    text = text.strip()
    if not text or len(text) > _MAX_TEXT or any(ord(char) < 32 for char in text):
        raise CollaborationStoreFormatError("invalid workspace_path")
    return str(Path(text).expanduser().resolve(strict=False))


def _time(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CollaborationStoreFormatError("invalid timestamp")
    return value


def _role(value: object) -> MembershipRole:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError("invalid membership role")
    try:
        return MembershipRole(value)
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid membership role") from exc


def _identity_key(channel: str, sender_id: str) -> str:
    return f"{channel}\x00{sender_id}"


def _member_key(project_id: str, user_id: str) -> str:
    return f"{project_id}\x00{user_id}"


def _channel_instance_key(channel_type: str, instance_id: str) -> str:
    return f"{channel_type}\x00{instance_id}"


def _conversation_key(channel: str, conversation_id: str, thread_id: str | None) -> str:
    return f"{channel}\x00{conversation_id}\x00{thread_id or ''}"


def _new_id() -> str:
    return f"col_{uuid.uuid4().hex}"


def _now() -> int:
    return time.time_ns() // 1_000_000


def _thread_id(metadata: Mapping[str, object]) -> str | None:
    for key in ("thread_id", "threadId", "message_thread_id", "messageThreadId", "root_id", "rootId"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _key(value, key)
    return None


def _is_direct(chat_id: str, sender_id: str, metadata: Mapping[str, object]) -> bool:
    if metadata.get("chat_type") == "p2p" or metadata.get("chatType") == "p2p":
        return True
    if str(metadata.get("chat_type") or metadata.get("chatType") or "").lower() == "group":
        return False
    if str(metadata.get("direct") or "").lower() in {"1", "true", "yes"}:
        return True
    if metadata.get("is_group") is True or metadata.get("isGroup") is True:
        return False
    if metadata.get("is_direct") is True or metadata.get("isDirect") is True:
        return True
    for key in ("chat_type", "chatType", "conversation_type", "conversationType"):
        value = metadata.get(key)
        if isinstance(value, str):
            value = value.strip().lower().replace("-", "_")
            if value in {"group", "group_chat", "groupchat", "channel", "room"}:
                return False
            if value in {"direct", "dm", "p2p", "private", "one_to_one"}:
                return True
    return chat_id == sender_id


def _scope_suffix(channel: str, chat_id: str, thread_id: str | None) -> str:
    return "collab-" + hashlib.sha256("\x00".join(
        ("v1", channel, chat_id, thread_id or "")).encode()).hexdigest()[:24]


def _fsync_dir(path: Path) -> None:
    with suppress(PermissionError, NotImplementedError):
        fd = os.open(path, os.O_RDONLY)
        try:
            try:
                os.fsync(fd)
            except OSError as exc:
                if exc.errno != 22:
                    raise
        finally:
            os.close(fd)
