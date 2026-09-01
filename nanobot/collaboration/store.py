"""Durable, local JSON storage for collaboration state."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import TypeAlias, TypedDict, TypeVar, cast

from filelock import FileLock

from nanobot.collaboration.models import (
    ContextSource,
    ContextSourceKind,
    ConversationBinding,
    ConversationScope,
    ConversationScopeKind,
    ExtensionProfile,
    JsonObject,
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
    freeze_json,
    thaw_json,
)
from nanobot.config.paths import get_runtime_subdir

_SCHEMA = 5
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_STRING = 512
_MAX_TEXT = 16_000
_MAX_JSON_ITEMS = 256
_MAX_JSON_DEPTH = 8


_Record: TypeAlias = dict[str, object]
_Entity = TypeVar("_Entity")


class _StoreState(TypedDict):
    schemaVersion: int
    localOwnerId: str | None
    users: dict[str, _Record]
    identities: dict[str, _Record]
    vaults: dict[str, _Record]
    personas: dict[str, _Record]
    shareGrants: dict[str, _Record]
    personalTasks: dict[str, _Record]
    organizations: dict[str, _Record]
    organizationMemberships: dict[str, _Record]
    projects: dict[str, _Record]
    memberships: dict[str, _Record]
    taskLists: dict[str, _Record]
    tasks: dict[str, _Record]
    conversationBindings: dict[str, _Record]
    extensionProfiles: dict[str, _Record]
    contextSources: dict[str, _Record]


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
        """Lazily initialize the local owner, default vault, organization, and project."""
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
            self._ensure_personal_organization(state, owner, now)
            vault_value = state["vaults"].get(owner.default_vault_id) if owner.default_vault_id else None
            if vault_value is None:
                vault = Vault(_new_id(), owner.id, "Private", VaultKind.PRIVATE, now, now)
                state["vaults"][vault.id] = _encode_vault(vault)
                owner = User(owner.id, owner.display_name, owner.default_project_id,
                             owner.created_at_ms, now, vault.id, owner.default_persona_id)
                state["users"][owner.id] = _encode_user(owner)
            project_value = state["projects"].get(
                owner.default_project_id) if owner.default_project_id is not None else None
            project = _project(project_value) if project_value is not None else None
            if project is None:
                project = Project(
                    _new_id(), "Local project", workspace, owner.id, now, now,
                    self._personal_organization_id(state, owner.id))
                state["projects"][project.id] = _encode_project(project)
                state["memberships"][_member_key(project.id, owner.id)] = _encode_membership(
                    ProjectMembership(project.id, owner.id, MembershipRole.OWNER, now))
                owner = User(owner.id, owner.display_name, project.id, owner.created_at_ms, now,
                             owner.default_vault_id, owner.default_persona_id)
                state["users"][owner.id] = _encode_user(owner)
            elif project.workspace_path != workspace:
                project = Project(project.id, project.name, workspace, project.created_by_user_id,
                                  project.created_at_ms, now, project.organization_id)
                state["projects"][project.id] = _encode_project(project)
            if _member_key(project.id, owner.id) not in state["memberships"]:
                state["memberships"][_member_key(project.id, owner.id)] = _encode_membership(
                    ProjectMembership(project.id, owner.id, MembershipRole.OWNER, now))
            self._save(state)
            return owner, project

    def create_user(self, display_name: str) -> User:
        with self._state() as state:
            now = _now()
            user_id = _new_id()
            vault = Vault(_new_id(), user_id, "Private", VaultKind.PRIVATE, now, now)
            user = User(
                user_id,
                _string(display_name, "display_name"),
                None,
                now,
                now,
                vault.id,
            )
            state["users"][user.id] = _encode_user(user)
            state["vaults"][vault.id] = _encode_vault(vault)
            self._ensure_personal_organization(state, user, now)
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
            updated = User(
                user.id,
                display_name,
                user.default_project_id,
                user.created_at_ms,
                _now(),
                user.default_vault_id,
                user.default_persona_id,
            )
            state["users"][updated.id] = _encode_user(updated)
            self._save(state)
            return updated

    def get_user(self, user_id: str) -> User | None:
        with self._state() as state:
            value = state["users"].get(_id(user_id, "user_id"))
            return _user(value) if value is not None else None

    def list_users(self) -> list[User]:
        with self._state() as state:
            return sorted((_user(value) for value in state["users"].values(
            )), key=lambda item: (item.created_at_ms, item.id))

    def update_user_default_project(
            self, user_id: str, project_id: str | None) -> User:
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            user = self._require_user(state, user_id)
            if project_id is not None:
                project_id = _id(project_id, "project_id")
                self._require_member(state, project_id, user_id)
            updated = User(
                user.id,
                user.display_name,
                project_id,
                user.created_at_ms,
                _now(),
                user.default_vault_id,
                user.default_persona_id)
            state["users"][user.id] = _encode_user(updated)
            self._save(state)
            return updated


    def create_vault(
        self, user_id: str, name: str, *, kind: VaultKind = VaultKind.PRIVATE
    ) -> Vault:
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            self._require_user(state, user_id)
            now = _now()
            vault = Vault(_new_id(), user_id, _string(name, "name"), kind, now, now)
            state["vaults"][vault.id] = _encode_vault(vault)
            self._save(state)
            return vault

    def list_vaults(self, user_id: str) -> list[Vault]:
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            self._require_user(state, user_id)
            return sorted(
                (vault for value in state["vaults"].values()
                 if (vault := _vault(value)).owner_user_id == user_id),
                key=lambda vault: (vault.created_at_ms, vault.id),
            )

    def get_vault(self, actor_user_id: str, vault_id: str) -> Vault | None:
        actor_user_id, vault_id = _id(actor_user_id, "actor_user_id"), _id(vault_id, "vault_id")
        with self._state() as state:
            self._require_user(state, actor_user_id)
            value = state["vaults"].get(vault_id)
            if value is None:
                return None
            vault = _vault(value)
            if self._can_access_vault(state, actor_user_id, vault_id, SharePermission.READ):
                return vault
            return None

    def update_user_default_vault(self, user_id: str, vault_id: str) -> User:
        user_id, vault_id = _id(user_id, "user_id"), _id(vault_id, "vault_id")
        with self._state() as state:
            user = self._require_user(state, user_id)
            vault = self._require_vault(state, vault_id)
            if vault.owner_user_id != user.id:
                raise CollaborationPermissionError("only the owner may select a default vault")
            updated = User(user.id, user.display_name, user.default_project_id,
                           user.created_at_ms, _now(), vault.id, user.default_persona_id)
            state["users"][user.id] = _encode_user(updated)
            self._save(state)
            return updated

    def create_persona(
        self, user_id: str, name: str, default_vault_id: str, *, instructions: str = ""
    ) -> Persona:
        user_id, default_vault_id = _id(user_id, "user_id"), _id(default_vault_id, "default_vault_id")
        with self._state() as state:
            user = self._require_user(state, user_id)
            vault = self._require_vault(state, default_vault_id)
            if vault.owner_user_id != user_id:
                raise CollaborationPermissionError("persona vault must be user-owned")
            now = _now()
            persona = Persona(
                _new_id(), user_id, _string(name, "name"), vault.id,
                _string(instructions, "instructions", empty=True), now, now,
            )
            state["personas"][persona.id] = _encode_persona(persona)
            if user.default_persona_id is None:
                user = User(user.id, user.display_name, user.default_project_id,
                            user.created_at_ms, now, user.default_vault_id, persona.id)
                state["users"][user.id] = _encode_user(user)
            self._save(state)
            return persona

    def list_personas(self, user_id: str) -> list[Persona]:
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            self._require_user(state, user_id)
            return sorted(
                (persona for value in state["personas"].values()
                 if (persona := _persona(value)).owner_user_id == user_id),
                key=lambda persona: (persona.created_at_ms, persona.id),
            )


    def update_user_default_persona(self, user_id: str, persona_id: str | None) -> User:
        user_id = _id(user_id, "user_id")
        persona_id = _id(persona_id, "persona_id") if persona_id is not None else None
        with self._state() as state:
            user = self._require_user(state, user_id)
            if persona_id is not None:
                value = state["personas"].get(persona_id)
                if value is None or _persona(value).owner_user_id != user.id:
                    raise CollaborationPermissionError("persona is not user-owned")
            updated = User(user.id, user.display_name, user.default_project_id,
                           user.created_at_ms, _now(), user.default_vault_id, persona_id)
            state["users"][updated.id] = _encode_user(updated)
            self._save(state)
            return updated

    def create_share_grant(
        self, owner_user_id: str, vault_id: str, grantee_user_id: str, *,
        resource_type: str, resource_id: str | None = None,
        permission: SharePermission = SharePermission.READ, expires_at_ms: int | None = None,
    ) -> ShareGrant:
        owner_user_id, vault_id, grantee_user_id = (
            _id(owner_user_id, "owner_user_id"), _id(vault_id, "vault_id"),
            _id(grantee_user_id, "grantee_user_id"),
        )
        with self._state() as state:
            vault = self._require_vault(state, vault_id)
            self._require_user(state, grantee_user_id)
            if vault.owner_user_id != owner_user_id:
                raise CollaborationPermissionError("only the vault owner may share data")
            organization_id = self._personal_organization_id(state, vault.owner_user_id)
            self._require_organization_member(state, organization_id, grantee_user_id)
            now = _now()
            grant = ShareGrant(
                _new_id(), vault.id, grantee_user_id, _key(resource_type, "resource_type"),
                _id(resource_id, "resource_id") if resource_id is not None else None,
                permission, expires_at_ms, now, None,
            )
            state["shareGrants"][grant.id] = _encode_share_grant(grant)
            self._save(state)
            return grant

    def revoke_share_grant(self, owner_user_id: str, grant_id: str) -> ShareGrant:
        owner_user_id, grant_id = _id(owner_user_id, "owner_user_id"), _id(grant_id, "grant_id")
        with self._state() as state:
            value = state["shareGrants"].get(grant_id)
            if value is None:
                raise CollaborationNotFoundError("share grant was not found")
            grant = _share_grant(value)
            if self._require_vault(state, grant.vault_id).owner_user_id != owner_user_id:
                raise CollaborationPermissionError("only the vault owner may revoke a share")
            revoked = ShareGrant(
                grant.id, grant.vault_id, grant.grantee_user_id, grant.resource_type, grant.resource_id,
                grant.permission, grant.expires_at_ms, grant.created_at_ms, _now(),
            )
            state["shareGrants"][grant.id] = _encode_share_grant(revoked)
            self._save(state)
            return revoked

    def authorize_vault(
        self, user_id: str, vault_id: str, permission: SharePermission = SharePermission.READ
    ) -> Vault:
        user_id, vault_id = _id(user_id, "user_id"), _id(vault_id, "vault_id")
        with self._state() as state:
            self._require_user(state, user_id)
            if not self._can_access_vault(state, user_id, vault_id, permission):
                raise CollaborationPermissionError("vault access is not authorized")
            return self._require_vault(state, vault_id)


    def create_personal_task(
        self, user_id: str, vault_id: str, title: str, *, note: str = "",
        status: TaskStatus = TaskStatus.TODO, priority: int = 0, due_at_ms: int | None = None,
        timezone: str | None = None, recurrence_rule: str | None = None,
        source_type: str = "user", source_ref: str | None = None,
        review_state: TaskReviewState = TaskReviewState.CONFIRMED,
    ) -> PersonalTask:
        user_id, vault_id = _id(user_id, "user_id"), _id(vault_id, "vault_id")
        if isinstance(priority, bool) or not 0 <= priority <= 3:
            raise CollaborationStoreFormatError("invalid personal task priority")
        with self._state() as state:
            vault = self._require_vault(state, vault_id)
            if vault.owner_user_id != user_id:
                raise CollaborationPermissionError("only the vault owner may create personal tasks")
            now = _now()
            task = PersonalTask(
                _new_id(), user_id, vault_id, _string(title, "title"),
                _string(note, "note", limit=_MAX_TEXT, empty=True), status, priority, due_at_ms,
                _optional_text(timezone, "timezone"), _optional_text(recurrence_rule, "recurrence_rule"),
                _string(source_type, "source_type"), _optional_text(source_ref, "source_ref"),
                None, None, None, review_state, now, now,
            )
            state["personalTasks"][task.id] = _encode_personal_task(task)
            self._save(state)
            return task

    def list_personal_tasks(self, user_id: str, vault_id: str) -> list[PersonalTask]:
        user_id, vault_id = _id(user_id, "user_id"), _id(vault_id, "vault_id")
        with self._state() as state:
            self._require_user(state, user_id)
            if not self._can_access_vault(state, user_id, vault_id, SharePermission.READ):
                raise CollaborationPermissionError("vault task list is not authorized")
            return sorted(
                (task for value in state["personalTasks"].values()
                 if (task := _personal_task(value)).vault_id == vault_id),
                key=lambda task: (task.due_at_ms is None, task.due_at_ms or 0, -task.priority, task.id),
            )

    def get_personal_task(self, user_id: str, task_id: str) -> PersonalTask | None:
        user_id, task_id = _id(user_id, "user_id"), _id(task_id, "task_id")
        with self._state() as state:
            self._require_user(state, user_id)
            value = state["personalTasks"].get(task_id)
            if value is None:
                return None
            task = _personal_task(value)
            if self._can_access_personal_task(state, user_id, task, SharePermission.READ):
                return task
            return None

    def update_personal_task(
        self, user_id: str, task_id: str, *, title: str | None = None, note: str | None = None,
        status: TaskStatus | None = None, priority: int | None = None, due_at_ms: int | None = None,
        review_state: TaskReviewState | None = None, external_provider: str | None = None,
        external_id: str | None = None, external_version: str | None = None,
    ) -> PersonalTask:
        user_id, task_id = _id(user_id, "user_id"), _id(task_id, "task_id")
        if priority is not None and (isinstance(priority, bool) or not 0 <= priority <= 3):
            raise CollaborationStoreFormatError("invalid personal task priority")
        with self._state() as state:
            value = state["personalTasks"].get(task_id)
            if value is None:
                raise CollaborationNotFoundError("personal task was not found")
            current = _personal_task(value)
            if not self._can_access_personal_task(state, user_id, current, SharePermission.COLLABORATE):
                raise CollaborationPermissionError("personal task update is not authorized")
            updated = PersonalTask(
                current.id, current.owner_user_id, current.vault_id,
                _string(title, "title") if title is not None else current.title,
                _string(note, "note", limit=_MAX_TEXT, empty=True) if note is not None else current.note,
                status or current.status, priority if priority is not None else current.priority,
                due_at_ms if due_at_ms is not None else current.due_at_ms,
                current.timezone, current.recurrence_rule, current.source_type, current.source_ref,
                _optional_text(external_provider, "external_provider") if external_provider is not None else current.external_provider,
                _optional_text(external_id, "external_id") if external_id is not None else current.external_id,
                _optional_text(external_version, "external_version") if external_version is not None else current.external_version,
                review_state or current.review_state, current.created_at_ms, _now(),
            )
            state["personalTasks"][updated.id] = _encode_personal_task(updated)
            self._save(state)
            return updated

    def delete_personal_task(self, user_id: str, task_id: str) -> bool:
        user_id, task_id = _id(user_id, "user_id"), _id(task_id, "task_id")
        with self._state() as state:
            value = state["personalTasks"].get(task_id)
            if value is None:
                return False
            task = _personal_task(value)
            if not self._can_access_personal_task(state, user_id, task, SharePermission.COLLABORATE):
                raise CollaborationPermissionError("personal task delete is not authorized")
            del state["personalTasks"][task_id]
            self._save(state)
            return True

    def bind_identity(self, channel: str, sender_id: str,
                      user_id: str) -> UserIdentity:
        channel, sender_id, user_id = _key(
            channel, "channel"), _key(
            sender_id, "sender_id"), _id(
            user_id, "user_id")
        with self._state() as state:
            self._require_user(state, user_id)
            key = _identity_key(channel, sender_id)
            existing = state["identities"].get(key)
            if existing is not None:
                identity = _identity(existing)
                if identity.user_id != user_id:
                    raise CollaborationConflictError(
                        "channel sender identity is already bound")
                return identity
            identity = UserIdentity(user_id, channel, sender_id, _now())
            state["identities"][key] = _encode_identity(identity)
            self._save(state)
            return identity

    def rebind_identity(self, channel: str, sender_id: str,
                        user_id: str) -> UserIdentity:
        """Move one channel identity after an external proof has been verified."""
        channel = _key(channel, "channel")
        sender_id = _key(sender_id, "sender_id")
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            self._require_user(state, user_id)
            identity = UserIdentity(user_id, channel, sender_id, _now())
            state["identities"][_identity_key(
                channel, sender_id)] = _encode_identity(identity)
            self._save(state)
            return identity

    def resolve_identity(self, channel: str, sender_id: str) -> User | None:
        with self._state() as state:
            identity = state["identities"].get(
                _identity_key(
                    _key(channel, "channel"), _key(sender_id, "sender_id")))
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
                    raise CollaborationConflictError(
                        "channel identity has no default project")
                project = self._require_project(state, user.default_project_id)
                return user, project

            now = _now()
            user_id = _new_id()
            workspace = self.root / "workspaces" / user_id / "default"
            workspace.mkdir(parents=True, exist_ok=True)
            with suppress(OSError):
                workspace.chmod(0o700)
            vault = Vault(_new_id(), user_id, "Private", VaultKind.PRIVATE, now, now)
            user = User(user_id, f"{channel} user", None, now, now, vault.id)
            state["users"][user.id] = _encode_user(user)
            state["vaults"][vault.id] = _encode_vault(vault)
            organization = self._ensure_personal_organization(state, user, now)
            project = Project(
                _new_id(), "Personal project", _workspace(workspace), user_id, now, now,
                organization.id)
            membership = ProjectMembership(
                project.id, user.id, MembershipRole.OWNER, now)
            identity = UserIdentity(user.id, channel, sender_id, now)
            user = User(user.id, user.display_name, project.id, user.created_at_ms, now, vault.id)
            state["users"][user.id] = _encode_user(user)
            state["projects"][project.id] = _encode_project(project)
            state["memberships"][_member_key(project.id, user.id)] = _encode_membership(
                membership
            )
            state["identities"][identity_key] = _encode_identity(identity)
            self._save(state)
            return user, project

    # -- organizations ---------------------------------------------------------

    def create_organization(self, owner_user_id: str, name: str) -> Organization:
        owner_user_id = _id(owner_user_id, "owner_user_id")
        with self._state() as state:
            self._require_user(state, owner_user_id)
            now = _now()
            organization = Organization(
                _new_id(), _string(name, "name"), owner_user_id, now, now, False)
            state["organizations"][organization.id] = _encode_organization(organization)
            membership = OrganizationMembership(
                organization.id, owner_user_id, OrganizationRole.OWNER, now)
            state["organizationMemberships"][_organization_member_key(
                organization.id, owner_user_id)] = _encode_organization_membership(membership)
            self._save(state)
            return organization

    def get_organization(self, user_id: str, organization_id: str) -> Organization | None:
        user_id, organization_id = _id(user_id, "user_id"), _id(
            organization_id, "organization_id")
        with self._state() as state:
            if not self._is_organization_member(state, organization_id, user_id):
                return None
            organization = state["organizations"].get(organization_id)
            return _organization(organization) if organization is not None else None

    def list_organizations(self, user_id: str) -> list[Organization]:
        user_id = _id(user_id, "user_id")
        with self._state() as state:
            organizations: list[Organization] = []
            for value in state["organizationMemberships"].values():
                membership = _organization_membership(value)
                if membership.user_id != user_id:
                    continue
                organization = state["organizations"].get(membership.organization_id)
                if organization is not None:
                    organizations.append(_organization(organization))
            return sorted(organizations, key=lambda item: (item.updated_at_ms, item.id), reverse=True)

    def update_organization(self, organization_id: str, actor_user_id: str, *,
                            name: str) -> Organization:
        organization_id, actor_user_id = _id(
            organization_id, "organization_id"), _id(actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_organization_admin(state, organization_id, actor_user_id)
            organization = self._require_organization(state, organization_id)
            updated = Organization(
                organization.id, _string(name, "name"), organization.created_by_user_id,
                organization.created_at_ms, _now(), organization.is_personal)
            state["organizations"][updated.id] = _encode_organization(updated)
            self._save(state)
            return updated

    def delete_organization(self, organization_id: str, actor_user_id: str) -> bool:
        organization_id, actor_user_id = _id(
            organization_id, "organization_id"), _id(actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_organization_owner(state, organization_id, actor_user_id)
            organization = self._require_organization(state, organization_id)
            if organization.is_personal:
                raise CollaborationConflictError("a personal organization cannot be deleted")
            if any(value["organizationId"] == organization_id for value in state["projects"].values()):
                raise CollaborationConflictError("an organization with projects cannot be deleted")
            del state["organizations"][organization_id]
            for key, value in tuple(state["organizationMemberships"].items()):
                if value["organizationId"] == organization_id:
                    del state["organizationMemberships"][key]
            self._save(state)
            return True

    def add_organization_member(
        self, organization_id: str, actor_user_id: str, user_id: str,
        role: OrganizationRole = OrganizationRole.MEMBER,
    ) -> OrganizationMembership:
        organization_id, actor_user_id, user_id = _id(
            organization_id, "organization_id"), _id(
            actor_user_id, "actor_user_id"), _id(user_id, "user_id")
        role = _organization_role(role)
        with self._state() as state:
            actor = self._require_organization_admin(state, organization_id, actor_user_id)
            self._require_user(state, user_id)
            organization = self._require_organization(state, organization_id)
            key = _organization_member_key(organization_id, user_id)
            existing = state["organizationMemberships"].get(key)
            current = _organization_membership(existing) if existing is not None else None
            if (
                organization.is_personal
                and organization.created_by_user_id == user_id
                and role is not OrganizationRole.OWNER
            ):
                raise CollaborationConflictError("a personal organization must retain its owner")
            if actor.role is OrganizationRole.ADMIN and (
                role is not OrganizationRole.MEMBER
                or (current is not None and current.role is not OrganizationRole.MEMBER)
            ):
                raise CollaborationPermissionError(
                    "organization admins may manage only member roles")
            if (
                current is not None
                and current.role is OrganizationRole.OWNER
                and role is not OrganizationRole.OWNER
                and self._organization_owner_count(state, organization_id) == 1
            ):
                raise CollaborationConflictError("an organization must retain an owner")
            created = current.created_at_ms if current is not None else _now()
            membership = OrganizationMembership(organization_id, user_id, role, created)
            state["organizationMemberships"][key] = _encode_organization_membership(membership)
            self._save(state)
            return membership

    def remove_organization_member(self, organization_id: str, actor_user_id: str,
                                   user_id: str) -> bool:
        organization_id, actor_user_id, user_id = _id(
            organization_id, "organization_id"), _id(
            actor_user_id, "actor_user_id"), _id(user_id, "user_id")
        with self._state() as state:
            actor = self._require_organization_admin(state, organization_id, actor_user_id)
            key = _organization_member_key(organization_id, user_id)
            value = state["organizationMemberships"].get(key)
            if value is None:
                return False
            membership = _organization_membership(value)
            organization = self._require_organization(state, organization_id)
            if organization.is_personal and organization.created_by_user_id == user_id:
                raise CollaborationConflictError("a personal organization must retain its owner")
            if actor.role is OrganizationRole.ADMIN and membership.role is not OrganizationRole.MEMBER:
                raise CollaborationPermissionError(
                    "organization admins may remove only member roles")
            if (
                membership.role is OrganizationRole.OWNER
                and self._organization_owner_count(state, organization_id) == 1
            ):
                raise CollaborationConflictError("an organization must retain an owner")
            for project_id, project_value in state["projects"].items():
                project = _project(project_value)
                if project.organization_id != organization_id:
                    continue
                project_membership = state["memberships"].get(_member_key(project_id, user_id))
                if (
                    project_membership is not None
                    and _membership(project_membership).role is MembershipRole.OWNER
                    and sum(
                        item["projectId"] == project_id
                        and item["role"] == MembershipRole.OWNER.value
                        for item in state["memberships"].values()
                    ) == 1
                ):
                    raise CollaborationConflictError("project must retain an owner")
            del state["organizationMemberships"][key]
            for project_id, project_value in tuple(state["projects"].items()):
                project = _project(project_value)
                if project.organization_id != organization_id:
                    continue
                project_member_key = _member_key(project_id, user_id)
                state["memberships"].pop(project_member_key, None)
                state["extensionProfiles"].pop(project_member_key, None)
                for binding_key, binding in tuple(state["conversationBindings"].items()):
                    if binding["projectId"] == project_id and binding["createdByUserId"] == user_id:
                        del state["conversationBindings"][binding_key]
                for task_key, task_value in tuple(state["tasks"].items()):
                    if task_value["projectId"] != project_id or task_value["assigneeUserId"] != user_id:
                        continue
                    task = _task(task_value)
                    state["tasks"][task_key] = _encode_task(Task(
                        task.id, task.project_id, task.task_list_id, task.title, task.status,
                        task.description, None, task.position, task.created_at_ms, _now()))
                user = self._require_user(state, user_id)
                if user.default_project_id == project_id:
                    state["users"][user_id] = _encode_user(User(
                        user.id, user.display_name, None, user.created_at_ms, _now(),
                        user.default_vault_id, user.default_persona_id))
            for grant_id, grant_value in tuple(state["shareGrants"].items()):
                grant = _share_grant(grant_value)
                if grant.grantee_user_id != user_id:
                    continue
                vault = self._require_vault(state, grant.vault_id)
                if self._personal_organization_id(state, vault.owner_user_id) == organization_id:
                    del state["shareGrants"][grant_id]
            self._save(state)
            return True

    def list_organization_members(self, organization_id: str,
                                  user_id: str) -> list[OrganizationMembership]:
        organization_id, user_id = _id(
            organization_id, "organization_id"), _id(user_id, "user_id")
        with self._state() as state:
            self._require_organization_member(state, organization_id, user_id)
            return sorted(
                (_organization_membership(value) for value in state["organizationMemberships"].values()
                 if value["organizationId"] == organization_id),
                key=lambda item: (item.created_at_ms, item.user_id),
            )

    # -- projects and membership ---------------------------------------------
    # -- projects and membership ---------------------------------------------

    def create_project(self, owner_user_id: str, name: str, workspace_path: str | Path, *,
                       organization_id: str | None = None) -> Project:
        owner_user_id = _id(owner_user_id, "owner_user_id")
        with self._state() as state:
            self._require_user(state, owner_user_id)
            resolved_organization_id = (
                _id(organization_id, "organization_id")
                if organization_id is not None
                else self._personal_organization_id(state, owner_user_id)
            )
            self._require_organization_member(state, resolved_organization_id, owner_user_id)
            now = _now()
            project = Project(
                _new_id(),
                _string(name, "name"),
                _workspace(workspace_path),
                owner_user_id,
                now,
                now,
                resolved_organization_id,
            )
            state["projects"][project.id] = _encode_project(project)
            state["memberships"][_member_key(project.id, owner_user_id)] = _encode_membership(
                ProjectMembership(project.id, owner_user_id, MembershipRole.OWNER, now))
            self._save(state)
            return project

    def get_project(self, user_id: str, project_id: str) -> Project | None:
        user_id, project_id = _id(user_id, "user_id"), _id(project_id, "project_id")
        with self._state() as state:
            if not self._is_member(state, project_id, user_id):
                return None
            project = state["projects"].get(project_id)
            return _project(project) if project is not None else None

    def list_projects(self, user_id: str) -> list[Project]:
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

    def update_project(self, project_id: str, actor_user_id: str, *, name: str |
                       None = None, workspace_path: str | Path | None = None) -> Project:
        if name is None and workspace_path is None:
            raise ValueError("provide name or workspace_path")
        project_id, actor_user_id = _id(
            project_id, "project_id"), _id(
            actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_owner(state, project_id, actor_user_id)
            project = self._require_project(state, project_id)
            updated = Project(
                project.id,
                _string(name, "name") if name is not None else project.name,
                _workspace(workspace_path) if workspace_path is not None else project.workspace_path,
                project.created_by_user_id,
                project.created_at_ms,
                _now(),
                project.organization_id,
            )
            state["projects"][project_id] = _encode_project(updated)
            self._save(state)
            return updated

    def delete_project(self, project_id: str, actor_user_id: str) -> bool:
        project_id, actor_user_id = _id(
            project_id, "project_id"), _id(
            actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_owner(state, project_id, actor_user_id)
            del state["projects"][project_id]
            for collection in ("memberships", "extensionProfiles", "taskLists",
                               "tasks", "conversationBindings", "contextSources"):
                for key, value in tuple(state[collection].items()):
                    if value["projectId"] == project_id:
                        del state[collection][key]
            for key, value in tuple(state["users"].items()):
                user = _user(value)
                if user.default_project_id == project_id:
                    state["users"][key] = _encode_user(
                        User(
                            user.id,
                            user.display_name,
                            None,
                            user.created_at_ms,
                            _now(),
                            user.default_vault_id,
                            user.default_persona_id))
            self._save(state)
            return True

    def add_member(self, project_id: str, actor_user_id: str, user_id: str,
                   role: MembershipRole = MembershipRole.MEMBER) -> ProjectMembership:
        project_id, actor_user_id, user_id = _id(
            project_id, "project_id"), _id(
            actor_user_id, "actor_user_id"), _id(user_id, "user_id")
        role = _role(role)
        with self._state() as state:
            self._require_owner(state, project_id, actor_user_id)
            self._require_user(state, user_id)
            project = self._require_project(state, project_id)
            if project.organization_id is None:
                raise CollaborationStoreFormatError("project organization is missing")
            self._require_organization_member(state, project.organization_id, user_id)
            key = _member_key(project_id, user_id)
            existing = state["memberships"].get(key)
            created = _membership(existing).created_at_ms if existing is not None else _now()
            membership = ProjectMembership(project_id, user_id, role, created)
            state["memberships"][key] = _encode_membership(membership)
            self._save(state)
            return membership

    def remove_member(self, project_id: str,
                      actor_user_id: str, user_id: str) -> bool:
        project_id, actor_user_id, user_id = _id(
            project_id, "project_id"), _id(
            actor_user_id, "actor_user_id"), _id(
            user_id, "user_id")
        with self._state() as state:
            self._require_owner(state, project_id, actor_user_id)
            key = _member_key(project_id, user_id)
            value = state["memberships"].get(key)
            if value is None:
                return False
            if _membership(value).role is MembershipRole.OWNER and sum(
                    item["projectId"] == project_id and item["role"] == "owner" for item in state["memberships"].values()) == 1:
                raise CollaborationConflictError(
                    "a project must retain an owner")
            del state["memberships"][key]
            state["extensionProfiles"].pop(key, None)
            for binding_key, binding in tuple(
                    state["conversationBindings"].items()):
                if binding["projectId"] == project_id and binding["createdByUserId"] == user_id:
                    del state["conversationBindings"][binding_key]
            for task_key, value in tuple(state["tasks"].items()):
                if value["projectId"] == project_id and value["assigneeUserId"] == user_id:
                    task = _task(value)
                    state["tasks"][task_key] = _encode_task(
                        Task(
                            task.id,
                            task.project_id,
                            task.task_list_id,
                            task.title,
                            task.status,
                            task.description,
                            None,
                            task.position,
                            task.created_at_ms,
                            _now()))
            user = self._require_user(state, user_id)
            if user.default_project_id == project_id:
                state["users"][user_id] = _encode_user(
                    User(user.id, user.display_name, None, user.created_at_ms, _now(), user.default_vault_id, user.default_persona_id))
            self._save(state)
            return True

    def list_members(self, project_id: str,
                     user_id: str) -> list[ProjectMembership]:
        project_id, user_id = _id(
            project_id, "project_id"), _id(
            user_id, "user_id")
        with self._state() as state:
            self._require_member(state, project_id, user_id)
            return sorted((_membership(value) for value in state["memberships"].values(
            ) if value["projectId"] == project_id), key=lambda item: (item.created_at_ms, item.user_id))

    # -- task lists and tasks ------------------------------------------------

    def create_task_list(self, project_id: str, actor_user_id: str,
                         name: str, *, position: int | None = None) -> TaskList:
        project_id, actor_user_id = _id(
            project_id, "project_id"), _id(
            actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_member(state, project_id, actor_user_id)
            now = _now()
            task_list = TaskList(
                _new_id(), project_id, _string(
                    name, "name"), _position(
                    position, _next_position(
                        state["taskLists"].values(), project_id, "projectId")), now, now)
            state["taskLists"][task_list.id] = _encode_task_list(task_list)
            self._save(state)
            return task_list

    def list_task_lists(self, user_id: str, project_id: str) -> list[TaskList]:
        user_id, project_id = _id(
            user_id, "user_id"), _id(
            project_id, "project_id")
        with self._state() as state:
            self._require_member(state, project_id, user_id)
            return sorted((_task_list(value) for value in state["taskLists"].values(
            ) if value["projectId"] == project_id), key=lambda item: (item.position, item.created_at_ms, item.id))

    def get_task_list(self, user_id: str,
                      task_list_id: str) -> TaskList | None:
        user_id, task_list_id = _id(user_id, "user_id"), _id(task_list_id, "task_list_id")
        with self._state() as state:
            value = state["taskLists"].get(task_list_id)
            if value is None:
                return None
            task_list = _task_list(value)
            return task_list if self._is_member(state, task_list.project_id, user_id) else None

    def update_task_list(self, task_list_id: str, actor_user_id: str, *,
                         name: str | None = None, position: int | None = None) -> TaskList:
        if name is None and position is None:
            raise ValueError("provide name or position")
        with self._state() as state:
            task_list = self._require_task_list(
                state, _id(task_list_id, "task_list_id"))
            self._require_member(
                state, task_list.project_id, _id(
                    actor_user_id, "actor_user_id"))
            updated = TaskList(
                task_list.id, task_list.project_id, _string(
                    name, "name") if name is not None else task_list.name, _position(
                    position, task_list.position), task_list.created_at_ms, _now())
            state["taskLists"][updated.id] = _encode_task_list(updated)
            self._save(state)
            return updated

    def delete_task_list(self, task_list_id: str, actor_user_id: str) -> bool:
        with self._state() as state:
            task_list = self._require_task_list(
                state, _id(task_list_id, "task_list_id"))
            self._require_member(
                state, task_list.project_id, _id(
                    actor_user_id, "actor_user_id"))
            del state["taskLists"][task_list.id]
            for task_id, task in tuple(state["tasks"].items()):
                if task["taskListId"] == task_list.id:
                    del state["tasks"][task_id]
            self._save(state)
            return True

    def create_task(self, project_id: str, task_list_id: str, actor_user_id: str, title: str, *, description: str = "",
                    assignee_user_id: str | None = None, status: TaskStatus = TaskStatus.TODO, position: int | None = None) -> Task:
        project_id, task_list_id, actor_user_id = _id(
            project_id, "project_id"), _id(
            task_list_id, "task_list_id"), _id(
            actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_member(state, project_id, actor_user_id)
            if self._require_task_list(
                    state, task_list_id).project_id != project_id:
                raise ValueError("task list does not belong to project")
            assignee = self._assignee(state, project_id, assignee_user_id)
            now = _now()
            task = Task(
                _new_id(), project_id, task_list_id, _string(
                    title, "title"), _status(status), _string(
                    description, "description", limit=_MAX_TEXT, empty=True), assignee, _position(
                    position, _next_position(
                        state["tasks"].values(), task_list_id, "taskListId")), now, now)
            state["tasks"][task.id] = _encode_task(task)
            self._save(state)
            return task

    def list_tasks(self, user_id: str, project_id: str, *,
                   task_list_id: str | None = None) -> list[Task]:
        user_id, project_id = _id(
            user_id, "user_id"), _id(
            project_id, "project_id")
        task_list_id = _id(
            task_list_id,
            "task_list_id") if task_list_id is not None else None
        with self._state() as state:
            self._require_member(state, project_id, user_id)
            return sorted((_task(value) for value in state["tasks"].values() if value["projectId"] == project_id and (
                task_list_id is None or value["taskListId"] == task_list_id)), key=lambda item: (item.task_list_id, item.position, item.created_at_ms, item.id))

    def get_task(self, user_id: str, task_id: str) -> Task | None:
        user_id, task_id = _id(user_id, "user_id"), _id(task_id, "task_id")
        with self._state() as state:
            value = state["tasks"].get(task_id)
            if value is None:
                return None
            task = _task(value)
            return task if self._is_member(state, task.project_id, user_id) else None

    def update_task(self, task_id: str, actor_user_id: str, *, title: str | None = None, description: str | None = None,
                    status: TaskStatus | None = None, assignee_user_id: str | None | object = ..., position: int | None = None) -> Task:
        if title is None and description is None and status is None and assignee_user_id is ... and position is None:
            raise ValueError("provide at least one task field")
        with self._state() as state:
            task = self._require_task(state, _id(task_id, "task_id"))
            self._require_member(
                state, task.project_id, _id(
                    actor_user_id, "actor_user_id"))
            assignee = task.assignee_user_id if assignee_user_id is ... else self._assignee(
                state, task.project_id, assignee_user_id if isinstance(
                    assignee_user_id, str) else None)
            updated = Task(
                task.id,
                task.project_id,
                task.task_list_id,
                _string(
                    title,
                    "title") if title is not None else task.title,
                _status(status) if status is not None else task.status,
                _string(
                    description,
                    "description",
                    limit=_MAX_TEXT,
                    empty=True) if description is not None else task.description,
                assignee,
                _position(
                    position,
                    task.position),
                task.created_at_ms,
                _now())
            state["tasks"][task.id] = _encode_task(updated)
            self._save(state)
            return updated

    def delete_task(self, task_id: str, actor_user_id: str) -> bool:
        with self._state() as state:
            task = self._require_task(state, _id(task_id, "task_id"))
            self._require_member(
                state, task.project_id, _id(
                    actor_user_id, "actor_user_id"))
            del state["tasks"][task.id]
            self._save(state)
            return True

    # -- conversation bindings, extension profiles, and context sources ------

    def bind_conversation(self, channel: str, conversation_id: str, project_id: str,
                          user_id: str, *, thread_id: str | None = None) -> ConversationBinding:
        channel, conversation_id, project_id, user_id = _key(
            channel, "channel"), _key(
            conversation_id, "conversation_id"), _id(
            project_id, "project_id"), _id(
                user_id, "user_id")
        thread_id = _optional_key(thread_id, "thread_id")
        with self._state() as state:
            self._require_member(state, project_id, user_id)
            key = _conversation_key(channel, conversation_id, thread_id)
            existing = state["conversationBindings"].get(key)
            if existing is not None:
                binding = _binding(existing)
                if binding.project_id != project_id or binding.created_by_user_id != user_id:
                    raise CollaborationConflictError(
                        "conversation is already bound")
                return binding
            now = _now()
            binding = ConversationBinding(
                _new_id(),
                channel,
                conversation_id,
                thread_id,
                project_id,
                user_id,
                now,
                now)
            state["conversationBindings"][key] = _encode_binding(binding)
            self._save(state)
            return binding

    def resolve_binding(self, channel: str, conversation_id: str, *,
                        thread_id: str | None = None) -> ConversationBinding | None:
        with self._state() as state:
            value = state["conversationBindings"].get(
                _conversation_key(
                    _key(
                        channel, "channel"), _key(
                        conversation_id, "conversation_id"), _optional_key(
                        thread_id, "thread_id")))
            return _binding(value) if value is not None else None

    def unbind_conversation(self, channel: str, conversation_id: str,
                            actor_user_id: str, *, thread_id: str | None = None) -> bool:
        with self._state() as state:
            key = _conversation_key(
                _key(
                    channel, "channel"), _key(
                    conversation_id, "conversation_id"), _optional_key(
                    thread_id, "thread_id"))
            value = state["conversationBindings"].get(key)
            if value is None:
                return False
            binding = _binding(value)
            self._require_owner(
                state, binding.project_id, _id(
                    actor_user_id, "actor_user_id"))
            del state["conversationBindings"][key]
            self._save(state)
            return True

    def get_extension_profile(self, user_id: str,
                              project_id: str) -> ExtensionProfile:
        user_id, project_id = _id(
            user_id, "user_id"), _id(
            project_id, "project_id")
        with self._state() as state:
            self._require_member(state, project_id, user_id)
            value = state["extensionProfiles"].get(
                _member_key(project_id, user_id))
            return _profile(value) if value is not None else ExtensionProfile(
                project_id, user_id, 0, _json_object({}, "settings"), 0)

    def update_extension_profile(self, user_id: str, project_id: str,
                                 settings: Mapping[str, object], *, expected_revision: int | None = None) -> ExtensionProfile:
        user_id, project_id = _id(
            user_id, "user_id"), _id(
            project_id, "project_id")
        settings = _json_object(settings, "settings")
        if expected_revision is not None and (isinstance(
                expected_revision, bool) or expected_revision < 0):
            raise ValueError("expected_revision must be non-negative")
        with self._state() as state:
            self._require_member(state, project_id, user_id)
            key = _member_key(project_id, user_id)
            prior = (
                _profile(state["extensionProfiles"][key])
                if key in state["extensionProfiles"]
                else ExtensionProfile(project_id, user_id, 0, _json_object({}, "settings"), 0)
            )
            if expected_revision is not None and prior.revision != expected_revision:
                raise CollaborationConflictError(
                    "extension profile revision changed")
            profile = ExtensionProfile(
                project_id, user_id, prior.revision + 1, settings, _now())
            state["extensionProfiles"][key] = _encode_profile(profile)
            self._save(state)
            return profile

    def create_context_source(self, project_id: str, actor_user_id: str, name: str, kind: ContextSourceKind,
                              *, config: Mapping[str, object] | None = None, enabled: bool = True) -> ContextSource:
        project_id, actor_user_id = _id(
            project_id, "project_id"), _id(
            actor_user_id, "actor_user_id")
        with self._state() as state:
            self._require_member(state, project_id, actor_user_id)
            now = _now()
            source = ContextSource(
                _new_id(), project_id, _string(
                    name, "name"), _context_kind(kind), enabled, _json_object(
                    config or {}, "config"), now, now)
            state["contextSources"][source.id] = _encode_source(source)
            self._save(state)
            return source

    def list_context_sources(self, user_id: str,
                             project_id: str) -> list[ContextSource]:
        user_id, project_id = _id(
            user_id, "user_id"), _id(
            project_id, "project_id")
        with self._state() as state:
            self._require_member(state, project_id, user_id)
            return sorted((_source(value) for value in state["contextSources"].values(
            ) if value["projectId"] == project_id), key=lambda item: (item.created_at_ms, item.id))

    def get_context_source(self, user_id: str,
                           source_id: str) -> ContextSource | None:
        user_id, source_id = _id(user_id, "user_id"), _id(source_id, "source_id")
        with self._state() as state:
            value = state["contextSources"].get(source_id)
            if value is None:
                return None
            source = _source(value)
            return source if self._is_member(state, source.project_id, user_id) else None

    def update_context_source(self, source_id: str, actor_user_id: str, *, name: str | None = None, kind: ContextSourceKind |
                              None = None, config: Mapping[str, object] | None = None, enabled: bool | None = None) -> ContextSource:
        if name is None and kind is None and config is None and enabled is None:
            raise ValueError("provide at least one context source field")
        with self._state() as state:
            source = self._require_source(state, _id(source_id, "source_id"))
            self._require_member(
                state, source.project_id, _id(
                    actor_user_id, "actor_user_id"))
            updated = ContextSource(
                source.id,
                source.project_id,
                _string(
                    name,
                    "name") if name is not None else source.name,
                _context_kind(kind) if kind is not None else source.kind,
                source.enabled if enabled is None else enabled,
                _json_object(
                    config,
                    "config") if config is not None else source.config,
                source.created_at_ms,
                _now())
            state["contextSources"][source.id] = _encode_source(updated)
            self._save(state)
            return updated

    def delete_context_source(self, source_id: str,
                              actor_user_id: str) -> bool:
        with self._state() as state:
            source = self._require_source(state, _id(source_id, "source_id"))
            self._require_member(
                state, source.project_id, _id(
                    actor_user_id, "actor_user_id"))
            del state["contextSources"][source.id]
            self._save(state)
            return True

    def resolve_scope(self, channel: str, sender_id: str, chat_id: str,
                      metadata: Mapping[str, object] | None, default_workspace: str | Path) -> ConversationScope:
        """Return the current request's only authorized project scope.

        An unbound group always remains isolated.  It never falls back to a
        sender's personal default project or workspace.  Explicit bindings are
        accepted only while both their creator and the sender are members.
        """
        channel, sender_id, chat_id = _key(
            channel, "channel"), _key(
            sender_id, "sender_id"), _key(
            chat_id, "chat_id")
        metadata = metadata if metadata is not None else {}
        thread_id = _thread_id(metadata)
        suffix = _scope_suffix(channel, chat_id, thread_id)
        with self._state() as state:
            identity = state["identities"].get(
                _identity_key(channel, sender_id))
            user = (
                _user(state["users"][_identity(identity).user_id])
                if identity is not None
                else None
            )
            binding_value = state["conversationBindings"].get(
                _conversation_key(channel, chat_id, thread_id))
            binding = _binding(
                binding_value) if binding_value is not None else None
            if binding is not None and user is not None and binding.project_id in state["projects"] and self._is_member(
                    state, binding.project_id, binding.created_by_user_id) and self._is_member(state, binding.project_id, user.id):
                return self._project_scope(ConversationScopeKind.BOUND, user, _project(
                    state["projects"][binding.project_id]), binding, state, suffix)
            if _is_direct(chat_id, sender_id, metadata) and user is not None and user.default_project_id is not None and user.default_project_id in state["projects"] and self._is_member(
                    state, user.default_project_id, user.id):
                return self._project_scope(ConversationScopeKind.DIRECT, user, _project(
                    state["projects"][user.default_project_id]), None, state, suffix)
            # validates the injected default without using it for isolation
            _workspace(default_workspace)
            return ConversationScope(ConversationScopeKind.ISOLATED,
                                     user.id if user else None, None, user, None, None, None, 0, None, suffix)

    # -- lock, normalization, and authorization ------------------------------

    def _project_scope(self, kind: ConversationScopeKind, user: User, project: Project,
                       binding: ConversationBinding | None, state: _StoreState, suffix: str) -> ConversationScope:
        value = state["extensionProfiles"].get(_member_key(project.id, user.id))
        profile = _profile(value) if value is not None else ExtensionProfile(
            project.id, user.id, 0, _json_object({}, "settings"), 0)
        persona_id = user.default_persona_id
        persona_value = state["personas"].get(persona_id) if persona_id is not None else None
        persona = _persona(persona_value) if persona_value is not None else None
        if persona is not None and persona.owner_user_id != user.id:
            raise CollaborationStoreFormatError("default persona is not user-owned")
        vault_id = persona.default_vault_id if persona is not None else user.default_vault_id
        if vault_id is None or vault_id not in state["vaults"]:
            raise CollaborationStoreFormatError("authorized user has no default vault")
        return ConversationScope(
            kind, user.id, project.id, user, project, binding, profile, profile.revision,
            project.workspace_path, suffix, vault_id, persona.id if persona is not None else None,
        )

    def _is_member(self, state: _StoreState,
                   project_id: str, user_id: str) -> bool:
        return _member_key(project_id, user_id) in state["memberships"]

    def _require_member(
            self, state: _StoreState, project_id: str, user_id: str) -> ProjectMembership:
        value = state["memberships"].get(_member_key(project_id, user_id))
        if value is None:
            raise CollaborationPermissionError(
                "project membership is required")
        return _membership(value)

    def _is_organization_member(
            self, state: _StoreState, organization_id: str, user_id: str) -> bool:
        return _organization_member_key(organization_id, user_id) in state["organizationMemberships"]

    def _require_organization_member(
            self, state: _StoreState, organization_id: str, user_id: str
    ) -> OrganizationMembership:
        value = state["organizationMemberships"].get(
            _organization_member_key(organization_id, user_id))
        if value is None:
            raise CollaborationPermissionError("organization membership is required")
        return _organization_membership(value)

    def _require_organization_admin(
            self, state: _StoreState, organization_id: str, user_id: str
    ) -> OrganizationMembership:
        membership = self._require_organization_member(state, organization_id, user_id)
        if membership.role not in {OrganizationRole.OWNER, OrganizationRole.ADMIN}:
            raise CollaborationPermissionError("organization admin role is required")
        return membership

    def _require_organization_owner(
            self, state: _StoreState, organization_id: str, user_id: str
    ) -> OrganizationMembership:
        membership = self._require_organization_member(state, organization_id, user_id)
        if membership.role is not OrganizationRole.OWNER:
            raise CollaborationPermissionError("organization owner role is required")
        return membership

    def _require_organization(self, state: _StoreState, organization_id: str) -> Organization:
        value = state["organizations"].get(organization_id)
        if value is None:
            raise CollaborationNotFoundError("organization not found")
        return _organization(value)

    def _organization_owner_count(self, state: _StoreState, organization_id: str) -> int:
        return sum(
            value["organizationId"] == organization_id and value["role"] == OrganizationRole.OWNER.value
            for value in state["organizationMemberships"].values()
        )

    def _personal_organization_id(self, state: _StoreState, user_id: str) -> str:
        organizations = [
            organization
            for value in state["organizations"].values()
            if (organization := _organization(value)).is_personal
            and organization.created_by_user_id == user_id
            and (membership := state["organizationMemberships"].get(
                _organization_member_key(organization.id, user_id))) is not None
            and _organization_membership(membership).role is OrganizationRole.OWNER
        ]
        if not organizations:
            raise CollaborationStoreFormatError("user personal organization is missing")
        return organizations[0].id

    def _ensure_personal_organization(
            self, state: _StoreState, user: User, now: int) -> Organization:
        try:
            return self._require_organization(
                state, self._personal_organization_id(state, user.id))
        except CollaborationStoreFormatError:
            organization = Organization(_new_id(), "Personal", user.id, now, now, True)
            state["organizations"][organization.id] = _encode_organization(organization)
            membership = OrganizationMembership(
                organization.id, user.id, OrganizationRole.OWNER, now)
            state["organizationMemberships"][_organization_member_key(
                organization.id, user.id)] = _encode_organization_membership(membership)
            return organization

    def _require_owner(
            self, state: _StoreState, project_id: str, user_id: str) -> ProjectMembership:
        membership = self._require_member(state, project_id, user_id)
        if membership.role is not MembershipRole.OWNER:
            raise CollaborationPermissionError(
                "project owner role is required")
        return membership

    def _require_user(self, state: _StoreState, user_id: str) -> User:
        value = state["users"].get(user_id)
        if value is None:
            raise CollaborationNotFoundError("user not found")
        return _user(value)


    def _require_vault(self, state: _StoreState, vault_id: str) -> Vault:
        value = state["vaults"].get(vault_id)
        if value is None:
            raise CollaborationNotFoundError("vault not found")
        return _vault(value)

    def _can_access_vault(
        self, state: _StoreState, user_id: str, vault_id: str, permission: SharePermission
    ) -> bool:
        vault = self._require_vault(state, vault_id)
        if vault.owner_user_id == user_id:
            return True
        now = _now()
        for value in state["shareGrants"].values():
            grant = _share_grant(value)
            if (
                grant.vault_id != vault_id
                or grant.grantee_user_id != user_id
                or grant.resource_type != "vault"
                or grant.resource_id is not None
            ):
                continue
            if grant.revoked_at_ms is not None:
                continue
            if grant.expires_at_ms is not None and grant.expires_at_ms <= now:
                continue
            if grant.permission is SharePermission.COLLABORATE or grant.permission is permission:
                return True
        return False


    def _can_access_personal_task(
        self, state: _StoreState, user_id: str, task: PersonalTask, permission: SharePermission
    ) -> bool:
        if task.owner_user_id == user_id:
            return True
        if self._can_access_vault(state, user_id, task.vault_id, permission):
            return True
        now = _now()
        for value in state["shareGrants"].values():
            grant = _share_grant(value)
            if (
                grant.vault_id != task.vault_id
                or grant.grantee_user_id != user_id
                or grant.resource_type != "personal_task"
                or grant.resource_id != task.id
                or grant.revoked_at_ms is not None
                or (grant.expires_at_ms is not None and grant.expires_at_ms <= now)
            ):
                continue
            if grant.permission is SharePermission.COLLABORATE or grant.permission is permission:
                return True
        return False

    def _require_project(
            self, state: _StoreState, project_id: str) -> Project:
        value = state["projects"].get(project_id)
        if value is None:
            raise CollaborationNotFoundError("project not found")
        return _project(value)

    def _require_task_list(
            self, state: _StoreState, task_list_id: str) -> TaskList:
        value = state["taskLists"].get(task_list_id)
        if value is None:
            raise CollaborationNotFoundError("task list not found")
        return _task_list(value)

    def _require_task(self, state: _StoreState, task_id: str) -> Task:
        value = state["tasks"].get(task_id)
        if value is None:
            raise CollaborationNotFoundError("task not found")
        return _task(value)

    def _require_source(
            self, state: _StoreState, source_id: str) -> ContextSource:
        value = state["contextSources"].get(source_id)
        if value is None:
            raise CollaborationNotFoundError("context source not found")
        return _source(value)

    def _assignee(
            self, state: _StoreState, project_id: str, user_id: str | None) -> str | None:
        if user_id is None:
            return None
        user_id = _id(user_id, "assignee_user_id")
        self._require_member(state, project_id, user_id)
        return user_id

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
            raise CollaborationStoreFormatError(
                "collaboration store exceeds size limit")
        try:
            payload: object = json.loads(raw.decode("utf-8"))
            state = _normalize(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollaborationStoreFormatError("collaboration store is not valid JSON") from exc
        version = _mapping(payload, "unsupported collaboration store schema").get("schemaVersion")
        if version != _SCHEMA:
            self._save(state)
        return state

    def _save(self, state: _StoreState) -> None:
        payload = json.dumps(
            _normalize(state),
            ensure_ascii=False,
            separators=(
                ",",
                ":"),
            allow_nan=False).encode()
        if len(payload) > _MAX_FILE_BYTES:
            raise CollaborationStoreError(
                "collaboration store exceeds size limit")
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp")
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
        "vaults": {},
        "personas": {},
        "shareGrants": {},
        "personalTasks": {},
        "organizations": {},
        "organizationMemberships": {},
        "projects": {},
        "memberships": {},
        "taskLists": {},
        "tasks": {},
        "conversationBindings": {},
        "extensionProfiles": {},
        "contextSources": {},
    }


def _legacy_vault_id(user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    return f"vault-{digest}"


def _legacy_organization_id(user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    return f"organization-{digest}"


def _migrate_v1(data: Mapping[str, object]) -> dict[str, object]:
    """Upgrade the original project-only store without dropping user data."""
    migrated = dict(data)
    migrated["schemaVersion"] = 2
    raw_users = migrated.get("users")
    users = _mapping(raw_users, "invalid legacy users") if raw_users is not None else {}
    vaults: dict[str, object] = {}
    now = _now()
    for user_id, raw_user in users.items():
        user = _mapping(raw_user, "invalid legacy user")
        vault_id = _legacy_vault_id(user_id)
        user["defaultVaultId"] = vault_id
        users[user_id] = user
        vaults[vault_id] = {
            "id": vault_id,
            "ownerUserId": user_id,
            "name": "Private",
            "kind": VaultKind.PRIVATE.value,
            "createdAtMs": user.get("createdAtMs", now),
            "updatedAtMs": user.get("updatedAtMs", now),
        }
    migrated["users"] = users
    migrated["vaults"] = vaults
    migrated["personas"] = {}
    migrated["shareGrants"] = {}
    return migrated


def _migrate_v2(data: Mapping[str, object]) -> dict[str, object]:
    """Add vault-owned personal tasks without changing project task records."""
    migrated = dict(data)
    migrated["schemaVersion"] = 3
    migrated["personalTasks"] = {}
    return migrated


def _migrate_v3(data: Mapping[str, object]) -> dict[str, object]:
    """Add an explicit default assistant identity to each user record."""
    migrated: dict[str, object] = dict(data)
    migrated["schemaVersion"] = 4
    raw_users = migrated.get("users")
    if isinstance(raw_users, Mapping):
        typed_users = cast(Mapping[str, object], raw_users)
        users: dict[str, object] = dict(typed_users)
        for user_id, raw_user in typed_users.items():
            if isinstance(raw_user, Mapping):
                user: dict[str, object] = dict(cast(Mapping[str, object], raw_user))
                user["defaultPersonaId"] = None
                users[user_id] = user
        migrated["users"] = users
    return migrated


def _migrate_v4(data: Mapping[str, object]) -> dict[str, object]:
    """Assign each legacy user a personal organization and scope every project."""
    migrated: dict[str, object] = dict(data)
    users = _mapping(migrated.get("users"), "invalid legacy users")
    organizations: dict[str, object] = {}
    organization_memberships: dict[str, object] = {}
    for user_id in sorted(users):
        user = _mapping(users[user_id], "invalid legacy user")
        organization_id = _legacy_organization_id(user_id)
        created_at_ms = user.get("createdAtMs")
        updated_at_ms = user.get("updatedAtMs")
        organizations[organization_id] = {
            "id": organization_id,
            "name": "Personal",
            "createdByUserId": user_id,
            "createdAtMs": created_at_ms,
            "updatedAtMs": updated_at_ms,
            "isPersonal": True,
        }
        organization_memberships[_organization_member_key(organization_id, user_id)] = {
            "organizationId": organization_id,
            "userId": user_id,
            "role": OrganizationRole.OWNER.value,
            "createdAtMs": created_at_ms,
        }
    projects = _mapping(migrated.get("projects"), "invalid legacy projects")
    legacy_memberships = _mapping(migrated.get("memberships"), "invalid legacy memberships")
    scoped_projects: dict[str, _Record] = {}
    for project_id, raw_project in projects.items():
        project = dict(_mapping(raw_project, "invalid legacy project"))
        owner_user_id = project.get("createdByUserId")
        if not isinstance(owner_user_id, str):
            raise CollaborationStoreFormatError("invalid legacy project")
        organization_id = _legacy_organization_id(owner_user_id)
        project["organizationId"] = organization_id
        scoped_projects[project_id] = project
    for raw_membership in legacy_memberships.values():
        membership = _mapping(raw_membership, "invalid legacy membership")
        project_id = membership.get("projectId")
        user_id = membership.get("userId")
        if not isinstance(project_id, str) or not isinstance(user_id, str):
            raise CollaborationStoreFormatError("invalid legacy membership")
        project = scoped_projects.get(project_id)
        if project is None:
            raise CollaborationStoreFormatError("invalid legacy membership")
        organization_id = project.get("organizationId")
        if not isinstance(organization_id, str):
            raise CollaborationStoreFormatError("invalid legacy membership")
        key = _organization_member_key(organization_id, user_id)
        if key not in organization_memberships:
            organization_memberships[key] = {
                "organizationId": organization_id,
                "userId": user_id,
                "role": OrganizationRole.MEMBER.value,
                "createdAtMs": membership.get("createdAtMs"),
            }
    legacy_vaults = _mapping(migrated.get("vaults"), "invalid legacy vaults")
    legacy_share_grants = _mapping(migrated.get("shareGrants"), "invalid legacy share grants")
    for raw_grant in legacy_share_grants.values():
        grant = _mapping(raw_grant, "invalid legacy share grant")
        vault_id = grant.get("vaultId")
        grantee_user_id = grant.get("granteeUserId")
        if not isinstance(vault_id, str) or not isinstance(grantee_user_id, str):
            raise CollaborationStoreFormatError("invalid legacy share grant")
        raw_vault = legacy_vaults.get(vault_id)
        if raw_vault is None:
            raise CollaborationStoreFormatError("invalid legacy share grant")
        vault = _mapping(raw_vault, "invalid legacy share grant")
        owner_user_id = vault.get("ownerUserId")
        if not isinstance(owner_user_id, str):
            raise CollaborationStoreFormatError("invalid legacy share grant")
        organization_id = _legacy_organization_id(owner_user_id)
        key = _organization_member_key(organization_id, grantee_user_id)
        if key not in organization_memberships:
            organization_memberships[key] = {
                "organizationId": organization_id,
                "userId": grantee_user_id,
                "role": OrganizationRole.MEMBER.value,
                "createdAtMs": grant.get("createdAtMs"),
            }
    migrated["schemaVersion"] = _SCHEMA
    migrated["organizations"] = organizations
    migrated["organizationMemberships"] = organization_memberships
    migrated["projects"] = scoped_projects
    return migrated


def _normalize(data: object) -> _StoreState:
    root = _mapping(data, "unsupported collaboration store schema")
    version = root.get("schemaVersion")
    if version == 1:
        root = _migrate_v1(root)
    if root.get("schemaVersion") == 2:
        root = _migrate_v2(root)
    if root.get("schemaVersion") == 3:
        root = _migrate_v3(root)
    if root.get("schemaVersion") == 4:
        root = _migrate_v4(root)
    if set(root) != set(_empty()) or root.get("schemaVersion") != _SCHEMA:
        raise CollaborationStoreFormatError(
            "unsupported collaboration store schema")
    result = _empty()
    local_owner = root["localOwnerId"]
    result["localOwnerId"] = _id(
        local_owner, "localOwnerId") if local_owner is not None else None
    result["users"] = _normalize_collection(root["users"], "users", _user, _encode_user,
                                              lambda user: user.id)
    result["identities"] = _normalize_collection(root["identities"], "identities", _identity,
                                                   _encode_identity, lambda identity: _identity_key(
                                                       identity.channel, identity.sender_id))
    result["vaults"] = _normalize_collection(root["vaults"], "vaults", _vault,
                                                _encode_vault, lambda vault: vault.id)
    result["personas"] = _normalize_collection(root["personas"], "personas", _persona,
                                                  _encode_persona, lambda persona: persona.id)
    result["shareGrants"] = _normalize_collection(root["shareGrants"], "shareGrants", _share_grant,
                                                     _encode_share_grant, lambda grant: grant.id)
    result["personalTasks"] = _normalize_collection(root["personalTasks"], "personalTasks",
                                                        _personal_task, _encode_personal_task,
                                                        lambda task: task.id)
    result["organizations"] = _normalize_collection(
        root["organizations"], "organizations", _organization, _encode_organization,
        lambda organization: organization.id)
    result["organizationMemberships"] = _normalize_collection(
        root["organizationMemberships"], "organizationMemberships",
        _organization_membership, _encode_organization_membership,
        lambda membership: _organization_member_key(membership.organization_id, membership.user_id))
    result["projects"] = _normalize_collection(root["projects"], "projects", _project,
                                                 _encode_project, lambda project: project.id)
    result["memberships"] = _normalize_collection(root["memberships"], "memberships",
                                                    _membership, _encode_membership,
                                                    lambda membership: _member_key(
                                                        membership.project_id, membership.user_id))
    result["taskLists"] = _normalize_collection(root["taskLists"], "taskLists", _task_list,
                                                  _encode_task_list, lambda task_list: task_list.id)
    result["tasks"] = _normalize_collection(root["tasks"], "tasks", _task, _encode_task,
                                              lambda task: task.id)
    result["conversationBindings"] = _normalize_collection(
        root["conversationBindings"], "conversationBindings", _binding, _encode_binding,
        lambda binding: _conversation_key(
            binding.channel, binding.conversation_id, binding.thread_id))
    result["extensionProfiles"] = _normalize_collection(
        root["extensionProfiles"], "extensionProfiles", _profile, _encode_profile,
        lambda profile: _member_key(profile.project_id, profile.user_id))
    result["contextSources"] = _normalize_collection(root["contextSources"], "contextSources",
                                                       _source, _encode_source,
                                                       lambda source: source.id)
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
    organizations = state["organizations"]
    organization_memberships = state["organizationMemberships"]
    vaults = state["vaults"]
    for value in vaults.values():
        if _vault(value).owner_user_id not in users:
            raise CollaborationStoreFormatError("vault owner is missing")
    for value in state["personas"].values():
        persona = _persona(value)
        vault = vaults.get(persona.default_vault_id)
        if vault is None or _vault(vault).owner_user_id != persona.owner_user_id:
            raise CollaborationStoreFormatError("invalid persona vault")
    for value in state["personalTasks"].values():
        task = _personal_task(value)
        vault = vaults.get(task.vault_id)
        if vault is None or _vault(vault).owner_user_id != task.owner_user_id:
            raise CollaborationStoreFormatError("invalid personal task vault")
    if state["localOwnerId"] is not None and state["localOwnerId"] not in users:
        raise CollaborationStoreFormatError("local owner is missing")
    for value in state["identities"].values():
        if _identity(value).user_id not in users:
            raise CollaborationStoreFormatError("identity references missing user")
    for value in organizations.values():
        if _organization(value).created_by_user_id not in users:
            raise CollaborationStoreFormatError("organization owner is missing")
    organization_owner_counts: dict[str, int] = {}
    personal_organizations: dict[str, str] = {}
    for value in organizations.values():
        organization = _organization(value)
        if organization.is_personal:
            if organization.created_by_user_id in personal_organizations:
                raise CollaborationStoreFormatError("user has multiple personal organizations")
            personal_organizations[organization.created_by_user_id] = organization.id
    for value in organization_memberships.values():
        membership = _organization_membership(value)
        if membership.organization_id not in organizations or membership.user_id not in users:
            raise CollaborationStoreFormatError("invalid organization membership")
        if membership.role is OrganizationRole.OWNER:
            organization_owner_counts[membership.organization_id] = (
                organization_owner_counts.get(membership.organization_id, 0) + 1)
    if set(organization_owner_counts) != set(organizations):
        raise CollaborationStoreFormatError("an organization must retain an owner")
    for user_id, organization_id in personal_organizations.items():
        membership = organization_memberships.get(_organization_member_key(organization_id, user_id))
        if membership is None or _organization_membership(membership).role is not OrganizationRole.OWNER:
            raise CollaborationStoreFormatError("user personal organization is missing")
    if set(personal_organizations) != set(users):
        raise CollaborationStoreFormatError("user personal organization is missing")
    for value in state["shareGrants"].values():
        grant = _share_grant(value)
        vault_value = vaults.get(grant.vault_id)
        if vault_value is None or grant.grantee_user_id not in users:
            raise CollaborationStoreFormatError("invalid share grant")
        vault = _vault(vault_value)
        organization_id = personal_organizations.get(vault.owner_user_id)
        if (
            organization_id is None
            or _organization_member_key(organization_id, grant.grantee_user_id)
            not in organization_memberships
        ):
            raise CollaborationStoreFormatError("invalid share grant organization")
    for value in projects.values():
        project = _project(value)
        if project.created_by_user_id not in users:
            raise CollaborationStoreFormatError("project owner is missing")
        if project.organization_id is None or project.organization_id not in organizations:
            raise CollaborationStoreFormatError("project organization is missing")
    project_owner_counts: dict[str, int] = {}
    for value in memberships.values():
        membership = _membership(value)
        project_value = projects.get(membership.project_id)
        if project_value is None or membership.user_id not in users:
            raise CollaborationStoreFormatError("invalid membership")
        project = _project(project_value)
        if project.organization_id is None or _organization_member_key(
            project.organization_id, membership.user_id
        ) not in organization_memberships:
            raise CollaborationStoreFormatError("invalid project organization membership")
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
        if user.default_vault_id is not None:
            vault = vaults.get(user.default_vault_id)
            if vault is None or _vault(vault).owner_user_id != user.id:
                raise CollaborationStoreFormatError("invalid default vault")
        if user.default_persona_id is not None:
            persona = state["personas"].get(user.default_persona_id)
            if persona is None or _persona(persona).owner_user_id != user.id:
                raise CollaborationStoreFormatError("invalid default persona")
    for value in state["taskLists"].values():
        if _task_list(value).project_id not in projects:
            raise CollaborationStoreFormatError("task list project is missing")
    for value in state["tasks"].values():
        task = _task(value)
        task_list = state["taskLists"].get(task.task_list_id)
        if task_list is None or _task_list(task_list).project_id != task.project_id:
            raise CollaborationStoreFormatError("invalid task list reference")
        if task.assignee_user_id is not None and _member_key(
                task.project_id, task.assignee_user_id) not in memberships:
            raise CollaborationStoreFormatError("invalid task assignee")
    for value in state["conversationBindings"].values():
        binding = _binding(value)
        if _member_key(binding.project_id, binding.created_by_user_id) not in memberships:
            raise CollaborationStoreFormatError("invalid conversation binding")
    for value in state["extensionProfiles"].values():
        profile = _profile(value)
        if _member_key(profile.project_id, profile.user_id) not in memberships:
            raise CollaborationStoreFormatError("invalid extension profile")
    for value in state["contextSources"].values():
        if _source(value).project_id not in projects:
            raise CollaborationStoreFormatError("context source project is missing")


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
    required_fields = {"id", "displayName", "createdAtMs", "updatedAtMs"}
    supported_fields = required_fields | {"defaultProjectId", "defaultVaultId", "defaultPersonaId"}
    fields = set(data)
    if not required_fields <= fields or not fields <= supported_fields:
        raise CollaborationStoreFormatError("record has unsupported shape")
    raw_default_project = data.get("defaultProjectId")
    raw_default_vault = data.get("defaultVaultId")
    raw_default_persona = data.get("defaultPersonaId")
    return User(
        _id(data["id"], "id"),
        _string(data["displayName"], "displayName"),
        _id(raw_default_project, "defaultProjectId") if raw_default_project is not None else None,
        _time(data["createdAtMs"]),
        _time(data["updatedAtMs"]),
        _id(raw_default_vault, "defaultVaultId") if raw_default_vault is not None else None,
        _id(raw_default_persona, "defaultPersonaId") if raw_default_persona is not None else None,
    )


def _encode_user(x: User) -> _Record:
    return {
        "id": x.id,
        "displayName": x.display_name,
        "defaultProjectId": x.default_project_id,
        "defaultVaultId": x.default_vault_id,
        "defaultPersonaId": x.default_persona_id,
        "createdAtMs": x.created_at_ms,
        "updatedAtMs": x.updated_at_ms,
    }


def _vault(data: Mapping[str, object]) -> Vault:
    _shape(data, {"id", "ownerUserId", "name", "kind", "createdAtMs", "updatedAtMs"})
    try:
        kind = VaultKind(_string(data["kind"], "kind"))
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid vault kind") from exc
    return Vault(
        _id(data["id"], "id"),
        _id(data["ownerUserId"], "ownerUserId"),
        _string(data["name"], "name"),
        kind,
        _time(data["createdAtMs"]),
        _time(data["updatedAtMs"]),
    )


def _encode_vault(x: Vault) -> _Record:
    return {
        "id": x.id,
        "ownerUserId": x.owner_user_id,
        "name": x.name,
        "kind": x.kind.value,
        "createdAtMs": x.created_at_ms,
        "updatedAtMs": x.updated_at_ms,
    }


def _persona(data: Mapping[str, object]) -> Persona:
    _shape(data, {"id", "ownerUserId", "name", "defaultVaultId", "instructions", "createdAtMs", "updatedAtMs"})
    return Persona(
        _id(data["id"], "id"),
        _id(data["ownerUserId"], "ownerUserId"),
        _string(data["name"], "name"),
        _id(data["defaultVaultId"], "defaultVaultId"),
        _string(data["instructions"], "instructions", empty=True),
        _time(data["createdAtMs"]),
        _time(data["updatedAtMs"]),
    )


def _encode_persona(x: Persona) -> _Record:
    return {
        "id": x.id,
        "ownerUserId": x.owner_user_id,
        "name": x.name,
        "defaultVaultId": x.default_vault_id,
        "instructions": x.instructions,
        "createdAtMs": x.created_at_ms,
        "updatedAtMs": x.updated_at_ms,
    }


def _share_grant(data: Mapping[str, object]) -> ShareGrant:
    _shape(data, {"id", "vaultId", "granteeUserId", "resourceType", "resourceId", "permission", "expiresAtMs", "createdAtMs", "revokedAtMs"})
    try:
        permission = SharePermission(_string(data["permission"], "permission"))
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid share permission") from exc
    resource_id = data["resourceId"]
    expires_at = data["expiresAtMs"]
    revoked_at = data["revokedAtMs"]
    return ShareGrant(
        _id(data["id"], "id"),
        _id(data["vaultId"], "vaultId"),
        _id(data["granteeUserId"], "granteeUserId"),
        _string(data["resourceType"], "resourceType"),
        _id(resource_id, "resourceId") if resource_id is not None else None,
        permission,
        _time(expires_at) if expires_at is not None else None,
        _time(data["createdAtMs"]),
        _time(revoked_at) if revoked_at is not None else None,
    )


def _encode_share_grant(x: ShareGrant) -> _Record:
    return {
        "id": x.id,
        "vaultId": x.vault_id,
        "granteeUserId": x.grantee_user_id,
        "resourceType": x.resource_type,
        "resourceId": x.resource_id,
        "permission": x.permission.value,
        "expiresAtMs": x.expires_at_ms,
        "createdAtMs": x.created_at_ms,
        "revokedAtMs": x.revoked_at_ms,
    }


def _optional_text(value: object, field: str) -> str | None:
    return _string(value, field, limit=_MAX_TEXT, empty=True) if value is not None else None


def _personal_task(data: Mapping[str, object]) -> PersonalTask:
    _shape(data, {
        "id", "ownerUserId", "vaultId", "title", "note", "status", "priority",
        "dueAtMs", "timezone", "recurrenceRule", "sourceType", "sourceRef",
        "externalProvider", "externalId", "externalVersion", "reviewState",
        "createdAtMs", "updatedAtMs",
    })
    try:
        status = TaskStatus(_string(data["status"], "status"))
        review_state = TaskReviewState(_string(data["reviewState"], "reviewState"))
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid personal task state") from exc
    priority = data["priority"]
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 3:
        raise CollaborationStoreFormatError("invalid personal task priority")
    due_at = data["dueAtMs"]
    return PersonalTask(
        _id(data["id"], "id"), _id(data["ownerUserId"], "ownerUserId"),
        _id(data["vaultId"], "vaultId"), _string(data["title"], "title"),
        _string(data["note"], "note", limit=_MAX_TEXT, empty=True), status, priority,
        _time(due_at) if due_at is not None else None, _optional_text(data["timezone"], "timezone"),
        _optional_text(data["recurrenceRule"], "recurrenceRule"),
        _string(data["sourceType"], "sourceType"), _optional_text(data["sourceRef"], "sourceRef"),
        _optional_text(data["externalProvider"], "externalProvider"),
        _optional_text(data["externalId"], "externalId"),
        _optional_text(data["externalVersion"], "externalVersion"), review_state,
        _time(data["createdAtMs"]), _time(data["updatedAtMs"]),
    )


def _encode_personal_task(x: PersonalTask) -> _Record:
    return {
        "id": x.id, "ownerUserId": x.owner_user_id, "vaultId": x.vault_id,
        "title": x.title, "note": x.note, "status": x.status.value, "priority": x.priority,
        "dueAtMs": x.due_at_ms, "timezone": x.timezone, "recurrenceRule": x.recurrence_rule,
        "sourceType": x.source_type, "sourceRef": x.source_ref,
        "externalProvider": x.external_provider, "externalId": x.external_id,
        "externalVersion": x.external_version, "reviewState": x.review_state.value,
        "createdAtMs": x.created_at_ms, "updatedAtMs": x.updated_at_ms,
    }


def _identity(data: Mapping[str, object]) -> UserIdentity:
    _shape(data, {"userId", "channel", "senderId", "createdAtMs"})
    return UserIdentity(_id(data["userId"], "userId"), _key(data["channel"], "channel"), _key(
        data["senderId"], "senderId"), _time(data["createdAtMs"]))


def _encode_identity(x: UserIdentity) -> _Record:
    return {"userId": x.user_id, "channel": x.channel, "senderId": x.sender_id,
            "createdAtMs": x.created_at_ms}


def _organization(data: Mapping[str, object]) -> Organization:
    _shape(data, {"id", "name", "createdByUserId", "createdAtMs", "updatedAtMs", "isPersonal"})
    is_personal = data["isPersonal"]
    if not isinstance(is_personal, bool):
        raise CollaborationStoreFormatError("invalid organization personal flag")
    return Organization(
        _id(data["id"], "id"), _string(data["name"], "name"),
        _id(data["createdByUserId"], "createdByUserId"),
        _time(data["createdAtMs"]), _time(data["updatedAtMs"]), is_personal,
    )


def _encode_organization(x: Organization) -> _Record:
    return {
        "id": x.id, "name": x.name, "createdByUserId": x.created_by_user_id,
        "createdAtMs": x.created_at_ms, "updatedAtMs": x.updated_at_ms,
        "isPersonal": x.is_personal,
    }


def _organization_membership(data: Mapping[str, object]) -> OrganizationMembership:
    _shape(data, {"organizationId", "userId", "role", "createdAtMs"})
    return OrganizationMembership(
        _id(data["organizationId"], "organizationId"), _id(data["userId"], "userId"),
        _organization_role(data["role"]), _time(data["createdAtMs"]),
    )


def _encode_organization_membership(x: OrganizationMembership) -> _Record:
    return {
        "organizationId": x.organization_id, "userId": x.user_id, "role": x.role.value,
        "createdAtMs": x.created_at_ms,
    }


def _project(data: Mapping[str, object]) -> Project:
    required = {"id", "name", "workspacePath", "createdByUserId", "createdAtMs", "updatedAtMs"}
    fields = set(data)
    if fields != required and fields != required | {"organizationId"}:
        raise CollaborationStoreFormatError("record has unsupported shape")
    raw_organization = data.get("organizationId")
    return Project(
        _id(data["id"], "id"), _string(data["name"], "name"),
        _workspace(data["workspacePath"]), _id(data["createdByUserId"], "createdByUserId"),
        _time(data["createdAtMs"]), _time(data["updatedAtMs"]),
        _id(raw_organization, "organizationId") if raw_organization is not None else None,
    )


def _encode_project(x: Project) -> _Record:
    return {
        "id": x.id, "name": x.name, "workspacePath": x.workspace_path,
        "createdByUserId": x.created_by_user_id, "organizationId": x.organization_id,
        "createdAtMs": x.created_at_ms, "updatedAtMs": x.updated_at_ms,
    }


def _membership(data: Mapping[str, object]) -> ProjectMembership:
    _shape(data, {"projectId", "userId", "role", "createdAtMs"})
    return ProjectMembership(_id(data["projectId"], "projectId"), _id(
        data["userId"], "userId"), _role(data["role"]), _time(data["createdAtMs"]))


def _encode_membership(x: ProjectMembership) -> _Record:
    return {"projectId": x.project_id, "userId": x.user_id, "role": x.role.value,
            "createdAtMs": x.created_at_ms}


def _task_list(data: Mapping[str, object]) -> TaskList:
    _shape(data, {"id", "projectId", "name", "position", "createdAtMs", "updatedAtMs"})
    return TaskList(_id(data["id"], "id"), _id(data["projectId"], "projectId"), _string(
        data["name"], "name"), _position(data["position"], 0), _time(data["createdAtMs"]), _time(data["updatedAtMs"]))


def _encode_task_list(x: TaskList) -> _Record:
    return {"id": x.id, "projectId": x.project_id, "name": x.name, "position": x.position,
            "createdAtMs": x.created_at_ms, "updatedAtMs": x.updated_at_ms}


def _task(data: Mapping[str, object]) -> Task:
    _shape(data,
           {"id", "projectId", "taskListId", "title", "status", "description", "assigneeUserId",
            "position", "createdAtMs", "updatedAtMs"})
    return Task(_id(data["id"], "id"), _id(data["projectId"], "projectId"), _id(data["taskListId"], "taskListId"), _string(data["title"], "title"), _status(data["status"]), _string(data["description"], "description",
                limit=_MAX_TEXT, empty=True), _id(data["assigneeUserId"], "assigneeUserId") if data["assigneeUserId"] is not None else None, _position(data["position"], 0), _time(data["createdAtMs"]), _time(data["updatedAtMs"]))


def _encode_task(x: Task) -> _Record:
    return {"id": x.id, "projectId": x.project_id, "taskListId": x.task_list_id, "title": x.title,
            "status": x.status.value, "description": x.description, "assigneeUserId": x.assignee_user_id,
            "position": x.position, "createdAtMs": x.created_at_ms, "updatedAtMs": x.updated_at_ms}


def _binding(data: Mapping[str, object]) -> ConversationBinding:
    _shape(data, {"id", "channel", "conversationId", "threadId", "projectId", "createdByUserId", "createdAtMs", "updatedAtMs"})
    return ConversationBinding(_id(data["id"], "id"), _key(data["channel"], "channel"), _key(data["conversationId"], "conversationId"), _optional_key(
        data["threadId"], "threadId"), _id(data["projectId"], "projectId"), _id(data["createdByUserId"], "createdByUserId"), _time(data["createdAtMs"]), _time(data["updatedAtMs"]))


def _encode_binding(x: ConversationBinding) -> _Record:
    return {"id": x.id, "channel": x.channel, "conversationId": x.conversation_id,
            "threadId": x.thread_id, "projectId": x.project_id,
            "createdByUserId": x.created_by_user_id, "createdAtMs": x.created_at_ms,
            "updatedAtMs": x.updated_at_ms}


def _profile(data: Mapping[str, object]) -> ExtensionProfile:
    _shape(data, {"projectId", "userId", "revision", "settings", "updatedAtMs"})
    revision = data["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise CollaborationStoreFormatError("invalid profile revision")
    return ExtensionProfile(_id(data["projectId"], "projectId"), _id(
        data["userId"], "userId"), revision, _json_object(data["settings"], "settings"), _time(data["updatedAtMs"]))


def _encode_profile(x: ExtensionProfile) -> _Record:
    return {"projectId": x.project_id, "userId": x.user_id, "revision": x.revision,
            "settings": thaw_json(x.settings), "updatedAtMs": x.updated_at_ms}


def _source(data: Mapping[str, object]) -> ContextSource:
    _shape(data, {"id", "projectId", "name", "kind", "enabled", "config", "createdAtMs", "updatedAtMs"})
    enabled = data["enabled"]
    if not isinstance(enabled, bool):
        raise CollaborationStoreFormatError("invalid context source enabled")
    return ContextSource(_id(data["id"], "id"), _id(data["projectId"], "projectId"), _string(data["name"], "name"), _context_kind(
        data["kind"]), enabled, _json_object(data["config"], "config"), _time(data["createdAtMs"]), _time(data["updatedAtMs"]))


def _encode_source(x: ContextSource) -> _Record:
    return {"id": x.id, "projectId": x.project_id, "name": x.name, "kind": x.kind.value,
            "enabled": x.enabled, "config": thaw_json(x.config), "createdAtMs": x.created_at_ms,
            "updatedAtMs": x.updated_at_ms}

def _string(value: object, field: str, *, limit: int = _MAX_STRING,
            empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError(f"{field} must be a string")
    value = value.strip()
    if (not value and not empty) or len(value) > limit or any(
            ord(char) < 32 for char in value):
        raise CollaborationStoreFormatError(f"invalid {field}")
    return value


def _id(value: object, field: str) -> str: return _string(value, field, limit=128)
def _key(value: object, field: str) -> str: return _string(value, field)


def _optional_key(value: object,
                  field: str) -> str | None: return _key(value,
                                                         field) if value is not None else None


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


def _position(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(
            value, int) or not 0 <= value <= _MAX_ITEMS:
        raise CollaborationStoreFormatError("invalid position")
    return value


def _organization_role(value: object) -> OrganizationRole:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError("invalid organization membership role")
    try:
        return OrganizationRole(value)
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid organization membership role") from exc


def _role(value: object) -> MembershipRole:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError("invalid membership role")
    try:
        return MembershipRole(value)
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid membership role") from exc


def _status(value: object) -> TaskStatus:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError("invalid task status")
    try:
        return TaskStatus(value)
    except ValueError as exc:
        raise CollaborationStoreFormatError("invalid task status") from exc


def _context_kind(value: object) -> ContextSourceKind:
    if not isinstance(value, str):
        raise CollaborationStoreFormatError("invalid context source kind")
    try:
        return ContextSourceKind(value)
    except ValueError as exc:
        raise CollaborationStoreFormatError(
            "invalid context source kind") from exc


def _json_object(value: object, field: str) -> JsonObject:
    data = _mapping(value, f"{field} must be an object")
    _json_value(data, field, 0)
    return freeze_json(data)


def _json_value(value: object, field: str, depth: int) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise CollaborationStoreFormatError(f"{field} is too deeply nested")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CollaborationStoreFormatError(f"invalid {field} number")
        return
    if isinstance(value, str):
        if len(value) > _MAX_TEXT or any(
                ord(char) < 32 and char not in "\n\t" for char in value):
            raise CollaborationStoreFormatError(f"invalid {field} string")
        return
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if len(mapping) > _MAX_JSON_ITEMS:
            raise CollaborationStoreFormatError(f"{field} has too many entries")
        for key, item in mapping.items():
            _key(key, f"{field} key")
            _json_value(item, field, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        items = cast(list[object] | tuple[object, ...], value)
        if len(items) > _MAX_JSON_ITEMS:
            raise CollaborationStoreFormatError(f"{field} has too many entries")
        for item in items:
            _json_value(item, field, depth + 1)
        return
    raise CollaborationStoreFormatError(f"{field} is not JSON-compatible")


def _identity_key(
    channel: str,
    sender_id: str) -> str: return f"{channel}\x00{sender_id}"


def _organization_member_key(
    organization_id: str,
    user_id: str) -> str: return f"{organization_id}\x00{user_id}"


def _member_key(
    project_id: str,
    user_id: str) -> str: return f"{project_id}\x00{user_id}"
def _conversation_key(channel: str, conversation_id: str, thread_id: str |
                      None) -> str: return f"{channel}\x00{conversation_id}\x00{thread_id or ''}"


def _new_id() -> str: return f"col_{uuid.uuid4().hex}"
def _now() -> int: return time.time_ns() // 1_000_000


def _next_position(values: Iterable[Mapping[str, object]], parent: str, field: str) -> int:
    return max(
        (_position(item["position"], 0) for item in values if item.get(field) == parent), default=-1) + 1


def _thread_id(metadata: Mapping[str, object]) -> str | None:
    for key in ("thread_id", "threadId", "message_thread_id",
                "messageThreadId", "root_id", "rootId"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _key(value, key)
    return None


def _is_direct(chat_id: str, sender_id: str,
               metadata: Mapping[str, object]) -> bool:
    if metadata.get("chat_type") == "p2p" or metadata.get("chatType") == "p2p":
        return True
    if str(metadata.get("chat_type") or metadata.get(
            "chatType") or "").lower() == "group":
        return False
    if str(metadata.get("direct") or "").lower() in {"1", "true", "yes"}:
        return True
    if metadata.get("is_group") is True or metadata.get("isGroup") is True:
        return False
    if metadata.get("is_direct") is True or metadata.get("isDirect") is True:
        return True
    for key in ("chat_type", "chatType", "conversation_type",
                "conversationType"):
        value = metadata.get(key)
        if isinstance(value, str):
            value = value.strip().lower().replace("-", "_")
            if value in {"group", "group_chat",
                         "groupchat", "channel", "room"}:
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
