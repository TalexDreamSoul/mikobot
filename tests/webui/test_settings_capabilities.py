from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.config.schema import Config
from nanobot.webui.settings_capabilities import (
    CapabilitySettingsHandler,
    CapabilitySettingsOperations,
    capability_settings_payload,
    update_api_settings,
    update_image_generation_settings,
    update_network_safety_settings,
    update_transcription_settings,
    update_web_search_settings,
)
from nanobot.webui.settings_contracts import SettingsRequest, WebUISettingsError
from nanobot.webui.settings_models import create_provider_settings, update_provider_settings
from nanobot.webui.settings_services import WebUISettingsServices


def test_dynamic_image_provider_selection_and_private_overrides():
    config = Config()
    name = create_provider_settings(config, {
        "name": ["SenseNova"], "apiBase": ["https://images.test/v1"],
    })
    assert update_image_generation_settings(config, {
        "provider": [name], "model": ["image-model"], "enabled": ["true"],
        "extraHeaders": ['{"X-Api-Key":"private-key"}'],
        "extraBody": ['{"watermark":false}'],
    }, oauth_status=_oauth_status)
    payload = capability_settings_payload(config, oauth_status=_oauth_status)["image_generation"]
    assert payload["provider"] == name
    assert payload["extra_headers"] == {"X-Api-Key": "••••••••"}
    assert "private-key" not in str(payload)
    row = next(row for row in payload["providers"] if row["name"] == name)
    assert row["label"] == "SenseNova" and row["configured"] and row["uses_images_api"]
    changed, reload_needed = update_provider_settings(config, {
        "provider": [name], "apiBase": ["https://updated.test/v1"],
    })
    assert changed and reload_needed
    update_image_generation_settings(config, {
        "extra_headers": ['{"X-Api-Key":"••••••••","X-Route":"images"}'],
    }, oauth_status=_oauth_status)
    assert config.tools.image_generation.extra_headers["X-Api-Key"] == "private-key"
    with pytest.raises(WebUISettingsError, match="JSON object"):
        update_image_generation_settings(config, {"extra_body": ["[]"]}, oauth_status=_oauth_status)
    config.providers.openai.api_key = "fixture"
    update_image_generation_settings(config, {"provider": ["openai"]}, oauth_status=_oauth_status)
    assert config.tools.image_generation.extra_headers is None
    assert config.tools.image_generation.extra_body is None


def test_image_provider_keeps_dynamic_id_case_and_rejects_missing_connection():
    config = Config.model_validate({"providers": {"CompanyGateway": {"apiBase": "https://images.test/v1"}}})
    update_image_generation_settings(config, {
        "provider": ["CompanyGateway"], "model": ["image"], "enabled": ["true"],
    }, oauth_status=_oauth_status)
    assert config.tools.image_generation.provider == "CompanyGateway"
    with pytest.raises(WebUISettingsError, match="unknown image generation provider"):
        update_image_generation_settings(config, {"provider": ["missing"]}, oauth_status=_oauth_status)


def _oauth_status(_spec: Any) -> dict[str, Any]:
    return {"configured": False}


def test_capability_domain_updates_representative_settings() -> None:
    config = Config()
    config.providers.openrouter.api_key = "sk-test"

    web_changed, web_restart = update_web_search_settings(
        config,
        {
            "provider": ["duckduckgo"],
            "max_results": ["7"],
            "use_jina_reader": ["false"],
        },
    )
    update_api_settings(
        config,
        {"host": ["127.0.0.2"], "port": ["8900"], "timeout": ["90"]},
    )
    image_changed = update_image_generation_settings(
        config,
        {"enabled": ["true"], "provider": ["openrouter"]},
        oauth_status=_oauth_status,
    )
    transcription_changed = update_transcription_settings(
        config,
        {"provider": ["openrouter"], "model": ["openai/whisper-large-v3"]},
    )
    network_changed, access_mode = update_network_safety_settings(
        config,
        {
            "webui_allow_local_service_access": ["false"],
            "webui_default_access_mode": ["restricted"],
        },
    )
    payload = capability_settings_payload(config, oauth_status=_oauth_status)

    assert (web_changed, web_restart) == (True, True)
    assert image_changed is True
    assert transcription_changed is True
    assert (network_changed, access_mode) == (True, "default")
    assert payload["web_search"]["max_results"] == 7
    assert payload["api"]["host"] == "127.0.0.2"
    assert payload["api"]["port"] == 8900
    assert payload["image_generation"]["enabled"] is True
    assert payload["transcription"]["provider"] == "openrouter"


def _recording_operations(applied: list[str]) -> CapabilitySettingsOperations:
    async def unused_reload() -> dict[str, Any]:
        raise AssertionError("image reload must not be used")

    def record(name: str):
        def operation(*_args: object, **_kwargs: object) -> dict[str, Any]:
            applied.append(name)
            return {}

        return operation

    return CapabilitySettingsOperations(
        update_web_search=record("web_search"),
        update_api=record("api"),
        update_image=record("image"),
        update_transcription=record("transcription"),
        update_network=record("network"),
        api_runtime=lambda: SimpleNamespace(),
        reload_image=unused_reload,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "api-update",
        "web-search-update",
        "transcription-update",
        "network-update",
        "image-update",
    ],
)
@pytest.mark.parametrize(
    ("actor_user_id", "system_admin"),
    [("ordinary-member", False), (None, True), ("   ", True)],
)
async def test_capability_domain_is_closed_to_everyone_but_administrators(
    tmp_path: Path,
    action: str,
    actor_user_id: str | None,
    system_admin: bool,
) -> None:
    """Every capability mutation is admin-only, including ones with no HTTP route."""
    applied: list[str] = []
    handler = CapabilitySettingsHandler(
        WebUISettingsServices.create(tmp_path / "config.json"),
        logger=SimpleNamespace(exception=lambda *_args: None),
    )

    result = await handler.handle(
        action,
        SettingsRequest(
            query={"provider": ["tavily"], "api_key": ["attacker-supplied-key"]},
            actor_user_id=actor_user_id,
            system_admin=system_admin,
        ),
        _recording_operations(applied),
    )

    assert result.status == 403
    assert result.error == "System administrator access is required"
    assert applied == []
    assert not (tmp_path / "config.json").exists()


@pytest.mark.asyncio
async def test_capability_api_status_stays_readable_for_members(tmp_path: Path) -> None:
    """The read-only API service status is the only member-visible capability action."""
    handler = CapabilitySettingsHandler(
        WebUISettingsServices.create(tmp_path / "config.json"),
        logger=SimpleNamespace(exception=lambda *_args: None),
    )
    operations = CapabilitySettingsOperations(
        update_web_search=lambda *_a, **_k: {},
        update_api=lambda *_a, **_k: {},
        update_image=lambda *_a, **_k: {},
        update_transcription=lambda *_a, **_k: {},
        update_network=lambda *_a, **_k: {},
        api_runtime=lambda: SimpleNamespace(
            status=lambda: SimpleNamespace(running=False)
        ),
        reload_image=lambda: None,
    )

    result = await handler.handle(
        "api-status",
        SettingsRequest(query={}, actor_user_id="ordinary-member", system_admin=False),
        operations,
    )

    assert result.status == 200
    assert result.payload is not None
    assert result.payload["running"] is False
