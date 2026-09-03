from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot import optional_features


def test_prepare_channel_dependencies_skips_install_when_dependencies_are_absent() -> None:
    """Channels without declared requirements are ready without probing or installing."""


    def unexpected_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("dependency-free channels must not invoke an installer")

    result = optional_features.prepare_channel_dependencies(
        "catalog",
        None,
        allow_install=True,
        runner=unexpected_runner,
    )

    assert result == optional_features.ChannelDependencyPreparation(ready=True)


def test_prepare_channel_dependencies_skips_install_when_already_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed channel requirements remain ready without a second package install."""
    checks: list[tuple[str, list[str] | None]] = []

    def installed(name: str, dependencies: list[str] | None) -> bool:
        checks.append((name, dependencies))
        return True

    def unexpected_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("available dependencies must not invoke an installer")

    monkeypatch.setattr(optional_features, "extra_installed", installed)

    result = optional_features.prepare_channel_dependencies(
        "catalog",
        ["catalog-sdk>=1"],
        allow_install=True,
        runner=unexpected_runner,
    )

    assert result == optional_features.ChannelDependencyPreparation(ready=True)
    assert checks == [("catalog", ["catalog-sdk>=1"])]


def test_prepare_channel_dependencies_blocks_missing_requirements_without_installing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install policy rejection reports the fixed safe error before command execution."""
    monkeypatch.setattr(optional_features, "extra_installed", lambda *_args: False)

    def unexpected_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("blocked installation must not invoke an installer")

    result = optional_features.prepare_channel_dependencies(
        "catalog",
        ["catalog-sdk>=1"],
        allow_install=False,
        runner=unexpected_runner,
    )

    assert result == optional_features.ChannelDependencyPreparation(
        ready=False,
        message="Channel dependency installation is disabled by policy.",
    )


def test_prepare_channel_dependencies_installs_once_then_verifies_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful command becomes ready only after one post-install availability check."""
    checks: list[tuple[str, list[str] | None]] = []
    commands: list[list[str]] = []

    def installed(name: str, dependencies: list[str] | None) -> bool:
        checks.append((name, dependencies))
        return len(checks) == 2

    def successful_runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="installed", stderr="")

    monkeypatch.setattr(optional_features, "extra_installed", installed)

    result = optional_features.prepare_channel_dependencies(
        "catalog",
        ["catalog-sdk>=1"],
        allow_install=True,
        runner=successful_runner,
    )

    assert result == optional_features.ChannelDependencyPreparation(ready=True, installed=True)
    assert len(commands) == 1
    assert checks == [
        ("catalog", ["catalog-sdk>=1"]),
        ("catalog", ["catalog-sdk>=1"]),
    ]


def test_prepare_channel_dependencies_rejects_unverified_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-exit installer result is not readiness when requirements remain missing."""
    commands: list[list[str]] = []
    monkeypatch.setattr(optional_features, "extra_installed", lambda *_args: False)

    def successful_runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="installed", stderr="")

    result = optional_features.prepare_channel_dependencies(
        "catalog",
        ["catalog-sdk>=1"],
        allow_install=True,
        runner=successful_runner,
    )

    assert result == optional_features.ChannelDependencyPreparation(
        ready=False,
        installed=True,
        message="Channel dependencies could not be installed. Check gateway logs.",
    )
    assert len(commands) == 1


def test_prepare_channel_dependencies_hides_failed_installer_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installer output, requirement text, and host paths remain confined to server logs."""
    raw_output = "token=super-secret failed at /Users/operator/private-wheel"
    requirement = "private-sdk @ file:///Users/operator/private-wheel"
    monkeypatch.setattr(optional_features, "extra_installed", lambda *_args: False)

    def failing_runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=raw_output)

    result = optional_features.prepare_channel_dependencies(
        "private-channel",
        [requirement],
        allow_install=True,
        runner=failing_runner,
    )

    assert result == optional_features.ChannelDependencyPreparation(
        ready=False,
        message="Channel dependencies could not be installed. Check gateway logs.",
    )
    assert raw_output not in result.message
    assert requirement not in result.message
    assert "/Users/operator/private-wheel" not in result.message


def test_startup_dependency_helper_delegates_once_per_enabled_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup uses the shared preparation service and preserves its safe failures."""
    calls: list[tuple[str, list[str] | None, bool, Any]] = []

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("startup must delegate rather than install directly")

    def prepare(
        name: str,
        dependencies: list[str] | None,
        *,
        allow_install: bool,
        runner: Any,
    ) -> optional_features.ChannelDependencyPreparation:
        calls.append((name, dependencies, allow_install, runner))
        if name == "blocked":
            return optional_features.ChannelDependencyPreparation(
                ready=False,
                message="Channel dependencies could not be installed. Check gateway logs.",
            )
        return optional_features.ChannelDependencyPreparation(ready=True)

    monkeypatch.setattr(optional_features, "prepare_channel_dependencies", prepare)

    failures = optional_features.ensure_enabled_channel_dependencies(
        {"ready", "blocked", "unknown"},
        {
            "ready": SimpleNamespace(dependencies=["ready-sdk>=1"]),
            "blocked": SimpleNamespace(dependencies=["blocked-sdk>=1"]),
        },
        runner=runner,
    )

    assert calls == [
        ("blocked", ["blocked-sdk>=1"], True, runner),
        ("ready", ["ready-sdk>=1"], True, runner),
    ]
    assert failures == {
        "blocked": "Channel dependencies could not be installed. Check gateway logs."
    }
