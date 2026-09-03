"""Read-only projection of standalone optional dependency groups."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar, cast

from nanobot.channels.registry import discover_plugins
from nanobot.extensions.adapters.common import canonical_extension_name, safe_extension_label
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionDiagnostic,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)
from nanobot.optional_features import (
    InstallResult,
    extra_installed,
    hidden_optional_feature_names,
    install_extra,
    optional_dependency_groups,
    optional_feature_requires_restart,
)

if TYPE_CHECKING:
    from nanobot.channels.plugin import ChannelPlugin

_MAX_PACKAGES = 1_024
_MAX_DIAGNOSTICS = 256
_MAX_REQUIREMENTS = 256
_MAX_REQUIREMENT_LENGTH = 2_000
_FALLBACK_PACKAGE_ID = extension_package_id(
    ExtensionSource.OPTIONAL_FEATURE, "optional-feature"
)


@dataclass(frozen=True, slots=True)
class OptionalFeatureExtensionServices:
    """Existing installer owner used only by executable registry actions."""
    install: Callable[..., InstallResult] = install_extra
    requires_restart: Callable[[str], bool] = optional_feature_requires_restart



_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


class OptionalFeatureExtensionAdapter:
    """Expose optional extras that are not owned by a channel manifest."""

    name = "optional-features"

    def __init__(
        self,
        groups_loader: Callable[[], dict[str, list[str] | None]] = optional_dependency_groups,
        channel_loader: Callable[[], Mapping[str, ChannelPlugin]] = discover_plugins,
        installed: Callable[[str, list[str] | None], bool] = extra_installed,
        *,
        services: OptionalFeatureExtensionServices | None = None,
    ) -> None:
        self._groups_loader = groups_loader
        self._channel_loader = channel_loader
        self._installed = installed
        self._services = services
        self._targets: dict[str, tuple[str, tuple[str, ...] | None]] = {}
        self._action_lock = asyncio.Lock()

    def snapshot(self) -> ExtensionAdapterSnapshot:
        """Return a bounded, dependency-free projection of visible standalone extras."""
        diagnostics: list[ExtensionDiagnostic] = []
        targets: dict[str, tuple[str, tuple[str, ...] | None]] = {}
        try:
            channel_names = self._channel_names()
            hidden_names = hidden_optional_feature_names()
        except Exception:
            self._add_diagnostic(diagnostics, "channel_inventory_unavailable")
            self._targets = {}
            return ExtensionAdapterSnapshot(adapter_name=self.name, diagnostics=tuple(diagnostics))

        try:
            groups = cast(object, self._groups_loader())
            if not isinstance(groups, Mapping):
                raise ValueError("optional dependency groups must be a mapping")
            group_items, truncated = self._bounded_items(
                cast(Mapping[object, object], groups),
                _MAX_PACKAGES,
            )
        except Exception:
            self._add_diagnostic(diagnostics, "optional_feature_inventory_unavailable")
            self._targets = {}
            return ExtensionAdapterSnapshot(adapter_name=self.name, diagnostics=tuple(diagnostics))

        packages: list[ExtensionPackageDescriptor] = []
        seen_package_ids: set[str] = set()
        for item in sorted(group_items, key=self._item_sort_key):
            try:
                raw_name, raw_requirements = self._group_item(item)
                if raw_name in channel_names or raw_name in hidden_names:
                    continue
                package = self._package(raw_name, raw_requirements)
                if package.id in seen_package_ids:
                    raise ValueError("optional feature identity collision")
                seen_package_ids.add(package.id)
                packages.append(package)
                requirements = self._requirements(raw_requirements)
                targets[package.id] = (raw_name, requirements)
                targets[package.components[0].id] = (raw_name, requirements)
            except Exception:
                self._add_diagnostic(diagnostics, "optional_feature_projection_failed")

        if truncated:
            self._add_diagnostic(diagnostics, "optional_feature_inventory_limited")
        self._targets = targets
        return ExtensionAdapterSnapshot(
            adapter_name=self.name,
            packages=tuple(packages),
            diagnostics=tuple(diagnostics),
        )

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        """Inspect or install one standalone optional feature under an adapter-local lock."""
        async with self._action_lock:
            if request.context.is_system_admin is not True:
                return self._failure(
                    request, _FALLBACK_PACKAGE_ID, "System administrator access is required."
                )
            snapshot = self.snapshot()
            package, descriptor = self._find_target(snapshot, request.target_id)
            package_id = package.id if package is not None else _FALLBACK_PACKAGE_ID
            if package is None or descriptor is None:
                return self._failure(request, package_id, "Optional feature action target is unavailable.")
            if request.action not in descriptor.actions:
                return self._failure(request, package.id, "Optional feature action is not supported.")

            revision = package.revision if descriptor is package else descriptor.revision
            if request.action is ExtensionAction.INSPECT:
                if request.expected_revision is not None and request.expected_revision != revision:
                    return self._failure(request, package.id, "Optional feature action revision is stale.")
                return ExtensionActionResult(
                    ok=True,
                    action=request.action,
                    package_id=package.id,
                    target_id=request.target_id,
                    lifecycle=descriptor.lifecycle,
                    message="Optional feature status is current.",
                )
            if request.expected_revision is None:
                return self._failure(
                    request, package.id, "Optional feature action requires a current revision."
                )
            if request.expected_revision != revision:
                return self._failure(request, package.id, "Optional feature action revision is stale.")
            if request.action is not ExtensionAction.INSTALL or descriptor is not package:
                return self._failure(request, package.id, "Optional feature action is not supported.")
            if self._services is None:
                return self._failure(request, package.id, "Optional feature action is not supported.")
            if not request.risk_acknowledged:
                return self._failure(
                    request, package.id, "Optional feature installation requires acknowledgement."
                )
            if not request.context.package_install_allowed:
                return self._failure(
                    request, package.id, "Optional feature installation is disabled by policy."
                )
            target = self._targets.get(request.target_id)
            if target is None:
                return self._failure(request, package.id, "Optional feature action target is unavailable.")
            raw_name, requirements = target
            install_requirements = list(requirements) if requirements is not None else None
            try:
                result = cast(
                    object,
                    await asyncio.to_thread(self._services.install, raw_name, install_requirements),
                )
            except Exception:
                return self._failure(
                    request,
                    package.id,
                    "Optional feature installation could not be completed.",
                    ExtensionLifecycle.UNAVAILABLE,
                )
            if not isinstance(result, InstallResult) or not result.ok:
                return self._failure(
                    request,
                    package.id,
                    "Optional feature installation could not be completed.",
                    ExtensionLifecycle.UNAVAILABLE,
                )
            try:
                ready = self._installed(raw_name, install_requirements)
            except Exception:
                ready = False
            if not ready:
                return self._failure(
                    request,
                    package.id,
                    "Optional feature installation could not be completed.",
                    ExtensionLifecycle.UNAVAILABLE,
                )
            try:
                requires_restart = self._services.requires_restart(raw_name)
            except Exception:
                requires_restart = True
            return ExtensionActionResult(
                ok=True,
                action=request.action,
                package_id=package.id,
                target_id=request.target_id,
                lifecycle=(
                    ExtensionLifecycle.RESTART_REQUIRED
                    if requires_restart
                    else ExtensionLifecycle.ENABLED
                ),
                message="Optional feature dependencies are ready.",
            )

    def _channel_names(self) -> frozenset[str]:
        channels = cast(object, self._channel_loader())
        if not isinstance(channels, Mapping):
            raise ValueError("channel inventory must be a mapping")
        items, truncated = self._bounded_items(
            cast(Mapping[object, object], channels),
            _MAX_PACKAGES,
        )
        if truncated:
            raise ValueError("channel inventory exceeds safe limit")

        names: set[str] = set()
        for item in items:
            key, plugin = item
            if isinstance(key, str):
                names.add(key)
            plugin_name: object = getattr(plugin, "name", None)
            if isinstance(plugin_name, str):
                names.add(plugin_name)
        return frozenset(names)

    def _package(
        self,
        raw_name: str,
        raw_requirements: object,
    ) -> ExtensionPackageDescriptor:
        requirements = self._requirements(raw_requirements)
        name = canonical_extension_name(raw_name, fallback="optional-feature")
        package_id = extension_package_id(ExtensionSource.OPTIONAL_FEATURE, name)
        is_installed = self._installed(raw_name, list(requirements) if requirements is not None else None)
        lifecycle = ExtensionLifecycle.ENABLED if is_installed else ExtensionLifecycle.UNAVAILABLE
        revision = self._revision(name, requirements, is_installed)
        display_name = safe_extension_label(raw_name, fallback=name)
        component = ExtensionComponentDescriptor(
            id=extension_component_id(
                package_id,
                ExtensionComponentKind.OPTIONAL_FEATURE,
                name,
            ),
            package_id=package_id,
            kind=ExtensionComponentKind.OPTIONAL_FEATURE,
            name=name,
            display_name=display_name,
            execution=ExtensionExecution.IN_PROCESS,
            lifecycle=lifecycle,
            revision=revision,
            actions=frozenset({ExtensionAction.INSPECT}),
        )
        actions = {ExtensionAction.INSPECT}
        if self._services is not None and lifecycle is ExtensionLifecycle.UNAVAILABLE:
            actions.add(ExtensionAction.INSTALL)
        return ExtensionPackageDescriptor(
            id=package_id,
            name=name,
            display_name=display_name,
            source=ExtensionSource.OPTIONAL_FEATURE,
            trust=ExtensionTrust.FIRST_PARTY,
            execution=ExtensionExecution.IN_PROCESS,
            lifecycle=lifecycle,
            revision=revision,
            permissions_enforced=False,
            actions=frozenset(actions),
            components=(component,),
        )

    @staticmethod
    def _requirements(value: object) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("optional feature requirements are malformed")
        raw_requirements = cast(list[object], value)
        if len(raw_requirements) > _MAX_REQUIREMENTS:
            raise ValueError("optional feature requirements are malformed")
        requirements: list[str] = []
        for requirement in raw_requirements:
            if not isinstance(requirement, str) or len(requirement) > _MAX_REQUIREMENT_LENGTH:
                raise ValueError("optional feature requirement is malformed")
            requirements.append(requirement)
        return tuple(requirements)

    @staticmethod
    def _revision(name: str, requirements: tuple[str, ...] | None, installed: bool) -> str:
        signature = {
            "installed": installed,
            "name": name,
            "requirements": sorted(requirements) if requirements is not None else None,
        }
        encoded = json.dumps(signature, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _bounded_items(
        mapping: Mapping[_Key, _Value],
        limit: int,
    ) -> tuple[tuple[tuple[_Key, _Value], ...], bool]:
        iterator = iter(mapping.items())
        items: list[tuple[_Key, _Value]] = []
        for _ in range(limit + 1):
            try:
                items.append(next(iterator))
            except StopIteration:
                return tuple(items), False
        return tuple(items[:limit]), True

    @staticmethod
    def _group_item(item: tuple[object, object]) -> tuple[str, object]:
        raw_name, requirements = item
        if not isinstance(raw_name, str):
            raise ValueError("optional feature name is malformed")
        return raw_name, requirements

    @staticmethod
    def _item_sort_key(item: tuple[object, object]) -> tuple[int, str]:
        raw_name, _ = item
        return (0, raw_name) if isinstance(raw_name, str) else (1, "")

    @staticmethod
    def _find_target(
        snapshot: ExtensionAdapterSnapshot, target_id: str
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


    def _add_diagnostic(self, diagnostics: list[ExtensionDiagnostic], code: str) -> None:
        if len(diagnostics) < _MAX_DIAGNOSTICS:
            diagnostics.append(
                ExtensionDiagnostic(
                    owner_id=self.name,
                    code=code,
                    message="An optional feature could not be projected.",
                )
            )


__all__ = ["OptionalFeatureExtensionAdapter", "OptionalFeatureExtensionServices"]
