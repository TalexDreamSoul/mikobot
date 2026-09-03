from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from typing import Callable

import pytest

from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
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
    ExtensionSnapshot,
    ExtensionSource,
    ExtensionTrust,
    extension_component_id,
    extension_package_id,
    requires_risk_acknowledgement,
    safe_extension_message,
)
from nanobot.extensions.registry import ExtensionRegistry, ExtensionRegistryError

_BUILTIN = ExtensionSource("builtin")
_WORKSPACE = ExtensionSource("workspace")
_FIRST_PARTY = ExtensionTrust("first_party")
_WORKSPACE_CONTENT = ExtensionTrust("workspace_content")
_DATA = ExtensionExecution("data")
_IN_PROCESS = ExtensionExecution("in_process")
_CHILD_PROCESS = ExtensionExecution("child_process")
_DISCOVERED = ExtensionLifecycle("discovered")
_ENABLED = ExtensionLifecycle("enabled")
_ENABLE = ExtensionAction("enable")
_DISABLE = ExtensionAction("disable")
_INSTALL = ExtensionAction("install")
_TOOL = ExtensionComponentKind("tool")


class _Adapter:
    def __init__(
        self,
        name: str,
        snapshot: ExtensionAdapterSnapshot | object,
        result: ExtensionActionResult | Callable[[ExtensionActionRequest], ExtensionActionResult] | None = None,
    ) -> None:
        self._name = name
        self._snapshot = snapshot
        self._result = result
        self.requests: list[ExtensionActionRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def snapshot(self) -> ExtensionAdapterSnapshot:
        if isinstance(self._snapshot, Exception):
            raise self._snapshot
        return self._snapshot  # type: ignore[return-value]

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult:
        self.requests.append(request)
        if callable(self._result):
            return self._result(request)
        if self._result is not None:
            return self._result
        return ExtensionActionResult(
            ok=True,
            action=request.action,
            package_id=_package_id_from_target(request.target_id),
            target_id=request.target_id,
            lifecycle=_ENABLED,
            message="applied",
        )


class _CountingValues:
    def __init__(self, value: object) -> None:
        self._value = value
        self.pulls = 0

    def __iter__(self) -> Iterator[object]:
        while True:
            self.pulls += 1
            yield self._value


def _package_id_from_target(target_id: str) -> str:
    return target_id.split("/", maxsplit=1)[0]


def _component(
    package_id: str,
    name: str = "operate",
    *,
    actions: frozenset[ExtensionAction] = frozenset({_ENABLE}),
) -> ExtensionComponentDescriptor:
    return ExtensionComponentDescriptor(
        id=extension_component_id(package_id, _TOOL, name),
        package_id=package_id,
        kind=_TOOL,
        name=name,
        display_name=f"{name} tool",
        actions=actions,
    )


def _package(
    name: str,
    *,
    source: ExtensionSource = _BUILTIN,
    trust: ExtensionTrust = _FIRST_PARTY,
    execution: ExtensionExecution = _IN_PROCESS,
    lifecycle: ExtensionLifecycle = _DISCOVERED,
    actions: frozenset[ExtensionAction] = frozenset({_ENABLE}),
    components: tuple[ExtensionComponentDescriptor, ...] = (),
    revision: str | None = None,
    isolated: bool | None = None,
    permissions: tuple[str, ...] = (),
    permissions_enforced: bool = False,
) -> ExtensionPackageDescriptor:
    package_id = extension_package_id(source, name)
    return ExtensionPackageDescriptor(
        id=package_id,
        name=name,
        display_name=f"{name} extension",
        source=source,
        trust=trust,
        execution=execution,
        lifecycle=lifecycle,
        revision=revision,
        isolated=isolated,
        permissions=permissions,
        permissions_enforced=permissions_enforced,
        actions=actions,
        components=components,
    )


def _snapshot(adapter_name: str, *packages: ExtensionPackageDescriptor) -> ExtensionAdapterSnapshot:
    return ExtensionAdapterSnapshot(adapter_name=adapter_name, packages=packages)


def _request(
    target_id: str,
    action: ExtensionAction = _ENABLE,
    *,
    is_system_admin: bool = True,
    expected_revision: str | None = None,
    risk_acknowledged: bool = False,
    values: dict[str, object] | None = None,
) -> ExtensionActionRequest:
    return ExtensionActionRequest(
        context=ExtensionActionContext(actor_id="operator", is_system_admin=is_system_admin),
        target_id=target_id,
        action=action,
        expected_revision=expected_revision,
        risk_acknowledged=risk_acknowledged,
        values={} if values is None else values,
    )


def test_public_enums_are_closed_json_stable_vocabularies() -> None:
    assert {member.value for member in ExtensionSource} == {
        "builtin",
        "agent_plugin",
        "python_entry_point",
        "workspace",
        "configured",
        "channel_package",
        "provider_registry",
        "cli_app",
        "optional_feature",
    }
    assert {member.value for member in ExtensionComponentKind} == {
        "skill",
        "mcp_server",
        "tool",
        "channel",
        "llm_provider",
        "image_provider",
        "transcription_provider",
        "hook",
        "cli_app",
        "optional_feature",
    }
    assert {member.value for member in ExtensionTrust} == {
        "first_party",
        "operator_trusted",
        "workspace_content",
        "remote_service",
    }
    assert {member.value for member in ExtensionExecution} == {
        "data",
        "in_process",
        "child_process",
        "remote",
    }
    assert {member.value for member in ExtensionLifecycle} == {
        "discovered",
        "unavailable",
        "disabled",
        "enabling",
        "enabled",
        "reloading",
        "disabling",
        "failed",
        "changed",
        "restart_required",
    }
    assert {member.value for member in ExtensionAction} == {
        "inspect",
        "configure",
        "enable",
        "disable",
        "reload",
        "reconnect",
        "install",
        "uninstall",
        "restart_required",
    }
    assert isinstance(_BUILTIN, str)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a" * 129,
        "Uppercase",
        "two words",
        "line\nbreak",
        "nul\x00byte",
        "café",
        "../escape",
        "a/b",
        "a\\b",
        "a..b",
        "-leading",
        "trailing-",
    ],
)
def test_canonical_id_helpers_reject_ambiguous_name_segments(name: str) -> None:
    with pytest.raises(ValueError):
        extension_package_id(_BUILTIN, name)

    with pytest.raises(ValueError):
        extension_component_id("ext:builtin:package", _TOOL, name)


