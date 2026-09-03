from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from nanobot.agent import plugins as agent_plugins
from nanobot.agent.plugins import (
    AGENT_PLUGIN_MCP_SCHEMA,
    AGENT_PLUGIN_SCHEMA,
    discover_agent_plugins,
    set_agent_plugin_enabled,
)
from nanobot.extensions.adapters.agent_plugins import AgentPluginExtensionAdapter
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
from nanobot.extensions.registry import ExtensionRegistry, ExtensionRegistryError


@pytest.fixture(autouse=True)
def _isolate_plugin_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_plugins, "get_config_path", lambda: tmp_path / "config" / "config.json"
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _plugin(workspace: Path, name: str = "desktop", **fields: object) -> Path:
    root = workspace / "plugins" / name
    _write_json(
        root / "plugin.json",
        {
            "$schema": AGENT_PLUGIN_SCHEMA,
            "name": name,
            **fields,
        },
    )
    return root


def _skill(plugin: Path, name: str) -> None:
    (plugin / "skills" / name).mkdir(parents=True)
    (plugin / "skills" / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill.\n---\n",
        encoding="utf-8",
    )


def _mcp(plugin: Path, server_name: str, *, marker: str) -> None:
    (plugin / "server.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(plugin / marker)!r}).write_text('started', encoding='utf-8')\n",
        encoding="utf-8",
    )
    _write_json(
        plugin / "mcp.json",
        {
            "$schema": AGENT_PLUGIN_MCP_SCHEMA,
            "mcpServers": {
                server_name: {
                    "type": "stdio",
                    "command": "python",
                    "args": ["${PLUGIN_ROOT}/server.py", "--private-argument"],
                    "env": {"PLUGIN_PRIVATE_TOKEN": "not-for-extension-output"},
                    "cwd": "${PLUGIN_ROOT}",
                }
            },
        },
    )


def _request(
    target_id: str,
    action: ExtensionAction,
    *,
    expected_revision: str | None = None,
    risk_acknowledged: bool = False,
) -> ExtensionActionRequest:
    return ExtensionActionRequest(
        context=ExtensionActionContext(actor_id="operator", is_system_admin=True),
        target_id=target_id,
        action=action,
        expected_revision=expected_revision,
        risk_acknowledged=risk_acknowledged,
    )


def _marker_paths(workspace: Path, name: str = "desktop") -> list[Path]:
    return list((workspace / "config" / "plugin-data").glob(f"*/{name}/enabled"))


def test_snapshot_projects_safe_agent_plugin_components_and_declared_permissions(
    tmp_path: Path,
) -> None:
    plugin = _plugin(
        tmp_path,
        extensions={
            "dev.nanobot": {
                "displayName": "Desktop Bridge",
                "permissions": ["network", "filesystem"],
            }
        },
    )
    _skill(plugin, "notes")
    _mcp(plugin, "GitHub Search!", marker="mcp-was-started")

    snapshot = AgentPluginExtensionAdapter(tmp_path).snapshot()

    assert snapshot.adapter_name == "agent-plugins"
    [package] = snapshot.packages
    package_id = extension_package_id(ExtensionSource.AGENT_PLUGIN, "desktop")
    canonical_mcp_name = f"github-search-{sha256(b'GitHub Search!').hexdigest()[:32]}"
    components = {(component.kind, component.name): component for component in package.components}

    assert package.id == package_id
    assert package.revision is not None
    assert package.source is ExtensionSource.AGENT_PLUGIN
    assert package.trust is ExtensionTrust.OPERATOR_TRUSTED
    assert package.execution is ExtensionExecution.CHILD_PROCESS
    assert package.isolated is False
    assert package.permissions == ("filesystem", "network")
    assert package.permissions_enforced is False
    assert package.actions == frozenset({ExtensionAction.ENABLE, ExtensionAction.DISABLE})
    assert set(components) == {
        (ExtensionComponentKind.SKILL, "notes"),
        (ExtensionComponentKind.MCP_SERVER, canonical_mcp_name),
    }
    assert components[ExtensionComponentKind.SKILL, "notes"].id == extension_component_id(
        package_id,
        ExtensionComponentKind.SKILL,
        "notes",
    )
    assert components[ExtensionComponentKind.MCP_SERVER, canonical_mcp_name].id == extension_component_id(
        package_id,
        ExtensionComponentKind.MCP_SERVER,
        canonical_mcp_name,
    )
    assert components[ExtensionComponentKind.MCP_SERVER, canonical_mcp_name].display_name == "GitHub Search!"

    public_output = repr(snapshot)
    for private_value in (
        str(plugin),
        str(tmp_path / "config"),
        "${PLUGIN_ROOT}",
        "--private-argument",
        "PLUGIN_PRIVATE_TOKEN",
        "not-for-extension-output",
    ):
        assert private_value not in public_output


