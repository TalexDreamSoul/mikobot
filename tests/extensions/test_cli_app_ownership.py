"""Single canonical ownership for a CLI app and its generated Agent Plugin root."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent import plugins as agent_plugins
from nanobot.agent.plugins import (
    _package_fingerprint,
    discover_agent_plugins,
    enabled_agent_plugin_skills,
    set_agent_plugin_enabled,
)
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.apps.cli.service import (
    AGENT_PLUGIN_SCHEMA,
    CliAppManager,
    CliAppsRuntimeConfig,
    _write_json,
)
from nanobot.config.schema import Config
from nanobot.extensions.adapters.agent_plugins import AgentPluginExtensionAdapter
from nanobot.extensions.adapters.cli_apps import CliAppExtensionAdapter
from nanobot.extensions.contracts import ExtensionSnapshot, ExtensionSource
from nanobot.extensions.runtime import build_core_extension_registry

_NANOBOT = Path(__file__).resolve().parents[2] / "nanobot"


@pytest.fixture(autouse=True)
def _isolate_plugin_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_plugins, "get_config_path", lambda: tmp_path / "config/config.json")


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


def _write_plugin(workspace: Path, plugin_name: str) -> Path:
    """Write a valid Agent Plugin, exactly as a hand-installed one would appear."""
    root = workspace / "plugins" / plugin_name
    (root / "skills" / plugin_name).mkdir(parents=True, exist_ok=True)
    (root / "skills" / plugin_name / "SKILL.md").write_text(
        f"---\nname: {plugin_name}\ndescription: Operate {plugin_name}.\n---\n\n# {plugin_name}\n",
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(
        json.dumps({
            "$schema": AGENT_PLUGIN_SCHEMA,
            "name": plugin_name,
            "description": f"Operate {plugin_name}.",
        }),
        encoding="utf-8",
    )
    return root


def _entry(entry_point: str) -> dict[str, Any]:
    return {
        "version": "1.4.0",
        "entry_point": entry_point,
        "source": "harness",
        "strategy": "pip",
    }


def _composed(manager: CliAppManager, monkeypatch: pytest.MonkeyPatch) -> ExtensionSnapshot:
    config = Config()
    config.agents.defaults.workspace = str(manager.workspace)
    monkeypatch.setattr(
        "nanobot.extensions.adapters.cli_apps.shutil.which",
        lambda command: f"/host/bin/{command}",
    )
    registry = build_core_extension_registry(
        config,
        ToolRegistry(),
        cli_app_owner=lambda: manager,
    )
    return registry.snapshot()


def _package_ids(snapshot: ExtensionSnapshot, source: ExtensionSource) -> list[str]:
    return [package.id for package in snapshot.packages if package.source is source]


def test_an_installed_cli_app_produces_exactly_one_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: the generated root no longer becomes a second Agent Plugin package."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry("cli-anything-gimp")})
    _write_plugin(manager.workspace, "cli-app-gimp")

    snapshot = _composed(manager, monkeypatch)

    assert _package_ids(snapshot, ExtensionSource.CLI_APP) == ["ext:cli_app:gimp"]
    assert _package_ids(snapshot, ExtensionSource.AGENT_PLUGIN) == []
    # The Skill is a component of the CLI app, and not a standalone Skill package.
    gimp = next(package for package in snapshot.packages if package.id == "ext:cli_app:gimp")
    assert [component.name for component in gimp.components] == ["gimp", "cli-app-gimp"]
    assert _package_ids(snapshot, ExtensionSource.WORKSPACE) == []
    # Nothing was written to achieve it: the app was not reinstalled or re-enabled.
    assert set(manager.installed_entries()) == {"gimp"}


def test_a_lookalike_directory_without_an_installed_app_stays_an_agent_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: a directory that merely looks generated is neither adopted nor hidden."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry("cli-anything-gimp")})
    _write_plugin(manager.workspace, "cli-app-gimp")
    _write_plugin(manager.workspace, "cli-app-impostor")
    _write_plugin(manager.workspace, "hand-installed")

    snapshot = _composed(manager, monkeypatch)

    assert _package_ids(snapshot, ExtensionSource.CLI_APP) == ["ext:cli_app:gimp"]
    assert _package_ids(snapshot, ExtensionSource.AGENT_PLUGIN) == [
        "ext:agent_plugin:cli-app-impostor",
        "ext:agent_plugin:hand-installed",
    ]


def test_ownership_claim_comes_from_installed_state_not_a_name_prefix(
    tmp_path: Path,
) -> None:
    """The manager claims exactly the roots its own durable inventory names."""
    manager = _manager(tmp_path)
    _write_installed(
        manager,
        {"gimp": _entry("cli-anything-gimp"), "my_app": _entry("cli-anything-my-app")},
    )

    assert manager.managed_plugin_names() == frozenset({"cli-app-gimp", "cli-app-my-app"})
    assert CliAppExtensionAdapter(lambda: manager).owned_plugin_names() == frozenset(
        {"cli-app-gimp", "cli-app-my-app"}
    )

    _write_installed(manager, {})
    assert manager.managed_plugin_names() == frozenset()


def test_the_default_construction_projects_every_plugin_unfiltered(tmp_path: Path) -> None:
    """The new parameter changes nothing for a caller that does not pass it."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_plugin(workspace, "cli-app-gimp")
    _write_plugin(workspace, "hand-installed")

    default = AgentPluginExtensionAdapter(workspace).snapshot()
    explicit_none = AgentPluginExtensionAdapter(workspace, owned_plugin_names=None).snapshot()
    empty_claim = AgentPluginExtensionAdapter(
        workspace, owned_plugin_names=frozenset
    ).snapshot()

    assert [package.id for package in default.packages] == [
        "ext:agent_plugin:cli-app-gimp",
        "ext:agent_plugin:hand-installed",
    ]
    assert explicit_none == default
    assert empty_claim == default