def test_canonical_id_helpers_preserve_valid_caller_name_exactly() -> None:
    package_id = extension_package_id(_WORKSPACE, "demo.plugin-1")

    assert package_id == "ext:workspace:demo.plugin-1"
    assert extension_component_id(package_id, _TOOL, "run-task") == (
        "ext:workspace:demo.plugin-1/tool:run-task"
    )

    with pytest.raises(ValueError):
        extension_package_id(_WORKSPACE, "Demo.Plugin-1")


@pytest.mark.parametrize(
    ("trust", "execution", "expected"),
    [
        (_FIRST_PARTY, _IN_PROCESS, False),
        (_WORKSPACE_CONTENT, _DATA, False),
        (_WORKSPACE_CONTENT, _IN_PROCESS, True),
        (_WORKSPACE_CONTENT, _CHILD_PROCESS, True),
    ],
)
def test_risk_acknowledgement_tracks_untrusted_executable_packages(
    trust: ExtensionTrust,
    execution: ExtensionExecution,
    expected: bool,
) -> None:
    assert requires_risk_acknowledgement(_package("risk-check", trust=trust, execution=execution)) is expected


def test_configuration_and_action_context_reject_noncanonical_values() -> None:
    with pytest.raises(ValueError):
        ExtensionConfigurationTarget(section="bad section")

    with pytest.raises(ValueError):
        ExtensionActionContext(actor_id="operator", is_system_admin=1)  # type: ignore[arg-type]


def test_descriptors_and_request_values_do_not_retain_mutable_caller_data() -> None:
    package_id = extension_package_id(_BUILTIN, "immutable")
    capabilities = ["write", "read"]
    permissions = ["network"]
    component_actions = {_ENABLE}
    component = ExtensionComponentDescriptor(
        id=extension_component_id(package_id, _TOOL, "operate"),
        package_id=package_id,
        kind=_TOOL,
        name="operate",
        display_name="Operate",
        capabilities=capabilities,  # type: ignore[arg-type]
        actions=component_actions,
    )
    descriptor = _package(
        "immutable",
        permissions=permissions,  # type: ignore[arg-type]
        actions={_ENABLE},  # type: ignore[arg-type]
        components=[component],  # type: ignore[arg-type]
    )
    request_values: dict[str, object] = {"nested": {"items": ["safe"]}}
    request = _request(descriptor.id, values=request_values)

    capabilities.append("admin")
    permissions.append("filesystem")
    component_actions.add(_DISABLE)
    request_values["nested"] = {"items": ["unsafe"]}

    assert descriptor.components[0].capabilities == ("read", "write")
    assert descriptor.permissions == ("network",)
    assert descriptor.components[0].actions == frozenset({_ENABLE})
    assert request.values["nested"] == {"items": ("safe",)}
    with pytest.raises((AttributeError, TypeError)):
        request.values["nested"]["items"].append("unsafe")  # type: ignore[index,union-attr]
    with pytest.raises((FrozenInstanceError, AttributeError)):
        descriptor.display_name = "changed"  # type: ignore[misc]


