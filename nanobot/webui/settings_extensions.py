"""Canonical extension inventory and lifecycle transport for the Settings surface.

This module is a transport and a presenter only. Every lifecycle decision stays with
the owning adapter behind ``ExtensionRegistry``; nothing here derives enabled state,
builds a second registry, or imports a family runtime.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionComponentDescriptor,
    ExtensionConfigurationTarget,
    ExtensionDiagnostic,
    ExtensionPackageDescriptor,
    ExtensionSnapshot,
    requires_risk_acknowledgement,
)
from nanobot.extensions.registry import ExtensionRegistry, ExtensionRegistryError
from nanobot.webui.settings_contracts import SettingsRequest, SettingsRouteResult

if TYPE_CHECKING:
    from nanobot.webui.settings_services import WebUISettingsServices

_ACTIONS_BY_VALUE = {action.value: action for action in ExtensionAction}
# Acknowledgement is disclosure bound to a revision, not authorization. It is refused
# with its own status so the surface can reopen the warning instead of reporting the
# generic revision conflict, which asks the operator to do something different.
_ACKNOWLEDGEMENT_REQUIRED_STATUS = 428
_ACTION_NOT_SUPPORTED_STATUS = 422
_MAX_ACTION_VALUE_KEYS = 64


def _is_host_administrator(request: SettingsRequest) -> bool:
    """Report whether the transport proved a host administrator, not merely a claim.

    `system_admin` is also raised for a member acting on a channel instance they own,
    which authorizes that one channel action but must not widen a host-wide inventory
    or a host-wide lifecycle action, so this boundary reads `host_admin`. An empty
    actor means no collaboration identity was ever derived, so it fails closed.
    """
    return request.host_admin is True and bool((request.actor_user_id or "").strip())


def _configuration_payload(
    configuration: ExtensionConfigurationTarget | None,
) -> dict[str, Any] | None:
    if configuration is None:
        return None
    return {"section": configuration.section, "item": configuration.item}


def _diagnostic_payload(diagnostic: ExtensionDiagnostic) -> dict[str, Any]:
    return {
        "owner_id": diagnostic.owner_id,
        "code": diagnostic.code,
        "message": diagnostic.message,
    }


def component_payload(component: ExtensionComponentDescriptor) -> dict[str, Any]:
    """Serialize one component exactly as its owning adapter declared it."""
    return {
        "id": component.id,
        "package_id": component.package_id,
        "kind": component.kind.value,
        "name": component.name,
        "display_name": component.display_name,
        "description": component.description,
        "capabilities": list(component.capabilities),
        "execution": component.execution.value,
        "lifecycle": component.lifecycle.value,
        "revision": component.revision,
        "actions": sorted(action.value for action in component.actions),
        "configuration": _configuration_payload(component.configuration),
        "diagnostic": (
            _diagnostic_payload(component.diagnostic)
            if component.diagnostic is not None
            else None
        ),
    }


def package_payload(package: ExtensionPackageDescriptor) -> dict[str, Any]:
    """Serialize one package with the trust facts the registry actually computed.

    `isolated` and `permissions_enforced` are carried verbatim so the surface renders
    the adapter's declaration instead of restating a hardcoded assumption about it.
    """
    return {
        "id": package.id,
        "name": package.name,
        "display_name": package.display_name,
        "description": package.description,
        "source": package.source.value,
        "trust": package.trust.value,
        "execution": package.execution.value,
        "isolated": package.isolated,
        "lifecycle": package.lifecycle.value,
        "version": package.version,
        "revision": package.revision,
        "permissions": list(package.permissions),
        "permissions_enforced": package.permissions_enforced,
        "risk_acknowledgement_required": requires_risk_acknowledgement(package),
        "actions": sorted(action.value for action in package.actions),
        "configuration": _configuration_payload(package.configuration),
        "diagnostic": (
            _diagnostic_payload(package.diagnostic) if package.diagnostic is not None else None
        ),
        "components": [component_payload(component) for component in package.components],
    }


def extension_snapshot_payload(snapshot: ExtensionSnapshot) -> dict[str, Any]:
    """Serialize the whole inventory in the registry's own deterministic order."""
    return {
        "available": True,
        "packages": [package_payload(package) for package in snapshot.packages],
        "diagnostics": [
            _diagnostic_payload(diagnostic) for diagnostic in snapshot.diagnostics
        ],
    }


