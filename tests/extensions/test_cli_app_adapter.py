"""Installed-only projection of CLI apps into the canonical extension registry."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from nanobot.apps.cli.service import AGENT_PLUGIN_SCHEMA, CliAppManager, CliAppsRuntimeConfig
from nanobot.extensions.adapters.cli_apps import CliAppExtensionAdapter
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionComponentKind,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_package_id,
    requires_risk_acknowledgement,
)

_ENTRY_POINT_PATH = "/opt/nanobot-host/bin/cli-anything-gimp"


def _manager(tmp_path: Path) -> CliAppManager:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return CliAppManager(
        workspace=workspace,
        data_dir=tmp_path / "data",
        runtime=CliAppsRuntimeConfig(catalog_ttl_seconds=3600, install_timeout=5, run_timeout=5),
    )


def _write_installed(manager: CliAppManager, apps: dict[str, Any]) -> None:
    manager.installed_path.parent.mkdir(parents=True, exist_ok=True)
    manager.installed_path.write_text(
        json.dumps({"schema_version": 1, "apps": apps}), encoding="utf-8"
    )


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "version": "1.4.0",
        "entry_point": "cli-anything-gimp",
        "entry_point_path": _ENTRY_POINT_PATH,
        "source": "harness",
        "strategy": "pip",
        "installed_at": 1_700_000_000,
        "display_name": "GIMP",
        "category": "image editing",
        "description": "Edit images from the command line.",
    }
    entry.update(overrides)
    return entry


def _write_generated_skill(manager: CliAppManager, app_name: str) -> Path:
    skill_name = manager.generated_skill_name(app_name)
    root = manager.workspace / "plugins" / skill_name
    (root / "skills" / skill_name).mkdir(parents=True, exist_ok=True)
    (root / "skills" / skill_name / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: Operate {app_name}.\n---\n\n# {app_name}\n",
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(
        json.dumps({
            "$schema": AGENT_PLUGIN_SCHEMA,
            "name": skill_name,
            "description": f"Operate {app_name}.",
        }),
        encoding="utf-8",
    )
    return root


def _resolve_only(*names: str):
    def which(command: str) -> str | None:
        return f"/host/bin/{command}" if command in names else None

    return which


def _adapter(manager: CliAppManager) -> CliAppExtensionAdapter:
    return CliAppExtensionAdapter(lambda: manager)


def _package(
    adapter: CliAppExtensionAdapter, package_id: str
) -> ExtensionPackageDescriptor | None:
    return next(
        (
            package
            for package in adapter.snapshot().packages
            if package.id == package_id
        ),
        None,
    )


def _text_values(package: ExtensionPackageDescriptor) -> list[str]:
    """Every operator-visible string the package and its components carry."""
    values = [
        package.id,
        package.name,
        package.display_name,
        package.description,
        package.version or "",
        package.revision or "",
        *package.permissions,
    ]
    if package.diagnostic is not None:
        values.extend([package.diagnostic.code, package.diagnostic.message])
    for component in package.components:
        values.extend([
            component.id,
            component.name,
            component.display_name,
            component.description,
            component.revision or "",
            *component.capabilities,
        ])
        if component.diagnostic is not None:
            values.extend([component.diagnostic.code, component.diagnostic.message])
    return values


def test_every_installed_app_appears_once_owning_its_executable_and_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: durable installed state produces one package per app, never two."""
    manager = _manager(tmp_path)
    _write_installed(
        manager,
        {
            "gimp": _entry(),
            "ffmpeg": _entry(entry_point="cli-anything-ffmpeg", display_name="FFmpeg"),
        },
    )
    _write_generated_skill(manager, "gimp")
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        _resolve_only("cli-anything-gimp", "cli-anything-ffmpeg"),
    )

    snapshot = _adapter(manager).snapshot()
    packages = {package.id: package for package in snapshot.packages}
    gimp = packages[extension_package_id(ExtensionSource.CLI_APP, "gimp")]
    ffmpeg = packages[extension_package_id(ExtensionSource.CLI_APP, "ffmpeg")]

    assert len(snapshot.packages) == 2
    assert snapshot.diagnostics == ()
    assert all(package.source is ExtensionSource.CLI_APP for package in snapshot.packages)
    assert [(component.kind, component.name) for component in gimp.components] == [
        (ExtensionComponentKind.CLI_APP, "gimp"),
        (ExtensionComponentKind.SKILL, "cli-app-gimp"),
    ]
    # The Skill component exists only where the generated Skill file actually does.
    assert [component.kind for component in ffmpeg.components] == [
        ExtensionComponentKind.CLI_APP
    ]