def test_package_rejects_component_owner_derived_id_and_duplicate_mismatches() -> None:
    package_id = extension_package_id(_BUILTIN, "owner")
    component = _component(package_id)

    with pytest.raises(ValueError):
        _package(
            "owner",
            components=(
                ExtensionComponentDescriptor(
                    id=component.id,
                    package_id=extension_package_id(_BUILTIN, "other"),
                    kind=component.kind,
                    name=component.name,
                    display_name=component.display_name,
                ),
            ),
        )

    with pytest.raises(ValueError):
        _package(
            "owner",
            components=(
                ExtensionComponentDescriptor(
                    id=extension_component_id(package_id, _TOOL, "different"),
                    package_id=package_id,
                    kind=_TOOL,
                    name="operate",
                    display_name="Operate",
                ),
            ),
        )

    with pytest.raises(ValueError):
        _package("owner", components=(component, component))


def test_warning_only_safety_contract_rejects_unsupported_enforcement_claims() -> None:
    with pytest.raises(ValueError):
        _package(
            "claimed-isolation",
            trust=_WORKSPACE_CONTENT,
            execution=_CHILD_PROCESS,
            isolated=True,
        )

    with pytest.raises(ValueError):
        _package(
            "claimed-permission-enforcement",
            trust=_WORKSPACE_CONTENT,
            permissions_enforced=True,
        )


def test_registration_rejects_duplicate_names_without_replacing_original_adapter() -> None:
    registry = ExtensionRegistry()
    original = _Adapter("same-name", _snapshot("same-name", _package("original")))
    duplicate = _Adapter("same-name", _snapshot("same-name", _package("replacement")))
    registry.register(original)

    with pytest.raises(ExtensionRegistryError):
        registry.register(duplicate)

    assert [package.id for package in registry.snapshot().packages] == [
        extension_package_id(_BUILTIN, "original")
    ]


def test_disposer_removes_only_its_own_registration_token() -> None:
    registry = ExtensionRegistry()
    first = _Adapter("replaceable", _snapshot("replaceable", _package("first")))
    first_dispose = registry.register(first)
    first_dispose()
    replacement = _Adapter("replaceable", _snapshot("replaceable", _package("replacement")))
    replacement_dispose = registry.register(replacement)

    first_dispose()

    assert [package.name for package in registry.snapshot().packages] == ["replacement"]
    replacement_dispose()
    replacement_dispose()
    assert registry.snapshot().packages == ()


def test_snapshot_sorting_and_collision_attribution_ignore_registration_order() -> None:
    shared = _package("shared")
    alpha = _Adapter("alpha", _snapshot("alpha", _package("alpha"), shared))
    zeta = _Adapter("zeta", _snapshot("zeta", _package("zeta"), shared))

    def collect(adapters: tuple[_Adapter, _Adapter]) -> tuple[list[str], list[str]]:
        registry = ExtensionRegistry()
        for adapter in adapters:
            registry.register(adapter)
        snapshot = registry.snapshot()
        return (
            [package.id for package in snapshot.packages],
            [diagnostic.owner_id for diagnostic in snapshot.diagnostics],
        )

    forward = collect((zeta, alpha))
    reverse = collect((alpha, zeta))

    assert forward == reverse
    assert forward == (
        [extension_package_id(_BUILTIN, name) for name in ("alpha", "shared")],
        ["zeta"],
    )


