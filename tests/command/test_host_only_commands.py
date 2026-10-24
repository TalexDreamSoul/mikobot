"""Commands that act on the host itself must refuse every other principal."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.collaboration import AsyncLocalCollaborationRepository, CollaborationStore
from nanobot.command import builtin
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.config.schema import ToolsConfig
from nanobot.session.manager import SessionManager


class _StrictProvider(SimpleNamespace):
    # The model runtime resolver tracks providers in a set.
    __hash__ = object.__hash__


@pytest_asyncio.fixture
async def host_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AgentLoop]:
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir", lambda name: tmp_path / "runtime" / name,
    )
    store = CollaborationStore(tmp_path / "collaboration")
    owner, _home = store.ensure_local_owner(host)
    store.bind_identity("feishu", "ou_owner", owner.id)
    admin, _project = store.ensure_identity_user("websocket", "oidc:admin", host)
    store.update_user_admin(admin.id, True)
    store.ensure_identity_user("feishu", "ou_member", host)
    store.ensure_identity_user("websocket", "proxy:member", host)
    repository = AsyncLocalCollaborationRepository(store)
    await repository.initialize()
    provider = _StrictProvider(
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
        yield loop
    finally:
        await repository.aclose()


def _message(channel: str, sender_id: str, chat_id: str | None = None, **metadata: object) -> InboundMessage:
    return InboundMessage(
        channel=channel, sender_id=sender_id, chat_id=chat_id or sender_id, content="", metadata=metadata,
    )


# (principal, may use host-only commands, may restart)
_PRINCIPALS = {
    "owner_browser": (_message("websocket", "browser"), True, True),
    "owner_direct_chat": (_message("feishu", "ou_owner"), True, True),
    "owner_in_group": (_message("feishu", "ou_owner", "oc_group", chat_type="group"), False, True),
    "administrator": (_message("websocket", "oidc:admin"), False, True),
    "member_direct_chat": (_message("feishu", "ou_member"), False, False),
    "proxy_member": (_message("websocket", "proxy:member"), False, False),
    "unknown_sender": (_message("feishu", "ou_stranger"), False, False),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", _PRINCIPALS)
async def test_host_authority_follows_the_resolved_principal(
    host_runtime: AgentLoop, principal: str,
) -> None:
    msg, host_only, restart = _PRINCIPALS[principal]

    assert await host_runtime.is_host_conversation(msg) is host_only
    assert await host_runtime.is_host_conversation(msg, allow_admin=True) is restart


_COMMANDS = [
    ("cmd_restart", "/restart", True),
    ("cmd_dream_log", "/dream-log", False),
    ("cmd_dream_log", "/dream-log abc123", False),
    ("cmd_dream_restore", "/dream-restore", False),
    ("cmd_dream_restore", "/dream-restore abc123", False),
    ("cmd_dream_prompt", "/dream-prompt", False),
    ("cmd_dream_prompt", "/dream-prompt init", False),
    ("cmd_evaluator_prompt", "/evaluator-prompt", False),
    ("cmd_evaluator_prompt", "/evaluator-prompt init", False),
    ("cmd_pairing", "/pairing", False),
    ("cmd_pairing", "/pairing list", False),
    ("cmd_pairing", "/pairing approve abc123", False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", _PRINCIPALS)
@pytest.mark.parametrize(("handler_name", "raw", "restart_command"), _COMMANDS)
async def test_registered_host_commands_run_only_for_the_host(
    host_runtime: AgentLoop,
    monkeypatch: pytest.MonkeyPatch,
    principal: str,
    handler_name: str,
    raw: str,
    restart_command: bool,
) -> None:
    ran: list[str] = []

    async def handler(ctx: CommandContext) -> OutboundMessage:
        ran.append(handler_name)
        return OutboundMessage(channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content="ran")

    monkeypatch.setattr(builtin, handler_name, handler)
    router = CommandRouter()
    builtin.register_builtin_commands(router)
    template, host_only, restart = _PRINCIPALS[principal]
    msg = InboundMessage(
        channel=template.channel,
        sender_id=template.sender_id,
        chat_id=template.chat_id,
        content=raw,
        metadata=dict(template.metadata),
    )
    ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=host_runtime)

    result = await (router.dispatch_priority(ctx) if restart_command else router.dispatch(ctx))

    assert isinstance(result, OutboundMessage)
    if restart if restart_command else host_only:
        assert ran == [handler_name]
        assert result.content == "ran"
    else:
        assert ran == []
        assert result.content == (
            "This command requires system administrator access."
            if restart_command
            else "This command is only available to the host owner."
        )
        assert result.channel == msg.channel and result.chat_id == msg.chat_id
