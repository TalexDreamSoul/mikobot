"""Behavioral coverage for standalone optional-feature inspect and install actions."""
from __future__ import annotations

from collections.abc import Callable

import pytest

from nanobot.channels.plugin import ChannelPlugin
from nanobot.extensions.adapters.optional_features import (
    OptionalFeatureExtensionAdapter,
    OptionalFeatureExtensionServices,
)
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionLifecycle,
)
from nanobot.optional_features import InstallResult

Requirements = list[str] | None
Installer = Callable[[str, Requirements], InstallResult]
Installed = Callable[[str, Requirements], bool]
RequiresRestart = Callable[[str], bool]


def _channel_plugin(name: str) -> ChannelPlugin:
    return ChannelPlugin(
        name=name,
        display_name=name.replace("_", " ").title(),
        runtime="nanobot.channels.fake:Channel",
    )


def _adapter(
    *,
    installed: Installed,
    installer: Installer | None,
    requires_restart: RequiresRestart | None = None,
    groups: dict[str, Requirements] | None = None,
    channels: dict[str, ChannelPlugin] | None = None,
) -> OptionalFeatureExtensionAdapter:
    services = (
        None
        if installer is None
        else OptionalFeatureExtensionServices(
            install=installer,
            requires_restart=(lambda _name: False) if requires_restart is None else requires_restart,
        )
    )
    return OptionalFeatureExtensionAdapter(
        groups_loader=lambda: {"catalog": ["catalog-sdk>=1"]} if groups is None else groups,
        channel_loader=lambda: {} if channels is None else channels,
        installed=installed,
        services=services,
    )


def _request(
    target_id: str,
    action: ExtensionAction,
    *,
    expected_revision: str | None,
    is_system_admin: bool = True,
    package_install_allowed: bool = True,
    risk_acknowledged: bool = True,
) -> ExtensionActionRequest:
    return ExtensionActionRequest(
        context=ExtensionActionContext(
            actor_id="operator",
            is_system_admin=is_system_admin,
            package_install_allowed=package_install_allowed,
        ),
        target_id=target_id,
        action=action,
        expected_revision=expected_revision,
        risk_acknowledged=risk_acknowledged,
    )


