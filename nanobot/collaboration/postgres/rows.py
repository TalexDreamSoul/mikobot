"""Strict PostgreSQL dictionary-row decoders for collaboration domain records."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TypeAlias, cast

from nanobot.collaboration.models import (
    Bot,
    BotCapabilityProfile,
    BotChannelAssignment,
    BotProjectAssignment,
    BotProjectChannel,
    BotState,
    ContextSource,
    ContextSourceKind,
    ConversationBinding,
    ExtensionProfile,
    JsonValue,
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
    freeze_json,
    thaw_json,
)

Row: TypeAlias = Mapping[str, object]
_MAX_JSON_BYTES = 65_536


def decode_user_row(row: Row) -> User:
    return User(
        id=_string(row, "id"),
        display_name=_string(row, "display_name"),
        default_project_id=_optional_string(row, "default_project_id"),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
        default_vault_id=_optional_string(row, "default_vault_id"),
        default_persona_id=_optional_string(row, "default_persona_id"),
        default_organization_id=(
            _optional_string(row, "default_organization_id")
            if "default_organization_id" in row else None
        ),
        default_bot_id=(
            _optional_string(row, "default_bot_id") if "default_bot_id" in row else None
        ),
    )


def decode_identity_row(row: Row) -> UserIdentity:
    return UserIdentity(
        user_id=_string(row, "user_id"),
        channel=_string(row, "channel"),
        sender_id=_string(row, "sender_id"),
        created_at_ms=_integer(row, "created_at_ms"),
    )


def decode_vault_row(row: Row) -> Vault:
    return Vault(
        id=_string(row, "id"),
        owner_user_id=_string(row, "owner_user_id"),
        name=_string(row, "name"),
        kind=VaultKind(_string(row, "kind")),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_persona_row(row: Row) -> Persona:
    return Persona(
        id=_string(row, "id"),
        owner_user_id=_string(row, "owner_user_id"),
        name=_string(row, "name"),
        default_vault_id=_string(row, "default_vault_id"),
        instructions=_string(row, "instructions"),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_bot_row(row: Row) -> Bot:
    return Bot(
        id=_string(row, "id"),
        organization_id=_string(row, "organization_id"),
        owner_user_id=_string(row, "owner_user_id"),
        name=_string(row, "name"),
        avatar_url=_optional_string(row, "avatar_url"),
        persona_id=_optional_string(row, "persona_id"),
        state=BotState(_string(row, "state")),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_bot_project_assignment_row(row: Row) -> BotProjectAssignment:
    return BotProjectAssignment(
        bot_id=_string(row, "bot_id"),
        project_id=_string(row, "project_id"),
        assigned_by_user_id=_string(row, "assigned_by_user_id"),
        created_at_ms=_integer(row, "created_at_ms"),
    )


def decode_bot_channel_assignment_row(row: Row) -> BotChannelAssignment:
    return BotChannelAssignment(
        bot_id=_string(row, "bot_id"),
        channel_type=_string(row, "channel_type"),
        instance_id=_string(row, "instance_id"),
        claimed_by_user_id=_string(row, "claimed_by_user_id"),
        created_at_ms=_integer(row, "created_at_ms"),
    )


def decode_bot_project_channel_row(row: Row) -> BotProjectChannel:
    return BotProjectChannel(
        bot_id=_string(row, "bot_id"),
        project_id=_string(row, "project_id"),
        channel_type=_string(row, "channel_type"),
        instance_id=_string(row, "instance_id"),
        enabled=_boolean(row, "enabled"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_bot_capability_profile_row(row: Row) -> BotCapabilityProfile:
    return BotCapabilityProfile(
        bot_id=_string(row, "bot_id"),
        project_id=_optional_string(row, "project_id"),
        revision=_integer(row, "revision"),
        settings=freeze_bounded_json(_required(row, "settings"), "settings"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_pairing_challenge_row(row: Row) -> PairingChallenge:
    return PairingChallenge(
        id=_string(row, "id"),
        code_digest=_string(row, "code_digest"),
        requested_by_user_id=_string(row, "requested_by_user_id"),
        purpose=PairingPurpose(_string(row, "purpose")),
        organization_id=_string(row, "organization_id"),
        bot_id=_string(row, "bot_id"),
        project_id=_optional_string(row, "project_id"),
        channel_type=_string(row, "channel_type"),
        instance_id=_string(row, "instance_id"),
        expires_at_ms=_integer(row, "expires_at_ms"),
        verified_at_ms=_optional_integer(row, "verified_at_ms"),
        verified_sender_id=_optional_string(row, "verified_sender_id"),
        consumed_at_ms=_optional_integer(row, "consumed_at_ms"),
        created_at_ms=_integer(row, "created_at_ms"),
    )


def decode_share_grant_row(row: Row) -> ShareGrant:
    return ShareGrant(
        id=_string(row, "id"),
        vault_id=_string(row, "vault_id"),
        grantee_user_id=_string(row, "grantee_user_id"),
        resource_type=_string(row, "resource_type"),
        resource_id=_optional_string(row, "resource_id"),
        permission=SharePermission(_string(row, "permission")),
        expires_at_ms=_optional_integer(row, "expires_at_ms"),
        created_at_ms=_integer(row, "created_at_ms"),
        revoked_at_ms=_optional_integer(row, "revoked_at_ms"),
    )


def decode_organization_row(row: Row) -> Organization:
    return Organization(
        id=_string(row, "id"),
        name=_string(row, "name"),
        created_by_user_id=_string(row, "created_by_user_id"),
        is_personal=_boolean(row, "is_personal"),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_organization_membership_row(row: Row) -> OrganizationMembership:
    return OrganizationMembership(
        organization_id=_string(row, "organization_id"),
        user_id=_string(row, "user_id"),
        role=OrganizationRole(_string(row, "role")),
        created_at_ms=_integer(row, "created_at_ms"),
    )


def decode_project_row(row: Row) -> Project:
    return Project(
        id=_string(row, "id"),
        name=_string(row, "name"),
        workspace_path=_string(row, "workspace_path"),
        created_by_user_id=_string(row, "created_by_user_id"),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
        organization_id=_string(row, "organization_id"),
    )


def decode_project_membership_row(row: Row) -> ProjectMembership:
    return ProjectMembership(
        project_id=_string(row, "project_id"),
        user_id=_string(row, "user_id"),
        role=MembershipRole(_string(row, "role")),
        created_at_ms=_integer(row, "created_at_ms"),
    )


def decode_task_list_row(row: Row) -> TaskList:
    return TaskList(
        id=_string(row, "id"),
        project_id=_string(row, "project_id"),
        name=_string(row, "name"),
        position=_integer(row, "position"),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_task_row(row: Row) -> Task:
    return Task(
        id=_string(row, "id"),
        project_id=_string(row, "project_id"),
        task_list_id=_string(row, "task_list_id"),
        title=_string(row, "title"),
        status=TaskStatus(_string(row, "status")),
        description=_string(row, "description"),
        assignee_user_id=_optional_string(row, "assignee_user_id"),
        position=_integer(row, "position"),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_personal_task_row(row: Row) -> PersonalTask:
    return PersonalTask(
        id=_string(row, "id"),
        owner_user_id=_string(row, "owner_user_id"),
        vault_id=_string(row, "vault_id"),
        title=_string(row, "title"),
        note=_string(row, "note"),
        status=TaskStatus(_string(row, "status")),
        priority=_integer(row, "priority"),
        due_at_ms=_optional_integer(row, "due_at_ms"),
        timezone=_optional_string(row, "timezone"),
        recurrence_rule=_optional_string(row, "recurrence_rule"),
        source_type=_string(row, "source_type"),
        source_ref=_optional_string(row, "source_ref"),
        external_provider=_optional_string(row, "external_provider"),
        external_id=_optional_string(row, "external_id"),
        external_version=_optional_string(row, "external_version"),
        review_state=TaskReviewState(_string(row, "review_state")),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_conversation_binding_row(row: Row) -> ConversationBinding:
    thread_id = _optional_string(row, "thread_id")
    return ConversationBinding(
        id=_string(row, "id"),
        channel=_string(row, "channel"),
        conversation_id=_string(row, "conversation_id"),
        thread_id=thread_id if thread_id else None,
        project_id=_string(row, "project_id"),
        created_by_user_id=_string(row, "created_by_user_id"),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_extension_profile_row(row: Row) -> ExtensionProfile:
    return ExtensionProfile(
        project_id=_string(row, "project_id"),
        user_id=_string(row, "user_id"),
        revision=_integer(row, "revision"),
        settings=freeze_bounded_json(_required(row, "settings"), "settings"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def decode_context_source_row(row: Row) -> ContextSource:
    return ContextSource(
        id=_string(row, "id"),
        project_id=_string(row, "project_id"),
        name=_string(row, "name"),
        kind=ContextSourceKind(_string(row, "kind")),
        enabled=_boolean(row, "enabled"),
        config=freeze_bounded_json(_required(row, "config"), "config"),
        created_at_ms=_integer(row, "created_at_ms"),
        updated_at_ms=_integer(row, "updated_at_ms"),
    )


def freeze_bounded_json(value: object, column: str) -> Mapping[str, JsonValue]:
    """Validate and immutably copy one bounded JSON object returned by PostgreSQL."""
    _validate_json_keys(value, column)
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{column} must be a JSON object")
    result = cast(Mapping[str, JsonValue], frozen)
    _check_json_size(result, column)
    return result


def thaw_bounded_json(value: Mapping[str, JsonValue], column: str) -> dict[str, object]:
    """Return a fresh bounded JSON object suitable for a PostgreSQL parameter."""
    frozen = freeze_bounded_json(value, column)
    thawed = thaw_json(frozen)
    if not isinstance(thawed, dict):
        raise TypeError(f"{column} must be a JSON object")
    return cast(dict[str, object], thawed)


def _required(row: Row, column: str) -> object:
    try:
        return row[column]
    except KeyError as exc:
        raise ValueError(f"missing PostgreSQL column: {column}") from exc


def _string(row: Row, column: str) -> str:
    value = _required(row, column)
    if not isinstance(value, str):
        raise TypeError(f"{column} must be a string")
    return value


def _optional_string(row: Row, column: str) -> str | None:
    value = _required(row, column)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{column} must be a string or null")
    return value


def _integer(row: Row, column: str) -> int:
    value = _required(row, column)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{column} must be an integer")
    return value


def _optional_integer(row: Row, column: str) -> int | None:
    value = _required(row, column)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{column} must be an integer or null")
    return value


def _boolean(row: Row, column: str) -> bool:
    value = _required(row, column)
    if not isinstance(value, bool):
        raise TypeError(f"{column} must be a boolean")
    return value


def _validate_json_keys(value: object, column: str) -> None:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, nested in mapping.items():
            if not isinstance(key, str):
                raise TypeError(f"{column} JSON object keys must be strings")
            _validate_json_keys(nested, column)
    elif isinstance(value, (list, tuple)):
        for nested in cast(tuple[object, ...] | list[object], value):
            _validate_json_keys(nested, column)


def _check_json_size(value: Mapping[str, JsonValue], column: str) -> None:
    encoded = json.dumps(
        thaw_json(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(encoded) > _MAX_JSON_BYTES:
        raise ValueError(f"{column} exceeds {_MAX_JSON_BYTES} UTF-8 bytes")