def test_only_the_core_composition_supplies_an_ownership_claim() -> None:
    """Every other construction site keeps the unfiltered default, by assertion."""
    sites: dict[str, list[bool]] = {}
    for path in sorted(_NANOBOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "AgentPluginExtensionAdapter"
            ):
                relative = path.relative_to(_NANOBOT.parent).as_posix()
                sites.setdefault(relative, []).append(
                    any(keyword.arg == "owned_plugin_names" for keyword in node.keywords)
                )

    assert sites == {"nanobot/extensions/runtime.py": [True]}


def test_a_failing_ownership_claim_keeps_plugins_visible(tmp_path: Path) -> None:
    """A broken owner restores a visible duplicate rather than hiding an extension."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_plugin(workspace, "cli-app-gimp")

    def broken() -> frozenset[str]:
        raise RuntimeError("installed state is unreadable")

    packages = (
        AgentPluginExtensionAdapter(workspace, owned_plugin_names=broken).snapshot().packages
    )

    assert [package.name for package in packages] == ["cli-app-gimp"]


def test_regenerating_a_skill_does_not_perturb_the_enablement_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bytes under `plugins/` carry no ownership, so enablement survives a rewrite.

    `_package_fingerprint` hashes every file in the root, including `plugin.json`, and
    a fingerprint that no longer matches the activation marker revokes it. Recording
    ownership in the manifest would therefore silently unload an enabled CLI app's
    Skill the first time the manager regenerated it.
    """
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_fetch_skill_content", lambda app: None)
    app = {"name": "gimp", "display_name": "GIMP", "version": "1.4.0"}
    root = manager.workspace / "plugins" / "cli-app-gimp"

    # An installation that predates this change: the manifest the previous code wrote.
    manager.install_skill(app)
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    _write_json(
        root / "plugin.json",
        {key: value for key, value in manifest.items() if key != "extensions"},
    )
    set_agent_plugin_enabled(manager.workspace, "cli-app-gimp", True)
    before = _package_fingerprint(root)
    assert enabled_agent_plugin_skills(manager.workspace) == [
        ("cli-app-gimp", root / "skills" / "cli-app-gimp" / "SKILL.md")
    ]

    manager.install_skill(app)

    assert "extensions" not in manifest
    assert set(manifest) <= {"$schema", "name", "version", "description"}
    assert _package_fingerprint(root) == before
    # Still loading, with no re-enable and no operator action in between.
    assert enabled_agent_plugin_skills(manager.workspace) == [
        ("cli-app-gimp", root / "skills" / "cli-app-gimp" / "SKILL.md")
    ]


def test_exclusion_leaves_skill_loading_precedence_and_markers_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: the exclusion is a projection rule and touches no Skill or marker state."""
    manager = _manager(tmp_path)
    _write_installed(manager, {"gimp": _entry("cli-anything-gimp")})
    root = _write_plugin(manager.workspace, "cli-app-gimp")
    set_agent_plugin_enabled(manager.workspace, "cli-app-gimp", True)

    # Plugin data, including the activation marker, lives beside the patched config path.
    plugin_data = tmp_path / "config"
    before_markers = {
        path: path.read_bytes() for path in sorted(plugin_data.rglob("*")) if path.is_file()
    }
    before_skills = enabled_agent_plugin_skills(manager.workspace)
    before_loaded = SkillsLoader(manager.workspace).load_skill("cli-app-gimp")

    snapshot = _composed(manager, monkeypatch)

    after_markers = {
        path: path.read_bytes() for path in sorted(plugin_data.rglob("*")) if path.is_file()
    }
    assert before_loaded is not None
    assert any(path.name == "enabled" for path in before_markers)
    assert SkillsLoader(manager.workspace).load_skill("cli-app-gimp") == before_loaded
    assert enabled_agent_plugin_skills(manager.workspace) == before_skills
    assert before_skills and before_skills[0][0] == "cli-app-gimp"
    assert after_markers == before_markers
    # The plugin is still discovered and enabled by its runtime owner; only the
    # control-plane projection changed.
    discovered = discover_agent_plugins(manager.workspace)
    assert [plugin.name for plugin in discovered] == ["cli-app-gimp"]
    assert discovered[0].enabled is True
    assert (root / "skills" / "cli-app-gimp" / "SKILL.md").is_file()
    assert _package_ids(snapshot, ExtensionSource.AGENT_PLUGIN) == []
