"""Safe WebUI serialization and input handling for collaboration state."""

from __future__ import annotations

from collections.abc import Mapping
from types import EllipsisType
from typing import cast

from nanobot.collaboration.models import (
    Bot,
    BotCapabilityProfile,
    BotChannelAssignment,
    BotProjectAssignment,
    BotProjectChannel,
    ChannelProvision,
    ContextSource,
    ContextSourceKind,
    ExtensionProfile,
    Organization,
    OrganizationMembership,
    PairingChallenge,
    PersonalTask,
    Project,
    ProjectMembership,
    Task,
    TaskList,
    TaskStatus,
    User,
    thaw_json,
)

_MAX_SELECTIONS = 256
_SECRET_FIELD_MARKERS = (
    "secret", "token", "password", "credential", "authorization", "api_key",
    "apikey", "private", "key", "env", "header",
)


def user_payload(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "display_name": user.display_name,
        "default_vault_id": user.default_vault_id,
        "default_persona_id": user.default_persona_id,
        "default_organization_id": user.default_organization_id,
        "default_bot_id": user.default_bot_id,
    }


def bot_payload(bot: Bot) -> dict[str, object]:
    return {
        "id": bot.id,
        "organization_id": bot.organization_id,
        "owner_user_id": bot.owner_user_id,
        "name": bot.name,
        "avatar_url": bot.avatar_url,
        "persona_id": bot.persona_id,
        "state": bot.state.value,
        "created_at_ms": bot.created_at_ms,
        "updated_at_ms": bot.updated_at_ms,
    }


def bot_project_payload(assignment: BotProjectAssignment) -> dict[str, object]:
    return {
        "bot_id": assignment.bot_id,
        "project_id": assignment.project_id,
        "assigned_by_user_id": assignment.assigned_by_user_id,
        "created_at_ms": assignment.created_at_ms,
    }


def bot_channel_payload(assignment: BotChannelAssignment) -> dict[str, object]:
    return {
        "bot_id": assignment.bot_id,
        "channel_type": assignment.channel_type,
        "instance_id": assignment.instance_id,
        "claimed_by_user_id": assignment.claimed_by_user_id,
        "created_at_ms": assignment.created_at_ms,
    }


def claimable_channel_payload(
    provision: ChannelProvision,
    *,
    channel_display_name: str,
    instance_display_name: str,
    status: str,
) -> dict[str, object]:
    """Serialize one claimable instance as the two public names the dropdown needs.

    Deliberately narrower than the host inventory it replaces: no configured values,
    no extension identity or revision, and no filesystem location.
    """
    return {
        "channel_type": provision.channel_type,
        "channel_display_name": channel_display_name,
        "instance_id": provision.instance_id,
        "display_name": instance_display_name,
        "status": status,
    }


def bot_project_channel_payload(route: BotProjectChannel) -> dict[str, object]:
    return {
        "bot_id": route.bot_id,
        "project_id": route.project_id,
        "channel_type": route.channel_type,
        "instance_id": route.instance_id,
        "enabled": route.enabled,
        "updated_at_ms": route.updated_at_ms,
    }


def bot_capability_payload(profile: BotCapabilityProfile) -> dict[str, object]:
    return {
        "bot_id": profile.bot_id,
        "project_id": profile.project_id,
        "revision": profile.revision,
        "settings": thaw_json(profile.settings),
        "updated_at_ms": profile.updated_at_ms,
    }


def pairing_challenge_payload(
    challenge: PairingChallenge, *, code: str | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": challenge.id,
        "purpose": challenge.purpose.value,
        "organization_id": challenge.organization_id,
        "bot_id": challenge.bot_id,
        "project_id": challenge.project_id,
        "channel_type": challenge.channel_type,
        "instance_id": challenge.instance_id,
        "expires_at_ms": challenge.expires_at_ms,
        "verified": challenge.verified_at_ms is not None,
        "consumed": challenge.consumed_at_ms is not None,
        "created_at_ms": challenge.created_at_ms,
    }
    if code is not None:
        payload["code"] = code
    return payload


def organization_payload(organization: Organization) -> dict[str, object]:
    return {
        "id": organization.id,
        "name": organization.name,
        "created_by_user_id": organization.created_by_user_id,
        "is_personal": organization.is_personal,
        "created_at_ms": organization.created_at_ms,
        "updated_at_ms": organization.updated_at_ms,
    }


def organization_member_payload(member: OrganizationMembership) -> dict[str, object]:
    return {
        "organization_id": member.organization_id,
        "user_id": member.user_id,
        "role": member.role.value,
        "created_at_ms": member.created_at_ms,
    }


def project_member_payload(member: ProjectMembership) -> dict[str, object]:
    return {
        "project_id": member.project_id,
        "user_id": member.user_id,
        "role": member.role.value,
        "created_at_ms": member.created_at_ms,
    }


def project_payload(project: Project) -> dict[str, object]:
    """Return project metadata without its private workspace location."""
    return {
        "id": project.id,
        "name": project.name,
        "organization_id": project.organization_id,
        "created_at_ms": project.created_at_ms,
        "updated_at_ms": project.updated_at_ms,
    }


def task_list_payload(task_list: TaskList) -> dict[str, object]:
    return {
        "id": task_list.id,
        "project_id": task_list.project_id,
        "name": task_list.name,
        "position": task_list.position,
        "created_at_ms": task_list.created_at_ms,
        "updated_at_ms": task_list.updated_at_ms,
    }


