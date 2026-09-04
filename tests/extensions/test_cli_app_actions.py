"""Revision-bound, admin-only CLI app lifecycle actions with bounded results."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from nanobot.extensions.adapters.cli_apps import CliAppExtensionAdapter
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionActionResult,
    ExtensionComponentKind,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
)
from nanobot.extensions.registry import ExtensionRegistry, ExtensionRegistryError

_PACKAGE_ID = "ext:cli_app:gimp"
_EXECUTABLE_ID = f"{_PACKAGE_ID}/cli_app:gimp"
_SKILL_ID = f"{_PACKAGE_ID}/skill:cli-app-gimp"
_HOST_PATH = "/opt/nanobot-host/lib/python3.11/site-packages"
_RAW_STDERR = (
    "ERROR: Could not install packages due to an OSError: "
    f"[Errno 13] Permission denied: '{_HOST_PATH}/gimp'\n"
    "Looking in indexes: https://buildbot:s3cr3t-index-token@pypi.internal/simple\n"
) + ("noise " * 4_000)


class _Owner:
    """A stand-in for CliAppManager that records exactly which transaction ran."""

    def __init__(
        self,
        *,
        installed: dict[str, Any] | None = None,
        skill: bool = True,
        outcomes: Mapping[str, object] | None = None,
        raises: str | None = None,
    ) -> None:
        self.installed: dict[str, Any] = (
            installed
            if installed is not None
            else {
                "gimp": {
                    "version": "1.4.0",
                    "entry_point": "cli-anything-gimp",
                    "entry_point_path": f"{_HOST_PATH}/bin/cli-anything-gimp",
                    "source": "harness",
                    "strategy": "pip",
                }
            }
        )
        self.skill = skill
        self.outcomes = dict(outcomes or {})
        self.raises = raises
        self.calls: list[tuple[str, str]] = []

    def installed_entries(self) -> dict[str, Any]:
        return dict(self.installed)

    def generated_skill_name(self, name: str) -> str:
        return f"cli-app-{name}"

    def skill_installed(self, name: str) -> bool:
        del name
        return self.skill

    def managed_plugin_names(self) -> frozenset[str]:
        return frozenset(f"cli-app-{name}" for name in self.installed)

    def _record(self, action: str, name: str) -> Mapping[str, object]:
        self.calls.append((action, name))
        if self.raises == action:
            raise ValueError(_RAW_STDERR)
        outcome = self.outcomes.get(action)
        return outcome if isinstance(outcome, Mapping) else {"last_action": {"ok": True}}

    def update(self, name: str) -> Mapping[str, object]:
        return self._record("update", name)

    def uninstall(self, name: str) -> Mapping[str, object]:
        outcome = self._record("uninstall", name)
        last_action = outcome.get("last_action")
        if isinstance(last_action, Mapping) and last_action.get("removed") is True:
            self.installed.pop(name, None)
        return outcome

    def test(self, name: str) -> Mapping[str, object]:
        return self._record("test", name)


@pytest.fixture(autouse=True)
def _resolvable_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        lambda command: f"/host/bin/{command}",
    )


def _adapter(owner: _Owner) -> CliAppExtensionAdapter:
    return CliAppExtensionAdapter(lambda: owner)


def _package(adapter: CliAppExtensionAdapter) -> ExtensionPackageDescriptor:
    return next(
        package for package in adapter.snapshot().packages if package.id == _PACKAGE_ID
    )


def _revision(adapter: CliAppExtensionAdapter, target_id: str) -> str:
    package = _package(adapter)
    if target_id == package.id:
        assert package.revision is not None
        return package.revision
    component = next(component for component in package.components if component.id == target_id)
    assert component.revision is not None
    return component.revision


def _request(
    action: ExtensionAction,
    *,
    target_id: str = _PACKAGE_ID,
    revision: str | None = None,
    acknowledged: bool = True,
    admin: bool = True,
) -> ExtensionActionRequest:
    return ExtensionActionRequest(
        context=ExtensionActionContext(actor_id="admin-1", is_system_admin=admin),
        target_id=target_id,
        action=action,
        expected_revision=revision,
        risk_acknowledged=acknowledged,
    )


def _run(
    adapter: CliAppExtensionAdapter, request: ExtensionActionRequest
) -> ExtensionActionResult:
    return asyncio.run(adapter.execute(request))


def test_a_non_admin_actor_is_refused_before_any_transaction_runs() -> None:
    """AC9: authorization is decided before the package manager is reachable."""
    owner = _Owner()
    adapter = _adapter(owner)
    revision = _revision(adapter, _PACKAGE_ID)

    result = _run(adapter, _request(ExtensionAction.UNINSTALL, revision=revision, admin=False))

    assert result.ok is False
    assert "administrator" in result.message.lower()
    assert owner.calls == []


def test_the_registry_refuses_a_non_admin_before_reaching_the_adapter() -> None:
    """AC9: the composed registry stops a non-admin before any adapter callback."""
    owner = _Owner()
    registry = ExtensionRegistry()
    registry.register(_adapter(owner))

    with pytest.raises(ExtensionRegistryError) as failure:
        asyncio.run(
            registry.execute(_request(ExtensionAction.UNINSTALL, revision="x", admin=False))
        )

    assert failure.value.status == 403
    assert owner.calls == []


@pytest.mark.parametrize(
    ("action", "target_id"),
    [
        (ExtensionAction.INSTALL, _PACKAGE_ID),
        (ExtensionAction.UNINSTALL, _PACKAGE_ID),
        (ExtensionAction.RELOAD, _EXECUTABLE_ID),
    ],
)
def test_a_stale_revision_fails_before_any_transaction_runs(
    action: ExtensionAction,
    target_id: str,
) -> None:
    """AC9: an app that changed between listing and action is never acted on."""
    owner = _Owner()
    adapter = _adapter(owner)

    result = _run(adapter, _request(action, target_id=target_id, revision="stale-revision"))

    assert result.ok is False
    assert result.message == "CLI app action revision is stale."
    assert owner.calls == []


@pytest.mark.parametrize(
    ("action", "target_id"),
    [
        (ExtensionAction.INSTALL, _PACKAGE_ID),
        (ExtensionAction.UNINSTALL, _PACKAGE_ID),
        (ExtensionAction.RELOAD, _EXECUTABLE_ID),
    ],
)
def test_a_missing_revision_fails_before_any_transaction_runs(
    action: ExtensionAction,
    target_id: str,
) -> None:
    """AC9: acting without the exact current revision is refused, not assumed."""
    owner = _Owner()
    adapter = _adapter(owner)

    result = _run(adapter, _request(action, target_id=target_id, revision=None))

    assert result.ok is False
    assert result.message == "CLI app action requires a current revision."
    assert owner.calls == []


def test_installing_external_code_requires_acknowledgement() -> None:
    """AC9: an update fetches and runs a package manager, so it refuses without disclosure."""
    owner = _Owner()
    adapter = _adapter(owner)
    revision = _revision(adapter, _PACKAGE_ID)

    result = _run(
        adapter,
        _request(
            ExtensionAction.INSTALL, target_id=_PACKAGE_ID, revision=revision, acknowledged=False
        ),
    )

    assert result.ok is False
    assert "acknowledgement" in result.message
    assert owner.calls == []


def test_testing_an_installed_app_needs_no_acknowledgement() -> None:
    """The contract scopes acknowledgement to enabling and installing executable code.

    A test probes an entry point that is already installed and that the agent already
    runs through ``run_cli_app`` unprompted, so a dialog here would add no disclosure.
    It would also be inert: the control plane prompts for ENABLE and INSTALL only, so
    requiring one would refuse every test the operator could actually trigger.
    """
    owner = _Owner()
    adapter = _adapter(owner)
    revision = _revision(adapter, _EXECUTABLE_ID)

    result = _run(
        adapter,
        _request(
            ExtensionAction.RELOAD, target_id=_EXECUTABLE_ID, revision=revision, acknowledged=False
        ),
    )

    assert result.ok is True
    assert owner.calls != []


def test_an_unknown_target_and_an_undeclared_action_both_fail_closed() -> None:
    """AC9: an unknown target or an unsupported action never reaches a transaction."""
    owner = _Owner()
    adapter = _adapter(owner)
    revision = _revision(adapter, _PACKAGE_ID)

    unknown = _run(
        adapter,
        _request(ExtensionAction.UNINSTALL, target_id="ext:cli_app:absent", revision=revision),
    )
    unsupported = _run(adapter, _request(ExtensionAction.ENABLE, revision=revision))
    on_skill = _run(
        adapter,
        _request(
            ExtensionAction.UNINSTALL,
            target_id=_SKILL_ID,
            revision=_revision(adapter, _SKILL_ID),
        ),
    )

    assert unknown.ok is False
    assert unknown.message == "CLI app action target is unavailable."
    assert unsupported.ok is False
    assert unsupported.message == "CLI app action is not supported."
    # The generated Skill is not independently removable; its app owns that action.
    assert on_skill.ok is False
    assert on_skill.message == "CLI app action is not supported."
    assert owner.calls == []


def test_testing_is_not_offered_when_the_entry_point_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC9: an app with no resolvable executable exposes nothing that would run it."""
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which", lambda _command: None
    )
    owner = _Owner()
    adapter = _adapter(owner)

    result = _run(
        adapter,
        _request(
            ExtensionAction.RELOAD,
            target_id=_EXECUTABLE_ID,
            revision=_revision(adapter, _EXECUTABLE_ID),
        ),
    )

    assert result.ok is False
    assert result.message == "CLI app action is not supported."
    assert owner.calls == []


