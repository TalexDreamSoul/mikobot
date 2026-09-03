from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.agent.skills import SkillsLoader
from nanobot.config.schema import MCPServerConfig
from nanobot.extensions.adapters.capabilities import (
    ConfiguredMcpExtensionAdapter,
    EffectiveSkillsExtensionAdapter,
)
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionActionContext,
    ExtensionActionRequest,
    ExtensionExecution,
    ExtensionLifecycle,
    ExtensionSource,
    ExtensionTrust,
    extension_package_id,
)


class _SkillLoader:
    def __init__(self, skills: list[dict[str, str]]) -> None:
        self._skills = skills

    def list_skills(self, *, filter_unavailable: bool = True) -> list[dict[str, str]]:
        return self._skills


def _write_skill(root: Path, name: str, content: str) -> Path:
    skill_file = root / name / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


def _configured_adapter(
    servers: dict[str, MCPServerConfig],
    statuses: dict[str, str] | None = None,
) -> ConfiguredMcpExtensionAdapter:
    config = SimpleNamespace(tools=SimpleNamespace(mcp_servers=servers))
    return ConfiguredMcpExtensionAdapter(lambda: config, lambda: statuses or {})


def _request() -> ExtensionActionRequest:
    return ExtensionActionRequest(
        context=ExtensionActionContext(actor_id="operator", is_system_admin=True),
        target_id=extension_package_id(ExtensionSource.CONFIGURED, "server"),
        action=ExtensionAction.RECONNECT,
    )


