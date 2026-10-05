import json
import os
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.cli_apps import CliAppsTool
from nanobot.agent.tools.context import RequestContext, ToolContext, request_context
from nanobot.agent.tools.exec_session import ExecSessionManager
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool
from nanobot.agent.tools.image_generation import ImageGenerationError, ImageGenerationTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.search import GrepTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.apps.cli.service import CliAppManager, CliAppsRuntimeConfig
from nanobot.bus.queue import MessageBus
from nanobot.collaboration import (
    AsyncLocalCollaborationRepository,
    CollaborationPermissionError,
    CollaborationStore,
)
from nanobot.collaboration.models import ConversationScope
from nanobot.config.schema import ImageGenerationToolConfig, ProviderConfig, ToolsConfig
from nanobot.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScopeError,
    WorkspaceScopeResolver,
    bind_workspace_scope,
    default_workspace_scope,
    reset_workspace_scope,
    validate_workspace_scope_payload,
    workspace_scope_from_metadata,
)
from nanobot.session.manager import SessionManager

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdacd\xfc\xff\x1f\x00\x03\x03"
    b"\x02\x00\xef\xbf\xa7\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"directory junction unavailable: {result.stderr or result.stdout}")
        return

    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")


@pytest_asyncio.fixture
async def main_project_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[SimpleNamespace]:
    """Use real project authorization with no host profiles, sessions, or external services."""
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir", lambda name: tmp_path / "runtime" / name,
    )
    store = CollaborationStore(tmp_path / "collaboration")
    owner, home = store.ensure_local_owner(host)
    main = store.create_project(owner.id, "Main release", tmp_path / "main")
    ordinary = store.create_project(owner.id, "Ordinary project", tmp_path / "ordinary")
    for project in (main, ordinary):
        Path(project.workspace_path).mkdir()
    store.update_project(main.id, owner.id, is_main=True)
    repository = AsyncLocalCollaborationRepository(store)
    await repository.initialize()
    provider = SimpleNamespace(
        get_default_model=lambda: "test-model",
        generation=SimpleNamespace(max_tokens=4096, temperature=0.1, reasoning_effort=None),
    )
    with patch("nanobot.agent.loop.SubagentManager") as subagents:
        subagents.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=MessageBus(), provider=provider, workspace=host,
            collaboration_repository=repository,
            session_manager=SessionManager(host, sessions_root=tmp_path / "sessions"),
            tools_config=ToolsConfig(restrict_to_workspace=False),
        )
    try:
        yield SimpleNamespace(loop=loop, store=store, owner=owner, home=home, main=main, ordinary=ordinary)
    finally:
        await repository.aclose()