def test_bad_adapter_snapshot_is_atomic_and_diagnostics_are_safe() -> None:
    registry = ExtensionRegistry()
    good = _Adapter("good", _snapshot("good", _package("available")))
    unsafe_message = "/Users/alice/private\n\x00" + "x" * 1_200
    bad = _Adapter("broken", RuntimeError(unsafe_message))
    registry.register(good)
    registry.register(bad)

    snapshot = registry.snapshot()

    assert [package.name for package in snapshot.packages] == ["available"]
    assert len(snapshot.diagnostics) == 1
    diagnostic = snapshot.diagnostics[0]
    assert diagnostic.owner_id == "broken"
    assert len(diagnostic.message) <= 1_000
    assert "/Users/alice/private" not in diagnostic.message
    assert "\n" not in diagnostic.message
    assert "\x00" not in diagnostic.message


def test_snapshot_rejects_malformed_owner_tree_without_leaking_partial_output() -> None:
    registry = ExtensionRegistry()
    valid = _package("valid")
    malformed = _package("malformed")
    component = _component(malformed.id)
    object.__setattr__(component, "package_id", valid.id)
    object.__setattr__(malformed, "components", (component,))
    registry.register(_Adapter("healthy", _snapshot("healthy", valid)))
    registry.register(_Adapter("malformed", _snapshot("malformed", malformed)))

    snapshot = registry.snapshot()

    assert [package.name for package in snapshot.packages] == ["valid"]
    assert [diagnostic.owner_id for diagnostic in snapshot.diagnostics] == ["malformed"]


def test_safe_extension_message_removes_controls_paths_and_excess_length() -> None:
    safe = safe_extension_message("C:\\Users\\alice\\secret\n\x00" + "x" * 1_200)

    assert len(safe) <= 1_000
    assert "C:\\Users\\alice\\secret" not in safe
    assert "\n" not in safe
    assert "\x00" not in safe


@pytest.mark.asyncio
async def test_execute_rejects_unauthorized_unsupported_missing_and_malformed_requests_before_callback() -> None:
    package = _package("actions", actions=frozenset({_DISABLE}))
    adapter = _Adapter("actions", _snapshot("actions", package))
    registry = ExtensionRegistry()
    registry.register(adapter)
    requests = (
        _request(package.id, _DISABLE, is_system_admin=False),
        _request(package.id, _ENABLE),
        _request(extension_component_id(package.id, _TOOL, "missing"), _DISABLE),
    )
    malformed = _request(package.id, _DISABLE)
    object.__setattr__(malformed, "values", {"invalid": object()})

    for request in (*requests, malformed):
        with pytest.raises(ExtensionRegistryError):
            await registry.execute(request)

    assert adapter.requests == []


@pytest.mark.asyncio
async def test_external_enable_and_install_require_current_revision_and_acknowledgement() -> None:
    package = _package(
        "external",
        source=_WORKSPACE,
        trust=_WORKSPACE_CONTENT,
        execution=_CHILD_PROCESS,
        revision="revision-7",
        actions=frozenset({_ENABLE, _INSTALL}),
    )
    adapter = _Adapter("external", _snapshot("external", package))
    registry = ExtensionRegistry()
    registry.register(adapter)

    for action in (_ENABLE, _INSTALL):
        for request in (
            _request(package.id, action),
            _request(package.id, action, risk_acknowledged=True),
            _request(
                package.id,
                action,
                expected_revision="stale",
                risk_acknowledged=True,
            ),
        ):
            with pytest.raises(ExtensionRegistryError):
                await registry.execute(request)

        result = await registry.execute(
            _request(
                package.id,
                action,
                expected_revision="revision-7",
                risk_acknowledged=True,
            )
        )
        assert result.ok is True

    unrevisioned = _package(
        "unrevisioned",
        source=_WORKSPACE,
        trust=_WORKSPACE_CONTENT,
        execution=_CHILD_PROCESS,
    )
    unrevisioned_adapter = _Adapter("unrevisioned", _snapshot("unrevisioned", unrevisioned))
    unrevisioned_registry = ExtensionRegistry()
    unrevisioned_registry.register(unrevisioned_adapter)
    with pytest.raises(ExtensionRegistryError):
        await unrevisioned_registry.execute(_request(unrevisioned.id, risk_acknowledged=True))

    assert [request.action for request in adapter.requests] == [_ENABLE, _INSTALL]
    assert [request.expected_revision for request in adapter.requests] == ["revision-7", "revision-7"]
    assert unrevisioned_adapter.requests == []