def test_catalog_candidates_cache_and_refresh_do_not_reach_the_installed_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: the snapshot ignores the catalog and performs no network or subprocess work."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry()})
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        _resolve_only("cli-anything-gimp"),
    )

    def _no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the installed projection must not reach the network")

    def _no_subprocess(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the installed projection must not run a process")

    monkeypatch.setattr("nanobot.apps.cli.service.httpx.get", _no_network)
    monkeypatch.setattr("nanobot.apps.cli.service.httpx.AsyncClient", _no_network)
    monkeypatch.setattr("nanobot.apps.cli.service.subprocess.run", _no_subprocess)

    adapter = _adapter(manager)
    without_catalog = adapter.snapshot()

    # A stale cache advertising a different version and two uninstalled candidates.
    for source in ("harness", "public", "extensions"):
        cache = tmp_path / "data" / f"{source}_registry_cache.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({
                "_cached_at": time.time() - 86_400,
                "data": {
                    "clis": [
                        {"name": "gimp", "version": "99.0.0", "display_name": "Stale GIMP"},
                        {"name": "inkscape", "entry_point": "cli-anything-inkscape"},
                    ]
                },
            }),
            encoding="utf-8",
        )
    with_catalog = adapter.snapshot()

    assert with_catalog == without_catalog
    assert [package.name for package in with_catalog.packages] == ["gimp"]
    assert with_catalog.packages[0].version == "1.4.0"


def test_cli_app_packages_disclose_unisolated_child_process_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: operator-trusted, unisolated, unenforced permissions; the Skill is data."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry()})
    _write_generated_skill(manager, "gimp")
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        _resolve_only("cli-anything-gimp"),
    )

    package = _package(_adapter(manager), extension_package_id(ExtensionSource.CLI_APP, "gimp"))
    assert package is not None
    executable, skill = package.components

    assert package.trust is ExtensionTrust.OPERATOR_TRUSTED
    assert package.execution is ExtensionExecution.CHILD_PROCESS
    assert package.isolated is False
    assert package.permissions == ()
    assert package.permissions_enforced is False
    assert requires_risk_acknowledgement(package) is True
    assert executable.execution is ExtensionExecution.CHILD_PROCESS
    assert skill.execution is ExtensionExecution.DATA
    disclosure = package.description.lower()
    assert "package manager" in disclosure
    assert "does not verify" in disclosure
    assert "sandbox" in disclosure


def test_unresolvable_entry_point_reports_unavailable_and_offers_no_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6: an installed app whose entry point is gone is never reported as enabled."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry()})
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which", _resolve_only()
    )

    package = _package(_adapter(manager), extension_package_id(ExtensionSource.CLI_APP, "gimp"))
    assert package is not None

    assert package.lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert package.components[0].lifecycle is ExtensionLifecycle.UNAVAILABLE
    # Testing runs the executable, so it is not offered when there is none to run.
    assert ExtensionAction.RELOAD not in package.components[0].actions
    assert package.actions == frozenset({
        ExtensionAction.INSPECT,
        ExtensionAction.INSTALL,
        ExtensionAction.UNINSTALL,
    })


def test_a_malformed_entry_point_is_neither_shown_nor_treated_as_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded entry point that is a path is not a command name and is not disclosed."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry(entry_point=_ENTRY_POINT_PATH)})
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        lambda command: f"/host/bin/{command}",
    )

    package = _package(_adapter(manager), extension_package_id(ExtensionSource.CLI_APP, "gimp"))
    assert package is not None

    assert package.lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert not any(
        capability.startswith("entry-point:") for capability in package.components[0].capabilities
    )


def test_no_descriptor_field_carries_a_host_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7: nothing projected contains entry_point_path, a resolved location, or argv."""
    manager = _manager(tmp_path)
    _write_installed(
        manager,
        {
            "gimp": _entry(
                description=f"Installs into {_ENTRY_POINT_PATH} on this host.",
                display_name=f"GIMP at {_ENTRY_POINT_PATH}",
                category="/opt/categories/image",
            )
        },
    )
    _write_generated_skill(manager, "gimp")
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        _resolve_only("cli-anything-gimp"),
    )

    snapshot = _adapter(manager).snapshot()
    values = [value for package in snapshot.packages for value in _text_values(package)]

    assert values
    for value in values:
        assert _ENTRY_POINT_PATH not in value
        assert str(tmp_path) not in value
        assert "entry_point_path" not in value
        assert "/opt/" not in value


def test_a_path_shaped_recorded_name_is_projected_with_safe_display_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7: a durable name is operator-written text and is sanitized before display."""
    manager = _manager(tmp_path)
    recorded_name = f"gimp {_ENTRY_POINT_PATH}"
    _write_installed(manager, {recorded_name: _entry(display_name="")})
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        _resolve_only("cli-anything-gimp"),
    )

    snapshot = _adapter(manager).snapshot()
    package = snapshot.packages[0]

    # The app is still projected normally; sanitizing it does not break it.
    assert len(snapshot.packages) == 1
    assert package.lifecycle is ExtensionLifecycle.ENABLED
    assert package.diagnostic is None
    assert _ENTRY_POINT_PATH not in package.display_name
    assert package.display_name.startswith("gimp ")


