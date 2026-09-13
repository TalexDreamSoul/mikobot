"""Black-box member tool boundaries for project-scoped collaboration turns."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.agent.tools.cli_apps import CliAppsTool
from nanobot.agent.tools.context import RequestContext, ToolContext, request_context
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool
from nanobot.agent.tools.image_generation import ImageGenerationError, ImageGenerationTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.search import GrepTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.bus.events import OutboundMessage
from nanobot.collaboration.models import ConversationScope, ConversationScopeKind, Project, User
from nanobot.config.schema import ImageGenerationToolConfig, ToolsConfig
from nanobot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)


def _member_scope(project: Path) -> ConversationScope:
    user = User(
        id="member",
        display_name="Member",
        default_project_id="alpha",
        created_at_ms=1,
        updated_at_ms=1,
    )
    project_record = Project(
        id="alpha",
        name="Alpha",
        workspace_path=str(project),
        created_by_user_id=user.id,
        created_at_ms=1,
        updated_at_ms=1,
        allowed_skills=("project-review",),
    )
    return ConversationScope(
        kind=ConversationScopeKind.DIRECT,
        user_id=user.id,
        project_id=project_record.id,
        user=user,
        project=project_record,
        binding=None,
        assignment=None,
        workspace_path=str(project),
        session_suffix="member:alpha",
    )


def _member_request(project: Path, attachment: Path | None = None) -> RequestContext:
    attributes: dict[str, object] = {"collaboration_scope": _member_scope(project)}
    if attachment is not None:
        attributes["authorized_attachment_paths"] = [str(attachment)]
    return RequestContext(
        channel="telegram",
        chat_id="member-chat",
        session_key="user:member:project:alpha:member-chat",
        attributes=attributes,
    )

@pytest.mark.asyncio
async def test_host_restricted_project_can_read_only_the_exact_agent_history_allowlist(
    tmp_path: Path,
) -> None:
    agent_workspace = tmp_path / "agent"
    project = tmp_path / "project"
    history = agent_workspace / "memory" / "history.jsonl"
    neighboring_memory = agent_workspace / "memory" / "host-secret.txt"
    history.parent.mkdir(parents=True)
    project.mkdir()
    history.write_text('{"content": "host history"}\n', encoding="utf-8")
    neighboring_memory.write_text("host secret", encoding="utf-8")
    tool = ReadFileTool.create(
        ToolContext(config=ToolsConfig(restrict_to_workspace=True), workspace=str(agent_workspace))
    )
    token = bind_workspace_scope(build_workspace_scope(project, "restricted"))
    try:
        allowed = await tool.execute(path=str(history))
        denied = await tool.execute(path=str(neighboring_memory))
    finally:
        reset_workspace_scope(token)

    assert "host history" in allowed
    assert "Error" in denied



@pytest.mark.asyncio
async def test_member_file_tools_allow_project_and_exact_attachment_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_workspace = tmp_path / "agent"
    project = tmp_path / "project-alpha"
    attachment = tmp_path / "runtime" / "users" / "member" / "media" / "alpha" / "receipt.txt"
    own_journal = tmp_path / "runtime" / "users" / "member" / "projects" / "alpha" / "memory" / "MEMORY.md"
    other_member_journal = tmp_path / "runtime" / "users" / "other-member" / "projects" / "alpha" / "memory" / "MEMORY.md"
    host_history = agent_workspace / "memory" / "history.jsonl"
    host_profile = agent_workspace / "USER.md"
    host_skill = agent_workspace / "skills" / "host-only" / "SKILL.md"
    other_project = tmp_path / "project-beta" / "other-project-secret.txt"
    global_media = tmp_path / "runtime" / "media" / "host-global-media.txt"
    project_secret = project / "project-secret.txt"
    project_skill = project / "skills" / "project-review" / "SKILL.md"
    traversal_to_host_history = project / "nested" / ".." / ".." / "agent" / "memory" / "history.jsonl"
    linked_host_history: Path | None = project / "linked-history.txt"
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: tmp_path / "runtime" / name,
    )
    for directory in (
        attachment.parent,
        own_journal.parent,
        other_member_journal.parent,
        global_media.parent,
        host_history.parent,
        host_skill.parent,
        other_project.parent,
        project_skill.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    attachment.write_text("member attachment", encoding="utf-8")
    own_journal.write_text("member journal", encoding="utf-8")
    other_member_journal.write_text("other member journal", encoding="utf-8")
    host_history.write_text("host history secret", encoding="utf-8")
    host_profile.write_text("host profile secret", encoding="utf-8")
    host_skill.write_text("host skill secret", encoding="utf-8")
    other_project.write_text("other project secret", encoding="utf-8")
    project_secret.write_text("alpha project secret", encoding="utf-8")
    project_skill.write_text("project skill instructions", encoding="utf-8")
    global_media.write_text("host global media secret", encoding="utf-8")
    try:
        linked_host_history.symlink_to(host_history)
    except OSError:
        linked_host_history = None

    ctx = ToolContext(config=ToolsConfig(restrict_to_workspace=True), workspace=str(agent_workspace))
    read = ReadFileTool.create(ctx)
    write = WriteFileTool.create(ctx)
    grep = GrepTool.create(ctx)
    member_output = project / "member-output.txt"
    host_write_target = agent_workspace / "host-write.txt"

    with request_context(_member_request(project, attachment)):
        project_result = await read.execute(path=str(project_secret))
        attachment_result = await read.execute(path=str(attachment))
        skill_result = await read.execute(path=str(project_skill))
        own_journal_result = await read.execute(path=str(own_journal))
        write_result = await write.execute(path=str(member_output), content="member output")
        own_journal_write = await write.execute(path=str(own_journal), content="updated journal")
        search_result = await grep.execute(
            path=str(project_secret),
            pattern="alpha project secret",
            output_mode="content",
        )
        forbidden_paths = [
            host_history,
            host_profile,
            host_skill,
            global_media,
            traversal_to_host_history,
            other_project,
            other_member_journal,
        ]
        if linked_host_history is not None:
            forbidden_paths.append(linked_host_history)
        forbidden_reads = [await read.execute(path=str(path)) for path in forbidden_paths]
        forbidden_write = await write.execute(path=str(host_write_target), content="host overwrite")

    assert "alpha project secret" in project_result
    assert "member attachment" in attachment_result
    assert "project skill instructions" in skill_result
    assert "member journal" in own_journal_result
    assert "Successfully wrote" in write_result
    assert "Successfully wrote" in own_journal_write
    assert member_output.read_text(encoding="utf-8") == "member output"
    assert own_journal.read_text(encoding="utf-8") == "updated journal"
    assert "alpha project secret" in search_result
    assert all("Error" in result for result in forbidden_reads)
    assert "host history secret" not in "\n".join(forbidden_reads)
    assert "host global media secret" not in "\n".join(forbidden_reads)
    assert "Error" in forbidden_write
    assert not host_write_target.exists()

@pytest.mark.asyncio
async def test_member_builtin_skill_access_respects_project_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project-alpha"
    agent_workspace = tmp_path / "agent"
    builtin_skills = tmp_path / "builtin-skills"
    allowed = builtin_skills / "project-review" / "SKILL.md"
    denied = builtin_skills / "host-only" / "SKILL.md"
    project.mkdir()
    allowed.parent.mkdir(parents=True)
    denied.parent.mkdir(parents=True)
    allowed.write_text("project review skill", encoding="utf-8")
    denied.write_text("host-only skill", encoding="utf-8")
    monkeypatch.setattr("nanobot.agent.skills.BUILTIN_SKILLS_DIR", builtin_skills)
    tool = ReadFileTool.create(
        ToolContext(config=ToolsConfig(restrict_to_workspace=True), workspace=str(agent_workspace))
    )

    with request_context(_member_request(project)):
        permitted = await tool.execute(path=str(allowed))
        refused = await tool.execute(path=str(denied))

    assert "project review skill" in permitted
    assert "Error" in refused




@pytest.mark.asyncio
async def test_member_shell_refuses_constructed_host_read_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project-alpha"
    project.mkdir()
    host_history = tmp_path / "host-history" / "history.jsonl"
    host_history.parent.mkdir()
    host_history.write_text("host history secret", encoding="utf-8")
    monkeypatch.setenv("NANOBOT_SANDBOX_ENFORCED", "bwrap")
    monkeypatch.setenv("HOST_SECRET", "host-only-token")

    with (
        patch("nanobot.agent.tools.sandbox.shutil.which", return_value=None),
        patch.object(ExecTool, "_spawn", new_callable=AsyncMock) as spawn,
    ):
        with request_context(_member_request(project)):
            result = await ExecTool(working_dir=str(tmp_path)).execute(
                command=(
                    "python3 -c \"from pathlib import Path; "
                    "print((Path.cwd().parent / 'host-history' / 'history.jsonl').read_text())\""
                )
            )
    assert result.is_error
    spawn.assert_not_awaited()
    assert host_history.read_text(encoding="utf-8") == "host history secret"


@pytest.mark.asyncio
async def test_member_shell_denies_windows_backend_without_falling_back_to_host_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project-alpha"
    project.mkdir()
    monkeypatch.setenv("NANOBOT_SANDBOX_ENFORCED", "bwrap")

    with (
        patch("nanobot.agent.tools.sandbox.sys.platform", "win32"),
        patch.object(ExecTool, "_spawn", new_callable=AsyncMock) as spawn,
    ):
        with request_context(_member_request(project)):
            result = await ExecTool(working_dir=str(tmp_path)).execute(command="echo should-not-run")

    assert result.is_error
    assert "only on Linux" in str(result)
    spawn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["shell", "login"])
async def test_member_shell_rejects_host_shell_startup_before_spawn(
    tmp_path: Path,
    option: str,
) -> None:
    project = tmp_path / "project-alpha"
    startup_marker = project / "startup-marker.txt"
    project.mkdir()
    (project / ".bash_profile").write_text(
        f"touch {startup_marker}\n",
        encoding="utf-8",
    )

    with patch.object(ExecTool, "_spawn", new_callable=AsyncMock) as spawn:
        with request_context(_member_request(project)):
            if option == "shell":
                result = await ExecTool(working_dir=str(tmp_path)).execute(
                    command="echo should-not-run",
                    shell="bash",
                )
            else:
                result = await ExecTool(working_dir=str(tmp_path)).execute(
                    command="echo should-not-run",
                    login=True,
                )

    assert result.is_error
    assert not startup_marker.exists()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_shell_removes_host_secrets_from_the_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project-alpha"
    project.mkdir()
    monkeypatch.setenv("HOST_SECRET", "host-only-token")
    process = AsyncMock()
    process.communicate.return_value = (b"", b"")
    process.returncode = 0
    process.pid = 1

    with (
        patch("nanobot.agent.tools.shell.wrap_command", return_value="bwrap -- member-command"),
        patch.object(ExecTool, "_spawn", return_value=process) as spawn,
    ):
        with request_context(_member_request(project)):
            result = await ExecTool(working_dir=str(tmp_path)).execute(command="echo safe")

    forwarded_env = spawn.await_args.args[2]
    assert "Exit code: 0" in result
    assert "HOST_SECRET" not in forwarded_env


def test_member_image_references_allow_project_files_and_exact_attachments_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project-alpha"
    project_image = project / "design.png"
    attachment = tmp_path / "runtime" / "users" / "member" / "media" / "alpha" / "input.png"
    global_media = tmp_path / "runtime" / "media" / "host-image.png"
    other_project = tmp_path / "project-beta" / "secret.png"
    for path in (project_image, attachment, global_media, other_project):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: tmp_path / "runtime" / name,
    )
    tool = ImageGenerationTool(
        workspace=tmp_path,
        config=ImageGenerationToolConfig(enabled=True),
    )

    with request_context(_member_request(project, attachment)):
        project_reference = tool._resolve_reference_image(str(project_image))
        attachment_reference = tool._resolve_reference_image(str(attachment))
        with pytest.raises(ImageGenerationError):
            tool._resolve_reference_image(str(global_media))
        with pytest.raises(ImageGenerationError):
            tool._resolve_reference_image(str(other_project))

    assert project_reference == str(project_image.resolve())
    assert attachment_reference == str(attachment.resolve())


@pytest.mark.asyncio
async def test_member_message_media_never_delivers_host_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project-alpha"
    project_file = project / "deliverable.txt"
    attachment = tmp_path / "runtime" / "users" / "member" / "media" / "alpha" / "receipt.txt"
    global_media = tmp_path / "runtime" / "media" / "host-secret.txt"
    for path, content in (
        (project_file, "project deliverable"),
        (attachment, "member receipt"),
        (global_media, "host-only media"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: tmp_path / "runtime" / name,
    )
    deliveries: list[OutboundMessage] = []

    async def send(message: OutboundMessage) -> None:
        deliveries.append(message)

    tool = MessageTool(send_callback=send, workspace=tmp_path)
    with request_context(_member_request(project, attachment)):
        delivered = await tool.execute(
            content="send approved files",
            media=[str(project_file), str(attachment)],
        )
        rejected = await tool.execute(content="send host file", media=[str(global_media)])

    assert "Message sent" in delivered
    assert len(deliveries) == 1
    assert deliveries[0].media == [str(project_file.resolve()), str(attachment.resolve())]
    assert rejected.is_error
    assert "host-only media" not in str(rejected)


@pytest.mark.asyncio
async def test_member_cli_app_execution_denies_before_constructing_native_runner(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project-alpha"
    project.mkdir()

    with patch("nanobot.agent.tools.cli_apps.CliAppManager") as manager:
        with request_context(_member_request(project)):
            result = await CliAppsTool(workspace=tmp_path).execute(
                name="untrusted-cli",
                args=["render"],
            )

    assert result.is_error
    assert "unavailable" in str(result)
    manager.assert_not_called()
