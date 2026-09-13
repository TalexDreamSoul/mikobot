"""Member visibility limits for the self-inspection tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.self import MyTool
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
        sender_id="member-sender",
        session_key="user:member:project:alpha:member-chat",
        attributes={"collaboration_scope": scope},
    )


@pytest.mark.asyncio
async def test_member_self_tool_can_inspect_only_its_request_metadata(tmp_path: Path) -> None:
    runtime = MagicMock()
    runtime.snapshot.side_effect = AssertionError("member request inspection must not snapshot host runtime")
    tool = MyTool(runtime_control=runtime)

    with request_context(_member_request(tmp_path)):
        result = await tool.execute(action="check", key="request")

    assert "telegram" in result
    assert "member-chat" in result
    assert "member-sender" in result
    runtime.snapshot.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "key", "value"),
    [
        ("check", None, None),
        ("check", "scratchpad", None),
        ("set", "member-note", "do not persist"),
    ],
)
async def test_member_self_tool_rejects_global_runtime_and_scratchpad_access_without_side_effects(
    tmp_path: Path,
    action: str,
    key: str | None,
    value: object,
) -> None:
    runtime = MagicMock()
    runtime.snapshot.side_effect = AssertionError("member access must not snapshot host runtime")
    tool = MyTool(runtime_control=runtime)

    with request_context(_member_request(tmp_path)):
        result = await tool.execute(action=action, key=key, value=value)

    assert result.is_error
    runtime.snapshot.assert_not_called()
    runtime.set_scratchpad.assert_not_called()