async def _assert_cross_root_project_access(
    runtime: SimpleNamespace,
    scope: ConversationScope,
    outside: Path,
    cmd_python: str,
    *,
    allow: bool,
    local_webui_source: str | None = None,
    requested_access_mode: str = "full",
) -> None:
    """Observe the derived policy through real reads, writes, and an actual shell command."""
    assert scope.workspace_path is not None
    project = Path(scope.workspace_path)
    (project / "inside.txt").write_text("PROJECT_SENTINEL", encoding="utf-8")
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("CROSS_ROOT_SECRET", encoding="utf-8")
    write_marker = outside / "write-marker.txt"
    exec_marker = outside / "exec-marker.txt"
    attributes = {"collaboration_scope": scope} if local_webui_source is None else {}
    workspace_metadata = {
        WORKSPACE_SCOPE_METADATA_KEY: {
            "project_path": str(project), "access_mode": requested_access_mode,
        },
    }
    effective = await runtime.loop._effective_workspace_scope(
        channel="websocket",
        message_metadata=workspace_metadata if local_webui_source in (None, "message") else None,
        session_metadata=workspace_metadata if local_webui_source == "session" else None,
        attributes=attributes,
    )
    assert effective.access_mode == ("full" if allow else "restricted")
    read = ReadFileTool(workspace=runtime.loop.workspace, restrict_to_workspace=True)
    write = WriteFileTool(workspace=runtime.loop.workspace, restrict_to_workspace=True)
    exec_sessions = ExecSessionManager()
    execute = ExecTool(
        working_dir=str(runtime.loop.workspace), restrict_to_workspace=True, timeout=5,
        session_manager=exec_sessions,
    )
    token = bind_workspace_scope(effective)
    try:
        with request_context(RequestContext(channel="websocket", chat_id="chat", attributes=attributes)):
            assert "PROJECT_SENTINEL" in await read.execute(path="inside.txt")
            inside_write = await write.execute(path="inside-write.txt", content="PROJECT_WRITE")
            cross_read = await read.execute(path=str(secret))
            cross_write = await write.execute(path=str(write_marker), content="CROSS_ROOT_WRITE")
            cross_exec = await execute.execute(
                command=(
                    f'{cmd_python} -c "from pathlib import Path; '
                    "Path('exec-marker.txt').write_text('CROSS_ROOT_EXEC')\""
                ),
                working_dir=str(outside),
            )
    finally:
        reset_workspace_scope(token)
        await exec_sessions.close_all()

    assert "Successfully wrote" in inside_write
    assert (project / "inside-write.txt").read_text(encoding="utf-8") == "PROJECT_WRITE"
    if allow:
        assert "CROSS_ROOT_SECRET" in cross_read
        assert "Successfully wrote" in cross_write
        assert write_marker.read_text(encoding="utf-8") == "CROSS_ROOT_WRITE"
        assert "Exit code: 0" in cross_exec
        assert exec_marker.read_text(encoding="utf-8") == "CROSS_ROOT_EXEC"
    else:
        assert "Error:" in cross_read and "CROSS_ROOT_SECRET" not in cross_read
        assert "Error:" in cross_write
        assert "outside the configured workspace" in cross_exec
        assert not write_marker.exists()
        assert not exec_marker.exists()
    assert secret.read_text(encoding="utf-8") == "CROSS_ROOT_SECRET"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor", "project_kind", "allow"),
    [
        ("local-owner", "main", True),
        ("local-owner", "ordinary", False),
        ("local-owner", "home", False),
        ("member", "main", False),
        ("oidc-admin", "main", False),
    ],
    ids=["owner-main", "owner-ordinary", "owner-non-main-builtin", "main-member", "main-oidc-admin"],
)
async def test_main_project_cross_root_access_requires_authenticated_local_owner(
    tmp_path: Path, cmd_python: str, main_project_runtime: SimpleNamespace,
    actor: str, project_kind: str, allow: bool,
) -> None:
    """Neither project membership, admin status, nor built-in identity substitutes for owner + main."""
    runtime = main_project_runtime
    project = getattr(runtime, project_kind)
    user = runtime.owner
    if actor != "local-owner":
        user = runtime.store.create_user(actor)
        runtime.store.add_member(project.id, runtime.owner.id, user.id)
        if actor == "oidc-admin":
            runtime.store.update_user_admin(user.id, True)
            runtime.store.bind_identity("oidc", "oidc:administrator", user.id)
    scope = runtime.store.resolve_session_scope(user.id, project.id, channel="websocket", chat_id="chat")
    assert scope is not None
    await _assert_cross_root_project_access(runtime, scope, tmp_path / "outside", cmd_python, allow=allow)


@pytest.mark.asyncio
async def test_main_project_switch_revalidates_cached_owner_scope_before_tools(
    tmp_path: Path, cmd_python: str, main_project_runtime: SimpleNamespace,
) -> None:
    """A captured main flag grants nothing after demotion; a captured non-main flag does not block promotion."""
    runtime = main_project_runtime
    old_scope = runtime.store.resolve_session_scope(
        runtime.owner.id, runtime.main.id, channel="websocket", chat_id="old-chat",
    )
    new_scope = runtime.store.resolve_session_scope(
        runtime.owner.id, runtime.ordinary.id, channel="websocket", chat_id="new-chat",
    )
    assert old_scope is not None and new_scope is not None
    await _assert_cross_root_project_access(runtime, old_scope, tmp_path / "before-old", cmd_python, allow=True)
    await _assert_cross_root_project_access(runtime, new_scope, tmp_path / "before-new", cmd_python, allow=False)

    writer = CollaborationStore(store_path=runtime.store.path)
    writer.update_project(runtime.ordinary.id, runtime.owner.id, is_main=True)

    await _assert_cross_root_project_access(runtime, old_scope, tmp_path / "after-old", cmd_python, allow=False)
    await _assert_cross_root_project_access(runtime, new_scope, tmp_path / "after-new", cmd_python, allow=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["message", "session", "owner-default"])
