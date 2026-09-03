"""Explicit, failure-isolated registry for extension adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, cast

from .contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapter,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionConfigurationTarget,
    ExtensionDiagnostic,
    ExtensionPackageDescriptor,
    ExtensionSnapshot,
    ExtensionSource,
    extension_package_id,
    requires_risk_acknowledgement,
    safe_extension_message,
)


class ExtensionRegistryError(ValueError):
    """A stable failure while registering or dispatching an extension action."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class _Registration:
    token: object
    adapter: ExtensionAdapter


@dataclass(frozen=True, slots=True)
class _Target:
    adapter: ExtensionAdapter
    package: ExtensionPackageDescriptor
    descriptor: ExtensionPackageDescriptor | ExtensionComponentDescriptor


class ExtensionRegistry:
    """Collect complete extension inventories and dispatch validated actions."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(self, adapter: ExtensionAdapter) -> Callable[[], None]:
        """Register an adapter and return a disposer for this registration only."""
        try:
            name = adapter.name
            extension_package_id(ExtensionSource.BUILTIN, name)
        except Exception as exc:
            raise ExtensionRegistryError("invalid_adapter", "adapter name is invalid") from exc

        if name in self._registrations:
            raise ExtensionRegistryError("duplicate_adapter", f"adapter is already registered: {name}")
        if len(self._registrations) >= 256:
            raise ExtensionRegistryError("adapter_limit_reached", "adapter registration limit reached", status=429)

        token = object()
        self._registrations[name] = _Registration(token=token, adapter=adapter)

        def dispose() -> None:
            registration = self._registrations.get(name)
            if registration is not None and registration.token is token:
                del self._registrations[name]

        return dispose

    def snapshot(self) -> ExtensionSnapshot:
        """Return the deterministic inventory from every currently valid adapter."""
        snapshot, _ = self._assemble_snapshot()
        return snapshot

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        """Validate an action against current ownership, then dispatch it once."""
        request = self._validated_request(request)
        if request.context.is_system_admin is not True:
            raise ExtensionRegistryError(
                "system_admin_required",
                "system administrator access is required",
                status=403,
            )

        _, targets = self._assemble_snapshot()
        target = targets.get(request.target_id)
        if target is None:
            raise ExtensionRegistryError(
                "target_not_found",
                "extension action target is unavailable",
                status=404,
            )

        if request.action not in target.descriptor.actions:
            raise ExtensionRegistryError("action_not_supported", "extension action is not supported")
        if (
            request.action in (ExtensionAction.ENABLE, ExtensionAction.INSTALL)
            and requires_risk_acknowledgement(target.package)
        ):
            if not request.risk_acknowledged:
                raise ExtensionRegistryError(
                    "risk_acknowledgement_required",
                    "risk acknowledgement is required for this extension action",
                    status=409,
                )
            if not target.package.revision:
                raise ExtensionRegistryError(
                    "revision_required",
                    "extension action requires a current package revision",
                    status=409,
                )
            if request.expected_revision != target.package.revision:
                raise ExtensionRegistryError(
                    "stale_revision",
                    "extension package revision is stale",
                    status=409,
                )

        try:
            result = await target.adapter.execute(request)
        except Exception as exc:
            raise ExtensionRegistryError(
                "adapter_execution_failed",
                safe_extension_message(exc),
                status=502,
            ) from exc

        return self._validated_result(result, request, target.package.id)

    def _assemble_snapshot(self) -> tuple[ExtensionSnapshot, dict[str, _Target]]:
        registrations = tuple(sorted(self._registrations.items()))
        packages: list[ExtensionPackageDescriptor] = []
        diagnostics: list[tuple[str, ExtensionDiagnostic]] = []
        targets: dict[str, _Target] = {}

        for adapter_name, registration in registrations:
            try:
                adapter_snapshot, local_targets = self._validated_adapter_snapshot(
                    adapter_name,
                    registration.adapter.snapshot(),
                    registration.adapter,
                )
                colliding_ids = sorted(set(local_targets).intersection(targets))
                if colliding_ids:
                    raise ValueError(f"extension target collision: {colliding_ids[0]}")
            except Exception as exc:
                diagnostics.append(
                    (
                        adapter_name,
                        ExtensionDiagnostic(
                            owner_id=adapter_name,
                            code="adapter_snapshot_failed",
                            message=safe_extension_message(exc),
                        ),
                    )
                )
                continue

            packages.extend(adapter_snapshot.packages)
            diagnostics.extend((adapter_name, diagnostic) for diagnostic in adapter_snapshot.diagnostics)
            targets.update(local_targets)

        return (
            ExtensionSnapshot(
                packages=tuple(sorted(packages, key=lambda package: package.id)),
                diagnostics=tuple(
                    diagnostic
                    for _, diagnostic in sorted(
                        diagnostics,
                        key=lambda item: (item[0], item[1].code, item[1].message),
                    )
                ),
            ),
            targets,
        )

    def _validated_adapter_snapshot(
        self,
        adapter_name: str,
        snapshot: object,
        adapter: ExtensionAdapter,
    ) -> tuple[ExtensionAdapterSnapshot, dict[str, _Target]]:
        if not isinstance(snapshot, ExtensionAdapterSnapshot):
            raise ValueError("adapter returned an invalid snapshot")
        bounded_snapshot = ExtensionAdapterSnapshot(
            adapter_name=snapshot.adapter_name,
            packages=snapshot.packages,
            diagnostics=snapshot.diagnostics,
        )
        if bounded_snapshot.adapter_name != adapter_name:
            raise ValueError("adapter snapshot name does not match its registration")

        packages = tuple(
            self._copy_package(package) for package in bounded_snapshot.packages
        )
        diagnostics = tuple(
            self._copy_diagnostic(diagnostic) for diagnostic in bounded_snapshot.diagnostics
        )
        validated_snapshot = ExtensionAdapterSnapshot(
            adapter_name=bounded_snapshot.adapter_name,
            packages=packages,
            diagnostics=diagnostics,
        )

        target_rows: list[tuple[str, _Target]] = []
        for package in validated_snapshot.packages:
            target_rows.append((package.id, _Target(adapter, package, package)))
            target_rows.extend(
                (component.id, _Target(adapter, package, component)) for component in package.components
            )

        target_ids: set[str] = set()
        duplicate_ids: set[str] = set()
        for target_id, _ in target_rows:
            if target_id in target_ids:
                duplicate_ids.add(target_id)
            else:
                target_ids.add(target_id)
        if duplicate_ids:
            raise ValueError(f"duplicate extension target: {min(duplicate_ids)}")

        valid_diagnostic_owners = target_ids | {adapter_name}
        if any(diagnostic.owner_id not in valid_diagnostic_owners for diagnostic in diagnostics):
            raise ValueError("adapter diagnostic owner is not local")
        return validated_snapshot, dict(target_rows)

    def _validated_request(self, request: ExtensionActionRequest) -> ExtensionActionRequest:
        try:
            raw_request = cast(object, request)
            if not isinstance(raw_request, ExtensionActionRequest):
                raise ValueError("request must be an ExtensionActionRequest")
            raw_context = cast(object, raw_request.context)
            if not isinstance(raw_context, ExtensionActionContext):
                raise ValueError("request context must be an ExtensionActionContext")
            context = ExtensionActionContext(
                actor_id=raw_context.actor_id,
                is_system_admin=raw_context.is_system_admin,
                package_install_allowed=raw_context.package_install_allowed,
                channel_pairing_completed=raw_context.channel_pairing_completed,
            )
            return ExtensionActionRequest(
                context=context,
                target_id=raw_request.target_id,
                action=raw_request.action,
                expected_revision=raw_request.expected_revision,
                risk_acknowledged=raw_request.risk_acknowledged,
                values=dict(raw_request.values),
            )
        except Exception as exc:
            raise ExtensionRegistryError(
                "invalid_request", "extension action request is invalid"
            ) from exc

    def _validated_result(
        self,
        result: object,
        request: ExtensionActionRequest,
        package_id: str,
    ) -> ExtensionActionResult:
        if not isinstance(result, ExtensionActionResult):
            raise ExtensionRegistryError(
                "invalid_adapter_result",
                "adapter returned an invalid action result",
                status=502,
            )
        try:
            validated_result = ExtensionActionResult(
                ok=result.ok,
                action=result.action,
                package_id=result.package_id,
                target_id=result.target_id,
                lifecycle=result.lifecycle,
                message=safe_extension_message(result.message),
            )
        except Exception as exc:
            raise ExtensionRegistryError(
                "invalid_adapter_result",
                "adapter returned an invalid action result",
                status=502,
            ) from exc
        if validated_result.action != request.action:
            raise ExtensionRegistryError(
                "invalid_adapter_result",
                "adapter returned a mismatched action",
                status=502,
            )
        if (
            validated_result.package_id != package_id
            or validated_result.target_id != request.target_id
        ):
            raise ExtensionRegistryError(
                "invalid_adapter_result",
                "adapter returned a mismatched target",
                status=502,
            )
        return validated_result

    def _copy_package(self, package: object) -> ExtensionPackageDescriptor:
        if not isinstance(package, ExtensionPackageDescriptor):
            raise ValueError("adapter returned an invalid package descriptor")
        bounded_package = ExtensionPackageDescriptor(
            id=package.id,
            name=package.name,
            display_name=package.display_name,
            source=package.source,
            trust=package.trust,
            execution=package.execution,
            lifecycle=package.lifecycle,
            description=package.description,
            version=package.version,
            revision=package.revision,
            isolated=package.isolated,
            permissions=package.permissions,
            permissions_enforced=package.permissions_enforced,
            actions=package.actions,
            configuration=package.configuration,
            components=package.components,
            diagnostic=package.diagnostic,
        )
        components = tuple(
            self._copy_component(component) for component in bounded_package.components
        )
        return ExtensionPackageDescriptor(
            id=bounded_package.id,
            name=bounded_package.name,
            display_name=bounded_package.display_name,
            source=bounded_package.source,
            trust=bounded_package.trust,
            execution=bounded_package.execution,
            lifecycle=bounded_package.lifecycle,
            description=bounded_package.description,
            version=bounded_package.version,
            revision=bounded_package.revision,
            isolated=bounded_package.isolated,
            permissions=bounded_package.permissions,
            permissions_enforced=bounded_package.permissions_enforced,
            actions=bounded_package.actions,
            configuration=self._copy_configuration(bounded_package.configuration),
            components=components,
            diagnostic=self._copy_optional_diagnostic(bounded_package.diagnostic),
        )

    def _copy_component(self, component: object) -> ExtensionComponentDescriptor:
        if not isinstance(component, ExtensionComponentDescriptor):
            raise ValueError("adapter returned an invalid component descriptor")
        return ExtensionComponentDescriptor(
            id=component.id,
            package_id=component.package_id,
            kind=component.kind,
            name=component.name,
            display_name=component.display_name,
            description=component.description,
            capabilities=component.capabilities,
            execution=component.execution,
            lifecycle=component.lifecycle,
            revision=component.revision,
            actions=component.actions,
            configuration=self._copy_configuration(component.configuration),
            diagnostic=self._copy_optional_diagnostic(component.diagnostic),
        )

    @staticmethod
    def _copy_configuration(
        configuration: ExtensionConfigurationTarget | None,
    ) -> ExtensionConfigurationTarget | None:
        if configuration is None:
            return None
        raw_configuration = cast(object, configuration)
        if not isinstance(raw_configuration, ExtensionConfigurationTarget):
            raise ValueError("extension configuration is invalid")
        return ExtensionConfigurationTarget(
            section=raw_configuration.section,
            item=raw_configuration.item,
        )

    @staticmethod
    def _copy_optional_diagnostic(
        diagnostic: ExtensionDiagnostic | None,
    ) -> ExtensionDiagnostic | None:
        if diagnostic is None:
            return None
        return ExtensionRegistry._copy_diagnostic(diagnostic)

    @staticmethod
    def _copy_diagnostic(diagnostic: object) -> ExtensionDiagnostic:
        if not isinstance(diagnostic, ExtensionDiagnostic):
            raise ValueError("extension diagnostic is invalid")
        return ExtensionDiagnostic(
            owner_id=diagnostic.owner_id,
            code=diagnostic.code,
            message=diagnostic.message,
        )
