"""Bounded, redacted results on the CLI app action paths this adapter migrates.

These cover the legacy edge that still produces the same outcomes, because the
canonical adapter converts exactly these responses and shipping their raw package
manager output through the new path would leak on day one.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from nanobot.apps.cli.service import CliAppError, CliAppManager, CliAppsRuntimeConfig

_HOST_PREFIX = "/opt/nanobot-host/lib/python3.11/site-packages"
_INDEX_CREDENTIAL = "s3cr3t-index-token"
_RAW_STDERR = (
    "ERROR: Could not install packages due to an OSError: "
    f"[Errno 13] Permission denied: '{_HOST_PREFIX}/gimp/__init__.py'\n"
    f"Looking in indexes: https://buildbot:{_INDEX_CREDENTIAL}@pypi.internal/simple\n"
    "WARNING: You are using pip version 23.0.1\n"
) + ("filler line of package manager noise\n" * 500)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid the network: a seeded cache, not a registry fetch, is what these test."""

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("these tests must not reach the CLI app registry")

    monkeypatch.setattr("nanobot.apps.cli.service.httpx.get", refuse)
    monkeypatch.setattr("nanobot.apps.cli.service.httpx.AsyncClient", refuse)


def _manager(tmp_path: Path) -> CliAppManager:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return CliAppManager(
        workspace=workspace,
        data_dir=tmp_path / "data",
        runtime=CliAppsRuntimeConfig(catalog_ttl_seconds=3600, install_timeout=5, run_timeout=5),
    )


def _seed_catalog(manager: CliAppManager, *, entry_point: str = "cli-anything-gimp") -> None:
    """Seed every catalog source so no test in this module can reach the network."""
    for source in ("harness", "public", "extensions"):
        cache = manager.data_dir / f"{source}_registry_cache.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({
                "_cached_at": 1 << 40,
                "data": {
                    "clis": [
                        {
                            "name": "gimp",
                            "display_name": "GIMP",
                            "version": "1.0.0",
                            "description": "Image editing",
                            "category": "image",
                            "install_cmd": "pip install cli-anything-gimp",
                            "update_cmd": "pip install cli-anything-gimp",
                            "entry_point": entry_point,
                        }
                    ]
                },
            }),
            encoding="utf-8",
        )


def _write_installed(manager: CliAppManager, apps: dict[str, Any]) -> None:
    manager.installed_path.parent.mkdir(parents=True, exist_ok=True)
    manager.installed_path.write_text(
        json.dumps({"schema_version": 1, "apps": apps}), encoding="utf-8"
    )


