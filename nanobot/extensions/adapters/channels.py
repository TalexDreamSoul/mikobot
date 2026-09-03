"""Canonical channel manifest projection and exact-instance lifecycle actions."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING, cast

from loguru import logger
from pydantic import BaseModel

from nanobot.channels.contracts import (
    ChannelActivation,
    channel_configure_instance,
    channel_field_value,
    channel_instance_specs,
    channel_local_state_present,
    channel_set_config_enabled,
    channel_value_present,
)
from nanobot.channels.registry import discover_plugins
from nanobot.extensions.adapters.common import canonical_extension_name, safe_extension_label
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
from nanobot.optional_features import (
    ChannelDependencyPreparation,
    extra_installed,
    prepare_channel_dependencies,
)

_MAX_CAPABILITIES = 256
_MAX_PACKAGES = 1_024
_MAX_DIAGNOSTICS = 256
_FALLBACK_PACKAGE_ID = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, "channel")


class _ChannelActionStaleError(Exception):
    """Abort a locked mutation whose exact instance changed since the snapshot."""


def _encode(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


# Keyed per process, not per adapter: every registry built in one process agrees on a
# revision, while the key never leaves the process. An unkeyed digest would be an offline
# oracle, because instances written outside `channel_configure_instance` carry no
# persisted `_extensionRevision` salt and their remaining fields are public.
_FINGERPRINT_KEY = secrets.token_bytes(32)


if TYPE_CHECKING:
    from nanobot.channels.plugin import ChannelPlugin
    from nanobot.config.schema import Config


@dataclass(frozen=True, slots=True)
class ChannelExtensionServices:
    """Existing channel owners needed only by executable registry actions."""

    mutate_config: Callable[[Callable[[Config], object]], object]
    runtime_action: Callable[[str, str, str], Awaitable[Mapping[str, object]]]
    prepare_dependencies: Callable[..., ChannelDependencyPreparation] = prepare_channel_dependencies
    refresh_metadata: Callable[[str, str], None] | None = None


class ChannelExtensionAdapter:
    """Project channel manifests and delegate actions to their existing owners."""

    name = "channels"

    def __init__(
        self,
        config_loader: Callable[[], Config],
        *,
        runtime_status: Callable[[], Mapping[str, Mapping[str, object]]] | None = None,
        dependencies_installed: Callable[[str, list[str] | None], bool] | None = None,
        services: ChannelExtensionServices | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._runtime_status = runtime_status
        self._dependencies_installed = dependencies_installed or extra_installed
        self._services = services
        self._targets: dict[str, tuple[str, str | None]] = {}
        self._target_fingerprints: dict[str, str] = {}
        self._action_lock = asyncio.Lock()

    def snapshot(self) -> ExtensionAdapterSnapshot:
        """Return saved declarations without importing channel runtime or connector modules."""
        diagnostics: list[ExtensionDiagnostic] = []
        try:
            config = self._config_loader()
        except Exception:
            self._add_diagnostic(diagnostics, "channel_config_unavailable")
            self._targets = {}
            self._target_fingerprints = {}
            return ExtensionAdapterSnapshot(adapter_name=self.name, diagnostics=tuple(diagnostics))

        try:
            plugins = discover_plugins()
        except Exception:
            self._add_diagnostic(diagnostics, "channel_inventory_unavailable")
            self._targets = {}
            self._target_fingerprints = {}
            return ExtensionAdapterSnapshot(adapter_name=self.name, diagnostics=tuple(diagnostics))

        statuses = self._status_index(diagnostics)
        packages: list[ExtensionPackageDescriptor] = []
        targets: dict[str, tuple[str, str | None]] = {}
        fingerprints: dict[str, str] = {}
        package_ids: set[str] = set()
        for plugin in islice(iter(plugins.values()), _MAX_PACKAGES + 1):
            if len(packages) >= _MAX_PACKAGES:
                self._add_diagnostic(diagnostics, "channel_inventory_limited")
                break
            try:
                package, package_targets, package_fingerprints = self._package(config, plugin, statuses)
                if package.id in package_ids or any(
                    target_id in targets for target_id in package_targets
                ):
                    raise ValueError("channel projection has duplicate canonical IDs")
            except Exception:
                self._add_diagnostic(diagnostics, "channel_projection_failed")
                continue
            package_ids.add(package.id)
            packages.append(package)
            targets.update(package_targets)
            fingerprints.update(package_fingerprints)

        self._targets = targets
        self._target_fingerprints = fingerprints
        return ExtensionAdapterSnapshot(
            adapter_name=self.name,
            packages=tuple(packages),
            diagnostics=tuple(diagnostics),
        )

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        """Run one fresh-revision action in the documented single-owner order."""
        async with self._action_lock:
            if request.context.is_system_admin is not True:
                return self._failure(
                    request, _FALLBACK_PACKAGE_ID, "System administrator access is required."
                )
            if self._services is None and request.action is not ExtensionAction.INSPECT:
                return self._failure(
                    request, _FALLBACK_PACKAGE_ID, "Channel action is not supported."
                )
            snapshot = self.snapshot()
            package, descriptor = self._find_target(snapshot, request.target_id)
            package_id = package.id if package is not None else _FALLBACK_PACKAGE_ID
            if package is None or descriptor is None:
                return self._failure(request, package_id, "Channel action target is unavailable.")
            if request.action not in descriptor.actions:
                return self._failure(request, package_id, "Channel action is not supported.")

            revision = package.revision if descriptor is package else descriptor.revision
            if request.action is ExtensionAction.INSPECT:
                if request.expected_revision is not None and request.expected_revision != revision:
                    return self._failure(request, package.id, "Channel action revision is stale.")
                return ExtensionActionResult(
                    ok=True,
                    action=request.action,
                    package_id=package.id,
                    target_id=request.target_id,
                    lifecycle=descriptor.lifecycle,
                    message="Channel status is current.",
                )
            if request.expected_revision is None:
                return self._failure(request, package.id, "Channel action requires a current revision.")
            if request.expected_revision != revision:
                return self._failure(request, package.id, "Channel action revision is stale.")

            target = self._targets.get(request.target_id)
            if target is None:
                return self._failure(request, package.id, "Channel action target is unavailable.")
            channel_type, instance_id = target
            if request.action is ExtensionAction.INSTALL:
                return await self._install(request, package, channel_type, instance_id)
            if instance_id is None:
                return self._failure(request, package.id, "Channel action is not supported.")
            fingerprint = self._target_fingerprints.get(request.target_id)
            if fingerprint is None:
                return self._failure(request, package.id, "Channel action target is unavailable.")
            if request.action is ExtensionAction.CONFIGURE:
                return await self._configure(request, package, channel_type, instance_id, fingerprint)
            if request.action is ExtensionAction.ENABLE:
                return await self._enable(request, package, channel_type, instance_id, fingerprint)
            if request.action is ExtensionAction.DISABLE:
                return await self._disable(request, package, channel_type, instance_id, fingerprint)
            if request.action is ExtensionAction.RECONNECT:
                return await self._reconnect(request, package, channel_type, instance_id)
            return self._failure(request, package.id, "Channel action is not supported.")

    async def _install(
        self,
        request: ExtensionActionRequest,
        package: ExtensionPackageDescriptor,
        channel_type: str,
        instance_id: str | None,
    ) -> ExtensionActionResult:
        """Prepare only the selected package's manifest dependencies once."""
        services = self._services
        if instance_id is not None or services is None:
            return self._failure(request, package.id, "Channel action is not supported.")
        if not request.risk_acknowledged:
            return self._failure(request, package.id, "Channel dependency installation requires acknowledgement.")
        if not request.context.package_install_allowed:
            return self._failure(request, package.id, "Channel dependency installation is disabled by policy.")
        plugin = self._plugin(channel_type)
        if plugin is None:
            return self._failure(request, package.id, "Channel action target is unavailable.")
        try:
            preparation_value = cast(
                object,
                await asyncio.to_thread(
                    services.prepare_dependencies,
                    channel_type,
                    list(plugin.dependencies) or None,
                    allow_install=True,
                ),
            )
        except Exception:
            logger.exception("Channel dependency preparation failed")
            return self._failure(request, package.id, "Channel dependencies could not be prepared.")
        if not isinstance(preparation_value, ChannelDependencyPreparation) or not preparation_value.ready:
            return self._failure(request, package.id, "Channel dependencies could not be prepared.")
        return self._current_result(request, package.id, "Channel dependencies are ready.")

    async def _configure(
        self,
        request: ExtensionActionRequest,
        package: ExtensionPackageDescriptor,
        channel_type: str,
        instance_id: str,
        fingerprint: str,
    ) -> ExtensionActionResult:
        """Validate and persist one exact instance without runtime work."""
        services = self._services
        if services is None:
            return self._failure(request, package.id, "Channel action is not supported.")
        if not request.values:
            return self._failure(request, package.id, "Channel configuration values are required.")
        plugin = self._plugin(channel_type)
        if plugin is None:
            return self._failure(request, package.id, "Channel action target is unavailable.")
        try:
            await asyncio.to_thread(
                services.mutate_config,
                lambda config: self._configure_instance(
                    config, plugin, channel_type, instance_id, request.values, fingerprint
                ),
            )
        except _ChannelActionStaleError:
            return self._failure(request, package.id, "Channel action revision is stale.")
        except Exception:
            logger.exception("Channel configuration update failed")
            return self._failure(request, package.id, "Channel configuration could not be saved.")
        return self._current_result(request, package.id, "Channel configuration was saved.")

    async def _enable(
        self,
        request: ExtensionActionRequest,
        package: ExtensionPackageDescriptor,
        channel_type: str,
        instance_id: str,
        fingerprint: str,
    ) -> ExtensionActionResult:
        """Prepare, persist, reconcile, then refresh metadata after success only."""
        services = self._services
        if services is None:
            return self._failure(request, package.id, "Channel action is not supported.")
        plugin = self._plugin(channel_type)
        if plugin is None or not self._instance_is_configured(plugin, channel_type, instance_id):
            return self._failure(request, package.id, "Channel is not ready to enable.")
        dependencies_missing = ExtensionAction.INSTALL in package.actions
        if dependencies_missing:
            if not request.risk_acknowledged:
                return self._failure(request, package.id, "Channel dependency installation requires acknowledgement.")
            if not request.context.package_install_allowed:
                return self._failure(request, package.id, "Channel dependency installation is disabled by policy.")
            try:
                preparation_value = cast(
                    object,
                    await asyncio.to_thread(
                        services.prepare_dependencies,
                        channel_type,
                        list(plugin.dependencies) or None,
                        allow_install=True,
                    ),
                )
            except Exception:
                logger.exception("Channel dependency preparation failed")
                return self._failure(request, package.id, "Channel dependencies could not be prepared.")
            if not isinstance(preparation_value, ChannelDependencyPreparation) or not preparation_value.ready:
                return self._failure(request, package.id, "Channel dependencies could not be prepared.")
        try:
            await asyncio.to_thread(
                services.mutate_config,
                lambda config: self._set_instance_enabled(
                    config,
                    plugin,
                    channel_type,
                    instance_id,
                    True,
                    fingerprint=fingerprint,
                    pairing_completed=request.context.channel_pairing_completed,
                ),
            )
        except _ChannelActionStaleError:
            return self._failure(request, package.id, "Channel action revision is stale.")
        except Exception:
            logger.exception("Channel enablement configuration update failed")
            return self._failure(request, package.id, "Channel could not be enabled.")
        result = await self._runtime_result(request, package, "enable", channel_type, instance_id)
        if result.ok:
            refresh_metadata = services.refresh_metadata
            if refresh_metadata is not None:
                try:
                    await asyncio.to_thread(refresh_metadata, channel_type, instance_id)
                except Exception:
                    logger.warning("Channel metadata refresh failed after enablement")
        return result

    async def _disable(
        self,
        request: ExtensionActionRequest,
        package: ExtensionPackageDescriptor,
        channel_type: str,
        instance_id: str,
        fingerprint: str,
    ) -> ExtensionActionResult:
        """Persist one exact disablement, then request one exact runtime stop."""
        services = self._services
        if services is None:
            return self._failure(request, package.id, "Channel action is not supported.")
        plugin = self._plugin(channel_type)
        if plugin is None:
            return self._failure(request, package.id, "Channel action target is unavailable.")
        try:
            await asyncio.to_thread(
                services.mutate_config,
                lambda config: self._set_instance_enabled(
                    config, plugin, channel_type, instance_id, False, fingerprint=fingerprint
                ),
            )
        except _ChannelActionStaleError:
            return self._failure(request, package.id, "Channel action revision is stale.")
        except Exception:
            logger.exception("Channel disablement configuration update failed")
            return self._failure(request, package.id, "Channel could not be disabled.")
        return await self._runtime_result(request, package, "disable", channel_type, instance_id)

    async def _reconnect(
        self,
        request: ExtensionActionRequest,
        package: ExtensionPackageDescriptor,
        channel_type: str,
        instance_id: str,
    ) -> ExtensionActionResult:
        """Delegate a selected connector-capable instance to the runtime owner once."""
        return await self._runtime_result(request, package, "reconnect", channel_type, instance_id)

    async def _runtime_result(
        self,
        request: ExtensionActionRequest,
        package: ExtensionPackageDescriptor,
        action: str,
        channel_type: str,
        instance_id: str,
    ) -> ExtensionActionResult:
        services = self._services
        if services is None:
            return self._failure(request, package.id, "Channel action is not supported.", ExtensionLifecycle.FAILED)
        try:
            result_value = cast(
                object, await services.runtime_action(action, channel_type, instance_id)
            )
        except Exception:
            logger.exception("Channel runtime action failed")
            return self._failure(request, package.id, "Channel action could not be completed.", ExtensionLifecycle.FAILED)
        if not isinstance(result_value, Mapping):
            return self._failure(request, package.id, "Channel action could not be completed.", ExtensionLifecycle.FAILED)
        result = cast(Mapping[str, object], result_value)
        if result.get("ok") is not True:
            lifecycle = (
                ExtensionLifecycle.RESTART_REQUIRED
                if result.get("requires_restart") is True
                else ExtensionLifecycle.FAILED
            )
            return self._failure(request, package.id, "Channel action could not be completed.", lifecycle)
        lifecycle = (
            ExtensionLifecycle.RESTART_REQUIRED
            if result.get("requires_restart") is True
            else ExtensionLifecycle.DISABLED if action == "disable" else ExtensionLifecycle.ENABLED
        )
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=package.id,
            target_id=request.target_id,
            lifecycle=lifecycle,
            message="Channel action completed.",
        )

    def _configure_instance(
        self,
        config: Config,
        plugin: ChannelPlugin,
        channel_type: str,
        instance_id: str,
        values: Mapping[str, object],
        fingerprint: str,
    ) -> object:
        section = getattr(config.channels, channel_type, None)
        self._require_current_fingerprint(plugin, section, instance_id, fingerprint)
        updated, _ = channel_configure_instance(
            plugin, section, values, instance_id=instance_id
        )
        setattr(config.channels, channel_type, updated)
        return updated

    def _set_instance_enabled(
        self,
        config: Config,
        plugin: ChannelPlugin,
        channel_type: str,
        instance_id: str,
        enabled: bool,
        *,
        fingerprint: str,
        pairing_completed: bool = False,
    ) -> object:
        section = getattr(config.channels, channel_type, None)
        self._require_current_fingerprint(plugin, section, instance_id, fingerprint)
        updated = channel_set_config_enabled(
            plugin,
            section,
            enabled,
            instance_id=instance_id,
            pairing_completed=pairing_completed,
        )
        setattr(config.channels, channel_type, updated)
        return updated

    @staticmethod
    def _configured(plugin: ChannelPlugin, instance_config: object, local_state: bool) -> bool:
        if plugin.setup is not None:
            return not plugin.setup.required or bool(plugin.setup.is_configured(instance_config))
        if plugin.management.local_state_present is not None:
            return local_state
        return True

    def _instance_is_configured(
        self, plugin: ChannelPlugin, channel_type: str, instance_id: str
    ) -> bool:
        try:
            config = self._config_loader()
            section = getattr(config.channels, channel_type, None)
            instance = next(
                (
                    candidate
                    for candidate in channel_instance_specs(plugin, section, enabled_only=False)
                    if candidate.instance_id == instance_id
                ),
                None,
            )
            if instance is None:
                return False
            return self._configured(
                plugin,
                instance.config,
                channel_local_state_present(plugin, section),
            )
        except Exception:
            return False

    @staticmethod
    def _plugin(channel_type: str) -> ChannelPlugin | None:
        try:
            return discover_plugins({channel_type}).get(channel_type)
        except Exception:
            return None

    def _current_result(
        self,
        request: ExtensionActionRequest,
        package_id: str,
        message: str,
    ) -> ExtensionActionResult:
        snapshot = self.snapshot()
        package, descriptor = self._find_target(snapshot, request.target_id)
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=package.id if package is not None else package_id,
            target_id=request.target_id,
            lifecycle=descriptor.lifecycle if descriptor is not None else None,
            message=message,
        )

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

    def _package(
        self,
        config: Config,
        plugin: ChannelPlugin,
        statuses: Mapping[tuple[str, str], Mapping[str, object]],
    ) -> tuple[
        ExtensionPackageDescriptor,
        dict[str, tuple[str, str | None]],
        dict[str, str],
    ]:
        raw_name = plugin.name
        name = canonical_extension_name(raw_name, fallback="channel")
        package_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, name)
        section = getattr(config.channels, raw_name, None)
        raw_dependencies = cast(object, plugin.dependencies)
        if not isinstance(raw_dependencies, tuple):
            raise TypeError("channel dependencies must be strings")
        dependency_values = cast(tuple[object, ...], raw_dependencies)
        if not all(isinstance(dependency, str) for dependency in dependency_values):
            raise TypeError("channel dependencies must be strings")
        dependencies = cast(tuple[str, ...], dependency_values)
        raw_capabilities = cast(object, plugin.capabilities)
        if not isinstance(raw_capabilities, Iterable):
            raise TypeError("channel capabilities must be a bounded collection of strings")
        capability_values = list(islice(cast(Iterable[object], raw_capabilities), _MAX_CAPABILITIES + 1))
        capabilities = tuple(capability for capability in capability_values if isinstance(capability, str))
        if len(capability_values) > _MAX_CAPABILITIES or len(capabilities) != len(capability_values):
            raise TypeError("channel capabilities must be a bounded collection of strings")
        dependencies_ready = bool(self._dependencies_installed(raw_name, list(dependencies) or None))
        local_state = channel_local_state_present(plugin, section)
        setup_fields = self._setup_fields(plugin, section)
        configuration = ExtensionConfigurationTarget(section="channels", item=name)

        components: list[ExtensionComponentDescriptor] = []
        targets: dict[str, tuple[str, str | None]] = {package_id: (raw_name, None)}
        component_revisions: list[str] = []
        fingerprints: dict[str, str] = {}
        for instance in channel_instance_specs(plugin, section, enabled_only=False):
            raw_instance_id = instance.instance_id
            instance_name = canonical_extension_name(raw_instance_id, fallback="instance")
            component_id = extension_component_id(package_id, ExtensionComponentKind.CHANNEL, instance_name)
            if component_id in targets:
                raise ValueError("channel package has duplicate canonical instance IDs")
            desired = ChannelActivation.from_config(instance.config).resolve(default=plugin.default_enabled)
            configured = self._configured(plugin, instance.config, local_state)
            lifecycle = self._component_lifecycle(
                desired=desired,
                configured=configured,
                dependencies_ready=dependencies_ready,
                status=statuses.get((raw_name, raw_instance_id)),
            )
            instance_setup_fields = self._setup_fields(plugin, instance.config)
            fingerprint = self._private_config_fingerprint(instance.config)
            revision = self._revision(
                {
                    "capabilities": capabilities,
                    "configured": configured,
                    "dependencies": dependencies,
                    "dependencies_ready": dependencies_ready,
                    "desired": desired,
                    "instance": raw_instance_id,
                    "local_state": local_state,
                    "plugin": raw_name,
                    "private_config": fingerprint,
                    "setup_fields": instance_setup_fields,
                }
            )
            component_revisions.append(revision)
            targets[component_id] = (raw_name, raw_instance_id)
            fingerprints[component_id] = fingerprint
            actions = {ExtensionAction.INSPECT}
            if self._services is not None:
                if plugin.setup is not None:
                    actions.add(ExtensionAction.CONFIGURE)
                if "always_enabled" not in capabilities:
                    actions.update({ExtensionAction.ENABLE, ExtensionAction.DISABLE})
                if plugin.connector is not None:
                    actions.add(ExtensionAction.RECONNECT)
            components.append(
                ExtensionComponentDescriptor(
                    id=component_id,
                    package_id=package_id,
                    kind=ExtensionComponentKind.CHANNEL,
                    name=instance_name,
                    display_name=safe_extension_label(raw_instance_id, fallback=instance_name),
                    capabilities=capabilities,
                    execution=ExtensionExecution.IN_PROCESS,
                    lifecycle=lifecycle,
                    revision=revision,
                    actions=frozenset(actions),
                    configuration=configuration,
                )
            )

        package_lifecycle = self._package_lifecycle(component.lifecycle for component in components)
        revision = self._revision(
            {
                "capabilities": capabilities,
                "components": tuple(sorted(component_revisions)),
                "dependencies": dependencies,
                "plugin": raw_name,
                "setup_fields": setup_fields,
            }
        )
        package_actions = {ExtensionAction.INSPECT}
        if self._services is not None and dependencies and not dependencies_ready:
            package_actions.add(ExtensionAction.INSTALL)
        return (
            ExtensionPackageDescriptor(
                id=package_id,
                name=name,
                display_name=safe_extension_label(plugin.display_name, fallback=name),
                source=ExtensionSource.CHANNEL_PACKAGE,
                trust=ExtensionTrust.FIRST_PARTY,
                execution=ExtensionExecution.IN_PROCESS,
                lifecycle=package_lifecycle,
                revision=revision,
                isolated=None,
                permissions_enforced=False,
                actions=frozenset(package_actions),
                configuration=configuration,
                components=tuple(components),
            ),
            targets,
            fingerprints,
        )

    @staticmethod
    def _setup_fields(plugin: ChannelPlugin, section: object) -> tuple[tuple[str, str, bool], ...]:
        if plugin.setup is None:
            return ()
        return tuple(
            sorted(
                (field_name, field.kind, channel_value_present(channel_field_value(section, field_name)))
                for field_name, field in plugin.setup.fields.items()
            )
        )

    @staticmethod
    def _component_lifecycle(
        *,
        desired: bool,
        configured: bool,
        dependencies_ready: bool,
        status: Mapping[str, object] | None,
    ) -> ExtensionLifecycle:
        if not dependencies_ready:
            return ExtensionLifecycle.UNAVAILABLE
        if not desired:
            return ExtensionLifecycle.DISABLED
        if not configured:
            return ExtensionLifecycle.UNAVAILABLE
        if status is None:
            return ExtensionLifecycle.FAILED
        if status.get("pairing_only") is True:
            return ExtensionLifecycle.RELOADING
        state = status.get("state")
        if state == "starting":
            return ExtensionLifecycle.ENABLING
        if state == "running":
            return ExtensionLifecycle.ENABLED
        return ExtensionLifecycle.FAILED

    @staticmethod
    def _package_lifecycle(lifecycles: Iterable[ExtensionLifecycle]) -> ExtensionLifecycle:
        values = tuple(lifecycles)
        if ExtensionLifecycle.FAILED in values:
            return ExtensionLifecycle.FAILED
        if ExtensionLifecycle.ENABLING in values:
            return ExtensionLifecycle.ENABLING
        if ExtensionLifecycle.RELOADING in values:
            return ExtensionLifecycle.RELOADING
        if ExtensionLifecycle.ENABLED in values:
            return ExtensionLifecycle.ENABLED
        if ExtensionLifecycle.UNAVAILABLE in values:
            return ExtensionLifecycle.UNAVAILABLE
        return ExtensionLifecycle.DISABLED

    def _status_index(
        self, diagnostics: list[ExtensionDiagnostic]
    ) -> dict[tuple[str, str], Mapping[str, object]]:
        if self._runtime_status is None:
            return {}
        try:
            raw_rows = cast(object, self._runtime_status())
        except Exception:
            self._add_diagnostic(diagnostics, "channel_runtime_status_unavailable")
            return {}
        if not isinstance(raw_rows, Mapping):
            self._add_diagnostic(diagnostics, "channel_runtime_status_unavailable")
            return {}
        indexed: dict[tuple[str, str], Mapping[str, object]] = {}
        for raw_row in islice(cast(Mapping[object, object], raw_rows).values(), _MAX_PACKAGES + 1):
            if not isinstance(raw_row, Mapping):
                continue
            row = cast(Mapping[object, object], raw_row)
            owner = row.get("owner")
            instance_id = row.get("instance_id")
            if isinstance(owner, str) and isinstance(instance_id, str):
                indexed[(owner, instance_id)] = cast(Mapping[str, object], row)
        return indexed

    def _private_config_fingerprint(self, value: object) -> str:
        """Digest one exact instance config, secrets included, without publishing them.

        Keyed with `_FINGERPRINT_KEY` so the published revision cannot be recomputed
        offline to confirm a guessed secret. Callers never supply a revision from an
        earlier process, so a per-process key costs nothing: `expected_revision` always
        comes from a snapshot taken by this same process.
        """
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json", by_alias=True)
        return hmac.new(_FINGERPRINT_KEY, _encode(value), hashlib.sha256).hexdigest()

    def _revision(self, value: object) -> str:
        """Derive one marker; equal inputs yield equal revisions within this process."""
        return hashlib.sha256(_encode(value)).hexdigest()

    def _current_fingerprint(
        self, plugin: ChannelPlugin, section: object, instance_id: str
    ) -> str | None:
        try:
            instance = next(
                (
                    candidate
                    for candidate in channel_instance_specs(plugin, section, enabled_only=False)
                    if candidate.instance_id == instance_id
                ),
                None,
            )
        except Exception:
            return None
        return None if instance is None else self._private_config_fingerprint(instance.config)

    def _require_current_fingerprint(
        self,
        plugin: ChannelPlugin,
        section: object,
        instance_id: str,
        expected: str,
    ) -> None:
        current = self._current_fingerprint(plugin, section, instance_id)
        if current is None or not hmac.compare_digest(current, expected):
            raise _ChannelActionStaleError

    @staticmethod
    def _add_diagnostic(diagnostics: list[ExtensionDiagnostic], code: str) -> None:
        if len(diagnostics) < _MAX_DIAGNOSTICS:
            diagnostics.append(
                ExtensionDiagnostic(
                    owner_id=ChannelExtensionAdapter.name,
                    code=code,
                    message="Channel extension metadata could not be projected.",
                )
            )


__all__ = ["ChannelExtensionAdapter", "ChannelExtensionServices"]