def test_update_delegates_once_and_reports_the_resulting_state() -> None:
    """AC8: update runs the manager's own transaction exactly once."""
    owner = _Owner()
    adapter = _adapter(owner)

    result = _run(
        adapter, _request(ExtensionAction.INSTALL, revision=_revision(adapter, _PACKAGE_ID))
    )

    assert owner.calls == [("update", "gimp")]
    assert result.ok is True
    assert result.package_id == _PACKAGE_ID
    assert result.lifecycle is ExtensionLifecycle.ENABLED
    assert result.message == "The CLI app was updated through its package manager."


def test_uninstall_delegates_once_and_reports_removal() -> None:
    """AC8: a completed removal reports the app as gone from the inventory."""
    owner = _Owner(outcomes={"uninstall": {"last_action": {"ok": True, "removed": True}}})
    adapter = _adapter(owner)

    result = _run(
        adapter, _request(ExtensionAction.UNINSTALL, revision=_revision(adapter, _PACKAGE_ID))
    )

    assert owner.calls == [("uninstall", "gimp")]
    assert result.ok is True
    assert result.lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert result.message == "The CLI app and its generated Skill were removed."
    assert owner.installed == {}


def test_uninstall_that_leaves_the_entry_point_reports_a_truthful_partial_outcome() -> None:
    """AC11: nanobot kept it installed, says so, and claims no rollback or path."""
    owner = _Owner(
        outcomes={
            "uninstall": {
                "last_action": {
                    "ok": False,
                    "removed": False,
                    "still_available": True,
                    "message": f"the recorded entry point at {_HOST_PATH}/bin still exists",
                }
            }
        }
    )
    adapter = _adapter(owner)

    result = _run(
        adapter, _request(ExtensionAction.UNINSTALL, revision=_revision(adapter, _PACKAGE_ID))
    )

    assert owner.calls == [("uninstall", "gimp")]
    assert result.ok is False
    assert "kept it installed" in result.message
    assert "changed nothing else" in result.message
    assert _HOST_PATH not in result.message
    assert "rolled back" not in result.message
    assert "rollback" not in result.message
    # The app is still installed, and the inventory still says so.
    assert owner.installed != {}
    assert result.lifecycle is ExtensionLifecycle.ENABLED


