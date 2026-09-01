"""Safe, file-free MCP configuration interchange with Codex."""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from nanobot.config.schema import Config, MCPServerConfig

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_CREDENTIAL_NAME = re.compile(
    r"(?:authorization|credential|secret|token|api[-_]?key|password|cookie|bearer|auth)",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE = re.compile(
    r"(?:^|\s)(?:bearer|basic)\s+|(?:^|[^A-Za-z0-9])(?:sk-|ghp_|github_pat_|glpat-|xox[baprs]-|ya29\.|eyJ)[A-Za-z0-9._-]+",
    re.IGNORECASE,
)
_SHELL_SYNTAX = frozenset(";$`|&<>(){}")


MCPServer = dict[str, object]
MCPServers = dict[str, MCPServer]
MCPConfig = dict[str, MCPServers]


@dataclass(frozen=True, slots=True)
class CodexConfigExport:
    """A rendered Codex fragment and any intentionally omitted fields."""

    toml: str
    warnings: tuple[str, ...]


def codex_mcp_config(
    servers: Mapping[str, MCPServerConfig],
    *,
    project: str | Path | None = None,
) -> CodexConfigExport:
    """Render configured MCP servers as a safe Codex TOML fragment.

    ``project`` is deliberately only an export-time CWD override. This function
    never creates a Codex file or changes the active nanobot configuration.
    """
    warnings: list[str] = []
    lines: list[str] = []
    project_cwd = _safe_path(project, "project") if project is not None else None

    for name, server in sorted(servers.items()):
        try:
            _require_safe_text(name, "server name")
            server_lines, server_warnings = _render_server(name, server, project_cwd)
        except ValueError as exc:
            warnings.append(f"Skipped MCP server {name!r}: {exc}")
            continue
        lines.extend(server_lines)
        lines.append("")
        warnings.extend(server_warnings)

    if lines:
        lines.pop()
    return CodexConfigExport("\n".join(lines) + ("\n" if lines else ""), tuple(warnings))


def configured_codex_mcp_config(
    config: Config,
    *,
    project: str | Path | None = None,
) -> CodexConfigExport:
    """Export configured and explicitly enabled Agent Plugin MCP servers."""
    from nanobot.agent.plugins import agent_plugin_mcp_servers

    servers = agent_plugin_mcp_servers(config.workspace_path, config.tools.mcp_servers)
    return codex_mcp_config(servers, project=project)


def codex_toml_to_nanobot_mcp(toml_text: str) -> MCPConfig:
    """Convert Codex TOML into nanobot's WebUI-compatible ``mcpServers`` JSON.

    Inline Codex environment values are never copied: imports preserve only an
    environment-variable reference, so printing a dry run cannot disclose them.
    """
    try:
        payload = _as_object(tomllib.loads(toml_text))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid Codex TOML: {exc}") from exc
    if payload is None:
        raise ValueError("Codex TOML must be a table")

    raw_servers = payload.get("mcp_servers")
    if raw_servers is None:
        return {"mcpServers": {}}
    servers_table = _as_object(raw_servers)
    if servers_table is None:
        raise ValueError("Codex 'mcp_servers' must be a table")

    servers: MCPServers = {}
    for name, raw_server in servers_table.items():
        server_table = _as_object(raw_server)
        if server_table is None:
            continue
        _require_safe_text(name, "server name")
        if server_table.get("enabled") is False:
            continue
        converted = _import_server(server_table)
        if converted is not None:
            servers[name] = converted
    return {"mcpServers": servers}


def read_codex_toml(path: str | Path) -> MCPConfig:
    """Read a Codex TOML file and convert it without writing any configuration."""
    config_path = Path(path).expanduser()
    try:
        return codex_toml_to_nanobot_mcp(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read Codex config {config_path}: {exc.strerror or exc}") from exc


def _render_server(
    name: str,
    server: MCPServerConfig,
    project_cwd: str | None,
) -> tuple[list[str], list[str]]:
    server_type = server.type or ("streamableHttp" if server.url else "stdio")
    if server_type == "stdio":
        return _render_stdio_server(name, server, project_cwd)
    if server_type == "streamableHttp":
        return _render_http_server(name, server)
    raise ValueError(f"Codex does not support nanobot MCP transport {server_type!r}")


def _render_stdio_server(
    name: str,
    server: MCPServerConfig,
    project_cwd: str | None,
) -> tuple[list[str], list[str]]:
    command = _safe_command(server.command)
    args = [_safe_text(item, f"argument for {name!r}") for item in server.args]
    cwd = project_cwd or (_safe_path(server.cwd, f"cwd for {name!r}") if server.cwd else None)
    env_lines, warnings = _render_environment(name, server.env)

    lines = [f"[mcp_servers.{_toml_key(name)}]", f"command = {_toml_string(command)}"]
    if args:
        lines.append(f"args = {_toml_array(args)}")
    if cwd:
        lines.append(f"cwd = {_toml_string(cwd)}")
    lines.append("enabled = true")
    lines.append(f"tool_timeout_sec = {_safe_timeout(server.tool_timeout, name)}")
    lines.extend(_render_enabled_tools(server.enabled_tools, name))
    lines.extend(env_lines)
    return lines, warnings


def _render_http_server(name: str, server: MCPServerConfig) -> tuple[list[str], list[str]]:
    url = _safe_url(server.url, name)
    header_entries, warnings = _render_http_headers(name, server.headers)

    lines = [f"[mcp_servers.{_toml_key(name)}]", f"url = {_toml_string(url)}", "enabled = true"]
    lines.append(f"tool_timeout_sec = {_safe_timeout(server.tool_timeout, name)}")
    lines.extend(_render_enabled_tools(server.enabled_tools, name))
    lines.extend(header_entries)
    return lines, warnings


def _render_enabled_tools(enabled_tools: list[str], name: str) -> list[str]:
    tools = [_safe_text(tool, f"enabled tool for {name!r}") for tool in enabled_tools]
    return [f"enabled_tools = {_toml_array(tools)}"] if tools else []


def _render_environment(name: str, env: Mapping[str, str]) -> tuple[list[str], list[str]]:
    names: list[str] = []
    warnings: list[str] = []
    for key, value in sorted(env.items()):
        if not _ENV_NAME.fullmatch(key):
            warnings.append(f"Omitted invalid environment variable name {key!r} for {name!r}")
            continue
        _require_safe_text(value, f"environment value for {name!r}")
        reference = _ENV_REF.fullmatch(value)
        if reference is not None and reference.group(1) != key:
            warnings.append(
                f"Omitted unsupported environment reference {value!r} for {key!r} on {name!r}; "
                "Codex cannot rename inherited variables"
            )
            continue
        names.append(key)
        if reference is None:
            warnings.append(
                f"Omitted inline environment value for {key!r} on {name!r}; "
                f"Codex will inherit {key}"
            )
    lines = [f"env_vars = {_toml_array(names)}"] if names else []
    return lines, warnings


def _render_http_headers(name: str, headers: Mapping[str, str]) -> tuple[list[str], list[str]]:
    literal: list[tuple[str, str]] = []
    env_headers: list[tuple[str, str]] = []
    env_names: list[str] = []
    warnings: list[str] = []
    for key, value in sorted(headers.items()):
        _require_safe_text(key, f"header name for {name!r}")
        _require_safe_text(value, f"header value for {name!r}")
        if _looks_like_credential(key, value):
            warnings.append(f"Omitted credential-like HTTP header {key!r} for {name!r}")
            continue
        reference = _ENV_REF.fullmatch(value)
        if reference:
            variable = reference.group(1)
            env_headers.append((key, variable))
            env_names.append(variable)
        else:
            literal.append((key, value))
    lines: list[str] = []
    if env_names:
        lines.append(f"env_vars = {_toml_array(sorted(set(env_names)))}")
    if literal:
        lines.append("http_headers = {" + ", ".join(
            f"{_toml_key(key)} = {_toml_string(value)}" for key, value in literal
        ) + "}")
    if env_headers:
        lines.append("env_http_headers = {" + ", ".join(
            f"{_toml_key(key)} = {_toml_string(variable)}" for key, variable in env_headers
        ) + "}")
    return lines, warnings


def _import_server(raw: Mapping[str, object]) -> MCPServer | None:
    has_url = isinstance(raw.get("url"), str) and bool(raw["url"])
    has_command = isinstance(raw.get("command"), str) and bool(raw["command"])
    if has_url == has_command:
        return None

    enabled_tools = _string_list(raw.get("enabled_tools"))
    timeout = raw.get("tool_timeout_sec", 30)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 0:
        timeout = 30

    if has_url:
        url = _safe_url(raw["url"], "imported server")
        result: MCPServer = {
            "type": "streamableHttp",
            "url": url,
            "tool_timeout": timeout,
            "enabled_tools": enabled_tools or ["*"],
        }
        headers = _import_headers(raw)
        if headers:
            result["headers"] = headers
        return result

    command = _safe_command(raw["command"])
    args = [_safe_text(item, "imported argument") for item in _string_list(raw.get("args"))]
    result = {
        "type": "stdio",
        "command": command,
        "args": args,
        "tool_timeout": timeout,
        "enabled_tools": enabled_tools or ["*"],
    }
    if isinstance(raw.get("cwd"), str) and raw["cwd"]:
        result["cwd"] = _safe_path(raw["cwd"], "imported cwd")
    env = _import_environment(raw)
    if env:
        result["env"] = env
    return result


def _import_environment(raw: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    env_vars = _string_list(raw.get("env_vars"))
    for name in env_vars:
        if _ENV_NAME.fullmatch(name):
            result[name] = f"${{{name}}}"
    environment = _as_object(raw.get("env"))
    if environment is None:
        return result
    for name, value in environment.items():
        if not _ENV_NAME.fullmatch(name) or not isinstance(value, str):
            continue
        reference = _ENV_REF.fullmatch(value)
        result[name] = value if reference else f"${{{name}}}"
    return result


def _import_headers(raw: Mapping[str, object]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in _toml_table_strings(raw.get("http_headers")).items():
        if not _looks_like_credential(key, value):
            headers[key] = value
    for key, env_name in _toml_table_strings(raw.get("env_http_headers")).items():
        if not _looks_like_credential(key, env_name) and _ENV_NAME.fullmatch(env_name):
            headers[key] = f"${{{env_name}}}"
    bearer = raw.get("bearer_token_env_var")
    if isinstance(bearer, str) and _ENV_NAME.fullmatch(bearer):
        headers["Authorization"] = f"Bearer ${{{bearer}}}"
    return headers


def _as_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _toml_table_strings(value: object) -> dict[str, str]:
    table = _as_object(value)
    if table is None:
        return {}
    return {
        key: item
        for key, item in table.items()
        if isinstance(item, str) and _is_safe_text(key) and _is_safe_text(item)
    }


def _string_list(value: object) -> list[str]:
    return [item for item in _as_list(value) if isinstance(item, str)]


def _safe_command(value: object) -> str:
    command = _safe_text(value, "command")
    if any(character.isspace() for character in command) or any(
        character in _SHELL_SYNTAX for character in command
    ):
        raise ValueError("command must be one executable, with arguments supplied separately")
    return command


def _safe_url(value: object, name: str) -> str:
    url = _safe_text(value, f"URL for {name!r}")
    if not url.startswith(("https://", "http://")):
        raise ValueError("URL must start with http:// or https://")
    if "@" in url.split("://", 1)[1].split("/", 1)[0]:
        raise ValueError("URL must not embed credentials")
    return url


def _safe_path(value: str | Path | object, label: str) -> str:
    return _safe_text(str(value), label)


def _safe_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    _require_safe_text(value, label)
    return value


def _require_safe_text(value: str, label: str) -> None:
    if not value:
        raise ValueError(f"{label} must not be empty")
    if not _is_safe_text(value):
        raise ValueError(f"{label} must not contain newlines or control characters")


def _is_safe_text(value: str) -> bool:
    return not any(unicodedata.category(character) == "Cc" for character in value)


def _looks_like_credential(key: str, value: str) -> bool:
    return bool(
        _CREDENTIAL_NAME.search(key)
        or _CREDENTIAL_NAME.search(value)
        or _CREDENTIAL_VALUE.search(value)
    )


def _safe_timeout(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 600:
        raise ValueError(f"tool timeout for {name!r} must be an integer from 1 to 600")
    return value


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_key(value: str) -> str:
    return _toml_string(value)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"
