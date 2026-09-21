"""Member visibility limits for the self-inspection tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.self import MyTool
from nanobot.bus.queue import MessageBus
from nanobot.collaboration.models import ConversationScope, ConversationScopeKind, Project, User
from nanobot.config.schema import ToolsConfig


def _member_request(project_path: Path, *, channel: str = "telegram") -> RequestContext:
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
        channel=channel,
        chat_id="member-chat",
        sender_id="member-sender",
        session_key="user:member:project:alpha:member-chat",
        attributes={"collaboration_scope": scope},
    )


def _channel_status() -> dict[str, dict[str, object]]:
    """The shape of ``ChannelManager.get_status()``, including host-only fields."""
    return {
        "feishu.assistant-2bf084": {
            "enabled": True,
            "running": False,
            "state": "failed",
            "owner": "feishu",
            "instance_id": "assistant-2bf084",
            "error": "app secret rejected",
            "app_secret": "feishu-app-secret",
        },
        "telegram": {
            "enabled": True,
            "running": True,
            "state": "running",
            "owner": "telegram",
            "instance_id": "default",
        },
        "websocket": {
            "enabled": False,
            "running": False,
            "state": "stopped",
            "owner": "websocket",
            "instance_id": "default",
            "pairing_only": True,
            "app_id": "cli_a3f9c2",
            "token": "webui-session-token",
            "config_path": "/etc/nanobot/gateway-secret.json",
        },
    }


def _member_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        tools_config=ToolsConfig(),
    )


def _member_my_tool(loop: AgentLoop) -> MyTool:
    tool = loop.tools.get("my")
    assert isinstance(tool, MyTool)
    return tool


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
        ("set", "channels", {"telegram": {}}),
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


@pytest.mark.asyncio
async def test_member_self_tool_sees_only_its_own_channel_instance(tmp_path: Path) -> None:
    loop = _member_loop(tmp_path)
    loop.register_channel_status_provider(_channel_status)
    tool = _member_my_tool(loop)

    with request_context(_member_request(tmp_path, channel="feishu.assistant-2bf084")):
        result = await tool.execute(action="check", key="channels")
        exposed = tool._runtime_control.snapshot().channels

    assert exposed == {
        "feishu.assistant-2bf084": {
            "enabled": True,
            "running": False,
            "state": "failed",
            "instance_id": "assistant-2bf084",
        },
    }
    assert result.startswith("channels: {'feishu.assistant-2bf084': {")
    for visible in ("'enabled': True", "'running': False", "'state': 'failed'",
                    "'instance_id': 'assistant-2bf084'"):
        assert visible in result
    for hidden in ("telegram", "websocket", "owner", "error", "pairing_only", "app_id",
                   "app_secret", "feishu-app-secret", "app secret rejected", "token",
                   "webui-session-token", "config_path", "/etc/nanobot/gateway-secret.json"):
        assert hidden not in result


@pytest.mark.asyncio
async def test_member_self_tool_gets_no_channel_status_without_its_own_instance(
    tmp_path: Path,
) -> None:
    loop = _member_loop(tmp_path)
    loop.register_channel_status_provider(_channel_status)
    tool = _member_my_tool(loop)

    with request_context(_member_request(tmp_path, channel="feishu.assistant-9fff")):
        result = await tool.execute(action="check", key="channels")
        exposed = tool._runtime_control.snapshot().channels

    assert exposed == {}
    assert result == "channels: {}"