def test_uninstall_that_leaves_an_unmanaged_command_reports_it_without_a_path() -> None:
    """AC11: removal succeeded while a same-named command survives outside nanobot."""
    owner = _Owner(
        outcomes={
            "uninstall": {
                "last_action": {"ok": True, "removed": True, "still_available": True}
            }
        }
    )
    adapter = _adapter(owner)

    result = _run(
        adapter, _request(ExtensionAction.UNINSTALL, revision=_revision(adapter, _PACKAGE_ID))
    )

    assert result.ok is True
    assert "managed outside nanobot" in result.message
    assert _HOST_PATH not in result.message


def test_test_reports_exit_status_with_a_bounded_redacted_excerpt() -> None:
    """AC12: the excerpt is bounded and redacted, never raw executable output."""
    owner = _Owner(
        outcomes={
            "test": {
                "last_action": {
                    "ok": True,
                    "exit_code": 0,
                    "output": (
                        f"usage: gimp [--config /etc/gimp.conf] token=abcdef0123456789 "
                        f"installed at {_HOST_PATH} " + "x" * 5_000
                    ),
                }
            }
        }
    )
    adapter = _adapter(owner)

    result = _run(
        adapter,
        _request(
            ExtensionAction.RELOAD,
            target_id=_EXECUTABLE_ID,
            revision=_revision(adapter, _EXECUTABLE_ID),
        ),
    )

    assert owner.calls == [("test", "gimp")]
    assert result.ok is True
    assert "exited 0" in result.message
    # An excerpt, not the whole output trimmed to the contract's own 1 000-character
    # cap: the status has to stay readable next to it.
    assert len(result.message) <= 400
    assert _HOST_PATH not in result.message
    assert "/etc/gimp.conf" not in result.message
    assert "abcdef0123456789" not in result.message
    assert "<redacted>" in result.message


def test_a_failing_test_reports_the_nonzero_status_without_claiming_success() -> None:
    """AC12: a nonzero exit is reported as a failure, not smoothed into ok."""
    owner = _Owner(
        outcomes={"test": {"last_action": {"ok": False, "exit_code": 127, "output": ""}}}
    )
    adapter = _adapter(owner)

    result = _run(
        adapter,
        _request(
            ExtensionAction.RELOAD,
            target_id=_EXECUTABLE_ID,
            revision=_revision(adapter, _EXECUTABLE_ID),
        ),
    )

    assert result.ok is False
    assert "exited 127" in result.message
    assert "Output:" not in result.message


