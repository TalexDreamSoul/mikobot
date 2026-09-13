"""Member Dream file-tool boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.collaboration.models import ConversationScope, ConversationScopeKind, Project, User


def _member_request(project_path: Path) -> RequestContext:
    user = User(
        id="member",
        display_name="Member",
        default_project_id="alpha",
        created_at_ms=1,
        updated_at_ms=1,
    )
    project = Project(
        id="alpha",
        name="Alpha",
        workspace_path=str(project_path),
        created_by_user_id=user.id,
        created_at_ms=1,
        updated_at_ms=1,
    )
    scope = ConversationScope(
        kind=ConversationScopeKind.DIRECT,
        user_id=user.id,
        project_id=project.id,
        user=user,
        project=project,
        binding=None,
        assignment=None,
        workspace_path=str(project_path),
        session_suffix="member:alpha",
    )
    return RequestContext(
        channel="telegram",
        chat_id="member-chat",
        session_key="user:member:project:alpha:member-chat",
        attributes={"collaboration_scope": scope},
    )


@pytest.mark.asyncio
async def test_member_dream_tools_write_only_own_private_memory_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "shared-project"
    private_root = tmp_path / "runtime" / "users" / "member" / "projects" / "alpha"
    shared_target = project / "shared-note.txt"
    project.mkdir()
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir",
        lambda name: tmp_path / "runtime" / name,
    )
    store = MemoryStore(private_root)
    store.history_file.write_text('{"content": "private history"}\n', encoding="utf-8")
    tools = store.build_dream_tools(allow_skill_generation=False)

    with request_context(_member_request(project)):
        own_profile = await tools.execute(
            "write_file",
            {"path": "USER.md", "content": "member profile"},
        )
        shared_write = await tools.execute(
            "write_file",
            {"path": str(shared_target), "content": "shared mutation"},
        )
        history_write = await tools.execute(
            "write_file",
            {"path": "memory/history.jsonl", "content": "overwritten history"},
        )

    assert "Successfully wrote" in own_profile
    assert store.user_file.read_text(encoding="utf-8") == "member profile"
    assert shared_write.is_error
    assert not shared_target.exists()
    assert history_write.is_error
    assert store.history_file.read_text(encoding="utf-8") == '{"content": "private history"}\n'
