"""Composition-declared long-lived agent hooks and their read-only projection.

Hooks are bound when an application composes its ``AgentLoop`` and cannot be
changed afterwards, so every row here is read-only and reports
``restart_required``. ``AgentLoop`` keeps executing the plain hook values it is
given; it gains no registry, descriptor storage, or adapter reference.

The declaration helpers below exist so a composition writes each hook literal
once. ``LongLivedHooks`` then yields both the values handed to ``AgentLoop`` and
the descriptors handed to the registry, which is what keeps the inventory from
drifting away from what the process actually runs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from nanobot.agent.hook import AgentHook, AgentTurnHookFactory
from nanobot.extensions.adapters.common import canonical_extension_name
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionAdapterSnapshot,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)

_MAX_HOOKS = 512
_PACKAGE_PREFIX = "agent-hooks"
_HOOK_DESCRIPTION = "A startup-bound in-process agent hook."
_FACTORY_DESCRIPTION = "A startup-bound in-process agent hook factory."
_RESTART_MESSAGE = "Registered agent hooks are bound at startup and change only on restart."


@dataclass(frozen=True, slots=True)
class RegisteredHook:
    """One long-lived ``AgentHook`` value and the identity it is inventoried under."""

    name: str
    hook: AgentHook


@dataclass(frozen=True, slots=True)
class RegisteredHookFactory:
    """One long-lived hook factory and the identity it is inventoried under."""

    name: str
    factory: AgentTurnHookFactory


HookDeclaration = RegisteredHook | RegisteredHookFactory


def _declared_name(value: object, field_name: str) -> str:
    """Accept only an already-canonical identity so two hooks never merge silently."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        extension_package_id(ExtensionSource.BUILTIN, value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be a canonical lowercase ASCII extension name"
        ) from exc
    return value


def _derived_name(target: object) -> str:
    """Derive a stable identity from a caller-supplied callable's module and qualname."""
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None)
    if not isinstance(module, str) or not module:
        raise ValueError("an externally supplied hook must expose a module name")
    if not isinstance(qualname, str) or not qualname:
        raise ValueError("an externally supplied hook must expose a qualified name")
    if "<lambda>" in qualname:
        raise ValueError("an externally supplied hook must not be an anonymous lambda")
    return canonical_extension_name(f"{module}.{qualname}", fallback="hook")


def _require_hook(hook: object) -> AgentHook:
    if not isinstance(hook, AgentHook):
        raise ValueError("a registered hook must be an AgentHook")
    return hook


def _require_factory(factory: object) -> AgentTurnHookFactory:
    if not callable(factory):
        raise ValueError("a registered hook factory must be callable")
    return cast(AgentTurnHookFactory, factory)


def registered_hook(name: str, hook: AgentHook) -> RegisteredHook:
    """Declare a first-party long-lived hook under an explicit stable name."""
    return RegisteredHook(name=_declared_name(name, "hook name"), hook=_require_hook(hook))


def registered_hook_factory(name: str, factory: AgentTurnHookFactory) -> RegisteredHookFactory:
    """Declare a first-party long-lived hook factory under an explicit stable name."""
    return RegisteredHookFactory(
        name=_declared_name(name, "hook factory name"),
        factory=_require_factory(factory),
    )


def external_hook(hook: AgentHook) -> RegisteredHook:
    """Declare a caller-supplied hook under its validated module and qualified name."""
    validated = _require_hook(hook)
    return RegisteredHook(name=_derived_name(type(validated)), hook=validated)


def external_hook_factory(factory: AgentTurnHookFactory) -> RegisteredHookFactory:
    """Declare a caller-supplied hook factory under its validated module and qualified name."""
    validated = _require_factory(factory)
    return RegisteredHookFactory(name=_derived_name(validated), factory=validated)


@dataclass(frozen=True, slots=True)
class LongLivedHooks:
    """One composition's startup-bound hooks, declared once for run and for inventory."""

    composition: str
    declarations: tuple[HookDeclaration, ...]

    @property
    def package_name(self) -> str:
        return f"{_PACKAGE_PREFIX}-{self.composition}"

    @property
    def hooks(self) -> list[AgentHook]:
        """The exact ``hooks=`` list this composition hands to ``AgentLoop``."""
        return [
            declaration.hook
            for declaration in self.declarations
            if isinstance(declaration, RegisteredHook)
        ]

    @property
    def hook_factories(self) -> list[AgentTurnHookFactory]:
        """The exact ``hook_factories=`` list this composition hands to ``AgentLoop``."""
        return [
            declaration.factory
            for declaration in self.declarations
            if isinstance(declaration, RegisteredHookFactory)
        ]