@pytest.mark.parametrize(
    ("action", "target_id", "operation", "expected"),
    [
        (
            ExtensionAction.INSTALL,
            _PACKAGE_ID,
            "update",
            "The CLI app could not be updated through its package manager.",
        ),
        (
            ExtensionAction.UNINSTALL,
            _PACKAGE_ID,
            "uninstall",
            "The CLI app could not be uninstalled through its package manager.",
        ),
        (
            ExtensionAction.RELOAD,
            _EXECUTABLE_ID,
            "test",
            "The CLI app entry point could not be run.",
        ),
    ],
)
def test_a_failing_transaction_returns_a_bounded_reason_and_no_raw_output(
    action: ExtensionAction,
    target_id: str,
    operation: str,
    expected: str,
) -> None:
    """AC10: the manager's raw package-manager text never reaches a canonical result."""
    owner = _Owner(raises=operation)
    adapter = _adapter(owner)

    result = _run(
        adapter, _request(action, target_id=target_id, revision=_revision(adapter, target_id))
    )

    assert owner.calls == [(operation, "gimp")]
    assert result.ok is False
    assert result.message == expected
    assert len(result.message) <= 1_000
    assert _HOST_PATH not in result.message
    assert "s3cr3t-index-token" not in result.message
    assert "Permission denied" not in result.message


def test_inspect_is_side_effect_free_and_reports_the_current_state() -> None:
    """Inspect never delegates, and reports the descriptor's own lifecycle."""
    owner = _Owner()
    adapter = _adapter(owner)

    package_result = _run(
        adapter, _request(ExtensionAction.INSPECT, revision=_revision(adapter, _PACKAGE_ID))
    )
    skill_result = _run(
        adapter,
        _request(
            ExtensionAction.INSPECT,
            target_id=_SKILL_ID,
            revision=_revision(adapter, _SKILL_ID),
        ),
    )

    assert owner.calls == []
    assert package_result.ok is True
    assert package_result.lifecycle is ExtensionLifecycle.ENABLED
    assert skill_result.ok is True
    assert skill_result.target_id == _SKILL_ID


def test_a_malformed_row_is_never_actionable() -> None:
    """A package projected from a malformed entry exposes no lifecycle transaction."""
    owner = _Owner(installed={"gimp": "not an object"})
    adapter = _adapter(owner)
    package = _package(adapter)

    result = _run(adapter, _request(ExtensionAction.UNINSTALL, revision=package.revision))

    assert package.lifecycle is ExtensionLifecycle.FAILED
    assert package.actions == frozenset({ExtensionAction.INSPECT})
    assert result.ok is False
    assert owner.calls == []


def test_the_composed_registry_dispatches_a_cli_app_action_to_this_adapter() -> None:
    """AC8: the canonical registry is the dispatch path, with its own gates applied."""
    owner = _Owner()
    adapter = _adapter(owner)
    registry = ExtensionRegistry()
    registry.register(adapter)
    revision = _revision(adapter, _PACKAGE_ID)

    with pytest.raises(ExtensionRegistryError) as unacknowledged:
        asyncio.run(
            registry.execute(_request(ExtensionAction.INSTALL, revision=revision, acknowledged=False))
        )
    result = asyncio.run(registry.execute(_request(ExtensionAction.INSTALL, revision=revision)))

    assert unacknowledged.value.code == "risk_acknowledgement_required"
    assert result.ok is True
    assert owner.calls == [("update", "gimp")]


def test_component_identities_are_stable_and_namespaced() -> None:
    """The IDs actions target are the canonical package/kind:name form."""
    adapter = _adapter(_Owner())
    package = _package(adapter)

    assert package.id == _PACKAGE_ID
    assert [component.id for component in package.components] == [_EXECUTABLE_ID, _SKILL_ID]
    assert [component.kind for component in package.components] == [
        ExtensionComponentKind.CLI_APP,
        ExtensionComponentKind.SKILL,
    ]


def test_an_action_never_receives_a_target_from_a_stale_snapshot() -> None:
    """Targets are resolved from the snapshot taken inside the action, not a cached one."""
    owner = _Owner()
    adapter = _adapter(owner)
    revision = _revision(adapter, _PACKAGE_ID)
    # The app is uninstalled out of band after the operator read its revision.
    owner.installed = {}

    result = _run(adapter, _request(ExtensionAction.UNINSTALL, revision=revision))

    assert result.ok is False
    assert result.message == "CLI app action target is unavailable."
    assert owner.calls == []
