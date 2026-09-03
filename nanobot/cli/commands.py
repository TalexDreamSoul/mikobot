"""CLI commands for nanobot."""

# pyright: reportConstantRedefinition=false, reportMissingTypeStubs=false, reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedImport=false

import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Never

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        with suppress(Exception):
            for stream in (sys.stdout, sys.stderr):
                reconfigure = getattr(stream, "reconfigure", None)
                if callable(reconfigure):
                    reconfigure(encoding="utf-8", errors="replace")

# Keep console encoding setup before importing CLI UI/logging libraries.
import typer  # noqa: E402
from loguru import logger  # noqa: E402

# Remove default handler and re-add with unified nanobot format
logger.remove()
_log_handler_id = logger.add(
    sys.stderr,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <5}</level> | "
        "<cyan>{extra[channel]}</cyan> | "
        "<level>{message}</level>"
    ),
    level="INFO",
    colorize=None,
    filter=lambda record: record["extra"].setdefault("channel", "-") or True,
)


from rich.console import Console  # noqa: E402
from rich.markup import escape  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from nanobot import __logo__, __version__  # noqa: E402
from nanobot.agent.hooks import create_file_edit_activity_hook  # noqa: E402
from nanobot.agent.loop import AgentLoop  # noqa: E402
from nanobot.agent.tools.mcp import MCPProvider  # noqa: E402
from nanobot.agent.tools.registry import ToolRegistry  # noqa: E402
from nanobot.cli import terminal as cli_terminal  # noqa: E402
from nanobot.cli.agent import agent  # noqa: E402
from nanobot.cli.gateway import create_gateway_app  # noqa: E402
from nanobot.cli.gateway_runtime import _run_gateway  # noqa: E402
from nanobot.cli.log_control import _set_nanobot_logs  # noqa: E402
from nanobot.cli.process_identity import set_cli_process_identity  # noqa: E402
from nanobot.cli.provider import provider_app  # noqa: E402
from nanobot.cli.runtime_config import (  # noqa: E402
    _load_config_for_cli,
    _load_inspection_config,
    _load_runtime_config,
    _model_display,
    _print_config_error,
    _print_model_setup_steps,
    _provider_setup_error,
)
from nanobot.cli.webui import webui  # noqa: E402
from nanobot.cli.webui_support import (  # noqa: E402
    _prepare_webui_bundle_for_gateway,
    _validate_gateway_startup,
)
from nanobot.config.paths import get_workspace_path  # noqa: E402
from nanobot.extensions.runtime import (  # noqa: E402
    build_core_extension_registry,
    runtime_skills_loader,
)
from nanobot.security.network import is_loopback_host  # noqa: E402
from nanobot.utils.helpers import sanitize_surrogates as _sanitize_surrogates  # noqa: E402,F401
from nanobot.utils.helpers import (  # noqa: E402
    sync_workspace_templates,
)

# Backward-compatible import for callers that used the former module location.
SafeFileHistory = cli_terminal.SafeFileHistory


app = typer.Typer(
    name="nanobot",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} nanobot - Personal AI Assistant",
    epilog=(
        "Run `nanobot` without a subcommand to start the terminal agent. "
        "Use `nanobot agent --help` for agent options."
    ),
    invoke_without_command=True,
    no_args_is_help=False,
)

console = Console()