def task_payload(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "task_list_id": task.task_list_id,
        "title": task.title,
        "status": task.status.value,
        "description": task.description,
        "assignee_user_id": task.assignee_user_id,
        "position": task.position,
        "created_at_ms": task.created_at_ms,
        "updated_at_ms": task.updated_at_ms,
    }


def personal_task_payload(task: PersonalTask) -> dict[str, object]:
    """Serialize a task without exposing its owning workspace or credentials."""
    return {
        "id": task.id,
        "vault_id": task.vault_id,
        "title": task.title,
        "note": task.note,
        "status": task.status.value,
        "priority": task.priority,
        "due_at_ms": task.due_at_ms,
        "timezone": task.timezone,
        "recurrence_rule": task.recurrence_rule,
        "source_type": task.source_type,
        "source_ref": task.source_ref,
        "external_provider": task.external_provider,
        "review_state": task.review_state.value,
        "created_at_ms": task.created_at_ms,
        "updated_at_ms": task.updated_at_ms,
    }


def context_source_payload(source: ContextSource) -> dict[str, object]:
    raw_config: object = thaw_json(source.config)
    return {
        "id": source.id,
        "project_id": source.project_id,
        "name": source.name,
        "kind": source.kind.value,
        "enabled": source.enabled,
        "config": _safe_config(raw_config),
        "created_at_ms": source.created_at_ms,
        "updated_at_ms": source.updated_at_ms,
    }


def extension_profile_payload(profile: ExtensionProfile) -> dict[str, object]:
    raw: object = thaw_json(profile.settings)
    settings = _string_mapping(raw) or {}
    return {
        "revision": profile.revision,
        "settings": {
            key: settings[key]
            for key in ("skills", "mcpServers", "contextMaxTokens")
            if key in settings
        },
    }


def profile_settings(
    raw: object,
    *,
    available_skill_ids: set[str],
    available_mcp_ids: set[str],
) -> dict[str, object]:
    """Validate the deliberately small, secret-free extension profile schema."""
    settings = _string_mapping(raw)
    allowed_keys = {"skills", "mcpServers", "contextMaxTokens"}
    if settings is None:
        raise ValueError("invalid extension settings")
    if unsupported_keys := set(settings) - allowed_keys:
        raise ValueError(
            f"unsupported extension setting: {', '.join(sorted(unsupported_keys))}"
        )
    result: dict[str, object] = {}
    for key, allowed in (
        ("skills", available_skill_ids),
        ("mcpServers", available_mcp_ids),
    ):
        if key not in settings:
            continue
        values = _string_list(settings[key], key)
        if any(value not in allowed for value in values):
            raise ValueError(f"unknown {key} selection")
        result[key] = values
    if "contextMaxTokens" in settings:
        value = settings["contextMaxTokens"]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 200_000
        ):
            raise ValueError("contextMaxTokens must be an integer between 1 and 200000")
        result["contextMaxTokens"] = value
    return result


def required_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def optional_string(payload: Mapping[str, object], name: str) -> str | None:
    if name not in payload:
        return None
    value = payload[name]
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > 16_000:
        raise ValueError(f"{name} must be a string")
    return value.strip()


def optional_position(payload: Mapping[str, object]) -> int | None:
    if "position" not in payload:
        return None
    value = payload["position"]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000:
        raise ValueError("position must be a non-negative integer")
    return value


def optional_nonnegative_int(
    payload: Mapping[str, object], key: str
) -> int | None:
    if key not in payload:
        return None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def create_assignee(payload: Mapping[str, object]) -> str | None:
    """Return the optional assignee for task creation."""
    if "assignee_user_id" not in payload:
        return None
    return _assignee_value(payload["assignee_user_id"])


def update_assignee(payload: Mapping[str, object]) -> str | None | EllipsisType:
    """Return an assignee update or the store's omission sentinel."""
    if "assignee_user_id" not in payload:
        return ...
    return _assignee_value(payload["assignee_user_id"])


def _assignee_value(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
        raise ValueError("assignee_user_id must be a string or null")
    return value.strip()


def optional_status(payload: Mapping[str, object]) -> TaskStatus | None:
    if "status" not in payload:
        return None
    try:
        return TaskStatus(payload["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid task status") from exc


def optional_source_kind(payload: Mapping[str, object]) -> ContextSourceKind | None:
    if "kind" not in payload:
        return None
    try:
        return ContextSourceKind(payload["kind"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid context source kind") from exc


def optional_config(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    if "config" not in payload:
        return None
    value = payload["config"]
    config = _string_mapping(value)
    if config is None:
        raise ValueError("config must be an object")
    return config


def required_source_kind(payload: Mapping[str, object]) -> ContextSourceKind:
    value = optional_source_kind(payload)
    if value is None:
        raise ValueError("kind is required")
    return value


def optional_enabled(payload: Mapping[str, object]) -> bool | None:
    if "enabled" not in payload:
        return None
    value = payload["enabled"]
    if not isinstance(value, bool):
        raise ValueError("enabled must be boolean")
    return value


def _string_list(raw: object, name: str) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError(f"{name} must be a bounded string array")
    raw_values = cast(list[object], raw)
    if len(raw_values) > _MAX_SELECTIONS:
        raise ValueError(f"{name} must be a bounded string array")
    values: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise ValueError(f"{name} must be a bounded string array")
        value = value.strip()
        if value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _safe_config(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {
            key: _safe_config(item)
            for key, item in mapping.items()
            if isinstance(key, str) and not _is_secret_field(key)
        }
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_safe_config(item) for item in items]
    return value


def _string_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast(Mapping[str, object], mapping)


def _is_secret_field(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_FIELD_MARKERS)