def long_lived_hooks(composition: str, *declarations: HookDeclaration) -> LongLivedHooks:
    """Declare every long-lived hook one composition registers, exactly once."""
    name = _declared_name(composition, "composition name")
    # Fail here rather than inside ``snapshot()``, where an over-long composition name
    # would drop the whole adapter instead of naming the caller that built it.
    extension_package_id(ExtensionSource.BUILTIN, f"{_PACKAGE_PREFIX}-{name}")
    if len(declarations) > _MAX_HOOKS:
        raise ValueError(f"a composition may declare at most {_MAX_HOOKS} long-lived hooks")
    seen: set[str] = set()
    for declaration in declarations:
        raw_declaration = cast(object, declaration)
        if not isinstance(raw_declaration, (RegisteredHook, RegisteredHookFactory)):
            raise ValueError("long-lived hook declarations must be registered hook values")
        if declaration.name in seen:
            raise ValueError(f"duplicate long-lived hook identity: {declaration.name}")
        seen.add(declaration.name)
    return LongLivedHooks(composition=name, declarations=tuple(declarations))


def _revision(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class HookExtensionAdapter:
    """Project one composition's startup-bound hooks as read-only inventory rows."""

    name = "agent-hooks"

    def __init__(self, hooks: LongLivedHooks) -> None:
        self._hooks = hooks

    def snapshot(self) -> ExtensionAdapterSnapshot:
        """Return declared hook identities only; no callable or bound value is exposed."""
        composition = self._hooks.composition
        package_name = self._hooks.package_name
        package_id = extension_package_id(ExtensionSource.BUILTIN, package_name)
        components: list[ExtensionComponentDescriptor] = []
        signature: list[list[str]] = []
        for declaration in self._hooks.declarations:
            is_factory = isinstance(declaration, RegisteredHookFactory)
            kind_label = "hook-factory" if is_factory else "hook"
            signature.append([declaration.name, kind_label])
            components.append(
                ExtensionComponentDescriptor(
                    id=extension_component_id(
                        package_id, ExtensionComponentKind.HOOK, declaration.name
                    ),
                    package_id=package_id,
                    kind=ExtensionComponentKind.HOOK,
                    name=declaration.name,
                    display_name=declaration.name,
                    description=_FACTORY_DESCRIPTION if is_factory else _HOOK_DESCRIPTION,
                    capabilities=(kind_label,),
                    execution=ExtensionExecution.IN_PROCESS,
                    lifecycle=ExtensionLifecycle.RESTART_REQUIRED,
                    revision=_revision(
                        {"composition": composition, "kind": kind_label, "name": declaration.name}
                    ),
                    actions=frozenset({ExtensionAction.INSPECT}),
                )
            )
        package = ExtensionPackageDescriptor(
            id=package_id,
            name=package_name,
            display_name=f"Agent hooks ({composition})",
            source=ExtensionSource.BUILTIN,
            trust=ExtensionTrust.OPERATOR_TRUSTED,
            execution=ExtensionExecution.IN_PROCESS,
            lifecycle=ExtensionLifecycle.RESTART_REQUIRED,
            description=(
                "In-process hook code registered when this application composed its agent."
            ),
            revision=_revision({"composition": composition, "hooks": signature}),
            isolated=False,
            permissions_enforced=False,
            actions=frozenset({ExtensionAction.INSPECT}),
            components=tuple(components),
        )
        return ExtensionAdapterSnapshot(adapter_name=self.name, packages=(package,))

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        """Report startup-bound hook status; hooks declare no mutating action."""
        package_id = extension_package_id(ExtensionSource.BUILTIN, self._hooks.package_name)
        if request.context.is_system_admin is not True:
            return self._failure(request, package_id, "System administrator access is required.")
        if request.action is not ExtensionAction.INSPECT:
            return self._failure(request, package_id, "Agent hook action is not supported.")
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=package_id,
            target_id=request.target_id,
            lifecycle=ExtensionLifecycle.RESTART_REQUIRED,
            message=_RESTART_MESSAGE,
        )

    @staticmethod
    def _failure(
        request: ExtensionActionRequest,
        package_id: str,
        message: str,
    ) -> ExtensionActionResult:
        return ExtensionActionResult(
            ok=False,
            action=request.action,
            package_id=package_id,
            target_id=request.target_id,
            message=message,
        )


__all__ = [
    "HookExtensionAdapter",
    "HookDeclaration",
    "LongLivedHooks",
    "RegisteredHook",
    "RegisteredHookFactory",
    "external_hook",
    "external_hook_factory",
    "long_lived_hooks",
    "registered_hook",
    "registered_hook_factory",
]