def empty_extension_snapshot_payload() -> dict[str, Any]:
    """Report an absent registry explicitly rather than as an empty host."""
    return {"available": False, "packages": [], "diagnostics": []}


def _descriptor_revision(
    descriptor: ExtensionPackageDescriptor | ExtensionComponentDescriptor,
) -> str:
    return descriptor.revision or ""


def _locate(
    snapshot: ExtensionSnapshot,
    target_id: str,
) -> tuple[ExtensionPackageDescriptor, ExtensionPackageDescriptor | ExtensionComponentDescriptor] | None:
    for package in snapshot.packages:
        if package.id == target_id:
            return package, package
        for component in package.components:
            if component.id == target_id:
                return package, component
    return None


class ExtensionActionRejectedError(ValueError):
    """A control-plane refusal raised before any adapter callback can run."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _action_values(raw: object) -> Mapping[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ExtensionActionRejectedError(400, "The extension action values are invalid.")
    mapping = cast(Mapping[object, object], raw)
    if len(mapping) > _MAX_ACTION_VALUE_KEYS:
        raise ExtensionActionRejectedError(400, "The extension action values are invalid.")
    values: dict[str, object] = {}
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise ExtensionActionRejectedError(400, "The extension action values are invalid.")
        values[key] = value
    return values


def _action_request(
    payload: Mapping[str, object],
    *,
    context: ExtensionActionContext,
) -> ExtensionActionRequest:
    """Build a validated request, refusing malformed input before any dispatch."""
    raw_target = payload.get("target_id")
    target_id = raw_target.strip() if isinstance(raw_target, str) else ""
    if not target_id:
        raise ExtensionActionRejectedError(400, "An extension action target is required.")
    raw_action = payload.get("action")
    action = _ACTIONS_BY_VALUE.get(raw_action.strip()) if isinstance(raw_action, str) else None
    if action is None:
        raise ExtensionActionRejectedError(400, "The extension action is unknown.")
    raw_revision = payload.get("expected_revision")
    if raw_revision is None:
        expected_revision = ""
    elif isinstance(raw_revision, str):
        expected_revision = raw_revision.strip()
    else:
        raise ExtensionActionRejectedError(400, "The extension action values are invalid.")
    raw_acknowledged = payload.get("risk_acknowledged")
    if isinstance(raw_acknowledged, bool):
        risk_acknowledged = raw_acknowledged
    elif isinstance(raw_acknowledged, str):
        risk_acknowledged = raw_acknowledged.strip().lower() in {"1", "true", "yes"}
    elif raw_acknowledged is None:
        risk_acknowledged = False
    else:
        raise ExtensionActionRejectedError(400, "The extension action values are invalid.")
    try:
        return ExtensionActionRequest(
            context=context,
            target_id=target_id,
            action=action,
            expected_revision=expected_revision,
            risk_acknowledged=risk_acknowledged,
            values=_action_values(payload.get("values")),
        )
    except ExtensionActionRejectedError:
        raise
    except ValueError as exc:
        raise ExtensionActionRejectedError(400, "The extension action values are invalid.") from exc


def action_result_payload(
    result: ExtensionActionResult,
    *,
    actor_id: str,
    package: ExtensionPackageDescriptor | None,
) -> dict[str, Any]:
    """Report the canonical outcome plus a freshly derived row for the affected package.

    The actor, stable extension ID, action, outcome, and bounded reason travel together
    so a later durable audit store can consume this shape unchanged.
    """
    return {
        "ok": result.ok,
        "actor_id": actor_id,
        "action": result.action.value,
        "package_id": result.package_id,
        "target_id": result.target_id,
        "lifecycle": result.lifecycle.value if result.lifecycle is not None else None,
        "message": result.message,
        "package": package_payload(package) if package is not None else None,
    }


class ExtensionSettingsHandler:
    """Serve the canonical extension inventory and dispatch validated actions."""

    def __init__(self, settings: WebUISettingsServices, logger: Any) -> None:
        self.settings = settings
        self.logger = logger

    async def handle(
        self,
        action: str,
        request: SettingsRequest,
    ) -> SettingsRouteResult:
        # Both the inventory and the actions are host-wide, so neither is reachable
        # without a proven host administrator. Refusing here keeps authorization ahead
        # of disclosure: a member never learns whether a target exists.
        if not _is_host_administrator(request):
            return SettingsRouteResult.failure(
                403, "System administrator access is required"
            )
        if action == "extensions-list":
            return await self._snapshot()
        if action == "extensions-action":
            return await self._action(request)
        return SettingsRouteResult.failure(404, "unknown settings action")

    def _registry(self) -> ExtensionRegistry | None:
        return self.settings.extensions

    async def _snapshot(self) -> SettingsRouteResult:
        registry = self._registry()
        if registry is None:
            return SettingsRouteResult.success(empty_extension_snapshot_payload())
        try:
            snapshot = await asyncio.to_thread(registry.snapshot)
        except Exception:
            self.logger.exception("failed to build the extension inventory")
            return SettingsRouteResult.failure(500, "failed to load extensions")
        return SettingsRouteResult.success(extension_snapshot_payload(snapshot))

    async def _action(self, request: SettingsRequest) -> SettingsRouteResult:
        registry = self._registry()
        if registry is None:
            return SettingsRouteResult.failure(503, "The extension registry is unavailable.")
        actor_id = (request.actor_user_id or "").strip()
        context = ExtensionActionContext(
            actor_id=actor_id,
            is_system_admin=True,
            package_install_allowed=self._allow_package_install(request),
        )
        try:
            action_request = _action_request(request.payload or {}, context=context)
            snapshot = await asyncio.to_thread(registry.snapshot)
            self._reject_before_dispatch(snapshot, action_request)
        except ExtensionActionRejectedError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception:
            self.logger.exception("failed to resolve an extension action target")
            return SettingsRouteResult.failure(500, "extension action could not be completed")

        try:
            result = await registry.execute(action_request)
        except ExtensionRegistryError as exc:
            return SettingsRouteResult.failure(exc.status, str(exc))
        except Exception:
            self.logger.exception("extension action '{}' failed", action_request.action.value)
            return SettingsRouteResult.failure(500, "extension action could not be completed")

        try:
            refreshed = await asyncio.to_thread(registry.snapshot)
        except Exception:
            self.logger.exception("failed to refresh the extension inventory")
            refreshed = None
        located = _locate(refreshed, result.package_id) if refreshed is not None else None
        payload = action_result_payload(
            result,
            actor_id=actor_id,
            package=located[0] if located is not None else None,
        )
        return SettingsRouteResult.success(
            payload,
            decorate_restart=True,
            restart_section="runtime" if result.lifecycle is not None else None,
        )

    @staticmethod
    def _reject_before_dispatch(
        snapshot: ExtensionSnapshot,
        request: ExtensionActionRequest,
    ) -> None:
        """Refuse every knowable failure while no adapter callback has run yet."""
        located = _locate(snapshot, request.target_id)
        if located is None:
            raise ExtensionActionRejectedError(404, "The extension action target is unavailable.")
        package, descriptor = located
        if request.action not in descriptor.actions:
            raise ExtensionActionRejectedError(
                _ACTION_NOT_SUPPORTED_STATUS,
                "This extension does not support that action.",
            )
        if _descriptor_revision(descriptor) != (request.expected_revision or ""):
            raise ExtensionActionRejectedError(
                409,
                "This extension changed. Reopen its details and try again.",
            )
        if (
            request.action in {ExtensionAction.ENABLE, ExtensionAction.INSTALL}
            and requires_risk_acknowledgement(package)
            and not request.risk_acknowledged
        ):
            raise ExtensionActionRejectedError(
                _ACKNOWLEDGEMENT_REQUIRED_STATUS,
                "This action requires an explicit risk acknowledgement.",
            )

    def _allow_package_install(self, request: SettingsRequest) -> bool:
        if request.local_browser:
            return True
        try:
            return bool(self.settings.config.load().tools.webui_allow_remote_package_install)
        except Exception:
            self.logger.exception("failed to load remote package install policy")
            return False


__all__ = [
    "ExtensionActionRejectedError",
    "ExtensionSettingsHandler",
    "action_result_payload",
    "component_payload",
    "empty_extension_snapshot_payload",
    "extension_snapshot_payload",
    "package_payload",
]