def test_snapshot_revalidates_fingerprint_before_publishing_enabled_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(tmp_path)
    _skill(plugin, "notes")
    set_agent_plugin_enabled(tmp_path, "desktop", True)
    original = agent_plugins._package_fingerprint
    calls = 0

    def counted_fingerprint(root: Path) -> str | None:
        nonlocal calls
        calls += 1
        return original(root)

    monkeypatch.setattr(agent_plugins, "_package_fingerprint", counted_fingerprint)

    [package] = AgentPluginExtensionAdapter(tmp_path).snapshot().packages

    assert 2 <= calls <= 3
    assert package.revision is not None
    assert package.lifecycle is ExtensionLifecycle.ENABLED


def test_skills_only_plugin_is_projected_as_data_with_only_skill_components(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    _skill(plugin, "notes")

    [package] = AgentPluginExtensionAdapter(tmp_path).snapshot().packages

    assert package.execution is ExtensionExecution.DATA
    assert {(component.kind, component.name) for component in package.components} == {
        (ExtensionComponentKind.SKILL, "notes")
    }


@pytest.mark.asyncio
async def test_registry_actions_change_only_marker_and_return_adapter_owned_results(
    tmp_path: Path,
) -> None:
    plugin = _plugin(tmp_path)
    _mcp(plugin, "desktop", marker="mcp-was-started")
    adapter = AgentPluginExtensionAdapter(tmp_path)
    registry = ExtensionRegistry()
    registry.register(adapter)
    [package] = registry.snapshot().packages

    enabled = await registry.execute(
        _request(
            package.id,
            ExtensionAction.ENABLE,
            expected_revision=package.revision,
            risk_acknowledged=True,
        )
    )

    assert enabled.ok is True
    assert enabled.action is ExtensionAction.ENABLE
    assert enabled.package_id == package.id
    assert enabled.target_id == package.id
    assert enabled.lifecycle is ExtensionLifecycle.ENABLED
    assert len(_marker_paths(tmp_path)) == 1
    assert not (plugin / "mcp-was-started").exists()

    disabled = await registry.execute(_request(package.id, ExtensionAction.DISABLE))

    assert disabled.ok is True
    assert disabled.action is ExtensionAction.DISABLE
    assert disabled.package_id == package.id
    assert disabled.target_id == package.id
    assert disabled.lifecycle is ExtensionLifecycle.DISABLED
    assert _marker_paths(tmp_path) == []
    assert not (plugin / "mcp-was-started").exists()


@pytest.mark.asyncio
async def test_replacement_revokes_existing_marker_and_rejects_listed_revision(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    _mcp(plugin, "desktop", marker="mcp-was-started")
    set_agent_plugin_enabled(tmp_path, "desktop", True)
    adapter = AgentPluginExtensionAdapter(tmp_path)
    registry = ExtensionRegistry()
    registry.register(adapter)
    [listed] = registry.snapshot().packages

    _mcp(plugin, "desktop", marker="replacement")
    [replacement] = adapter.snapshot().packages

    assert replacement.revision != listed.revision
    assert replacement.lifecycle is ExtensionLifecycle.DISABLED
    assert _marker_paths(tmp_path) == []
    with pytest.raises(ExtensionRegistryError) as error:
        await registry.execute(
            _request(
                listed.id,
                ExtensionAction.ENABLE,
                expected_revision=listed.revision,
                risk_acknowledged=True,
            )
        )
    assert error.value.code == "stale_revision"
    assert _marker_paths(tmp_path) == []
    assert discover_agent_plugins(tmp_path)[0].enabled is False


def test_mcp_discovery_drops_changed_package_after_marker_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(tmp_path)
    _mcp(plugin, "trusted", marker="trusted-server")
    set_agent_plugin_enabled(tmp_path, "desktop", True)
    original_mcp_servers = agent_plugins._plugin_mcp_servers
    changed = False

    def materialize_replacement(workspace: Path, plugin_state: object) -> object:
        nonlocal changed
        if not changed:
            changed = True
            _mcp(plugin, "replacement", marker="replacement-server")
        return original_mcp_servers(workspace, plugin_state)  # type: ignore[arg-type]

    monkeypatch.setattr(agent_plugins, "_plugin_mcp_servers", materialize_replacement)

    assert agent_plugins.agent_plugin_mcp_servers(tmp_path) == {}
    assert _marker_paths(tmp_path) == []


def test_discovery_drops_changed_declarations_before_reporting_enabled_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(tmp_path)
    _skill(plugin, "trusted")
    _mcp(plugin, "trusted", marker="trusted-server")
    set_agent_plugin_enabled(tmp_path, "desktop", True)
    original_skills = agent_plugins._discover_plugin_skills
    changed = False

    def materialize_replacement(plugin_name: str, plugin_root: Path) -> list[tuple[str, Path]]:
        nonlocal changed
        if not changed:
            changed = True
            (plugin / "skills" / "trusted").rename(plugin / "skills" / "replacement")
            _mcp(plugin, "replacement", marker="replacement-server")
        return original_skills(plugin_name, plugin_root)

    monkeypatch.setattr(agent_plugins, "_discover_plugin_skills", materialize_replacement)

    [state] = discover_agent_plugins(tmp_path)

    assert state.enabled is False
    assert state.skills == ()
    assert state.mcp_servers == ()
    assert _marker_paths(tmp_path) == []


def test_invalid_plugin_projection_keeps_other_packages_visible(tmp_path: Path) -> None:
    _plugin(tmp_path, "healthy")
    _plugin(
        tmp_path,
        "oversized",
        extensions={
            "dev.nanobot": {
                "permissions": [f"permission-{index}" for index in range(129)],
            }
        },
    )

    packages = {
        package.name: package
        for package in AgentPluginExtensionAdapter(tmp_path).snapshot().packages
    }

    assert set(packages) == {"healthy", "oversized"}
    assert packages["healthy"].diagnostic is None
    assert packages["oversized"].components == ()
    assert packages["oversized"].diagnostic is not None
    assert packages["oversized"].diagnostic.code == "projection_failed"


def test_legacy_marker_migration_revalidates_package_after_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(tmp_path)
    set_agent_plugin_enabled(tmp_path, "desktop", True)
    marker = _marker_paths(tmp_path)[0]
    marker.write_text(str(plugin), encoding="utf-8")
    original_fingerprint = agent_plugins._package_fingerprint
    calls = 0

    def changed_after_migration(root: Path) -> str | None:
        nonlocal calls
        calls += 1
        return original_fingerprint(root) if calls == 1 else "changed-after-migration"

    monkeypatch.setattr(agent_plugins, "_package_fingerprint", changed_after_migration)

    [plugin_state] = discover_agent_plugins(tmp_path)

    assert plugin_state.enabled is False
    assert _marker_paths(tmp_path) == []
