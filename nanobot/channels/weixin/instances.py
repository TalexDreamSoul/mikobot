"""Weixin-owned multi-instance configuration helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, cast

from loguru import logger

from nanobot.channels.contracts import ChannelInstanceSpec, ChannelManagementSpec
from nanobot.channels.weixin.state import local_state_present
from nanobot.config.loader import merge_missing_defaults
from nanobot.config.paths import get_config_path

DEFAULT_INSTANCE_ID = "default"
_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_instance_id(value: str) -> str:
    instance_id = value.strip()
    if not instance_id or not _INSTANCE_ID_RE.fullmatch(instance_id):
        raise ValueError("instance id must match [A-Za-z0-9_-]+")
    return instance_id


def runtime_channel_name(base_name: str, instance_id: str) -> str:
    return base_name if instance_id == DEFAULT_INSTANCE_ID else f"{base_name}.{instance_id}"


def weixin_default_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "allowFrom": [],
        "baseUrl": "https://ilinkai.weixin.qq.com",
        "cdnBaseUrl": "https://novac2c.cdn.weixin.qq.com/c2c",
        "routeTag": None,
        "token": "",
        "stateDir": "",
        "pollTimeout": 35,
        "sendProgress": False,
        "sendToolHints": False,
        "replyProgressMessages": False,
        "replyProgressMaxMessages": 2,
        "contextMessageBudget": 8,
        "streaming": True,
        "blockStreaming": False,
        "blockStreamingMinChars": 1200,
        "blockStreamingMaxMessages": 3,
    }


def managed_weixin_instance_specs(
    section: Any,
    *,
    enabled_only: bool = True,
) -> list[ChannelInstanceSpec]:
    return weixin_instance_specs(section, weixin_default_config(), enabled_only=enabled_only)


def update_managed_weixin_instance(
    section: Any,
    values: dict[str, Any],
    *,
    instance_id: str = DEFAULT_INSTANCE_ID,
) -> dict[str, Any]:
    existing = cast(dict[str, Any], section) if isinstance(section, dict) else {}
    return upsert_weixin_instance(existing, weixin_default_config(), instance_id, values)


def _base_instance_config(defaults: dict[str, Any]) -> dict[str, Any]:
    config = dict(defaults)
    config["instanceId"] = DEFAULT_INSTANCE_ID
    config["name"] = "WeChat"
    return config


def _instance_inputs(
    section: Any,
    defaults: dict[str, Any],
) -> tuple[list[Any], dict[str, Any] | None]:
    if hasattr(section, "model_dump"):
        section = section.model_dump(mode="json", by_alias=True)
    if not isinstance(section, dict):
        section = {}
    section_data = cast(dict[str, Any], section)
    instances = section_data.get("instances")
    if isinstance(instances, list):
        inherited = {key: value for key, value in section_data.items() if key != "instances"}
        return list(cast(list[Any], instances)), inherited
    return ([section_data] if section_data else [_base_instance_config(defaults)]), None


def _state_dir(
    raw: dict[str, Any],
    inherited: dict[str, Any] | None,
    instance_id: str,
) -> str:
    explicit = str(raw.get("stateDir") or raw.get("state_dir") or "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    inherited_dir = str((inherited or {}).get("stateDir") or "").strip()
    base = Path(inherited_dir).expanduser() if inherited_dir else get_config_path().parent / "weixin"
    return str(base if instance_id == DEFAULT_INSTANCE_ID else base / instance_id)


def _normalize_instance(
    raw: dict[str, Any],
    defaults: dict[str, Any],
    *,
    inherited: dict[str, Any] | None = None,
    fallback_id: str = DEFAULT_INSTANCE_ID,
) -> dict[str, Any]:
    config = cast(dict[str, Any], merge_missing_defaults(inherited or {}, defaults))
    config = cast(dict[str, Any], merge_missing_defaults(raw, config))
    raw_id = raw.get("id") or raw.get("instanceId") or raw.get("instance_id") or fallback_id
    instance_id = validate_instance_id(str(raw_id))
    config["id"] = instance_id
    config["instanceId"] = instance_id
    config["name"] = str(
        raw.get("name")
        or config.get("name")
        or ("WeChat" if instance_id == DEFAULT_INSTANCE_ID else f"WeChat {instance_id}")
    ).strip()
    config["stateDir"] = _state_dir(raw, inherited, instance_id)
    return config


def _identity_key(config: dict[str, Any]) -> str:
    token = str(config.get("token") or "").strip()
    return hashlib.sha256(token.encode()).hexdigest() if token else ""


def weixin_instance_specs(
    section: Any,
    defaults: dict[str, Any],
    *,
    enabled_only: bool = False,
) -> list[ChannelInstanceSpec]:
    raw_specs, inherited = _instance_inputs(section, defaults)
    specs: list[ChannelInstanceSpec] = []
    instance_ids: set[str] = set()
    state_dirs: set[str] = set()
    identity_owners: dict[str, str] = {}
    for index, raw in enumerate(raw_specs):
        if not isinstance(raw, dict):
            logger.warning("Skipping invalid Weixin instance at index {}", index)
            continue
        fallback_id = DEFAULT_INSTANCE_ID if index == 0 else f"wechat-{index + 1}"
        try:
            config = _normalize_instance(
                cast(dict[str, Any], raw), defaults, inherited=inherited, fallback_id=fallback_id
            )
        except ValueError as exc:
            logger.warning("Skipping invalid Weixin instance config: {}", exc)
            continue
        instance_id = str(config["instanceId"])
        state_dir = str(Path(str(config["stateDir"])).expanduser().resolve(strict=False))
        if instance_id in instance_ids or state_dir in state_dirs:
            logger.warning("Skipping duplicate Weixin instance or state directory '{}': {}", instance_id, state_dir)
            continue
        instance_ids.add(instance_id)
        state_dirs.add(state_dir)
        enabled = bool(config.get("enabled", defaults.get("enabled", False)))
        if enabled_only and not enabled:
            continue
        identity = _identity_key(config)
        if enabled_only and identity:
            if identity in identity_owners:
                logger.warning(
                    "Skipping Weixin instance '{}' because it shares credentials with '{}'",
                    instance_id,
                    identity_owners[identity],
                )
                continue
            identity_owners[identity] = instance_id
        specs.append(ChannelInstanceSpec(instance_id=instance_id, config=config))
    return specs


def canonical_weixin_section(section: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    raw_specs, inherited = _instance_inputs(section, defaults)
    instances: list[dict[str, Any]] = []
    ids: set[str] = set()
    state_dirs: set[str] = set()
    for index, raw in enumerate(raw_specs):
        if not isinstance(raw, dict):
            raise ValueError(f"Weixin instance at index {index} must be an object")
        fallback_id = DEFAULT_INSTANCE_ID if index == 0 else f"wechat-{index + 1}"
        config = _normalize_instance(
            cast(dict[str, Any], raw), defaults, inherited=inherited, fallback_id=fallback_id
        )
        instance_id = str(config["instanceId"])
        state_dir = str(Path(str(config["stateDir"])).expanduser().resolve(strict=False))
        if instance_id in ids:
            raise ValueError(f"duplicate Weixin instance id '{instance_id}'")
        if state_dir in state_dirs:
            raise ValueError(f"duplicate Weixin state directory '{state_dir}'")
        ids.add(instance_id)
        state_dirs.add(state_dir)
        instances.append(config)
    return {"instances": instances}


def upsert_weixin_instance(
    section: Any,
    defaults: dict[str, Any],
    instance_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    instance_id = validate_instance_id(instance_id)
    canonical = canonical_weixin_section(section, defaults)
    instances = cast(list[dict[str, Any]], canonical.setdefault("instances", []))
    for instance in instances:
        if instance.get("id") == instance_id or instance.get("instanceId") == instance_id:
            instance.update(values)
            instance["id"] = instance_id
            instance["instanceId"] = instance_id
            normalized = _normalize_instance(instance, defaults, fallback_id=instance_id)
            instance.clear()
            instance.update(normalized)
            canonical_weixin_section(canonical, defaults)
            return canonical
    config = _normalize_instance(
        {**values, "id": instance_id}, defaults, fallback_id=instance_id
    )
    instances.append(config)
    canonical_weixin_section(canonical, defaults)
    return canonical


def update_weixin_instance_preserving_shape(
    section: Any,
    defaults: dict[str, Any],
    instance_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    instance_id = validate_instance_id(instance_id)
    if hasattr(section, "model_dump"):
        section = section.model_dump(mode="json", by_alias=True)
    if instance_id == DEFAULT_INSTANCE_ID and (
        section is None
        or (
            isinstance(section, dict)
            and not isinstance(cast(dict[str, Any], section).get("instances"), list)
        )
    ):
        existing = cast(dict[str, Any], section) if isinstance(section, dict) else {}
        return {**existing, **values}
    return upsert_weixin_instance(section, defaults, instance_id, values)


WEIXIN_MANAGEMENT = ChannelManagementSpec(
    multi_instance=True,
    default_config=weixin_default_config,
    instance_specs=managed_weixin_instance_specs,
    update_instance_config=update_managed_weixin_instance,
    runtime_name=runtime_channel_name,
    local_state_present=local_state_present,
)

__all__ = [
    "DEFAULT_INSTANCE_ID",
    "WEIXIN_MANAGEMENT",
    "canonical_weixin_section",
    "runtime_channel_name",
    "update_weixin_instance_preserving_shape",
    "upsert_weixin_instance",
    "validate_instance_id",
    "weixin_default_config",
    "weixin_instance_specs",
]