@pytest.mark.parametrize("project_kind", ["main", "ordinary"])
async def test_local_owner_webui_resolves_registered_project_before_cross_root_access(
    tmp_path: Path, cmd_python: str, main_project_runtime: SimpleNamespace,
    source: str, project_kind: str,
) -> None:
    """Unscoped owner WebUI turns cannot use stale full metadata to bypass a non-main project."""
    runtime = main_project_runtime
    project = getattr(runtime, project_kind)
    runtime.store.update_user_default_project(runtime.owner.id, project.id)
    scope = runtime.store.resolve_session_scope(
        runtime.owner.id, project.id, channel="websocket", chat_id="local-chat",
    )
    assert scope is not None
    await _assert_cross_root_project_access(
        runtime, scope, tmp_path / "outside", cmd_python,
        allow=project_kind == "main", local_webui_source=source,
    )


@pytest.mark.asyncio
async def test_local_owner_uses_foreign_main_without_membership_but_oidc_admin_cannot(
    tmp_path: Path, cmd_python: str, main_project_runtime: SimpleNamespace,
) -> None:
    """Only the host owner may select and use a foreign main without joining it."""
    runtime = main_project_runtime
    project_owner = runtime.store.create_user("Foreign project owner")
    project = runtime.store.create_project(project_owner.id, "Foreign main", tmp_path / "foreign")
    Path(project.workspace_path).mkdir()
    admin = runtime.store.create_user("OIDC administrator")
    runtime.store.update_user_admin(admin.id, True)
    runtime.store.bind_identity("oidc", "oidc:foreign-main-admin", admin.id)
    members = runtime.store.list_members(project.id, project_owner.id)

    runtime.store.update_project(project.id, runtime.owner.id, is_main=True)
    runtime.store.update_user_default_project(runtime.owner.id, project.id)
    scope = runtime.store.resolve_session_scope(
        runtime.owner.id, project.id, channel="websocket", chat_id="owner-chat",
    )
    assert scope is not None
    await _assert_cross_root_project_access(
        runtime, scope, tmp_path / "owner-outside", cmd_python,
        allow=True, local_webui_source="owner-default",
    )
    assert runtime.store.list_members(project.id, project_owner.id) == members

    with pytest.raises(CollaborationPermissionError):
        runtime.store.update_user_default_project(admin.id, project.id)
    assert runtime.store.resolve_session_scope(
        admin.id, project.id, channel="websocket", chat_id="admin-chat",
    ) is None

    runtime.store.add_member(project.id, project_owner.id, admin.id)
    runtime.store.update_user_default_project(admin.id, project.id)
    admin_scope = runtime.store.resolve_session_scope(
        admin.id, project.id, channel="websocket", chat_id="admin-chat",
    )
    assert admin_scope is not None
    await _assert_cross_root_project_access(
        runtime, admin_scope, tmp_path / "admin-outside", cmd_python, allow=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["message", "session"])
async def test_temporary_main_owner_scope_keeps_explicit_cross_root_restrictions(
    tmp_path: Path, cmd_python: str, main_project_runtime: SimpleNamespace, source: str,
) -> None:
    """An explicit restricted temporary main chat cannot inherit the owner's full file access."""
    runtime = main_project_runtime
    runtime.store.update_user_default_project(runtime.owner.id, runtime.main.id)
    scope = runtime.store.resolve_session_scope(
        runtime.owner.id, runtime.main.id, channel="websocket", chat_id="temporary-chat",
    )
    assert scope is not None
    await _assert_cross_root_project_access(
        runtime, scope, tmp_path / "outside", cmd_python,
        allow=False, local_webui_source=source, requested_access_mode="restricted",
    )