@pytest.mark.asyncio
async def test_execute_dispatches_only_to_the_exact_owner_and_normalizes_result_message() -> None:
    first = _package("first")
    second = _package("second")
    target = _component(second.id, "operate")
    second = _package("second", actions=frozenset(), components=(target,))
    untrusted_result = ExtensionActionResult(
        ok=True,
        action=_ENABLE,
        package_id=second.id,
        target_id=target.id,
        lifecycle=_ENABLED,
        message="applied",
    )
    object.__setattr__(untrusted_result, "message", "/Users/alice/private\n" + "x" * 1_200)
    first_adapter = _Adapter("first", _snapshot("first", first))
    second_adapter = _Adapter(
        "second",
        _snapshot("second", second),
        untrusted_result,
    )
    registry = ExtensionRegistry()
    registry.register(first_adapter)
    registry.register(second_adapter)

    result = await registry.execute(_request(target.id))

    assert first_adapter.requests == []
    assert [request.target_id for request in second_adapter.requests] == [target.id]
    assert result.target_id == target.id
    assert len(result.message) <= 1_000
    assert "/Users/alice/private" not in result.message
    assert "\n" not in result.message


@pytest.mark.asyncio
async def test_execute_rejects_adapter_result_for_another_target() -> None:
    package = _package("owner")
    other = _package("other")
    adapter = _Adapter(
        "owner",
        _snapshot("owner", package),
        ExtensionActionResult(
            ok=True,
            action=_ENABLE,
            package_id=other.id,
            target_id=other.id,
            lifecycle=_ENABLED,
            message="wrong owner",
        ),
    )
    registry = ExtensionRegistry()
    registry.register(adapter)

    with pytest.raises(ExtensionRegistryError):
        await registry.execute(_request(package.id))

    assert [request.target_id for request in adapter.requests] == [package.id]


def test_adapter_snapshot_diagnostic_is_an_immutable_value_object() -> None:
    diagnostic = ExtensionDiagnostic(owner_id="adapter", code="broken", message="safe")
    snapshot = ExtensionAdapterSnapshot(adapter_name="adapter", diagnostics=(diagnostic,))

    assert snapshot.diagnostics == (diagnostic,)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        diagnostic.message = "changed"  # type: ignore[misc]


