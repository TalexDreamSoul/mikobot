"""Long-lived hook inventory: identity, honesty, exclusions, and preserved execution order."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from nanobot.agent.hook import AgentHook, AgentHookContext, AgentTurnHookContext
from nanobot.agent.loop import AgentLoop
from nanobot.agent.progress_hook import AgentProgressHook
from nanobot.agent.runner import AgentRunner
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.turn_hooks import AgentTurnHookSpec, build_agent_turn_hook
from nanobot.config.schema import Config
from nanobot.extensions.adapters.hooks import (
    HookExtensionAdapter,
    LongLivedHooks,
    RegisteredHook,
    RegisteredHookFactory,
    external_hook,
    external_hook_factory,
    long_lived_hooks,
    registered_hook,
    registered_hook_factory,
)
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionComponentKind,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
)
from nanobot.extensions.runtime import build_core_extension_registry

_ADMIN = ExtensionActionContext(actor_id="owner", is_system_admin=True)
_USER = ExtensionActionContext(actor_id="member", is_system_admin=False)
_GATEWAY_PACKAGE = extension_package_id(ExtensionSource.BUILTIN, "agent-hooks-gateway")


class _Recording(AgentHook):
    def __init__(self, events: list[str], label: str) -> None:
        super().__init__()
        self._events = events
        self._label = label

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._events.append(f"{self._label}:{context.iteration}")

    async def on_stream(self, _context: AgentHookContext, delta: str) -> None:
        self._events.append(f"{self._label}:{delta}")


def _factory(events: list[str], label: str):
    def create(_context: AgentTurnHookContext) -> AgentHook:
        return _Recording(events, label)

    return create


def _bundle(events: list[str]) -> LongLivedHooks:
    return long_lived_hooks(
        "gateway",
        registered_hook("mcp-readiness", _Recording(events, "registered")),
        registered_hook_factory("file-edit-activity", _factory(events, "registered_factory")),
    )


def test_hook_rows_are_read_only_restart_bound_and_inspect_only() -> None:
    """Startup-bound hooks claim no hot reload and offer no enable, disable, or reconnect."""
    package = HookExtensionAdapter(_bundle([])).snapshot().packages[0]

    assert package.id == _GATEWAY_PACKAGE
    assert package.trust is ExtensionTrust.OPERATOR_TRUSTED
    assert package.execution is ExtensionExecution.IN_PROCESS
    assert package.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
    assert package.isolated is False
    assert package.permissions_enforced is False
    assert package.actions == frozenset({ExtensionAction.INSPECT})
    for component in package.components:
        assert component.kind is ExtensionComponentKind.HOOK
        assert component.execution is ExtensionExecution.IN_PROCESS
        assert component.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
        assert component.actions == frozenset({ExtensionAction.INSPECT})


def test_each_declared_hook_appears_exactly_once_with_a_stable_id() -> None:
    """Identity comes from a validated name, never an object address or repr."""
    events: list[str] = []
    first = HookExtensionAdapter(_bundle(events)).snapshot().packages[0]
    second = HookExtensionAdapter(_bundle(events)).snapshot().packages[0]

    assert [component.id for component in first.components] == [
        extension_component_id(
            _GATEWAY_PACKAGE, ExtensionComponentKind.HOOK, "file-edit-activity"
        ),
        extension_component_id(_GATEWAY_PACKAGE, ExtensionComponentKind.HOOK, "mcp-readiness"),
    ]
    assert [c.id for c in first.components] == [c.id for c in second.components]
    assert [c.revision for c in first.components] == [c.revision for c in second.components]
    assert first.revision == second.revision
    assert "0x" not in "".join(c.id + c.display_name for c in first.components)


def test_externally_supplied_callables_use_a_validated_module_and_qualname() -> None:
    """A caller-supplied hook is inventoried without inventing a first-party name."""

    class _Other(AgentHook):
        pass

    bundle = long_lived_hooks(
        "sdk",
        external_hook(_Recording([], "external")),
        external_hook(_Other()),
        external_hook_factory(_factory([], "external")),
    )
    names = [declaration.name for declaration in bundle.declarations]
    package_id = extension_package_id(ExtensionSource.BUILTIN, "agent-hooks-sdk")

    # Distinct callables in one module never collapse onto one identity.
    assert len(set(names)) == 3
    assert names[0].startswith(f"{_Recording.__module__}._recording-".lower())
    assert names[2].startswith(f"{_factory.__module__}._factory".lower())
    for name in names:
        extension_component_id(package_id, ExtensionComponentKind.HOOK, name)

    # The identity is derived, so it is stable for a second instance of the same class.
    repeated = long_lived_hooks("sdk", external_hook(_Recording([], "again")))
    assert repeated.declarations[0].name == names[0]


def test_anonymous_and_malformed_hook_identities_are_refused() -> None:
    """An identity that cannot be validated fails at declaration, not at snapshot time."""
    with pytest.raises(ValueError):
        external_hook_factory(lambda _context: None)
    with pytest.raises(ValueError):
        registered_hook("Not Canonical", _Recording([], "x"))
    with pytest.raises(ValueError):
        registered_hook_factory("upper-Case", _factory([], "x"))
    with pytest.raises(ValueError):
        registered_hook("plain-callable", _factory([], "x"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        registered_hook_factory("not-callable", object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        long_lived_hooks("Gateway")


def test_duplicate_hook_identity_fails_visibly_instead_of_merging_rows() -> None:
    """Two hooks claiming one identity is an error, not a silently collapsed row."""
    events: list[str] = []
    with pytest.raises(ValueError) as excinfo:
        long_lived_hooks(
            "gateway",
            registered_hook("shared", _Recording(events, "a")),
            registered_hook_factory("shared", _factory(events, "b")),
        )

    assert "duplicate long-lived hook identity: shared" in str(excinfo.value)


def test_descriptors_carry_no_callable_bound_object_or_path(tmp_path: Path) -> None:
    """Only validated identity text reaches a descriptor."""
    hook = _Recording([], f"workspace {tmp_path}")
    bundle = long_lived_hooks("cli", registered_hook("recording", hook))
    package = HookExtensionAdapter(bundle).snapshot().packages[0]

    values = [
        package.id,
        package.name,
        package.display_name,
        package.description,
        package.revision or "",
        *(
            text
            for component in package.components
            for text in (
                component.id,
                component.name,
                component.display_name,
                component.description,
                component.revision or "",
                *component.capabilities,
            )
        ),
    ]
    for value in values:
        assert isinstance(value, str)
        assert str(tmp_path) not in value
        assert repr(hook) not in value
    assert not hasattr(package, "hook")


def test_inventory_set_equals_the_executed_hook_set() -> None:
    """The declared descriptors and the values handed to AgentLoop are one declaration."""
    events: list[str] = []
    bundle = _bundle(events)
    package = HookExtensionAdapter(bundle).snapshot().packages[0]

    declared_values = [
        declaration.hook if isinstance(declaration, RegisteredHook) else declaration.factory
        for declaration in bundle.declarations
    ]
    executed_values = [*bundle.hooks, *bundle.hook_factories]

    assert {id(value) for value in executed_values} == {id(value) for value in declared_values}
    assert len(package.components) == len(bundle.declarations)
    assert {component.name for component in package.components} == {
        declaration.name for declaration in bundle.declarations
    }
    assert [
        component.capabilities[0]
        for component in sorted(package.components, key=lambda c: c.name)
    ] == ["hook-factory", "hook"]


def test_hook_and_factory_lists_preserve_declaration_order() -> None:
    """Relative order within each list is what AgentLoop executes, so it is preserved."""
    events: list[str] = []
    bundle = long_lived_hooks(
        "gateway",
        registered_hook_factory("first-factory", _factory(events, "f1")),
        registered_hook("first-hook", _Recording(events, "h1")),
        registered_hook_factory("second-factory", _factory(events, "f2")),
        registered_hook("second-hook", _Recording(events, "h2")),
    )

    assert [type(value).__name__ for value in bundle.hooks] == ["_Recording", "_Recording"]
    assert len(bundle.hook_factories) == 2
    assert bundle.hooks[0] is bundle.declarations[1].hook  # type: ignore[union-attr]
    assert bundle.hook_factories[0] is bundle.declarations[0].factory  # type: ignore[union-attr]


async def test_declared_hooks_execute_in_the_unchanged_five_position_order() -> None:
    """Progress, registered factories, registered hooks, turn factories, turn hooks."""
    events: list[str] = []
    bundle = _bundle(events)

    async def on_stream(delta: str) -> None:
        events.append(f"progress:{delta}")

    hook = build_agent_turn_hook(
        AgentTurnHookSpec(
            on_stream=on_stream,
            registered_hook_factories=bundle.hook_factories,
            registered_hooks=bundle.hooks,
            turn_hook_factories=[_factory(events, "turn_factory")],
            turn_hooks=[_Recording(events, "turn")],
        )
    )

    # ``AgentProgressHook.before_iteration`` only logs, so the progress hook's own
    # position is observable through the streaming path.
    await hook.on_stream(AgentHookContext(iteration=3, messages=[]), "delta")
    assert events == [
        "progress:delta",
        "registered_factory:delta",
        "registered:delta",
        "turn_factory:delta",
        "turn:delta",
    ]

    events.clear()
    await hook.before_iteration(AgentHookContext(iteration=3, messages=[]))
    assert events == [
        "registered_factory:3",
        "registered:3",
        "turn_factory:3",
        "turn:3",
    ]


async def test_per_turn_and_progress_hooks_are_never_inventoried() -> None:
    """Runtime hook values stay out of the inventory even while they run in the same turn."""
    events: list[str] = []
    bundle = _bundle(events)
    package = HookExtensionAdapter(bundle).snapshot().packages[0]

    turn_hook = _Recording(events, "turn")
    chain = build_agent_turn_hook(
        AgentTurnHookSpec(
            on_progress=None,
            registered_hook_factories=bundle.hook_factories,
            registered_hooks=bundle.hooks,
            turn_hook_factories=[_factory(events, "subagent")],
            turn_hooks=[turn_hook],
        )
    )
    await chain.before_iteration(AgentHookContext(iteration=1, messages=[]))
    inventoried = {component.name for component in package.components}

    assert inventoried == {"mcp-readiness", "file-edit-activity"}
    assert "turn" not in inventoried
    assert "subagent" not in inventoried
    assert AgentProgressHook.__name__.lower() not in "".join(inventoried)
    assert events == ["registered_factory:1", "registered:1", "subagent:1", "turn:1"]


async def test_ephemeral_turns_suppress_registered_hooks_and_honor_the_opt_in() -> None:
    """Ephemeral suppression and its explicit opt-in are unchanged by inventory."""
    events: list[str] = []
    bundle = _bundle(events)

    suppressed = build_agent_turn_hook(
        AgentTurnHookSpec(
            registered_hook_factories=bundle.hook_factories,
            registered_hooks=bundle.hooks,
            ephemeral=True,
        )
    )
    await suppressed.before_iteration(AgentHookContext(iteration=1, messages=[]))
    assert events == []

    opted_in = build_agent_turn_hook(
        AgentTurnHookSpec(
            registered_hook_factories=bundle.hook_factories,
            registered_hooks=bundle.hooks,
            ephemeral=True,
            run_extra_hooks_for_ephemeral=True,
        )
    )
    await opted_in.before_iteration(AgentHookContext(iteration=2, messages=[]))
    assert events == ["registered_factory:2", "registered:2"]


async def test_one_failing_declared_factory_does_not_suppress_the_others() -> None:
    """Per-factory exception isolation is preserved for declared hooks."""
    events: list[str] = []

    def exploding(_context: AgentTurnHookContext) -> AgentHook:
        raise RuntimeError("factory failed")

    bundle = long_lived_hooks(
        "gateway",
        registered_hook_factory("exploding", exploding),
        registered_hook_factory("healthy", _factory(events, "healthy")),
        registered_hook("tail", _Recording(events, "tail")),
    )
    chain = build_agent_turn_hook(
        AgentTurnHookSpec(
            registered_hook_factories=bundle.hook_factories,
            registered_hooks=bundle.hooks,
        )
    )
    await chain.before_iteration(AgentHookContext(iteration=1, messages=[]))

    assert events == ["healthy:1", "tail:1"]
    assert len(HookExtensionAdapter(bundle).snapshot().packages[0].components) == 3


def test_agent_loop_and_runner_hold_no_registry_descriptor_or_adapter() -> None:
    """Option A keeps the inventory entirely outside the core loop."""
    loop_source = inspect.getsource(AgentLoop.__init__)
    runner_source = inspect.getsource(AgentRunner)

    for source in (loop_source, runner_source):
        assert "extension" not in source.lower()
        assert "LongLivedHooks" not in source
        assert "descriptor" not in source.lower()
    assert "extension" not in Path(inspect.getfile(AgentLoop)).read_text("utf-8").lower()
    assert "extension" not in Path(inspect.getfile(AgentRunner)).read_text("utf-8").lower()


def test_public_constructors_still_accept_plain_hooks_and_factories() -> None:
    """Callers that do not participate in inventory keep the original signature."""
    parameters = inspect.signature(AgentLoop.__init__).parameters

    assert parameters["hooks"].default is None
    assert parameters["hook_factories"].default is None
    assert parameters["hooks"].annotation == "list[AgentHook] | None"
    assert parameters["hook_factories"].annotation == "list[AgentTurnHookFactory] | None"

    events: list[str] = []
    spec = AgentTurnHookSpec(
        registered_hooks=[_Recording(events, "plain")],
        registered_hook_factories=[_factory(events, "plain_factory")],
    )
    assert isinstance(build_agent_turn_hook(spec), AgentHook)


async def test_inspect_is_the_only_action_the_hook_adapter_executes() -> None:
    """Any other action, and any non-administrator, is refused."""
    adapter = HookExtensionAdapter(_bundle([]))
    target = extension_component_id(
        _GATEWAY_PACKAGE, ExtensionComponentKind.HOOK, "mcp-readiness"
    )

    inspected = await adapter.execute(
        ExtensionActionRequest(context=_ADMIN, target_id=target, action=ExtensionAction.INSPECT)
    )
    assert inspected.ok is True
    assert inspected.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
    assert inspected.package_id == _GATEWAY_PACKAGE

    for action in (
        ExtensionAction.ENABLE,
        ExtensionAction.DISABLE,
        ExtensionAction.RELOAD,
        ExtensionAction.RECONNECT,
        ExtensionAction.CONFIGURE,
        ExtensionAction.UNINSTALL,
    ):
        refused = await adapter.execute(
            ExtensionActionRequest(context=_ADMIN, target_id=target, action=action)
        )
        assert refused.ok is False

    denied = await adapter.execute(
        ExtensionActionRequest(context=_USER, target_id=target, action=ExtensionAction.INSPECT)
    )
    assert denied.ok is False


def test_a_bundle_built_around_the_helper_fails_alone_at_snapshot_time(
    tmp_path: Path,
) -> None:
    """The one snapshot-time hook failure is contained; the other seven adapters survive.

    ``long_lived_hooks`` normally rejects a malformed identity at composition time, which
    is the right handling for a first-party programming error. Constructing
    ``LongLivedHooks`` directly bypasses that gate, so the failure surfaces during
    ``snapshot()`` instead — where the registry's adapter isolation must contain it.
    """
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    smuggled = LongLivedHooks(
        composition="gateway",
        declarations=(RegisteredHook(name="Not A Canonical Name", hook=_Recording([], "x")),),
    )

    with pytest.raises(ValueError):
        HookExtensionAdapter(smuggled).snapshot()

    broken = build_core_extension_registry(config, ToolRegistry(), hooks=smuggled).snapshot()
    healthy = build_core_extension_registry(config, ToolRegistry()).snapshot()

    assert [(d.owner_id, d.code) for d in broken.diagnostics] == [
        ("agent-hooks", "adapter_snapshot_failed")
    ]
    assert not [p for p in broken.packages if p.name.startswith("agent-hooks")]
    # Every package from the other seven adapters is byte-for-byte intact.
    assert broken.packages == healthy.packages
    assert healthy.diagnostics == ()


def test_declaration_values_are_reachable_only_through_the_bundle() -> None:
    """`RegisteredHook`/`RegisteredHookFactory` are the only carriers of a hook value."""
    events: list[str] = []
    hook = _Recording(events, "h")
    declaration = registered_hook("h", hook)
    factory = _factory(events, "f")
    factory_declaration = registered_hook_factory("f", factory)

    assert isinstance(declaration, RegisteredHook)
    assert declaration.hook is hook
    assert isinstance(factory_declaration, RegisteredHookFactory)
    assert factory_declaration.factory is factory
    with pytest.raises(AttributeError):
        declaration.name = "other"  # type: ignore[misc]
