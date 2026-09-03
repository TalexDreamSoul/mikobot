"""Contracts for component revisions and package-install action policy."""

from __future__ import annotations

import pytest

from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
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
from nanobot.extensions.registry import ExtensionRegistry

_INSPECT = ExtensionAction.INSPECT
_TOOL = ExtensionComponentKind.TOOL


class _PolicyObservingAdapter:
    name = "component-contract"

    def __init__(
        self,
        snapshot: ExtensionAdapterSnapshot,
        *,
        mutate_policy: bool = False,
        mutate_pairing: bool = False,
    ) -> None:
        self._snapshot = snapshot
        self._mutate_policy = mutate_policy
        self._mutate_pairing = mutate_pairing
        self.observed_policies: list[bool] = []
        self.observed_pairing_states: list[bool] = []

    def snapshot(self) -> ExtensionAdapterSnapshot:
        return self._snapshot

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        self.observed_policies.append(request.context.package_install_allowed)
        self.observed_pairing_states.append(request.context.channel_pairing_completed)
        if self._mutate_policy:
            object.__setattr__(request.context, "package_install_allowed", False)
        if self._mutate_pairing:
            object.__setattr__(request.context, "channel_pairing_completed", False)
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=request.target_id.split("/", maxsplit=1)[0],
            target_id=request.target_id,
            lifecycle=ExtensionLifecycle.DISCOVERED,
            message="inspected",
        )


def _component(
    package_id: str,
    name: str,
    *,
    revision: str | None = None,
) -> ExtensionComponentDescriptor:
    return ExtensionComponentDescriptor(
        id=extension_component_id(package_id, _TOOL, name),
        package_id=package_id,
        kind=_TOOL,
        name=name,
        display_name=f"{name} tool",
        revision=revision,
        actions=frozenset({_INSPECT}),
    )


def _package(*components: ExtensionComponentDescriptor) -> ExtensionPackageDescriptor:
    package_id = extension_package_id(ExtensionSource.BUILTIN, "component-contract")
    return ExtensionPackageDescriptor(
        id=package_id,
        name="component-contract",
        display_name="Component contract",
        source=ExtensionSource.BUILTIN,
        trust=ExtensionTrust.FIRST_PARTY,
        execution=ExtensionExecution.DATA,
        lifecycle=ExtensionLifecycle.DISCOVERED,
        actions=frozenset({_INSPECT}),
        components=components,
    )


@pytest.mark.parametrize(
    "revision",
    [
        pytest.param(1, id="integer"),
        pytest.param(["revision"], id="mutable-list"),
        pytest.param("/private/revision", id="absolute-path"),
        pytest.param("revision\x00with-control", id="control-character"),
    ],
)
def test_component_revision_rejects_nonpublic_values(revision: object) -> None:
    """A component revision is safe public text, never arbitrary or mutable caller data."""
    package = _package()

    with pytest.raises(ValueError):
        _component(package.id, "inspect", revision=revision)  # type: ignore[arg-type]


def test_registry_retains_each_component_revision_in_an_immutable_target_snapshot() -> None:
    """A later adapter mutation cannot rewrite the revision published for either exact target."""
    package_id = extension_package_id(ExtensionSource.BUILTIN, "component-contract")
    alpha = _component(package_id, "alpha", revision="alpha-revision")
    beta = _component(package_id, "beta", revision="beta-revision")
    adapter_snapshot = ExtensionAdapterSnapshot(
        adapter_name=_PolicyObservingAdapter.name,
        packages=(_package(alpha, beta),),
    )
    registry = ExtensionRegistry()
    registry.register(_PolicyObservingAdapter(adapter_snapshot))

    published = registry.snapshot().packages[0]
    published_revisions = {component.id: component.revision for component in published.components}
    object.__setattr__(alpha, "revision", "changed-after-publication")

    assert published_revisions == {
        alpha.id: "alpha-revision",
        beta.id: "beta-revision",
    }
    assert published.components[0].revision == "alpha-revision"


@pytest.mark.parametrize(
    "package_install_allowed",
    [
        pytest.param(0, id="integer-zero"),
        pytest.param(1, id="integer-one"),
        pytest.param("true", id="text"),
        pytest.param(None, id="none"),
    ],
)
def test_action_context_rejects_non_boolean_package_install_policy(
    package_install_allowed: object,
) -> None:
    """Install authority is an explicit boolean security fact, not a truthy caller value."""
    with pytest.raises(ValueError):
        ExtensionActionContext(
            actor_id="operator",
            is_system_admin=True,
            package_install_allowed=package_install_allowed,  # type: ignore[arg-type]
        )

@pytest.mark.parametrize(
    "channel_pairing_completed",
    [
        pytest.param(0, id="integer-zero"),
        pytest.param(1, id="integer-one"),
        pytest.param("complete", id="text"),
        pytest.param(None, id="none"),
    ],
)
def test_action_context_rejects_non_boolean_channel_pairing_authority(
    channel_pairing_completed: object,
) -> None:
    """Pairing authority is an explicit boolean server fact, not a truthy caller value."""
    with pytest.raises(ValueError):
        ExtensionActionContext(
            actor_id="operator",
            is_system_admin=True,
            channel_pairing_completed=channel_pairing_completed,  # type: ignore[arg-type]
        )


def test_action_context_defaults_authority_facts_to_denied() -> None:
    """Absent installation and pairing authority must not grant adapter permissions."""
    context = ExtensionActionContext(actor_id="operator", is_system_admin=True)

    assert context.package_install_allowed is False
    assert context.channel_pairing_completed is False

@pytest.mark.asyncio
async def test_registry_passes_defensive_copies_of_authority_facts_to_adapter() -> None:
    """An adapter cannot alter the caller's explicit authority while handling its action."""
    package_id = extension_package_id(ExtensionSource.BUILTIN, "component-contract")
    component = _component(package_id, "inspect", revision="inspect-revision")
    adapter = _PolicyObservingAdapter(
        ExtensionAdapterSnapshot(
            adapter_name=_PolicyObservingAdapter.name,
            packages=(_package(component),),
        ),
        mutate_policy=True,
        mutate_pairing=True,
    )
    registry = ExtensionRegistry()
    registry.register(adapter)
    request = ExtensionActionRequest(
        context=ExtensionActionContext(
            actor_id="operator",
            is_system_admin=True,
            package_install_allowed=True,
            channel_pairing_completed=True,
        ),
        target_id=component.id,
        action=_INSPECT,
    )

    result = await registry.execute(request)

    assert result.ok is True
    assert adapter.observed_policies == [True]
    assert adapter.observed_pairing_states == [True]
    assert request.context.package_install_allowed is True
    assert request.context.channel_pairing_completed is True
