"""Provider lifecycle actions: one delegated image reload, and nothing else mutating."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from nanobot.config.schema import Config
from nanobot.extensions.adapters.providers import (
    ProviderExtensionServices,
    ProviderRegistryExtensionAdapter,
)
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionComponentKind,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    extension_component_id,
    extension_package_id,
)
from nanobot.extensions.registry import ExtensionRegistry, ExtensionRegistryError

_ADMIN = ExtensionActionContext(actor_id="owner", is_system_admin=True)
_USER = ExtensionActionContext(actor_id="member", is_system_admin=False)
_OPENAI = extension_package_id(ExtensionSource.PROVIDER_REGISTRY, "openai")
_OPENAI_IMAGE = extension_component_id(_OPENAI, ExtensionComponentKind.IMAGE_PROVIDER, "openai")
_OPENAI_LLM = extension_component_id(_OPENAI, ExtensionComponentKind.LLM_PROVIDER, "openai")
_OPENROUTER = extension_package_id(ExtensionSource.PROVIDER_REGISTRY, "openrouter")
_OPENROUTER_IMAGE = extension_component_id(
    _OPENROUTER, ExtensionComponentKind.IMAGE_PROVIDER, "openrouter"
)


class _Reloader:
    """Stand-in for the one existing image runtime-control owner."""

    def __init__(self, result: Mapping[str, object] | None = None, error: bool = False) -> None:
        self.calls = 0
        self._result = result if result is not None else {"ok": True, "requires_restart": False}
        self._error = error

    async def __call__(self) -> Mapping[str, object]:
        self.calls += 1
        if self._error:
            raise RuntimeError("hot reload exploded")
        return self._result


def _config(workspace: Path, *, enabled: bool = True, provider: str = "openai") -> Config:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.providers.openai.api_key = "sk-live"
    config.tools.image_generation.enabled = enabled
    config.tools.image_generation.provider = provider
    return config


def _adapter(
    config: Config,
    services: ProviderExtensionServices | None = None,
) -> ProviderRegistryExtensionAdapter:
    return ProviderRegistryExtensionAdapter(lambda: config, services=services)


def _package(
    adapter: ProviderRegistryExtensionAdapter,
    name: str,
) -> ExtensionPackageDescriptor:
    return next(p for p in adapter.snapshot().packages if p.name == name)


def _actions(adapter: ProviderRegistryExtensionAdapter, target_id: str) -> frozenset[ExtensionAction]:
    for package in adapter.snapshot().packages:
        if package.id == target_id:
            return package.actions
        for component in package.components:
            if component.id == target_id:
                return component.actions
    raise AssertionError(f"missing target: {target_id}")


def test_only_the_selected_enabled_image_provider_declares_reload(tmp_path: Path) -> None:
    """Reload belongs to the current image selection alone."""
    adapter = _adapter(_config(tmp_path), ProviderExtensionServices(reload_image=_Reloader()))

    assert ExtensionAction.RELOAD in _actions(adapter, _OPENAI_IMAGE)
    assert "refresh:image-reload" in _image_capabilities(adapter, "openai")
    assert ExtensionAction.RELOAD not in _actions(adapter, _OPENROUTER_IMAGE)
    assert "refresh:image-reload" not in _image_capabilities(adapter, "openrouter")
    assert ExtensionAction.RELOAD not in _actions(adapter, _OPENAI)
    assert ExtensionAction.RELOAD not in _actions(adapter, _OPENAI_LLM)


def _image_capabilities(adapter: ProviderRegistryExtensionAdapter, name: str) -> tuple[str, ...]:
    package = _package(adapter, name)
    component = next(
        c for c in package.components if c.kind is ExtensionComponentKind.IMAGE_PROVIDER
    )
    return component.capabilities


def test_disabled_image_generation_declares_no_reload_anywhere(tmp_path: Path) -> None:
    """An explicitly disabled image tool has no selected provider to reload."""
    adapter = _adapter(
        _config(tmp_path, enabled=False), ProviderExtensionServices(reload_image=_Reloader())
    )

    for package in adapter.snapshot().packages:
        for component in package.components:
            assert ExtensionAction.RELOAD not in component.actions


def test_reload_is_not_declared_without_the_runtime_control_owner(tmp_path: Path) -> None:
    """A composition that owns no image runtime control never offers reload."""
    adapter = _adapter(_config(tmp_path))

    assert ExtensionAction.RELOAD not in _actions(adapter, _OPENAI_IMAGE)


async def test_reload_delegates_exactly_once_to_the_existing_owner(tmp_path: Path) -> None:
    """One accepted reload produces exactly one delegated call."""
    reloader = _Reloader()
    config = _config(tmp_path)
    adapter = _adapter(config, ProviderExtensionServices(reload_image=reloader))
    before = config.model_dump()

    result = await adapter.execute(
        ExtensionActionRequest(
            context=_ADMIN, target_id=_OPENAI_IMAGE, action=ExtensionAction.RELOAD
        )
    )

    assert reloader.calls == 1
    assert result.ok is True
    assert result.lifecycle is ExtensionLifecycle.ENABLED
    assert result.target_id == _OPENAI_IMAGE
    assert config.model_dump() == before


@pytest.mark.parametrize(
    "reloader",
    [
        _Reloader({"ok": False, "requires_restart": True, "message": "hot reload failed"}),
        _Reloader({"ok": True, "requires_restart": True}),
        _Reloader(error=True),
        _Reloader({"unexpected": "shape"}),
    ],
)
async def test_reload_failure_keeps_configuration_and_reports_restart_required(
    reloader: _Reloader,
    tmp_path: Path,
) -> None:
    """A failed reload invents no rollback and truthfully reports restart-required."""
    config = _config(tmp_path)
    adapter = _adapter(config, ProviderExtensionServices(reload_image=reloader))
    before = config.model_dump()

    result = await adapter.execute(
        ExtensionActionRequest(
            context=_ADMIN, target_id=_OPENAI_IMAGE, action=ExtensionAction.RELOAD
        )
    )

    assert reloader.calls == 1
    assert result.ok is False
    assert result.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
    assert config.model_dump() == before
    assert str(tmp_path) not in result.message


async def test_reload_on_an_unselected_image_component_is_refused(tmp_path: Path) -> None:
    """The adapter refuses a reload it did not declare, without delegating."""
    reloader = _Reloader()
    adapter = _adapter(_config(tmp_path), ProviderExtensionServices(reload_image=reloader))

    result = await adapter.execute(
        ExtensionActionRequest(
            context=_ADMIN, target_id=_OPENROUTER_IMAGE, action=ExtensionAction.RELOAD
        )
    )

    assert reloader.calls == 0
    assert result.ok is False


async def test_non_admin_actor_is_rejected_before_any_side_effect(tmp_path: Path) -> None:
    """A non-administrator never reaches the image runtime-control owner."""
    reloader = _Reloader()
    adapter = _adapter(_config(tmp_path), ProviderExtensionServices(reload_image=reloader))

    result = await adapter.execute(
        ExtensionActionRequest(
            context=_USER, target_id=_OPENAI_IMAGE, action=ExtensionAction.RELOAD
        )
    )

    assert reloader.calls == 0
    assert result.ok is False
    assert result.message == "System administrator access is required."


async def test_registry_rejects_a_non_admin_before_the_adapter_callback(tmp_path: Path) -> None:
    """The registry gate fires before the provider adapter is consulted at all."""
    reloader = _Reloader()
    registry = ExtensionRegistry()
    registry.register(_adapter(_config(tmp_path), ProviderExtensionServices(reload_image=reloader)))

    with pytest.raises(ExtensionRegistryError) as excinfo:
        await registry.execute(
            ExtensionActionRequest(
                context=_USER, target_id=_OPENAI_IMAGE, action=ExtensionAction.RELOAD
            )
        )

    assert excinfo.value.status == 403
    assert reloader.calls == 0


async def test_registry_refuses_an_undeclared_provider_action(tmp_path: Path) -> None:
    """Enable, disable, and install are never dispatched to a provider family."""
    reloader = _Reloader()
    registry = ExtensionRegistry()
    registry.register(_adapter(_config(tmp_path), ProviderExtensionServices(reload_image=reloader)))

    for action in (
        ExtensionAction.ENABLE,
        ExtensionAction.DISABLE,
        ExtensionAction.INSTALL,
        ExtensionAction.UNINSTALL,
        ExtensionAction.RECONNECT,
    ):
        with pytest.raises(ExtensionRegistryError) as excinfo:
            await registry.execute(
                ExtensionActionRequest(context=_ADMIN, target_id=_OPENAI, action=action)
            )
        assert excinfo.value.code == "action_not_supported"
    assert reloader.calls == 0


async def test_configure_reports_the_settings_owner_without_writing_configuration(
    tmp_path: Path,
) -> None:
    """Configure is navigational: credentials and OAuth stay provider-specific Settings work."""
    config = _config(tmp_path)
    adapter = _adapter(config)
    before = config.model_dump()

    result = await adapter.execute(
        ExtensionActionRequest(
            context=_ADMIN, target_id=_OPENAI_LLM, action=ExtensionAction.CONFIGURE
        )
    )

    assert result.ok is True
    assert "next turn" in result.message
    assert config.model_dump() == before


async def test_stale_revision_is_refused_before_delegating(tmp_path: Path) -> None:
    """A caller holding an old revision cannot trigger the delegated reload."""
    reloader = _Reloader()
    adapter = _adapter(_config(tmp_path), ProviderExtensionServices(reload_image=reloader))

    result = await adapter.execute(
        ExtensionActionRequest(
            context=_ADMIN,
            target_id=_OPENAI_IMAGE,
            action=ExtensionAction.RELOAD,
            expected_revision="stale",
        )
    )

    assert reloader.calls == 0
    assert result.ok is False
    assert result.message == "Provider action revision is stale."


async def test_inspect_returns_current_lifecycle_for_a_family(tmp_path: Path) -> None:
    """Inspect reports the current state without touching any owner."""
    adapter = _adapter(_config(tmp_path))

    result = await adapter.execute(
        ExtensionActionRequest(context=_ADMIN, target_id=_OPENAI, action=ExtensionAction.INSPECT)
    )

    assert result.ok is True
    assert result.lifecycle is ExtensionLifecycle.ENABLED
    assert result.package_id == _OPENAI


async def test_unknown_target_is_reported_without_a_package_claim(tmp_path: Path) -> None:
    """An action for a family this adapter does not own fails safely."""
    adapter = _adapter(_config(tmp_path))
    missing = extension_package_id(ExtensionSource.PROVIDER_REGISTRY, "opencode_zen")

    result = await adapter.execute(
        ExtensionActionRequest(context=_ADMIN, target_id=missing, action=ExtensionAction.INSPECT)
    )

    assert result.ok is False
    assert result.package_id == extension_package_id(
        ExtensionSource.PROVIDER_REGISTRY, "provider"
    )