def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    # Editable/source installs can retain an older generated console script that
    # imports this Typer app directly instead of ``nanobot.cli.entry``. Keep the
    # role identity correct until that launcher is regenerated.
    command = ctx.invoked_subcommand
    set_cli_process_identity([command] if command else ["agent"])
    if command is None:
        from nanobot.cli.entry import _run_agent

        _run_agent([], prog_name="nanobot")


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    wizard: bool = typer.Option(False, "--wizard", help="Use interactive wizard"),
    non_interactive_refresh: bool = typer.Option(False, "--refresh", help="Refresh config, preserving existing settings without prompting"),
):
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_path, load_config, save_config, set_config_path
    from nanobot.config.schema import Config

    explicit_config = config is not None
    if config:
        config_path = Path(config).expanduser().resolve()
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")
    else:
        config_path = get_config_path()

    def _apply_workspace_override(loaded: Config) -> Config:
        if workspace:
            loaded.agents.defaults.workspace = workspace
        return loaded

    loaded_config: Config | None = None
    # Create or update config
    if config_path.exists():
        if wizard:
            loaded_config = _apply_workspace_override(load_config(config_path))
        else:
            should_refresh = non_interactive_refresh
            if not non_interactive_refresh:
                console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
                console.print(
                    "  [bold]y[/bold] = overwrite with defaults (existing values will be lost)"
                )
                console.print(
                    "  [bold]N[/bold] = refresh config, keeping existing values and adding new fields"
                )
                if typer.confirm("Overwrite?"):
                    loaded_config = _apply_workspace_override(Config())
                    save_config(loaded_config, config_path)
                    console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
                else:
                    should_refresh = True

            if should_refresh:
                loaded_config = _apply_workspace_override(load_config(config_path))
                save_config(loaded_config, config_path)
                console.print(
                    f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)"
                )
    else:
        loaded_config = _apply_workspace_override(Config())
        # In wizard mode, don't save yet - the wizard will handle saving if should_save=True
        if not wizard:
            save_config(loaded_config, config_path)
            console.print(f"[green]✓[/green] Created config at {config_path}")

    assert loaded_config is not None

    # Run interactive wizard if enabled
    if wizard:
        from nanobot.cli.onboard import run_onboard

        try:
            result = run_onboard(initial_config=loaded_config)
            if not result.should_save:
                console.print("[yellow]Configuration discarded. No changes were saved.[/yellow]")
                return

            loaded_config = result.config
            save_config(loaded_config, config_path)
            console.print(f"[green]✓[/green] Config saved at {config_path}")
        except Exception as e:
            console.print(f"[red]✗[/red] Error during configuration: {e}")
            console.print("[yellow]Please run 'nanobot onboard' again to complete setup.[/yellow]")
            raise typer.Exit(1)
    _onboard_plugins(config_path)

    # Create workspace, preferring the configured workspace path.
    workspace_path = get_workspace_path(loaded_config.workspace_path)
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace_path}")

    sync_workspace_templates(workspace_path)

    webui_cmd = "nanobot webui"
    if explicit_config:
        webui_cmd += f' -c "{config_path}"'

    typer.echo(f"\n✓ nanobot is ready. Run: {webui_cmd}")


def _onboard_plugins(config_path: Path) -> None:
    """Inject default config for all discovered channels (built-in + plugins)."""
    import json

    from nanobot.channels.contracts import channel_default_config
    from nanobot.channels.registry import discover_plugins
    from nanobot.config.loader import merge_missing_defaults

    plugins = discover_plugins()
    if not plugins:
        return

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    channels = data.setdefault("channels", {})
    for name, plugin in plugins.items():
        defaults = channel_default_config(plugin)
        if name not in channels:
            channels[name] = defaults
        else:
            channels[name] = merge_missing_defaults(channels[name], defaults)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)




def _read_trigger_cli_message(message: str | None) -> str:
    """Read a trigger message from an argument or stdin."""
    if message and message.strip():
        return message
    try:
        if not sys.stdin.isatty():
            content = sys.stdin.read()
            if content.strip():
                return content
    except Exception:
        pass
    console.print("[red]Error: trigger message is required[/red]")
    raise typer.Exit(1)


