"""Canonical projection and lifecycle for CLI apps in durable installed state.

Inventory comes from the manager's durable installed state, never from the catalog.
A cold cache or an offline host must not make an installed app disappear from the
extension tree, and a catalog candidate is not an installed extension. Snapshot
construction therefore performs no catalog request, no package-manager invocation,
and no execution of an app's entry point; the one host lookup it does perform is a
PATH resolution, which decides availability and whose result is never disclosed.

Update, uninstall, and test delegate to the existing ``CliAppManager`` transactions.
This adapter creates no second installer, uninstaller, or executor, and it converts
their results into bounded, redacted, path-free canonical outcomes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar, cast

from loguru import logger

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
    safe_extension_message,
)

_MAX_PACKAGES = 1_024
_MAX_DIAGNOSTICS = 256
_MAX_APP_NAME = 256
_MAX_TEST_EXCERPT = 240

_FALLBACK_PACKAGE_ID = extension_package_id(ExtensionSource.CLI_APP, "cli-app")
_APPS_CONFIGURATION = ExtensionConfigurationTarget(section="apps", item="cli")

# A recorded entry point is only shown when it is a bare command name; an absolute
# location is never part of a descriptor, a revision, or an action result.
_ENTRY_POINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
# Structural display tokens are reduced to identifier characters, which also removes
# every path separator a recorded category or install source could carry.
_UNSAFE_FLAG_RE = re.compile(r"[^A-Za-z0-9._+-]+")
_MAX_FLAG_LENGTH = 64

_DISCLOSURE = (
    "Third-party executable code installed from a package manager at an operator's "
    "request. Nanobot does not verify, signature-check, or sandbox it: it runs as a "
    "child process with access to the files, network, and resources the nanobot "
    "process can reach. Any permissions it declares are self-declared and unenforced."
)
_SKILL_DESCRIPTION = (
    "Prompt text generated for this CLI app. It is data loaded into the agent's "
    "context, not executable code, though its content still steers the agent."
)

_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


class CliAppOwner(Protocol):
    """The existing ``CliAppManager`` surface this adapter is allowed to use."""

    def installed_entries(self) -> Mapping[str, object]: ...

    def generated_skill_name(self, name: str) -> str: ...

    def skill_installed(self, name: str) -> bool: ...

    def managed_plugin_names(self) -> frozenset[str]: ...

    def update(self, name: str) -> Mapping[str, object]: ...

    def uninstall(self, name: str) -> Mapping[str, object]: ...

    def test(self, name: str) -> Mapping[str, object]: ...


def _revision(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _entry_point(value: object) -> str | None:
    """Return a recorded entry point only when it is a bare command name."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _ENTRY_POINT_RE.fullmatch(text) else None


def _flag(value: object) -> str | None:
    """Reduce a recorded structural fact to one short, path-free display token."""
    if not isinstance(value, str):
        return None
    return _UNSAFE_FLAG_RE.sub("-", value.strip())[:_MAX_FLAG_LENGTH].strip("-") or None


def _display_text(value: object, *, fallback: str) -> str:
    """Return bounded display text, falling back rather than rendering a non-string."""
    if not isinstance(value, str) or not value.strip():
        return fallback
    return safe_extension_label(value, fallback=fallback)


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