def test_workspace_scope_defaults_match_legacy_config(tmp_path: Path) -> None:
    unrestricted = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    restricted = default_workspace_scope(tmp_path, restrict_to_workspace=True)

    assert unrestricted.project_path == tmp_path.resolve()
    assert unrestricted.access_mode == "full"
    assert unrestricted.restrict_to_workspace is False
    assert restricted.access_mode == "restricted"
    assert restricted.restrict_to_workspace is True


def test_workspace_scope_rejects_invalid_project_path(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceScopeError, match="absolute"):
        validate_workspace_scope_payload(
            {"project_path": "relative/project", "access_mode": "restricted"},
            default_workspace=tmp_path,
            default_restrict_to_workspace=False,
        )

    with pytest.raises(WorkspaceScopeError, match="existing directory"):
        validate_workspace_scope_payload(
            {"project_path": str(tmp_path / "missing"), "access_mode": "restricted"},
            default_workspace=tmp_path,
            default_restrict_to_workspace=False,
        )


def test_workspace_scope_accepts_home_relative_project_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = home / "Desktop" / "Photos"
    project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    scope = validate_workspace_scope_payload(
        {"project_path": "~/Desktop/Photos", "access_mode": "restricted"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=False,
    )

    assert scope.project_path == project.resolve()
    assert scope.metadata()["project_path"] == str(project.resolve())


@pytest.mark.parametrize("access_mode", ["restricted", "full"])
def test_selected_websocket_project_is_visible_to_the_model(
    tmp_path: Path,
    access_mode: str,
) -> None:
    agent_home = tmp_path / "agent-home"
    project = tmp_path / "project"
    agent_home.mkdir()
    project.mkdir()
    resolver = WorkspaceScopeResolver(agent_home, default_restrict_to_workspace=False)

    scope = resolver.for_turn(
        channel="websocket",
        message_metadata={
            WORKSPACE_SCOPE_METADATA_KEY: {
                "project_path": str(project),
                "access_mode": access_mode,
            }
        },
        session_metadata=None,
    )
    prompt = ContextBuilder(agent_home).build_system_prompt(workspace=scope.project_path)

    assert prompt.index("# Tool Usage Notes") < prompt.index("# Current Project")
    assert f"Working directory: {project.resolve()}" in prompt
    assert "Use it as the default root for project files" in prompt


def test_workspace_scope_metadata_falls_back_for_stale_session(tmp_path: Path) -> None:
    scope = workspace_scope_from_metadata(
        {
            WORKSPACE_SCOPE_METADATA_KEY: {
                "project_path": str(tmp_path / "missing"),
                "access_mode": "restricted",
            }
        },
        default_workspace=tmp_path,
        default_restrict_to_workspace=False,
    )

    assert scope.project_path == tmp_path.resolve()
    assert scope.access_mode == "full"


@pytest.mark.asyncio
async def test_filesystem_tool_uses_current_restricted_workspace_scope(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope")
    inside = project / "inside.txt"
    inside.write_text("ok")
    tool = ReadFileTool(workspace=tmp_path, restrict_to_workspace=False)
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=False,
    )
    token = bind_workspace_scope(scope)
    try:
        assert "ok" in await tool.execute(path="inside.txt")
        assert "outside allowed directory" in await tool.execute(path=str(outside))
    finally:
        reset_workspace_scope(token)


@pytest.mark.asyncio
async def test_restricted_project_can_read_agent_skills_and_exact_history(tmp_path: Path) -> None:
    agent_workspace = tmp_path / "agent"
    project = tmp_path / "project"
    skill_file = agent_workspace / "skills" / "custom" / "SKILL.md"
    history_file = agent_workspace / "memory" / "history.jsonl"
    private_memory_file = agent_workspace / "memory" / "private.txt"
    private_file = agent_workspace / "private.txt"
    project_file = project / "project.txt"
    skill_file.parent.mkdir(parents=True)
    history_file.parent.mkdir(parents=True)
    project.mkdir()
    skill_file.write_text("global skill", encoding="utf-8")
    history_file.write_text('{"content":"global history"}\n', encoding="utf-8")
    private_memory_file.write_text("private memory", encoding="utf-8")
    private_file.write_text("private", encoding="utf-8")
    project_file.write_text("project", encoding="utf-8")

    ctx = ToolContext(
        config=ToolsConfig(restrict_to_workspace=True),
        workspace=str(agent_workspace),
    )
    read_tool = ReadFileTool.create(ctx)
    grep_tool = GrepTool.create(ctx)
    write_tool = WriteFileTool.create(ctx)
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=agent_workspace,
        default_restrict_to_workspace=True,
    )

    token = bind_workspace_scope(scope)
    try:
        project_result = await read_tool.execute(path="project.txt")
        skill_result = await read_tool.execute(path=str(skill_file))
        history_result = await grep_tool.execute(
            pattern="global history",
            path=str(history_file),
            output_mode="content",
        )
        private_memory_result = await read_tool.execute(path=str(private_memory_file))
        private_result = await read_tool.execute(path=str(private_file))
        write_result = await write_tool.execute(path=str(skill_file), content="changed")
        history_write_result = await write_tool.execute(path=str(history_file), content="changed")
    finally:
        reset_workspace_scope(token)

    assert "project" in project_result
    assert "global skill" in skill_result
    assert "global history" in history_result
    assert "outside allowed directory" in private_memory_result
    assert "outside allowed directory" in private_result
    assert "outside allowed directory" in write_result
    assert "outside allowed directory" in history_write_result
    assert skill_file.read_text(encoding="utf-8") == "global skill"
    assert history_file.read_text(encoding="utf-8") == '{"content":"global history"}\n'


