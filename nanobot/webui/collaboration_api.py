"""Safe WebUI serialization and input handling for collaboration state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from nanobot.collaboration.models import (
    ChannelAssignment,
    ChannelProvision,
    PairingChallenge,
    Project,
    ProjectMembership,
    User,
)

_MAX_SELECTIONS = 256


def user_payload(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "default_project_id": user.default_project_id,
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


def channel_assignment_payload(
    assignment: ChannelAssignment,
    *,
    presentation: tuple[str, str, str] | None = None,
) -> dict[str, object]:
    """Serialize one assignment, naming the instance when the runtime still has it.

    The two display names and the runtime status are the same three public fields
    the claimable listing already exposes, and only reach people who manage or
    belong to the assignment's project.
    """
    payload: dict[str, object] = {
        "channel_type": assignment.channel_type,
        "instance_id": assignment.instance_id,
        "project_id": assignment.project_id,
        "assignee_user_id": assignment.assignee_user_id,
        "enabled": assignment.enabled,
        "created_by_user_id": assignment.created_by_user_id,
        "created_at_ms": assignment.created_at_ms,
        "updated_at_ms": assignment.updated_at_ms,
    }
    if presentation is not None:
        channel_display_name, instance_display_name, status = presentation
        payload["channel_display_name"] = channel_display_name
        payload["display_name"] = instance_display_name
        payload["status"] = status
    return payload


def pairing_challenge_payload(
    challenge: PairingChallenge, *, code: str | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": challenge.id,
        "project_id": challenge.project_id,
        "assignee_user_id": challenge.assignee_user_id,
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
        "created_by_user_id": project.created_by_user_id,
        "allowed_skills": list(project.allowed_skills) if project.allowed_skills is not None else None,
        "allowed_mcp_servers": (
            list(project.allowed_mcp_servers) if project.allowed_mcp_servers is not None else None
        ),
        "created_at_ms": project.created_at_ms,
        "updated_at_ms": project.updated_at_ms,
    }


def capability_allowlists(
    raw: object,
    *,
    available_skill_ids: set[str],
    available_mcp_ids: set[str],
) -> dict[str, list[str] | None]:
    """Validate the project capability allowlists submitted by the WebUI.

    Each key is optional. ``null`` lifts the restriction; a list restricts the
    project to those entries, which must currently exist.
    """
    settings = _string_mapping(raw)
    if settings is None:
        raise ValueError("invalid capability settings")
    allowed_keys = {"allowed_skills", "allowed_mcp_servers"}
    if unsupported_keys := set(settings) - allowed_keys:
        raise ValueError(
            f"unsupported capability setting: {', '.join(sorted(unsupported_keys))}"
        )
    result: dict[str, list[str] | None] = {}
    for key, allowed in (
        ("allowed_skills", available_skill_ids),
        ("allowed_mcp_servers", available_mcp_ids),
    ):
        if key not in settings:
            continue
        if settings[key] is None:
            result[key] = None
            continue
        values = _string_list(settings[key], key)
        if any(value not in allowed for value in values):
            raise ValueError(f"unknown {key} selection")
        result[key] = values
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


def _string_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast(Mapping[str, object], mapping)