@app.command()
def trigger(
    trigger_id: str = typer.Argument(..., help="Trigger ID returned by /trigger"),
    message: str | None = typer.Argument(None, help="Message to deliver; stdin is used when omitted"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Deliver a local trigger message to its bound chat session."""
    from nanobot.triggers.local_store import (
        LocalTriggerStore,
        TriggerDisabledError,
        TriggerNotFoundError,
        TriggerStoreError,
    )

    runtime_config = _load_runtime_config(config, workspace)
    content = _read_trigger_cli_message(message)
    store = LocalTriggerStore(runtime_config.workspace_path)
    try:
        delivery = store.enqueue(trigger_id, content)
    except (TriggerNotFoundError, TriggerDisabledError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    except (TriggerStoreError, ValueError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Queued[/green] {delivery.trigger_id} ({delivery.id})")


# ============================================================================
# OpenAI-Compatible API Server
# ============================================================================


@app.command()
def serve(
    port: int | None = typer.Option(None, "--port", "-p", help="API server port"),
    host: str | None = typer.Option(None, "--host", "-H", help="Bind address"),
    timeout: float | None = typer.Option(None, "--timeout", "-t", help="Per-request timeout (seconds)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show nanobot runtime logs"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the OpenAI-compatible API server (/v1/chat/completions)."""
    try:
        from aiohttp import web  # noqa: F401
    except ImportError:
        console.print("[red]aiohttp is required. Install with: nanobot plugins enable api[/red]")
        raise typer.Exit(1)

    from nanobot.api.server import create_app
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.image_generation import image_gen_provider_configs
    from nanobot.session.manager import SessionManager

    _set_nanobot_logs(verbose)

    runtime_config = _load_runtime_config(config, workspace)
    api_cfg = runtime_config.api
    host = host if host is not None else api_cfg.host
    port = port if port is not None else api_cfg.port
    timeout = timeout if timeout is not None else api_cfg.timeout
    api_key = api_cfg.api_key.strip() if api_cfg.api_key else ""
    if not is_loopback_host(host) and not api_key:
        console.print(
            f"[red]Error: host {host} is available beyond this device but api_key is not set. "
            "Set api.api_key in config to prevent unauthenticated access.[/red]"
        )
        raise typer.Exit(1)
    sync_workspace_templates(runtime_config.workspace_path)
    bus = MessageBus()
    session_manager = SessionManager(runtime_config.workspace_path)
    tools = ToolRegistry()
    mcp_provider = MCPProvider.from_config(runtime_config, tools)
    try:
        agent_loop = AgentLoop.from_config(
            runtime_config, bus,
            session_manager=session_manager,
            image_generation_provider_configs=image_gen_provider_configs(runtime_config),
            hook_factories=[create_file_edit_activity_hook],
            tool_registry=tools,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    _extensions = build_core_extension_registry(
        runtime_config,
        tools,
        skills_loader=runtime_skills_loader(agent_loop),
        mcp_runtime_status=mcp_provider.runtime_status,
    )

    model_name, preset_tag = _model_display(runtime_config)
    console.print(f"{__logo__} Starting OpenAI-compatible API server")
    console.print(f"  [cyan]Endpoint[/cyan] : http://{host}:{port}/v1/chat/completions")
    console.print(f"  [cyan]Model[/cyan]    : {model_name}{preset_tag}")
    console.print("  [cyan]Session[/cyan]  : api:default")
    console.print(f"  [cyan]Timeout[/cyan]  : {timeout}s")
    if not is_loopback_host(host):
        console.print(
            "[yellow]API is available beyond this device "
            "(authentication required).[/yellow]"
        )
    console.print()

    api_app = create_app(
        agent_loop, model_name=model_name, request_timeout=timeout,
        api_key=api_key,
        prepare_agent=mcp_provider.connect,
    )

    async def on_startup(_app: Any) -> None:
        await mcp_provider.connect()

    async def on_cleanup(_app: Any, _extensions: object = _extensions) -> None:
        try:
            await agent_loop.aclose()
        finally:
            await mcp_provider.aclose()

    api_app.on_startup.append(on_startup)
    api_app.on_cleanup.append(on_cleanup)

    def _log_aiohttp(message: object) -> None:
        logger.info("{}", message)

    web.run_app(api_app, host=host, port=port, print=_log_aiohttp)


# ============================================================================
# WebUI Launcher
# ============================================================================

app.command(name="webui")(webui)


# ============================================================================
# Gateway / Server
# ============================================================================


app.add_typer(
    create_gateway_app(
        console=console,
        log_handler_id=_log_handler_id,
        load_runtime_config=_load_runtime_config,
        run_gateway=_run_gateway,
        validate_startup_config=_validate_gateway_startup,
        prepare_webui_bundle=lambda config, mode: _prepare_webui_bundle_for_gateway(
            config,
            mode=mode,
        ),
    ),
    name="gateway",
)


# ============================================================================
# Agent Commands
# ============================================================================


app.command(name="agent")(agent)


# ============================================================================
# Session Commands
# ============================================================================


sessions_app = typer.Typer(help="Manage persisted session history")
app.add_typer(sessions_app, name="sessions")


@sessions_app.command("restore-workspace")
def sessions_restore_workspace(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
) -> None:
    """Copy sessions back into the workspace before downgrading nanobot."""
    from nanobot.session.manager import SessionManager

    runtime_config = _load_runtime_config(config, workspace)
    data_dir = runtime_config.runtime_data_dir
    manager = SessionManager(
        runtime_config.workspace_path,
        sessions_root=data_dir / "sessions" if data_dir is not None else None,
    )
    result = manager.restore_sessions_to_workspace()
    console.print(
        f"Restored {result.restored} session file(s) to "
        f"{escape(str(runtime_config.workspace_path / 'sessions'))}; "
        f"{result.unchanged} already matched."
    )
    if result.conflicts:
        console.print(
            "[red]Rollback is incomplete: existing or invalid files require manual review.[/red]"
        )
        for path in result.conflicts:
            console.print(Text(f"- {path}", style="red"))
        raise typer.Exit(1)


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


def _management_config_path(config: str | None) -> Path:
    """Select one config identity before channel management callbacks run."""
    from nanobot.config.loader import get_config_path, set_config_path

    path = (
        Path(config).expanduser().resolve(strict=False)
        if config
        else get_config_path().expanduser().resolve(strict=False)
    )
    set_config_path(path)
    return path


def _offline_channel_lifecycle_label(lifecycle: object) -> str:
    """Render a management-only absent runtime as configuration state, not failure."""
    from nanobot.extensions.contracts import ExtensionLifecycle

    if lifecycle is ExtensionLifecycle.FAILED:
        return "configured, desired enabled (offline)"
    return str(lifecycle)


@channels_app.command("status")
def channels_status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show canonical channel package and instance lifecycle."""
    selected_config_path = _management_config_path(config)
    from nanobot.extensions.contracts import ExtensionComponentKind, ExtensionSource
    from nanobot.extensions.management import build_management_extension_registry

    snapshot = build_management_extension_registry(selected_config_path).snapshot()
    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Instance")
    table.add_column("Status")
    for package in snapshot.packages:
        if package.source is not ExtensionSource.CHANNEL_PACKAGE:
            continue
        for component in package.components:
            if component.kind is ExtensionComponentKind.CHANNEL:
                table.add_row(
                    package.display_name,
                    component.display_name,
                    _offline_channel_lifecycle_label(component.lifecycle),
                )
    console.print(table)


@channels_app.command("login")
def channels_login(
    channel_name: str = typer.Argument(..., help="Channel name (e.g. weixin, whatsapp)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-authentication even if already logged in"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Authenticate with one selected channel runtime."""
    selected_config_path = _management_config_path(config)
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.registry import load_channel_plugin

    try:
        plugin = load_channel_plugin(channel_name)
    except ImportError:
        console.print(f"[red]Unknown channel: {escape(channel_name)}[/red]")
        raise typer.Exit(1) from None
    loaded = _load_config_for_cli(selected_config_path)
    channel_cfg: Any = getattr(loaded.channels, channel_name, None) or {}
    console.print(f"{__logo__} {plugin.display_name} Login\n")
    channel = plugin.load_channel_class()(channel_cfg, bus=MessageBus())
    if not asyncio.run(channel.login(force=force)):
        raise typer.Exit(1)


# ============================================================================
# Plugin Commands
# ============================================================================

plugins_app = typer.Typer(help="Manage optional nanobot features")
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list(
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """List canonical channel and standalone optional feature descriptors."""
    selected_config_path = _management_config_path(config_path)
    from nanobot.extensions.contracts import ExtensionComponentKind, ExtensionSource
    from nanobot.extensions.management import build_management_extension_registry

    snapshot = build_management_extension_registry(selected_config_path).snapshot()
    table = Table(title="Available Features")
    table.add_column("Name", style="cyan")
    table.add_column("Instance")
    table.add_column("Type")
    table.add_column("Status")
    for package in snapshot.packages:
        if package.source is ExtensionSource.CHANNEL_PACKAGE:
            for component in package.components:
                if component.kind is ExtensionComponentKind.CHANNEL:
                    table.add_row(
                        package.display_name,
                        component.display_name,
                        "channel",
                        _offline_channel_lifecycle_label(component.lifecycle),
                    )
        elif package.source is ExtensionSource.OPTIONAL_FEATURE:
            table.add_row(package.display_name, "-", "feature", package.lifecycle.value)
    console.print(table)


def _extension_action_error(message: str) -> Never:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(1)


@plugins_app.command("enable")
def plugins_enable(
    name: str = typer.Argument(..., help="Feature name (e.g. weixin, matrix, bedrock)"),
    instance: str | None = typer.Option(None, "--instance", help="Exact channel instance"),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Enable one exact channel instance or install one optional feature."""
    selected_config_path = _management_config_path(config_path)
    from nanobot.extensions.contracts import (
        ExtensionAction,
        ExtensionActionContext,
        ExtensionActionRequest,
        ExtensionLifecycle,
        ExtensionSource,
        safe_extension_message,
    )
    from nanobot.extensions.management import build_management_extension_registry
    from nanobot.extensions.registry import ExtensionRegistryError
    from nanobot.extensions.targets import resolve_extension_feature_target

    registry = build_management_extension_registry(selected_config_path)
    target = resolve_extension_feature_target(registry.snapshot(), name, instance)
    if target is None:
        _extension_action_error("Extension action target is unavailable.")
    if target.source is ExtensionSource.OPTIONAL_FEATURE:
        if target.lifecycle is not ExtensionLifecycle.UNAVAILABLE:
            console.print("[green]Optional feature is already enabled.[/green]")
            return
        action = ExtensionAction.INSTALL
    else:
        action = ExtensionAction.ENABLE
    try:
        result = asyncio.run(
            registry.execute(
                ExtensionActionRequest(
                    context=ExtensionActionContext(
                        actor_id="cli",
                        is_system_admin=True,
                        package_install_allowed=True,
                    ),
                    target_id=target.target_id,
                    action=action,
                    expected_revision=target.revision,
                    risk_acknowledged=True,
                )
            )
        )
    except ExtensionRegistryError as exc:
        _extension_action_error(safe_extension_message(exc))
    if not result.ok:
        _extension_action_error("Extension action could not be completed.")
    if result.lifecycle is ExtensionLifecycle.RESTART_REQUIRED:
        console.print("[green]Channel enabled; restart required.[/green]")
    else:
        console.print("[green]Optional feature is ready.[/green]")


@plugins_app.command("disable")
def plugins_disable(
    name: str = typer.Argument(..., help="Channel name (e.g. telegram, matrix, slack)"),
    instance: str | None = typer.Option(None, "--instance", help="Exact channel instance"),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Disable one exact channel instance."""
    selected_config_path = _management_config_path(config_path)
    from nanobot.extensions.contracts import (
        ExtensionAction,
        ExtensionActionContext,
        ExtensionActionRequest,
        ExtensionLifecycle,
        ExtensionSource,
        safe_extension_message,
    )
    from nanobot.extensions.management import build_management_extension_registry
    from nanobot.extensions.registry import ExtensionRegistryError
    from nanobot.extensions.targets import resolve_extension_feature_target

    registry = build_management_extension_registry(selected_config_path)
    target = resolve_extension_feature_target(registry.snapshot(), name, instance)
    if target is None or target.source is not ExtensionSource.CHANNEL_PACKAGE:
        _extension_action_error("Only channel instances can be disabled.")
    try:
        result = asyncio.run(
            registry.execute(
                ExtensionActionRequest(
                    context=ExtensionActionContext(actor_id="cli", is_system_admin=True),
                    target_id=target.target_id,
                    action=ExtensionAction.DISABLE,
                    expected_revision=target.revision,
                )
            )
        )
    except ExtensionRegistryError as exc:
        _extension_action_error(safe_extension_message(exc))
    if not result.ok:
        _extension_action_error("Extension action could not be completed.")
    message = (
        "Channel disabled; restart required."
        if result.lifecycle is ExtensionLifecycle.RESTART_REQUIRED
        else "Channel disabled."
    )
    console.print(f"[green]{message}[/green]")

# ============================================================================
# MCP Interoperability Commands
# ============================================================================


mcp_app = typer.Typer(help="Export and inspect MCP configuration without writing Codex files")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("codex-config")
def mcp_codex_config(
    server: list[str] = typer.Option([], "--server", "-s", help="Only export this MCP server (repeatable)"),
    project: str | None = typer.Option(
        None,
        "--project",
        help="Project path to use as exported stdio server CWD",
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to nanobot config file"),
    include_nanobot: bool = typer.Option(
        False,
        "--include-nanobot",
        help="Also export nanobot's project/task/context stdio MCP server",
    ),
    project_id: str | None = typer.Option(
        None,
        "--project-id",
        help="Project id pinned into the exported nanobot MCP server",
    ),
) -> None:
    """Print a Codex MCP config fragment; never writes a Codex config file."""
    from nanobot.agent.plugins import agent_plugin_mcp_servers
    from nanobot.config.loader import set_config_path
    from nanobot.mcp_interop import codex_mcp_config

    config_path = Path(config).expanduser().resolve(strict=False) if config else None
    if config_path is not None:
        set_config_path(config_path)
    loaded = _load_config_for_cli(config_path, resolve_env=False)
    servers = agent_plugin_mcp_servers(loaded.workspace_path, loaded.tools.mcp_servers)
    if include_nanobot:
        from nanobot.config.schema import MCPServerConfig

        bridge_name = "nanobot-project"
        if bridge_name in servers:
            console.print("[red]MCP server name 'nanobot-project' is already configured[/red]")
            raise typer.Exit(1)
        bridge_args = ["-m", "nanobot", "mcp", "serve"]
        if config_path is not None:
            bridge_args.extend(["--config", str(config_path)])
        if project_id:
            bridge_args.extend(["--project-id", project_id])
        servers[bridge_name] = MCPServerConfig(
            type="stdio",
            command=sys.executable,
            args=bridge_args,
            enabled_tools=[
                "nanobot_projects",
                "nanobot_task_lists",
                "nanobot_tasks",
                "nanobot_task_create",
                "nanobot_task_update",
                "nanobot_context_sources",
                "nanobot_context_read",
            ],
        )
    if server:
        unknown = sorted(set(server) - servers.keys())
        if unknown:
            console.print(f"[red]Unknown MCP server(s): {escape(', '.join(unknown))}[/red]")
            raise typer.Exit(1)
        servers = {name: servers[name] for name in server}

    project_path = Path(project).expanduser().resolve(strict=False) if project else None
    exported = codex_mcp_config(servers, project=project_path)
    placement = (
        project_path / ".codex" / "config.toml"
        if project_path is not None
        else Path.home() / ".codex" / "config.toml"
    )
    typer.echo(f"# Recommended placement: {placement}")
    if project_path is not None:
        typer.echo("# Codex loads project configuration only after the project is trusted.")
    if exported.toml:
        typer.echo(exported.toml, nl=False)
    for warning in exported.warnings:
        typer.echo(f"Warning: {warning}", err=True)


@mcp_app.command("import-codex")
def mcp_import_codex(
    path: str = typer.Option(..., "--path", help="Path to a Codex config.toml file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Explicitly confirm this is output-only"),
) -> None:
    """Print WebUI-compatible nanobot MCP JSON; this command never writes config."""
    from nanobot.mcp_interop import read_codex_toml

    try:
        imported = read_codex_toml(path)
    except (TypeError, ValueError) as exc:
        console.print(f"[red]Error: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(imported, indent=2, ensure_ascii=False))

@mcp_app.command("serve")
def mcp_serve(
    project_id: str | None = typer.Option(
        None,
        "--project-id",
        help="Local-owner project id to expose; defaults to the owner's default project",
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to nanobot config file"),
) -> None:
    """Serve project tasks and bounded context to local MCP clients over stdio."""
    from nanobot.collaboration import build_collaboration_repository
    from nanobot.collaboration.mcp_server import run_collaboration_mcp
    from nanobot.config.loader import load_config, set_config_path

    if config:
        set_config_path(Path(config).expanduser().resolve(strict=False))
    loaded = load_config()
    collaboration = build_collaboration_repository(loaded.collaboration)

    async def _serve() -> None:
        try:
            await collaboration.initialize()
            await run_collaboration_mcp(
                collaboration,
                workspace_path=loaded.workspace_path,
                project_id=project_id,
            )
        finally:
            await collaboration.aclose()

    asyncio.run(_serve())


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
):
    """Show nanobot status."""
    config_path, loaded = _load_inspection_config(config=config, workspace=workspace)
    workspace_path = loaded.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(
        f"Workspace: {workspace_path} "
        f"{'[green]✓[/green]' if workspace_path.exists() else '[red]✗[/red]'}"
    )

    if config_path.exists():
        from nanobot.config.errors import ConfigLoadError
        from nanobot.config.loader import resolve_config_env_vars, resolve_env_refs
        from nanobot.providers.registry import PROVIDERS

        _model, _preset_tag = _model_display(loaded)
        console.print(f"Model: {_model}{_preset_tag}")

        provider_ready = False
        try:
            resolved = resolve_config_env_vars(
                loaded.model_copy(deep=True),
                config_path=config_path,
            )
        except ConfigLoadError as exc:
            console.print("Agent: [red]✗ configuration is not ready[/red]")
            _print_config_error(exc)
        else:
            provider_error = _provider_setup_error(resolved)
            if provider_error:
                console.print(Text(f"Agent: ✗ {provider_error}", style="red"))
                console.print("Complete provider/model setup:")
                _print_model_setup_steps(config_path)
            else:
                provider_ready = True
                console.print("Agent: [green]✓ provider/model configuration is ready[/green]")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(loaded.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if resolve_env_refs(p.api_base or ""):
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(resolve_env_refs(p.api_key or ""))
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")

        if provider_ready:
            console.print()
            console.print('Next: [cyan]nanobot agent -m "Hello!"[/cyan]')
            console.print(
                "[dim]Status does not call the model or verify network access and credentials.[/dim]"
            )
    else:
        console.print("Agent: [red]✗ configuration file not found[/red]")
        console.print("Create the provider/model configuration:")
        _print_model_setup_steps(config_path)


# ============================================================================
# OAuth Login
# ============================================================================

app.add_typer(provider_app, name="provider")


if __name__ == "__main__":
    app()