def test_effective_skills_preserve_loader_precedence_and_exclude_plugin_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only workspace/builtin winners selected by SkillsLoader become standalone packages."""
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "builtin"
    _write_skill(workspace / "skills", "shared", "workspace winner")
    _write_skill(builtin, "shared", "builtin loser")
    _write_skill(builtin, "builtin-only", "builtin winner")
    plugin_shared = _write_skill(tmp_path / "plugin", "shared", "plugin loser")
    plugin_only = _write_skill(tmp_path / "plugin", "plugin-only", "plugin-owned")
    monkeypatch.setattr(
        "nanobot.agent.plugins.enabled_agent_plugin_skills",
        lambda _workspace: (("shared", plugin_shared), ("plugin-only", plugin_only)),
    )

    snapshot = EffectiveSkillsExtensionAdapter(SkillsLoader(workspace, builtin)).snapshot()

    packages = {(package.source, package.name): package for package in snapshot.packages}
    assert set(packages) == {
        (ExtensionSource.WORKSPACE, "shared"),
        (ExtensionSource.BUILTIN, "builtin-only"),
    }
    assert packages[ExtensionSource.WORKSPACE, "shared"].revision == sha256(
        b"workspace winner"
    ).hexdigest()
    assert packages[ExtensionSource.BUILTIN, "builtin-only"].revision == sha256(
        b"builtin winner"
    ).hexdigest()
    assert packages[ExtensionSource.WORKSPACE, "shared"].trust is ExtensionTrust.WORKSPACE_CONTENT
    assert packages[ExtensionSource.BUILTIN, "builtin-only"].trust is ExtensionTrust.FIRST_PARTY


def test_effective_skills_hash_safe_content_and_report_unreadable_or_oversized_files(
    tmp_path: Path,
) -> None:
    """Skill revisions describe bounded selected content while failures reveal neither paths nor bytes."""
    safe = _write_skill(tmp_path, "safe", "safe content")
    oversized = _write_skill(tmp_path, "oversized", "x" * ((1 << 20) + 1))
    missing = tmp_path / "private" / "unreadable" / "SKILL.md"
    loader = _SkillLoader(
        [
            {"name": "safe", "source": "workspace", "path": str(safe)},
            {"name": "unreadable", "source": "workspace", "path": str(missing)},
            {"name": "oversized", "source": "builtin", "path": str(oversized)},
        ]
    )

    snapshot = EffectiveSkillsExtensionAdapter(loader).snapshot()  # type: ignore[arg-type]

    packages = {package.name: package for package in snapshot.packages}
    assert packages["safe"].revision == sha256(b"safe content").hexdigest()
    assert packages["unreadable"].revision is None
    assert packages["oversized"].revision is None
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == [
        "skill_revision_oversized",
        "skill_revision_unavailable",
    ]
    exposed = repr(snapshot)
    assert str(missing) not in exposed
    assert str(oversized) not in exposed
    assert "safe content" not in exposed


def test_configured_mcp_classifies_transports_and_excludes_all_sensitive_values() -> None:
    """MCP packages disclose transport class but never connection credentials or process details."""
    secret = "super-secret-value"
    servers = {
        "Private MCP!": MCPServerConfig(
            type="stdio",
            command="/private/bin/mcp-server",
            args=["--credential", secret],
            env={"PRIVATE_ENV": secret},
            cwd="/private/runtime",
            enabled_tools=["search"],
            tool_timeout=17,
        ),
        "Remote MCP!": MCPServerConfig(
            type="streamableHttp",
            auth="oauth",
            url=f"https://user:{secret}@mcp.example.test/connect?token={secret}",
            headers={"Authorization": f"Bearer {secret}"},
            enabled_tools=["query"],
            tool_timeout=29,
        ),
    }

    snapshot = _configured_adapter(servers, {"Private MCP!": "connected"}).snapshot()

    packages = {package.display_name: package for package in snapshot.packages}
    stdio = packages["Private MCP!"]
    remote = packages["Remote MCP!"]
    assert stdio.trust is ExtensionTrust.OPERATOR_TRUSTED
    assert stdio.execution is ExtensionExecution.CHILD_PROCESS
    assert stdio.isolated is False
    assert stdio.lifecycle is ExtensionLifecycle.ENABLED
    assert remote.trust is ExtensionTrust.REMOTE_SERVICE
    assert remote.execution is ExtensionExecution.REMOTE
    assert remote.isolated is None
    assert remote.lifecycle is ExtensionLifecycle.DISCOVERED
    assert stdio.configuration is not None
    assert (stdio.configuration.section, stdio.configuration.item) == ("apps", "mcp")
    assert stdio.components[0].configuration == stdio.configuration
    exposed = repr(snapshot)
    for private_value in (
        secret,
        "/private/bin/mcp-server",
        "--credential",
        "PRIVATE_ENV",
        "/private/runtime",
        "Authorization",
        "user:",
        "?token=",
    ):
        assert private_value not in exposed


def test_configured_mcp_revisions_use_safe_structure_not_secret_values() -> None:
    """Changing only credentials, argv, paths, or URL userinfo/query cannot change a public revision."""
    first = MCPServerConfig(
        type="streamableHttp",
        auth="oauth",
        command="/private/first-command",
        args=["--token", "first-secret"],
        env={"API_TOKEN": "first-secret"},
        cwd="/private/first-cwd",
        url="https://first-user:first-secret@mcp.example.test/connect?token=first-secret",
        headers={"Authorization": "Bearer first-secret"},
        enabled_tools=["query"],
        tool_timeout=11,
    )
    replacement = MCPServerConfig(
        type="streamableHttp",
        auth="oauth",
        command="/different/command",
        args=["--token", "replacement-secret"],
        env={"API_TOKEN": "replacement-secret"},
        cwd="/different/cwd",
        url="https://replacement-user:replacement-secret@mcp.example.test/connect?token=replacement-secret",
        headers={"Authorization": "Bearer replacement-secret"},
        enabled_tools=["query"],
        tool_timeout=11,
    )

    first_revision = _configured_adapter({"remote": first}).snapshot().packages[0].revision
    replacement_revision = _configured_adapter({"remote": replacement}).snapshot().packages[0].revision
    structural_replacement = replacement.model_copy(
        update={"headers": {"X-Replaced-Header": "Bearer replacement-secret"}}
    )
    structural_revision = _configured_adapter(
        {"remote": structural_replacement}
    ).snapshot().packages[0].revision

    assert replacement_revision == first_revision
    assert structural_revision != first_revision


def test_configured_mcp_canonicalizes_names_orders_packages_and_maps_runtime_status() -> None:
    """Arbitrary server names retain distinct canonical IDs and a 128-bit collision suffix."""
    arbitrary = "Vercel Search!"
    similar = "Vercel Search?"
    expected = {
        arbitrary: f"vercel-search-{sha256(arbitrary.encode()).hexdigest()[:32]}",
        similar: f"vercel-search-{sha256(similar.encode()).hexdigest()[:32]}",
    }
    servers = {
        arbitrary: MCPServerConfig(type="stdio", command="vercel-mcp"),
        similar: MCPServerConfig(type="stdio", command="vercel-mcp"),
        "gamma": MCPServerConfig(type="stdio", command="gamma-mcp"),
        "alpha": MCPServerConfig(type="stdio", command="alpha-mcp"),
        "beta": MCPServerConfig(type="stdio", command="beta-mcp"),
    }
    snapshot = _configured_adapter(
        servers,
        {arbitrary: "failed", "gamma": "connecting", "alpha": "connected"},
    ).snapshot()

    assert [package.name for package in snapshot.packages] == [
        "alpha",
        "beta",
        "gamma",
        *sorted(expected.values()),
    ]
    packages = {package.name: package for package in snapshot.packages}
    assert set(expected.values()).issubset(packages)
    assert expected[arbitrary] != expected[similar]
    assert all(len(name.rsplit("-", 1)[1]) == 32 for name in expected.values())
    assert packages[expected[arbitrary]].id == extension_package_id(
        ExtensionSource.CONFIGURED, expected[arbitrary]
    )
    assert packages[expected[arbitrary]].id != packages[expected[similar]].id
    assert packages[expected[arbitrary]].lifecycle is ExtensionLifecycle.FAILED
    assert packages["gamma"].lifecycle is ExtensionLifecycle.RELOADING
    assert packages["alpha"].lifecycle is ExtensionLifecycle.ENABLED
    assert packages["beta"].lifecycle is ExtensionLifecycle.DISCOVERED


@pytest.mark.asyncio
async def test_capability_adapters_declare_no_actions_and_reject_action_requests(tmp_path: Path) -> None:
    """Effective Skills and configured MCP inventory remains read-only despite executable targets."""
    skill = _write_skill(tmp_path, "notes", "notes")
    skills = EffectiveSkillsExtensionAdapter(
        _SkillLoader([{"name": "notes", "source": "workspace", "path": str(skill)}])  # type: ignore[arg-type]
    )
    mcp = _configured_adapter({"server": MCPServerConfig(type="stdio", command="server")})

    assert skills.snapshot().packages[0].actions == frozenset()
    assert skills.snapshot().packages[0].components[0].actions == frozenset()
    assert mcp.snapshot().packages[0].actions == frozenset()
    assert mcp.snapshot().packages[0].components[0].actions == frozenset()
    with pytest.raises(ValueError, match="do not support lifecycle actions"):
        await skills.execute(_request())
    with pytest.raises(ValueError, match="do not support lifecycle actions"):
        await mcp.execute(_request())