class CliAppExtensionAdapter:
    """Own each installed CLI app once, covering its executable and generated Skill."""

    name = "cli-apps"

    def __init__(self, owner_loader: Callable[[], CliAppOwner]) -> None:
        self._owner_loader = owner_loader
        self._action_lock = asyncio.Lock()

    def owned_plugin_names(self) -> frozenset[str]:
        """Report the generated Agent Plugin roots this family already publishes.

        Returning nothing on failure is deliberate. An empty claim restores the
        duplicate row the Agent Plugin adapter used to emit, which is visible and
        recoverable; claiming ownership that this adapter cannot then publish would
        make an installed app vanish from the inventory entirely.
        """
        try:
            return self._owner_loader().managed_plugin_names()
        except Exception:
            logger.warning("CLI app plugin ownership is unavailable for this snapshot")
            return frozenset()

    def snapshot(self) -> ExtensionAdapterSnapshot:
        """Project durable installed state only; no catalog, network, or execution."""
        try:
            owner = self._owner_loader()
        except Exception:
            return ExtensionAdapterSnapshot(
                adapter_name=self.name,
                diagnostics=(self._diagnostic("cli_app_inventory_unavailable"),),
            )
        snapshot, _targets = self._snapshot(owner)
        return snapshot

    def _snapshot(self, owner: CliAppOwner) -> tuple[ExtensionAdapterSnapshot, dict[str, str]]:
        """Build the inventory and the target-to-recorded-name map actions dispatch on."""
        diagnostics: list[ExtensionDiagnostic] = []
        try:
            entries = cast(object, owner.installed_entries())
            if not isinstance(entries, Mapping):
                raise ValueError("installed CLI app state must be a mapping")
            items, truncated = _bounded_items(
                cast(Mapping[object, object], entries), _MAX_PACKAGES
            )
        except Exception:
            self._add_diagnostic(diagnostics, "cli_app_inventory_unavailable")
            return (
                ExtensionAdapterSnapshot(adapter_name=self.name, diagnostics=tuple(diagnostics)),
                {},
            )

        packages: list[ExtensionPackageDescriptor] = []
        targets: dict[str, str] = {}
        seen_ids: set[str] = set()
        for raw_name, raw_entry in sorted(items, key=self._item_sort_key):
            actionable = True
            try:
                package = self._package(owner, raw_name, raw_entry)
            except Exception:
                actionable = False
                package = self._failed_package(raw_name)
            if package is None:
                self._add_diagnostic(diagnostics, "cli_app_projection_failed")
                continue
            if package.id in seen_ids:
                self._add_diagnostic(diagnostics, "cli_app_identity_collision")
                continue
            seen_ids.add(package.id)
            packages.append(package)
            if actionable and isinstance(raw_name, str):
                targets[package.id] = raw_name.strip()
                for component in package.components:
                    targets[component.id] = raw_name.strip()

        if truncated:
            self._add_diagnostic(diagnostics, "cli_app_inventory_limited")
        return (
            ExtensionAdapterSnapshot(
                adapter_name=self.name,
                packages=tuple(packages),
                diagnostics=tuple(diagnostics),
            ),
            targets,
        )

    def _package(
        self,
        owner: CliAppOwner,
        raw_name: object,
        raw_entry: object,
    ) -> ExtensionPackageDescriptor:
        if not isinstance(raw_name, str):
            raise ValueError("installed CLI app name is malformed")
        app_name = raw_name.strip()
        if not app_name or len(app_name) > _MAX_APP_NAME:
            raise ValueError("installed CLI app name is malformed")
        if not isinstance(raw_entry, Mapping):
            raise ValueError("installed CLI app entry is malformed")
        entry = cast(Mapping[str, object], raw_entry)

        name = canonical_extension_name(app_name, fallback="cli-app")
        package_id = extension_package_id(ExtensionSource.CLI_APP, name)
        display_name = _display_text(entry.get("display_name"), fallback=app_name[:128])
        display_name = safe_extension_label(display_name, fallback=name)
        version = _display_text(entry.get("version"), fallback="") or None
        category = _flag(entry.get("category"))
        strategy = _flag(entry.get("strategy"))
        install_source = _flag(entry.get("source"))
        entry_point = _entry_point(entry.get("entry_point"))
        skill_present = bool(owner.skill_installed(app_name))

        # A PATH lookup, not an execution: it decides whether the recorded app is
        # still usable. The location it resolves is deliberately discarded.
        available = entry_point is not None and shutil.which(entry_point) is not None
        lifecycle = ExtensionLifecycle.ENABLED if available else ExtensionLifecycle.UNAVAILABLE

        # Structural facts only. ``entry_point_path``, the resolved location, and the
        # argv a package manager would run are all excluded, so a revision cannot be
        # invalidated by a host move and cannot disclose one either.
        signature: dict[str, object] = {
            "name": app_name,
            "skill": skill_present,
            "source": install_source,
            "strategy": strategy,
            "version": version,
        }
        revision = _revision(signature)

        capabilities = tuple(
            flag
            for flag in (
                f"category:{category}" if category else None,
                f"strategy:{strategy}" if strategy else None,
                f"install-source:{install_source}" if install_source else None,
                f"entry-point:{entry_point}" if entry_point else None,
                "skill:present" if skill_present else "skill:absent",
            )
            if flag is not None
        )

        components = [
            ExtensionComponentDescriptor(
                id=extension_component_id(package_id, ExtensionComponentKind.CLI_APP, name),
                package_id=package_id,
                kind=ExtensionComponentKind.CLI_APP,
                name=name,
                display_name=display_name,
                description=_display_text(entry.get("description"), fallback=""),
                capabilities=capabilities,
                execution=ExtensionExecution.CHILD_PROCESS,
                lifecycle=lifecycle,
                revision=_revision({**signature, "component": "cli_app"}),
                # Testing runs the app's own executable, so it is only offered when
                # that executable actually resolves.
                actions=frozenset(
                    {ExtensionAction.INSPECT, ExtensionAction.RELOAD}
                    if available
                    else {ExtensionAction.INSPECT}
                ),
                configuration=_APPS_CONFIGURATION,
            )
        ]
        if skill_present:
            skill_name = canonical_extension_name(
                owner.generated_skill_name(app_name), fallback="cli-app-skill"
            )
            components.append(
                ExtensionComponentDescriptor(
                    id=extension_component_id(
                        package_id, ExtensionComponentKind.SKILL, skill_name
                    ),
                    package_id=package_id,
                    kind=ExtensionComponentKind.SKILL,
                    name=skill_name,
                    display_name=safe_extension_label(skill_name, fallback=name),
                    description=_SKILL_DESCRIPTION,
                    # The owning package is executable; its generated Skill is not.
                    execution=ExtensionExecution.DATA,
                    # The Agent Plugin enablement marker underneath stays an internal
                    # detail of the manager and is not a second operator lifecycle.
                    lifecycle=ExtensionLifecycle.ENABLED,
                    revision=_revision({**signature, "component": "skill"}),
                    actions=frozenset({ExtensionAction.INSPECT}),
                    configuration=_APPS_CONFIGURATION,
                )
            )

        return ExtensionPackageDescriptor(
            id=package_id,
            name=name,
            display_name=display_name,
            source=ExtensionSource.CLI_APP,
            trust=ExtensionTrust.OPERATOR_TRUSTED,
            execution=ExtensionExecution.CHILD_PROCESS,
            lifecycle=lifecycle,
            description=_DISCLOSURE,
            version=version,
            revision=revision,
            isolated=False,
            permissions_enforced=False,
            actions=frozenset(
                {ExtensionAction.INSPECT, ExtensionAction.INSTALL, ExtensionAction.UNINSTALL}
            ),
            configuration=_APPS_CONFIGURATION,
            components=tuple(components),
        )

    def _failed_package(self, raw_name: object) -> ExtensionPackageDescriptor | None:
        """Keep one malformed entry visible and bounded instead of dropping the row."""
        if not isinstance(raw_name, str) or not raw_name.strip():
            return None
        try:
            name = canonical_extension_name(raw_name.strip()[:_MAX_APP_NAME], fallback="cli-app")
            package_id = extension_package_id(ExtensionSource.CLI_APP, name)
            return ExtensionPackageDescriptor(
                id=package_id,
                name=name,
                display_name=safe_extension_label(raw_name, fallback=name),
                source=ExtensionSource.CLI_APP,
                trust=ExtensionTrust.OPERATOR_TRUSTED,
                execution=ExtensionExecution.CHILD_PROCESS,
                lifecycle=ExtensionLifecycle.FAILED,
                description=_DISCLOSURE,
                isolated=False,
                permissions_enforced=False,
                actions=frozenset({ExtensionAction.INSPECT}),
                configuration=_APPS_CONFIGURATION,
                diagnostic=ExtensionDiagnostic(
                    owner_id=package_id,
                    code="installed_entry_malformed",
                    message="The recorded CLI app entry could not be projected.",
                ),
            )
        except Exception:
            return None

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        """Refuse every knowable failure first, then delegate exactly once."""
        async with self._action_lock:
            if request.context.is_system_admin is not True:
                return self._failure(
                    request, _FALLBACK_PACKAGE_ID, "System administrator access is required."
                )
            try:
                owner = self._owner_loader()
            except Exception:
                return self._failure(
                    request, _FALLBACK_PACKAGE_ID, "CLI app inventory is unavailable."
                )
            snapshot, targets = self._snapshot(owner)
            package, descriptor = _find_target(snapshot, request.target_id)
            if package is None or descriptor is None:
                return self._failure(
                    request, _FALLBACK_PACKAGE_ID, "CLI app action target is unavailable."
                )
            if request.action not in descriptor.actions:
                return self._failure(request, package.id, "CLI app action is not supported.")

            revision = package.revision if descriptor is package else descriptor.revision
            if request.action is ExtensionAction.INSPECT:
                if request.expected_revision and request.expected_revision != revision:
                    return self._failure(request, package.id, "CLI app action revision is stale.")
                return ExtensionActionResult(
                    ok=True,
                    action=request.action,
                    package_id=package.id,
                    target_id=request.target_id,
                    lifecycle=descriptor.lifecycle,
                    message="CLI app status is current.",
                )

            if not request.expected_revision:
                return self._failure(
                    request, package.id, "CLI app action requires a current revision."
                )
            if request.expected_revision != revision:
                return self._failure(request, package.id, "CLI app action revision is stale.")
            app_name = targets.get(request.target_id)
            if not app_name:
                return self._failure(
                    request, package.id, "CLI app action target is unavailable."
                )

            if request.action is ExtensionAction.INSTALL:
                if not request.risk_acknowledged:
                    return self._failure(
                        request, package.id, "Updating a CLI app requires acknowledgement."
                    )
                return await self._update(request, owner, package, app_name)
            if request.action is ExtensionAction.RELOAD:
                # No acknowledgement. The contract scopes it to enabling or installing
                # executable third-party code (`registry.py` gates only ENABLE and
                # INSTALL); a test probes an entry point that is already installed and
                # that the agent already runs through `run_cli_app` without a prompt.
                # Demanding a dialog here would also be inert in practice: the control
                # plane only prompts for ENABLE and INSTALL, so the test action would
                # refuse every time it was pressed.
                return await self._test(request, owner, package, app_name)
            if request.action is ExtensionAction.UNINSTALL:
                return await self._uninstall(request, owner, package, app_name)
            return self._failure(request, package.id, "CLI app action is not supported.")

    async def _update(
        self,
        request: ExtensionActionRequest,
        owner: CliAppOwner,
        package: ExtensionPackageDescriptor,
        app_name: str,
    ) -> ExtensionActionResult:
        """Run the manager's existing update transaction and report the resulting state."""
        outcome = await self._delegate(owner.update, app_name)
        lifecycle = self._current_lifecycle(owner, package.id)
        if _last_action(outcome) is None:
            return self._failure(
                request,
                package.id,
                "The CLI app could not be updated through its package manager.",
                lifecycle,
            )
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=package.id,
            target_id=request.target_id,
            lifecycle=lifecycle,
            # The manager reinstalls the executable and rewrites the generated Skill in
            # place, and every reader of both re-reads them per invocation, so there is
            # nothing a running process is still holding and no restart to claim.
            message="The CLI app was updated through its package manager.",
        )

    async def _uninstall(
        self,
        request: ExtensionActionRequest,
        owner: CliAppOwner,
        package: ExtensionPackageDescriptor,
        app_name: str,
    ) -> ExtensionActionResult:
        """Report removal, a truthful partial outcome, or failure, never a rollback."""
        outcome = await self._delegate(owner.uninstall, app_name)
        last_action = _last_action(outcome)
        lifecycle = self._current_lifecycle(owner, package.id)
        if last_action is None:
            return self._failure(
                request,
                package.id,
                "The CLI app could not be uninstalled through its package manager.",
                lifecycle,
            )
        if last_action.get("removed") is not True:
            return self._failure(
                request,
                package.id,
                "The package manager reported success but the app's entry point still "
                "resolves, so nanobot kept it installed and changed nothing else.",
                lifecycle,
            )
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=package.id,
            target_id=request.target_id,
            lifecycle=lifecycle,
            message=(
                "The CLI app was removed from nanobot, but a command of the same name is "
                "still on PATH because it is managed outside nanobot."
                if last_action.get("still_available") is True
                else "The CLI app and its generated Skill were removed."
            ),
        )

    async def _test(
        self,
        request: ExtensionActionRequest,
        owner: CliAppOwner,
        package: ExtensionPackageDescriptor,
        app_name: str,
    ) -> ExtensionActionResult:
        """Report the entry point's exit status with a bounded, redacted excerpt."""
        outcome = await self._delegate(owner.test, app_name)
        last_action = _last_action(outcome)
        lifecycle = self._current_lifecycle(owner, package.id)
        if last_action is None:
            return self._failure(
                request, package.id, "The CLI app entry point could not be run.", lifecycle
            )
        exit_code = last_action.get("exit_code")
        status = (
            f"exited {exit_code}" if isinstance(exit_code, int) else "produced no exit status"
        )
        raw_excerpt = last_action.get("output")
        excerpt = (
            safe_extension_message(raw_excerpt)[:_MAX_TEST_EXCERPT]
            if isinstance(raw_excerpt, str) and raw_excerpt.strip()
            else ""
        )
        message = f"The CLI app entry point {status}."
        if excerpt:
            message = f"{message} Output: {excerpt}"
        return ExtensionActionResult(
            ok=last_action.get("ok") is True,
            action=request.action,
            package_id=package.id,
            target_id=request.target_id,
            lifecycle=lifecycle,
            message=message,
        )

    @staticmethod
    async def _delegate(
        operation: Callable[[str], Mapping[str, object]],
        app_name: str,
    ) -> Mapping[str, object] | None:
        """Call one existing manager transaction once, off the event loop.

        The manager's own exception text carries raw package-manager output, so it is
        logged and discarded here; the caller reports a bounded reason of its own.
        """
        try:
            result = cast(object, await asyncio.to_thread(operation, app_name))
        except Exception:
            logger.exception("A CLI app extension action failed")
            return None
        return cast(Mapping[str, object], result) if isinstance(result, Mapping) else None

    def _current_lifecycle(self, owner: CliAppOwner, package_id: str) -> ExtensionLifecycle:
        """Re-derive the package's state after an action rather than assuming it."""
        try:
            snapshot, _targets = self._snapshot(owner)
        except Exception:
            return ExtensionLifecycle.UNAVAILABLE
        for package in snapshot.packages:
            if package.id == package_id:
                return package.lifecycle
        return ExtensionLifecycle.UNAVAILABLE

    def _diagnostic(self, code: str) -> ExtensionDiagnostic:
        return ExtensionDiagnostic(
            owner_id=self.name,
            code=code,
            message="A CLI app could not be projected.",
        )

    def _add_diagnostic(self, diagnostics: list[ExtensionDiagnostic], code: str) -> None:
        if len(diagnostics) < _MAX_DIAGNOSTICS:
            diagnostics.append(self._diagnostic(code))

    @staticmethod
    def _item_sort_key(item: tuple[object, object]) -> tuple[int, str]:
        raw_name, _entry = item
        return (0, raw_name) if isinstance(raw_name, str) else (1, "")

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


def _last_action(outcome: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if outcome is None:
        return None
    last_action = outcome.get("last_action")
    return cast(Mapping[str, object], last_action) if isinstance(last_action, Mapping) else None


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


__all__ = ["CliAppExtensionAdapter", "CliAppOwner"]