def test_bounded_constructor_consumption_stops_after_first_over_limit_value() -> None:
    permission_values = _CountingValues("network")
    capability_values = _CountingValues("network")
    action_values = _CountingValues(_ENABLE)
    package_id = extension_package_id(_BUILTIN, "bounded-components")
    component_values = _CountingValues(_component(package_id))
    package_values = _CountingValues(_package("bounded-snapshot"))
    diagnostic_values = _CountingValues(
        ExtensionDiagnostic(owner_id="bounded-diagnostics", code="notice", message="safe")
    )
    registry_package_values = _CountingValues(_package("bounded-registry-snapshot"))
    registry_diagnostic_values = _CountingValues(
        ExtensionDiagnostic(owner_id="bounded-registry", code="notice", message="safe")
    )

    with pytest.raises(ValueError):
        _package("bounded-permissions", permissions=permission_values)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExtensionComponentDescriptor(
            id=extension_component_id(package_id, _TOOL, "bounded-capabilities"),
            package_id=package_id,
            kind=_TOOL,
            name="bounded-capabilities",
            display_name="Bounded capabilities",
            capabilities=capability_values,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        _package("bounded-actions", actions=action_values)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _package("bounded-components", components=component_values)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _request(
            extension_package_id(_BUILTIN, "bounded-actions"),
            values={"items": ["safe"] * 2_000},
        )
    with pytest.raises(ValueError):
        ExtensionAdapterSnapshot(adapter_name="bounded-packages", packages=package_values)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExtensionAdapterSnapshot(
            adapter_name="bounded-diagnostics",
            diagnostics=diagnostic_values,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        ExtensionSnapshot(packages=registry_package_values)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExtensionSnapshot(
            packages=(),
            diagnostics=registry_diagnostic_values,  # type: ignore[arg-type]
        )

    assert permission_values.pulls == 129
    assert capability_values.pulls == 257
    assert action_values.pulls == len(ExtensionAction) + 1
    assert component_values.pulls == 513
    assert package_values.pulls == 1_025
    assert diagnostic_values.pulls == 257
    assert registry_package_values.pulls == 4_097
    assert registry_diagnostic_values.pulls == 4_097


def test_registration_limit_rejects_only_the_257th_adapter() -> None:
    registry = ExtensionRegistry()
    for index in range(256):
        name = f"adapter-{index}"
        package = _package(f"package-{index}")
        registry.register(_Adapter(name, _snapshot(name, package)))

    overflow = _Adapter("adapter-overflow", _snapshot("adapter-overflow", _package("overflow")))
    with pytest.raises(ExtensionRegistryError):
        registry.register(overflow)

    snapshot = registry.snapshot()
    assert [package.name for package in snapshot.packages] == sorted(
        f"package-{index}" for index in range(256)
    )
    assert snapshot.diagnostics == ()


def test_oversized_adapter_snapshot_is_atomic_and_keeps_unrelated_packages() -> None:
    oversized_snapshot = _snapshot("oversized")
    object.__setattr__(oversized_snapshot, "packages", _CountingValues(_package("oversized-package")))
    registry = ExtensionRegistry()
    registry.register(_Adapter("healthy", _snapshot("healthy", _package("healthy"))))
    registry.register(_Adapter("oversized", oversized_snapshot))

    snapshot = registry.snapshot()

    assert [package.name for package in snapshot.packages] == ["healthy"]
    assert [diagnostic.owner_id for diagnostic in snapshot.diagnostics] == ["oversized"]


@pytest.mark.parametrize(
    "absolute_path",
    [
        "/private/alice/project",
        r"C:\\Users\alice\project",
        "~/private/project",
        "~alice/private project",
        '"/Users/alice/private project"',
        '"C:\\Users\alice\\private project"',
    ],
)
def test_snapshot_descriptor_metadata_rejects_absolute_host_paths(absolute_path: str) -> None:
    package_id = extension_package_id(_BUILTIN, "metadata-paths")
    package_fields: dict[str, object] = {
        "id": package_id,
        "name": "metadata-paths",
        "display_name": "Metadata paths",
        "source": _BUILTIN,
        "trust": _FIRST_PARTY,
        "execution": _IN_PROCESS,
        "lifecycle": _DISCOVERED,
    }
    component_fields: dict[str, object] = {
        "id": extension_component_id(package_id, _TOOL, "metadata-paths"),
        "package_id": package_id,
        "kind": _TOOL,
        "name": "metadata-paths",
        "display_name": "Metadata paths",
    }

    for overrides in (
        {"display_name": absolute_path},
        {"description": absolute_path},
        {"version": absolute_path},
        {"revision": absolute_path},
        {"permissions": (absolute_path,)},
    ):
        with pytest.raises(ValueError):
            ExtensionPackageDescriptor(**(package_fields | overrides))  # type: ignore[arg-type]

    for overrides in (
        {"display_name": absolute_path},
        {"description": absolute_path},
        {"capabilities": (absolute_path,)},
    ):
        with pytest.raises(ValueError):
            ExtensionComponentDescriptor(**(component_fields | overrides))  # type: ignore[arg-type]


def test_snapshot_descriptor_metadata_accepts_normal_prose_and_urls() -> None:
    package_id = extension_package_id(_BUILTIN, "web-metadata")
    component = ExtensionComponentDescriptor(
        id=extension_component_id(package_id, _TOOL, "web-metadata"),
        package_id=package_id,
        kind=_TOOL,
        name="web-metadata",
        display_name="Web metadata https://example.test/components",
        description="Configure through https://example.test/docs.",
        capabilities=("https://example.test/capability",),
    )

    package = ExtensionPackageDescriptor(
        id=package_id,
        name="web-metadata",
        display_name="Web metadata https://example.test",
        source=_BUILTIN,
        trust=_FIRST_PARTY,
        execution=_IN_PROCESS,
        lifecycle=_DISCOVERED,
        description="Managed at https://example.test/docs.",
        version="https://example.test/releases/1",
        revision="https://example.test/revisions/1",
        permissions=("https://example.test/permission",),
        components=(component,),
    )

    assert package.description == "Managed at https://example.test/docs."
    assert package.components[0].capabilities == ("https://example.test/capability",)


def test_safe_diagnostics_redact_full_quoted_paths_but_preserve_urls() -> None:
    message = safe_extension_message(
        'failed at "/Users/alice/quoted secret/config.toml", '
        '"C:\\Users\alice\\windows secret\run.py", and ~alice/tilde project; '
        "details: https://example.test/extensions?source=workspace"
    )

    assert "/Users/alice/quoted secret/config.toml" not in message
    assert "quoted secret" not in message
    assert "C:\\Users\alice\\windows secret\run.py" not in message
    assert "windows secret" not in message
    assert "~alice/tilde" not in message
    assert "https://example.test/extensions?source=workspace" in message
    assert "<path>" in message