def test_revision_tracks_structural_facts_and_ignores_the_recorded_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7: revisions move with version, source, strategy, and Skill presence only."""
    manager = _manager(tmp_path)
    package_id = extension_package_id(ExtensionSource.CLI_APP, "gimp")
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        _resolve_only("cli-anything-gimp"),
    )
    adapter = _adapter(manager)

    def revision(**overrides: Any) -> str:
        _write_installed(manager, {"gimp": _entry(**overrides)})
        package = _package(adapter, package_id)
        assert package is not None and package.revision is not None
        return package.revision

    baseline = revision()

    assert revision() == baseline
    # A host move rewrites the recorded location but changes nothing structural.
    assert revision(entry_point_path="/somewhere/else/cli-anything-gimp") == baseline
    assert revision(installed_at=1_800_000_000) == baseline
    assert revision(version="1.5.0") != baseline
    assert revision(strategy="npm") != baseline
    assert revision(source="extensions") != baseline

    _write_installed(manager, {"gimp": _entry()})
    _write_generated_skill(manager, "gimp")
    with_skill = _package(adapter, package_id)
    assert with_skill is not None
    assert with_skill.revision != baseline


def test_one_malformed_entry_is_bounded_and_hides_no_other_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC13: a broken row becomes one diagnostic package and suppresses nothing."""
    manager = _manager(tmp_path)
    _write_installed(
        manager,
        {
            "broken": "this entry is not an object",
            "gimp": _entry(),
            "": _entry(),
        },
    )
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        _resolve_only("cli-anything-gimp"),
    )

    snapshot = _adapter(manager).snapshot()
    packages = {package.name: package for package in snapshot.packages}
    broken = packages["broken"]

    assert packages["gimp"].lifecycle is ExtensionLifecycle.ENABLED
    assert broken.lifecycle is ExtensionLifecycle.FAILED
    assert broken.components == ()
    assert broken.diagnostic is not None
    assert broken.diagnostic.code == "installed_entry_malformed"
    assert len(broken.diagnostic.message) <= 1_000
    # The unusable empty name cannot become a package, so it is reported adapter-wide.
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == [
        "cli_app_projection_failed"
    ]


def test_an_unavailable_manager_reports_one_diagnostic_instead_of_an_empty_host(
    tmp_path: Path,
) -> None:
    """A failing owner is visible, not silently rendered as "no CLI apps installed"."""
    del tmp_path

    def broken_loader() -> CliAppManager:
        raise RuntimeError("no runtime data directory")

    snapshot = CliAppExtensionAdapter(broken_loader).snapshot()

    assert snapshot.packages == ()
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == [
        "cli_app_inventory_unavailable"
    ]


def test_snapshot_leaves_durable_state_and_generated_files_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC14: adding the projection changes no app's installed state as a side effect."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry()})
    plugin_root = _write_generated_skill(manager, "gimp")
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        _resolve_only("cli-anything-gimp"),
    )
    before = {
        path: path.read_bytes()
        for path in sorted((tmp_path).rglob("*"))
        if path.is_file()
    }

    _adapter(manager).snapshot()

    after = {
        path: path.read_bytes()
        for path in sorted((tmp_path).rglob("*"))
        if path.is_file()
    }
    assert after == before
    assert (plugin_root / "plugin.json").is_file()


def test_the_generated_manifest_carries_no_control_plane_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: ownership is published from installed state, never written under plugins/."""
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_fetch_skill_content", lambda app: None)

    manager.install_skill({"name": "gimp", "display_name": "GIMP", "version": "1.4.0"})

    manifest = json.loads(
        (manager.workspace / "plugins" / "cli-app-gimp" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    # These bytes are inside the package fingerprint that binds plugin enablement.
    assert "extensions" not in manifest
    assert set(manifest) <= {"$schema", "name", "version", "description"}
    # It remains a valid Agent Plugins v1 manifest.
    assert manifest["$schema"] == AGENT_PLUGIN_SCHEMA
    assert manifest["name"] == "cli-app-gimp"


def test_installed_entries_read_durable_state_without_the_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inventory accessor is the durable file, not the catalog projection."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry()})

    def _no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("installed_entries must not reach the network")

    monkeypatch.setattr("nanobot.apps.cli.service.httpx.get", _no_network)

    entries = manager.installed_entries()

    assert set(entries) == {"gimp"}
    assert entries["gimp"]["entry_point"] == "cli-anything-gimp"
    # A defensive copy: a caller cannot mutate the manager's view of installed state.
    entries.pop("gimp")
    assert set(manager.installed_entries()) == {"gimp"}