def _failing_run(_argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    del timeout
    return subprocess.CompletedProcess([], 1, stdout="", stderr=_RAW_STDERR)


def _assert_safe(message: str) -> None:
    assert _HOST_PREFIX not in message
    assert _INDEX_CREDENTIAL not in message
    assert "Permission denied" not in message
    assert "pypi.internal" not in message
    assert len(message) <= 400


@pytest.mark.parametrize(
    ("action", "verb"),
    [("install", "Installing"), ("update", "Updating"), ("uninstall", "Uninstalling")],
)
def test_a_failing_package_manager_returns_a_bounded_safe_reason(
    action: str,
    verb: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: raw stdout and stderr never reach the response body."""
    manager = _manager(tmp_path)
    _seed_catalog(manager)
    if action != "install":
        _write_installed(manager, {"gimp": {"entry_point": "cli-anything-gimp"}})
    monkeypatch.setattr(manager, "_run_argv", _failing_run)
    monkeypatch.setattr(manager, "_pip_available", staticmethod(lambda: True))
    monkeypatch.setattr("nanobot.apps.cli.service.shutil.which", lambda _command: None)

    with pytest.raises(CliAppError) as failure:
        getattr(manager, action)("gimp")

    message = failure.value.message
    _assert_safe(message)
    assert failure.value.status == 500
    # Attributable: which app, which operation, and the package manager's exit code.
    assert "GIMP" in message
    assert message.startswith(f"{verb} GIMP failed:")
    assert "exited 1" in message
    assert "server log" in message


def test_the_full_package_manager_output_stays_in_the_server_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: the operator's real diagnostic is preserved, in the log rather than the body."""
    from loguru import logger

    manager = _manager(tmp_path)
    _seed_catalog(manager)
    monkeypatch.setattr(manager, "_run_argv", _failing_run)
    monkeypatch.setattr(manager, "_pip_available", staticmethod(lambda: True))
    monkeypatch.setattr("nanobot.apps.cli.service.shutil.which", lambda _command: None)
    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="ERROR")

    try:
        with pytest.raises(CliAppError):
            manager.install("gimp")
    finally:
        logger.remove(sink_id)

    logged = "\n".join(records)
    assert _HOST_PREFIX in logged
    assert "'gimp'" in logged
    assert "exit code 1" in logged


def test_the_test_action_redacts_and_bounds_the_executables_own_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC12: exit status plus a bounded redacted excerpt, not 3000 raw characters."""
    manager = _manager(tmp_path)
    _seed_catalog(manager)
    help_output = (
        "usage: gimp [options]\n"
        f"  config file: {_HOST_PREFIX}/gimp.conf\n"
        "  api_key=sk-livekey0123456789abcdef\n"
        "  Authorization: Bearer abcdef0123456789\n"
    ) + ("padding padding padding\n" * 500)
    monkeypatch.setattr(
        "nanobot.apps.cli.service.shutil.which",
        lambda command: f"/host/bin/{command}",
    )
    monkeypatch.setattr(
        manager,
        "_run_argv",
        lambda argv, *, timeout: subprocess.CompletedProcess(argv, 0, stdout=help_output, stderr=""),
    )
    monkeypatch.setattr(manager, "payload", lambda **_kwargs: {"apps": []})

    last_action = manager.test("gimp")["last_action"]

    output = last_action["output"]
    assert last_action["ok"] is True
    assert last_action["exit_code"] == 0
    assert len(output) <= 1_000
    assert _HOST_PREFIX not in output
    assert "sk-livekey0123456789abcdef" not in output
    assert "abcdef0123456789" not in output
    assert "<redacted>" in output
    assert "usage: gimp" in output
    # The message names the entry point, which is a command name, not a location.
    assert last_action["message"] == "cli-anything-gimp --help exited 0"


def test_a_path_shaped_entry_point_is_not_echoed_by_the_test_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7: a recorded entry point that is a location is never rendered back."""
    manager = _manager(tmp_path)
    _seed_catalog(manager, entry_point=f"{_HOST_PREFIX}/bin/cli-anything-gimp")
    monkeypatch.setattr(
        "nanobot.apps.cli.service.shutil.which", lambda command: f"/host/bin/{command}"
    )
    monkeypatch.setattr(
        manager,
        "_run_argv",
        lambda argv, *, timeout: subprocess.CompletedProcess(argv, 0, stdout="ok", stderr=""),
    )
    monkeypatch.setattr(manager, "payload", lambda **_kwargs: {"apps": []})

    last_action = manager.test("gimp")["last_action"]

    assert _HOST_PREFIX not in last_action["message"]
    assert last_action["message"] == "the recorded entry point --help exited 0"


def test_uninstall_partial_outcome_describes_the_condition_without_the_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC11: the recorded location is a host path and stays out of the 200 body."""
    manager = _manager(tmp_path)
    _seed_catalog(manager)
    resolved = tmp_path / "host-bin" / "cli-anything-gimp"
    resolved.parent.mkdir(parents=True)
    resolved.write_text("#!/bin/sh\n", encoding="utf-8")
    _write_installed(
        manager,
        {"gimp": {"entry_point": "cli-anything-gimp", "entry_point_path": str(resolved)}},
    )
    monkeypatch.setattr(manager, "_pip_available", staticmethod(lambda: True))
    monkeypatch.setattr(
        manager,
        "_run_argv",
        lambda argv, *, timeout: subprocess.CompletedProcess(argv, 0, stdout="ok", stderr=""),
    )
    monkeypatch.setattr(manager, "payload", lambda **_kwargs: {"apps": []})

    last_action = manager.uninstall("gimp")["last_action"]

    assert last_action["ok"] is False
    assert last_action["removed"] is False
    assert "its recorded entry point still exists" in last_action["message"]
    assert str(resolved) not in last_action["message"]
    assert str(tmp_path) not in last_action["message"]
    # No rollback is fabricated: the app stays exactly as it was.
    assert set(manager.installed_entries()) == {"gimp"}