@pytest.mark.asyncio
async def test_snapshot_declares_inspect_install_and_shared_component_revision() -> None:
    """Only an unavailable service-backed package declares install; both targets retain inspect."""
    unexpected_calls: list[tuple[str, Requirements]] = []

    def unexpected_installer(name: str, requirements: Requirements) -> InstallResult:
        unexpected_calls.append((name, requirements))
        raise AssertionError("declarations and inspect must not install")

    adapter = _adapter(installed=lambda _name, _requirements: False, installer=unexpected_installer)
    [package] = adapter.snapshot().packages
    [component] = package.components

    assert package.actions == frozenset({ExtensionAction.INSPECT, ExtensionAction.INSTALL})
    assert component.actions == frozenset({ExtensionAction.INSPECT})
    assert component.revision == package.revision
    assert ExtensionAction.DISABLE not in package.actions | component.actions
    assert ExtensionAction.UNINSTALL not in package.actions | component.actions

    package_inspect = await adapter.execute(
        _request(package.id, ExtensionAction.INSPECT, expected_revision=None)
    )
    component_inspect = await adapter.execute(
        _request(component.id, ExtensionAction.INSPECT, expected_revision=component.revision)
    )
    stale_inspect = await adapter.execute(
        _request(component.id, ExtensionAction.INSPECT, expected_revision="obsolete")
    )

    assert package_inspect.ok is True
    assert package_inspect.lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert component_inspect.ok is True
    assert component_inspect.lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert stale_inspect.ok is False
    assert unexpected_calls == []

    installed_adapter = _adapter(installed=lambda _name, _requirements: True, installer=unexpected_installer)
    [installed_package] = installed_adapter.snapshot().packages
    assert installed_package.actions == frozenset({ExtensionAction.INSPECT})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requires_restart", "expected_lifecycle"),
    [
        pytest.param(False, ExtensionLifecycle.ENABLED, id="ready-without-restart"),
        pytest.param(True, ExtensionLifecycle.RESTART_REQUIRED, id="restart-required"),
    ],
)
async def test_install_runs_once_verifies_once_and_reports_restart_truth(
    requires_restart: bool,
    expected_lifecycle: ExtensionLifecycle,
) -> None:
    """A permitted install verifies once and accurately surfaces whether a restart is required."""
    installed_state = False
    installer_calls: list[tuple[str, Requirements]] = []
    installed_checks: list[tuple[str, Requirements]] = []
    restart_checks: list[str] = []

    def installed(name: str, requirements: Requirements) -> bool:
        installed_checks.append((name, requirements))
        return installed_state

    def installer(name: str, requirements: Requirements) -> InstallResult:
        nonlocal installed_state
        installer_calls.append((name, requirements))
        installed_state = True
        return InstallResult(True, "catalog support", ["python", "-m", "pip", "install", "catalog"])

    def restart_check(name: str) -> bool:
        restart_checks.append(name)
        return requires_restart

    adapter = _adapter(
        installed=installed,
        installer=installer,
        requires_restart=restart_check,
    )
    [package] = adapter.snapshot().packages
    installed_checks.clear()
    assert restart_checks == []

    result = await adapter.execute(
        _request(package.id, ExtensionAction.INSTALL, expected_revision=package.revision)
    )

    assert result.ok is True
    assert result.lifecycle is expected_lifecycle
    assert installer_calls == [("catalog", ["catalog-sdk>=1"])]
    assert installed_checks == [
        ("catalog", ["catalog-sdk>=1"]),
        ("catalog", ["catalog-sdk>=1"]),
    ]
    assert restart_checks == ["catalog"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "non_admin",
        "component_install",
        "missing_revision",
        "stale_revision",
        "acknowledgement_denied",
        "policy_denied",
    ],
)
async def test_install_gates_reject_without_starting_installer(case: str) -> None:
    """Authorization, exact target, revision, acknowledgement, and policy gates precede installation."""
    installer_calls: list[tuple[str, Requirements]] = []

    def installer(name: str, requirements: Requirements) -> InstallResult:
        installer_calls.append((name, requirements))
        return InstallResult(True, "catalog support", ["private-command", "/private/path"])

    adapter = _adapter(installed=lambda _name, _requirements: False, installer=installer)
    [package] = adapter.snapshot().packages
    [component] = package.components
    request = _request(package.id, ExtensionAction.INSTALL, expected_revision=package.revision)
    if case == "non_admin":
        request = _request(
            package.id,
            ExtensionAction.INSTALL,
            expected_revision=package.revision,
            is_system_admin=False,
        )
    elif case == "component_install":
        request = _request(component.id, ExtensionAction.INSTALL, expected_revision=component.revision)
    elif case == "missing_revision":
        request = _request(package.id, ExtensionAction.INSTALL, expected_revision=None)
    elif case == "stale_revision":
        request = _request(package.id, ExtensionAction.INSTALL, expected_revision="obsolete")
    elif case == "acknowledgement_denied":
        request = _request(
            package.id,
            ExtensionAction.INSTALL,
            expected_revision=package.revision,
            risk_acknowledged=False,
        )
    elif case == "policy_denied":
        request = _request(
            package.id,
            ExtensionAction.INSTALL,
            expected_revision=package.revision,
            package_install_allowed=False,
        )

    result = await adapter.execute(request)

    assert result.ok is False, case
    assert installer_calls == [], case
    assert "/private/path" not in result.message, case
    assert "private-command" not in result.message, case


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["installer_failed", "unverified", "installer_exception"])
async def test_install_failures_are_redacted_and_never_claim_success(outcome: str) -> None:
    """Installer failure, failed verification, and exceptions expose no installer detail or false lifecycle."""
    secret = "token=secret /private/installer-output"
    installed_state = False
    events: list[str] = []

    def installed(_name: str, _requirements: Requirements) -> bool:
        events.append("verify")
        return installed_state

    def installer(_name: str, _requirements: Requirements) -> InstallResult:
        nonlocal installed_state
        events.append("install")
        if outcome == "installer_exception":
            raise RuntimeError(secret)
        if outcome == "installer_failed":
            return InstallResult(False, secret, ["private-command"], output=secret)
        return InstallResult(True, secret, ["private-command"], output=secret)

    adapter = _adapter(installed=installed, installer=installer)
    [package] = adapter.snapshot().packages
    events.clear()

    result = await adapter.execute(
        _request(package.id, ExtensionAction.INSTALL, expected_revision=package.revision)
    )

    assert result.ok is False
    assert result.lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert events[0] == "verify"
    assert events.count("install") == 1
    assert events.count("verify") == (2 if outcome == "unverified" else 1)
    assert secret not in result.message
    assert "/private/installer-output" not in result.message
    assert "private-command" not in result.message


def test_snapshot_retains_channel_owned_and_hidden_exclusions_without_disable_claims() -> None:
    """Channel-owned and hidden groups remain absent while the standalone package makes no removal claim."""
    adapter = _adapter(
        installed=lambda _name, _requirements: False,
        installer=None,
        groups={
            "owned_channel": ["channel-sdk>=1"],
            "documents": ["document-sdk>=1"],
            "standalone": ["standalone-sdk>=1"],
        },
        channels={"registered_as": _channel_plugin("owned_channel")},
    )

    [package] = adapter.snapshot().packages
    [component] = package.components

    assert package.name == "standalone"
    assert package.actions == frozenset({ExtensionAction.INSPECT})
    assert component.actions == frozenset({ExtensionAction.INSPECT})
    assert ExtensionAction.DISABLE not in package.actions | component.actions
    assert ExtensionAction.UNINSTALL not in package.actions | component.actions