@pytest.mark.asyncio
async def test_restricted_project_reads_history_from_linked_agent_workspace(
    tmp_path: Path,
) -> None:
    real_agent_workspace = tmp_path / "real-agent"
    linked_agent_workspace = tmp_path / "agent-link"
    project = tmp_path / "project"
    history_file = real_agent_workspace / "memory" / "history.jsonl"
    history_file.parent.mkdir(parents=True)
    project.mkdir()
    history_file.write_text('{"content":"linked history"}\n', encoding="utf-8")
    _make_directory_link(linked_agent_workspace, real_agent_workspace)

    ctx = ToolContext(
        config=ToolsConfig(restrict_to_workspace=True),
        workspace=str(linked_agent_workspace),
    )
    grep_tool = GrepTool.create(ctx)
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=linked_agent_workspace,
        default_restrict_to_workspace=True,
    )

    token = bind_workspace_scope(scope)
    try:
        result = await grep_tool.execute(
            pattern="linked history",
            path=str(history_file.resolve()),
            output_mode="content",
        )
    finally:
        reset_workspace_scope(token)

    assert "linked history" in result


@pytest.mark.asyncio
async def test_filesystem_write_tool_full_scope_allows_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path, restrict_to_workspace=True)
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "full"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        result = await tool.execute(path=str(outside / "outside.txt"), content="ok")
    finally:
        reset_workspace_scope(token)

    assert "Successfully wrote" in result
    assert (outside / "outside.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
async def test_exec_tool_uses_scope_project_as_default_cwd(
    tmp_path: Path,
    cmd_python: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=False, timeout=5)
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=False,
    )
    token = bind_workspace_scope(scope)
    try:
        result = await tool.execute(
            command=(
                f'{cmd_python} -c "from pathlib import Path; '
                "Path('scoped-marker.txt').write_text('ok')\""
            )
        )
    finally:
        reset_workspace_scope(token)

    assert "Exit code: 0" in result
    assert (project / "scoped-marker.txt").read_text() == "ok"


@pytest.mark.asyncio
async def test_exec_full_scope_allows_explicit_cwd_outside_project(
    tmp_path: Path,
    cmd_python: str,
) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=True, timeout=5)
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "full"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        result = await tool.execute(
            command=(
                f'{cmd_python} -c "from pathlib import Path; '
                "Path('outside-marker.txt').write_text('ok')\""
            ),
            working_dir=str(outside),
        )
    finally:
        reset_workspace_scope(token)

    assert "Exit code: 0" in result
    assert (outside / "outside-marker.txt").read_text() == "ok"


