"""Read-only projection of the LLM, image, and transcription provider registries.

Every image provider name and every transcription provider name is also an LLM
provider name, so one package per registry would emit colliding
``ext:provider_registry:<name>`` IDs and fail the whole adapter. One package per
provider *family* owning up to three capability components is the only shape the
canonical ID space allows.

Discovery reads the three static registries and the saved configuration only. It
constructs no provider client, opens no connection, and never calls
``TranscriptionProviderSpec.load_adapter`` or instantiates an image client.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

from nanobot.audio.transcription_registry import transcription_provider_names
from nanobot.extensions.adapters.common import safe_extension_label
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionConfigurationTarget,
    ExtensionDiagnostic,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)
from nanobot.providers.image_generation import image_gen_provider_names
from nanobot.providers.registry import PROVIDERS, ProviderSpec

if TYPE_CHECKING:
    from nanobot.config.schema import Config

_MAX_DIAGNOSTICS = 256
_FALLBACK_PACKAGE_ID = extension_package_id(ExtensionSource.PROVIDER_REGISTRY, "provider")
_MODELS_SECTION = "models"
_IMAGE_CONFIGURATION = ExtensionConfigurationTarget(section="image")
_VOICE_CONFIGURATION = ExtensionConfigurationTarget(section="voice")

_LLM_DESCRIPTION = (
    "Model changes take effect on the next turn through the existing model runtime "
    "resolution; there is no hot reload."
)
_IMAGE_DESCRIPTION = (
    "Image generation reload is owned by the image runtime control and applies only to "
    "the currently selected and enabled provider."
)
_TRANSCRIPTION_DESCRIPTION = (
    "The transcription adapter is resolved and constructed per request, so no reload exists."
)

# Reported instead of a configured/unconfigured claim when the family's credential
# lives outside the saved configuration (OAuth token storage) and this composition
# supplied no reader for it.
_UNKNOWN_CONFIGURATION = "configuration-unknown"

OAuthStatusReader = Callable[[ProviderSpec], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class ProviderExtensionServices:
    """The existing image runtime-control owner, used only by the one reload action."""

    reload_image: Callable[[], Awaitable[Mapping[str, object]]]


def _requires_api_key(spec: ProviderSpec) -> bool:
    """Mirror ``nanobot.webui.settings_models.provider_requires_api_key``."""
    if spec.name == "azure_openai":
        return False
    if spec.is_oauth:
        return False
    if spec.is_local or spec.is_direct:
        return False
    return True


def _requires_api_base(spec: ProviderSpec) -> bool:
    """Mirror ``nanobot.webui.settings_models.provider_requires_api_base``."""
    if spec.name == "azure_openai":
        return True
    return bool(spec.backend == "openai_compat" and spec.is_direct and not spec.default_api_base)


def _spec_configured(spec: ProviderSpec, provider_config: object) -> bool:
    """Mirror the Settings configured-check for every non-OAuth family.

    Duplicated rather than imported: the owning predicate lives in
    ``nanobot.webui.settings_models`` and this edge adapter must not depend on the
    WebUI layer. ``tests/extensions/test_provider_adapter.py`` asserts the two agree
    for every registry spec, so the two projections cannot disagree silently.
    """
    if _requires_api_base(spec):
        return bool(getattr(provider_config, "api_base", None))
    if _requires_api_key(spec):
        return bool(getattr(provider_config, "api_key", None))
    return bool(
        getattr(provider_config, "api_key", None)
        or getattr(provider_config, "api_base", None)
        or getattr(provider_config, "region", None)
        or getattr(provider_config, "profile", None)
    )


def _revision(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _Family:
    """One canonical provider family and the members that were grouped into it."""

    spec: ProviderSpec
    aliases: tuple[str, ...]


class ProviderRegistryExtensionAdapter:
    """Project provider families with only the capability components they implement."""

    name = "provider-registry"

    def __init__(
        self,
        config_loader: Callable[[], Config],
        *,
        oauth_status: OAuthStatusReader | None = None,
        services: ProviderExtensionServices | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._oauth_status = oauth_status
        self._services = services
        self._action_lock = asyncio.Lock()

    def snapshot(self) -> ExtensionAdapterSnapshot:
        """Return static registry metadata plus safe configured state; no client is built."""
        snapshot, _ = self._snapshot()
        return snapshot

    def _snapshot(self) -> tuple[ExtensionAdapterSnapshot, str | None]:
        """Build the inventory and the one image component that may declare reload."""
        diagnostics: list[ExtensionDiagnostic] = []
        try:
            config = self._config_loader()
        except Exception:
            self._add_diagnostic(diagnostics, "provider_config_unavailable")
            return (
                ExtensionAdapterSnapshot(adapter_name=self.name, diagnostics=tuple(diagnostics)),
                None,
            )

        image_names = frozenset(image_gen_provider_names())
        transcription_names = frozenset(transcription_provider_names())
        image_selection = self._image_selection(config)
        packages: list[ExtensionPackageDescriptor] = []
        seen_ids: set[str] = set()
        reloadable_target: str | None = None
        for family in _families():
            try:
                package, reloadable = self._package(
                    family,
                    config,
                    image_names=image_names,
                    transcription_names=transcription_names,
                    image_selection=image_selection,
                )
                if package.id in seen_ids:
                    raise ValueError("provider family identity collision")
                seen_ids.add(package.id)
                packages.append(package)
                if reloadable is not None:
                    reloadable_target = reloadable
            except Exception:
                self._add_diagnostic(diagnostics, "provider_projection_failed")

        return (
            ExtensionAdapterSnapshot(
                adapter_name=self.name,
                packages=tuple(packages),
                diagnostics=tuple(diagnostics),
            ),
            reloadable_target,
        )

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        """Point at the owning Settings section, or delegate one image reload."""
        async with self._action_lock:
            if request.context.is_system_admin is not True:
                return self._failure(
                    request, _FALLBACK_PACKAGE_ID, "System administrator access is required."
                )
            snapshot, reloadable_target = self._snapshot()
            package, descriptor = _find_target(snapshot, request.target_id)
            if package is None or descriptor is None:
                return self._failure(
                    request, _FALLBACK_PACKAGE_ID, "Provider action target is unavailable."
                )
            if request.action not in descriptor.actions:
                return self._failure(request, package.id, "Provider action is not supported.")

            revision = package.revision if descriptor is package else descriptor.revision
            if request.expected_revision is not None and request.expected_revision != revision:
                return self._failure(request, package.id, "Provider action revision is stale.")

            if request.action is ExtensionAction.INSPECT:
                return ExtensionActionResult(
                    ok=True,
                    action=request.action,
                    package_id=package.id,
                    target_id=request.target_id,
                    lifecycle=descriptor.lifecycle,
                    message="Provider status is current.",
                )
            if request.action is ExtensionAction.CONFIGURE:
                return ExtensionActionResult(
                    ok=True,
                    action=request.action,
                    package_id=package.id,
                    target_id=request.target_id,
                    lifecycle=descriptor.lifecycle,
                    message=(
                        "Provider credentials stay in Settings; model changes apply on the "
                        "next turn."
                    ),
                )
            if request.action is not ExtensionAction.RELOAD:
                return self._failure(request, package.id, "Provider action is not supported.")
            return await self._reload_image(request, package, reloadable_target)

    async def _reload_image(
        self,
        request: ExtensionActionRequest,
        package: ExtensionPackageDescriptor,
        reloadable_target: str | None,
    ) -> ExtensionActionResult:
        """Delegate once to the existing image runtime-control owner and report the truth."""
        services = self._services
        if services is None or request.target_id != reloadable_target:
            return self._failure(request, package.id, "Provider action is not supported.")
        try:
            result = cast(object, await services.reload_image())
        except Exception:
            logger.exception("Image generation reload failed for an extension action")
            return self._failure(
                request,
                package.id,
                "Image generation settings were kept and require a restart.",
                ExtensionLifecycle.RESTART_REQUIRED,
            )
        applied = (
            isinstance(result, Mapping)
            and bool(cast(Mapping[str, object], result).get("ok"))
            and not cast(Mapping[str, object], result).get("requires_restart")
        )
        if not applied:
            return self._failure(
                request,
                package.id,
                "Image generation settings were kept and require a restart.",
                ExtensionLifecycle.RESTART_REQUIRED,
            )
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=package.id,
            target_id=request.target_id,
            lifecycle=ExtensionLifecycle.ENABLED,
            message="Image generation settings were applied without restarting nanobot.",
        )

    def _package(
        self,
        family: _Family,
        config: Config,
        *,
        image_names: frozenset[str],
        transcription_names: frozenset[str],
        image_selection: str | None,
    ) -> tuple[ExtensionPackageDescriptor, str | None]:
        spec = family.spec
        name = spec.name
        package_id = extension_package_id(ExtensionSource.PROVIDER_REGISTRY, name)
        lifecycle, configured = self._configuration(spec, config)
        display_name = safe_extension_label(spec.label, fallback=name)
        flags = _structural_flags(spec)
        if configured is None and lifecycle is not ExtensionLifecycle.UNAVAILABLE:
            flags = (*flags, _UNKNOWN_CONFIGURATION)
        kinds: list[str] = []
        components: list[ExtensionComponentDescriptor] = []
        reloadable_target: str | None = None

        if not spec.is_transcription_only:
            kinds.append(ExtensionComponentKind.LLM_PROVIDER.value)
            components.append(
                ExtensionComponentDescriptor(
                    id=extension_component_id(
                        package_id, ExtensionComponentKind.LLM_PROVIDER, name
                    ),
                    package_id=package_id,
                    kind=ExtensionComponentKind.LLM_PROVIDER,
                    name=name,
                    display_name=display_name,
                    description=_LLM_DESCRIPTION,
                    capabilities=(*flags, "refresh:next-turn", *_alias_capabilities(family)),
                    execution=ExtensionExecution.REMOTE,
                    lifecycle=lifecycle,
                    revision=_revision(
                        {"configured": configured, "flags": flags, "kind": "llm", "name": name}
                    ),
                    actions=frozenset({ExtensionAction.INSPECT, ExtensionAction.CONFIGURE}),
                    configuration=ExtensionConfigurationTarget(
                        section=_MODELS_SECTION, item=name
                    ),
                )
            )

        if name in image_names:
            kinds.append(ExtensionComponentKind.IMAGE_PROVIDER.value)
            component_id = extension_component_id(
                package_id, ExtensionComponentKind.IMAGE_PROVIDER, name
            )
            selected = image_selection == name
            reloadable = selected and self._services is not None
            if reloadable:
                reloadable_target = component_id
            components.append(
                ExtensionComponentDescriptor(
                    id=component_id,
                    package_id=package_id,
                    kind=ExtensionComponentKind.IMAGE_PROVIDER,
                    name=name,
                    display_name=display_name,
                    description=_IMAGE_DESCRIPTION,
                    capabilities=(
                        (*flags, "selected", "refresh:image-reload")
                        if reloadable
                        else ((*flags, "selected") if selected else flags)
                    ),
                    execution=ExtensionExecution.REMOTE,
                    lifecycle=lifecycle,
                    revision=_revision(
                        {
                            "configured": configured,
                            "flags": flags,
                            "kind": "image",
                            "name": name,
                            "selected": selected,
                        }
                    ),
                    actions=frozenset(
                        {ExtensionAction.INSPECT, ExtensionAction.CONFIGURE, ExtensionAction.RELOAD}
                        if reloadable
                        else {ExtensionAction.INSPECT, ExtensionAction.CONFIGURE}
                    ),
                    configuration=_IMAGE_CONFIGURATION,
                )
            )

        if name in transcription_names:
            kinds.append(ExtensionComponentKind.TRANSCRIPTION_PROVIDER.value)
            components.append(
                ExtensionComponentDescriptor(
                    id=extension_component_id(
                        package_id, ExtensionComponentKind.TRANSCRIPTION_PROVIDER, name
                    ),
                    package_id=package_id,
                    kind=ExtensionComponentKind.TRANSCRIPTION_PROVIDER,
                    name=name,
                    display_name=display_name,
                    description=_TRANSCRIPTION_DESCRIPTION,
                    capabilities=(*flags, "refresh:per-request"),
                    execution=ExtensionExecution.REMOTE,
                    lifecycle=lifecycle,
                    revision=_revision(
                        {
                            "configured": configured,
                            "flags": flags,
                            "kind": "transcription",
                            "name": name,
                        }
                    ),
                    actions=frozenset({ExtensionAction.INSPECT, ExtensionAction.CONFIGURE}),
                    configuration=_VOICE_CONFIGURATION,
                )
            )

        package = ExtensionPackageDescriptor(
            id=package_id,
            name=name,
            display_name=display_name,
            source=ExtensionSource.PROVIDER_REGISTRY,
            trust=(
                ExtensionTrust.OPERATOR_TRUSTED if spec.is_local else ExtensionTrust.REMOTE_SERVICE
            ),
            execution=ExtensionExecution.REMOTE,
            lifecycle=lifecycle,
            description="Provider registry metadata for one provider family.",
            revision=_revision(
                {
                    "aliases": list(family.aliases),
                    "configured": configured,
                    "flags": flags,
                    "kinds": kinds,
                    "name": name,
                }
            ),
            permissions_enforced=False,
            actions=frozenset({ExtensionAction.INSPECT, ExtensionAction.CONFIGURE}),
            configuration=ExtensionConfigurationTarget(section=_MODELS_SECTION, item=name),
            components=tuple(components),
        )
        return package, reloadable_target

    def _configuration(
        self,
        spec: ProviderSpec,
        config: Config,
    ) -> tuple[ExtensionLifecycle, bool | None]:
        """Return the family lifecycle and the safe configured boolean fed to revisions."""
        provider_config = cast(object, getattr(config.providers, spec.name, None))
        if provider_config is None:
            return ExtensionLifecycle.UNAVAILABLE, None
        if spec.is_oauth:
            reader = self._oauth_status
            if reader is None:
                # Reading OAuth token storage would import an optional runtime SDK, so no
                # configured claim is made unless the composition supplied the reader.
                return ExtensionLifecycle.DISCOVERED, None
            try:
                configured = bool(reader(spec).get("configured"))
            except Exception:
                return ExtensionLifecycle.DISCOVERED, None
        else:
            configured = _spec_configured(spec, provider_config)
        return (
            ExtensionLifecycle.ENABLED if configured else ExtensionLifecycle.DISCOVERED,
            configured,
        )

    @staticmethod
    def _image_selection(config: Config) -> str | None:
        image = config.tools.image_generation
        return image.provider if image.enabled and image.provider else None

    def _add_diagnostic(self, diagnostics: list[ExtensionDiagnostic], code: str) -> None:
        if len(diagnostics) < _MAX_DIAGNOSTICS:
            diagnostics.append(
                ExtensionDiagnostic(
                    owner_id=self.name,
                    code=code,
                    message="A provider family could not be projected.",
                )
            )

    @staticmethod
    def _failure(
        request: ExtensionActionRequest,
        package_id: str,
        message: str,
        lifecycle: ExtensionLifecycle | None = None,
    ) -> ExtensionActionResult:
        return ExtensionActionResult(
            ok=False,
            action=request.action,
            package_id=package_id,
            target_id=request.target_id,
            lifecycle=lifecycle,
            message=message,
        )


def _families() -> tuple[_Family, ...]:
    """Group alias specs into their canonical family, matching the Settings rule."""
    aliases: dict[str, list[str]] = {}
    for spec in PROVIDERS:
        if spec.settings_alias_for:
            aliases.setdefault(spec.settings_alias_for, []).append(spec.name)
    return tuple(
        _Family(spec=spec, aliases=tuple(aliases.get(spec.name, ())))
        for spec in PROVIDERS
        if not spec.settings_alias_for
    )


def _structural_flags(spec: ProviderSpec) -> tuple[str, ...]:
    """Derive display facts from spec flags only, never from provider-name matching."""
    flags = [f"backend:{spec.backend}"]
    if spec.is_gateway:
        flags.append("gateway")
    if spec.is_local:
        flags.append("local-deployment")
    if spec.is_oauth:
        flags.append("oauth")
    if spec.is_direct:
        flags.append("direct-endpoint")
    if spec.is_transcription_only:
        flags.append("transcription-only")
    return tuple(flags)


def _alias_capabilities(family: _Family) -> tuple[str, ...]:
    return tuple(f"alias:{alias}" for alias in family.aliases)


def _find_target(
    snapshot: ExtensionAdapterSnapshot,
    target_id: str,
) -> tuple[
    ExtensionPackageDescriptor | None,
    ExtensionPackageDescriptor | ExtensionComponentDescriptor | None,
]:
    for package in snapshot.packages:
        if package.id == target_id:
            return package, package
        for component in package.components:
            if component.id == target_id:
                return package, component
    return None, None


__all__ = [
    "OAuthStatusReader",
    "ProviderExtensionServices",
    "ProviderRegistryExtensionAdapter",
]