def test_image_reference_scope_restricted_blocks_outside_and_full_allows(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    ref = outside / "ref.png"
    ref.write_bytes(PNG_BYTES)
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(enabled=True),
        provider_config=ProviderConfig(api_key="sk-test"),
    )

    restricted = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=False,
    )
    token = bind_workspace_scope(restricted)
    try:
        with pytest.raises(ImageGenerationError, match="inside the workspace"):
            tool._resolve_reference_image(str(ref))
    finally:
        reset_workspace_scope(token)

    full = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "full"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(full)
    try:
        assert tool._resolve_reference_image(str(ref)) == str(ref.resolve())
    finally:
        reset_workspace_scope(token)


def test_message_media_scope_restricted_blocks_outside_and_full_allows(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    media = outside / "shot.png"
    media.write_bytes(PNG_BYTES)
    tool = MessageTool(workspace=tmp_path, restrict_to_workspace=True)

    restricted = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=False,
    )
    token = bind_workspace_scope(restricted)
    try:
        with pytest.raises(PermissionError):
            tool._resolve_media([str(media)])
    finally:
        reset_workspace_scope(token)

    full = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "full"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(full)
    try:
        assert tool._resolve_media([str(media)]) == [str(media)]
    finally:
        reset_workspace_scope(token)


@pytest.mark.asyncio
async def test_cli_app_scope_controls_working_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    data_dir = tmp_path / "data"
    project.mkdir()
    outside.mkdir()
    registry = {
        "meta": {},
        "clis": [
            {
                "name": "demo",
                "display_name": "Demo",
                "version": "1.0",
                "description": "demo",
                "category": "test",
                "install_cmd": "pip install demo",
                "entry_point": "demo-cli",
            }
        ],
    }
    data_dir.mkdir()
    (data_dir / "harness_registry_cache.json").write_text(
        json.dumps({"_cached_at": time.time(), "data": registry}),
        encoding="utf-8",
    )
    (data_dir / "public_registry_cache.json").write_text(
        json.dumps({"_cached_at": time.time(), "data": {"meta": {}, "clis": []}}),
        encoding="utf-8",
    )
    (data_dir / "extensions_registry_cache.json").write_text(
        json.dumps({"_cached_at": time.time(), "data": {"meta": {}, "clis": []}}),
        encoding="utf-8",
    )
    CliAppManager(workspace=project, data_dir=data_dir)._save_installed(
        {"demo": {"entry_point": "demo-cli"}}
    )
    monkeypatch.setattr("nanobot.apps.cli.service.get_runtime_subdir", lambda _name: data_dir)
    monkeypatch.setattr(
        "nanobot.apps.cli.service.shutil.which",
        lambda entry: "/usr/bin/demo-cli" if entry == "demo-cli" else None,
    )

    seen: dict[str, str] = {}

    def fake_run(argv, **kwargs):
        seen["cwd"] = kwargs["cwd"]
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("nanobot.apps.cli.service.subprocess.run", fake_run)
    tool = CliAppsTool(
        workspace=tmp_path,
        restrict_to_workspace=True,
        runtime=CliAppsRuntimeConfig(run_timeout=5),
    )

    restricted = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=False,
    )
    token = bind_workspace_scope(restricted)
    try:
        blocked = await tool.execute(name="demo", working_dir=str(outside))
    finally:
        reset_workspace_scope(token)
    assert "outside the configured workspace" in blocked

    full = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "full"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(full)
    try:
        result = await tool.execute(name="demo", working_dir=str(outside))
    finally:
        reset_workspace_scope(token)
    assert "CLI app 'demo' exited 0" in result
    assert seen["cwd"] == str(outside.resolve())


@pytest.mark.asyncio
async def test_spawn_tool_forwards_current_workspace_scope(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=tmp_path,
        default_restrict_to_workspace=False,
    )

    class Manager:
        max_concurrent_subagents = 4

        def __init__(self) -> None:
            self.seen = None

        def get_running_count(self) -> int:
            return 0

        async def spawn(self, **kwargs):
            self.seen = kwargs
            return "spawned"

    manager = Manager()
    tool = SpawnTool(manager)  # type: ignore[arg-type]
    token = bind_workspace_scope(scope)
    try:
        with request_context(RequestContext(
            channel="test",
            chat_id="chat",
            runtime=MagicMock(),
        )):
            result = await tool.execute(task="inspect")
    finally:
        reset_workspace_scope(token)

    assert result == "spawned"
    assert manager.seen["workspace_scope"] == scope
