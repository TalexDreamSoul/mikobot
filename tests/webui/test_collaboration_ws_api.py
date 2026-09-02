from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from nanobot.channels.websocket.runtime import TrustedProxyAuthConfig, WebSocketConfig
from nanobot.collaboration import AsyncLocalCollaborationRepository, CollaborationStore
from nanobot.collaboration.models import COLLABORATION_USER_METADATA_KEY, PairingPurpose
from nanobot.cron.types import CronJob, CronPayload, CronSchedule
from nanobot.session.manager import SessionManager
from nanobot.webui.forking import handle_webui_fork_chat
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.inbound_commands import WebUICommandRouter
from nanobot.webui.oidc_auth import OidcAuthenticator
from nanobot.webui.session_access import WebuiSessionAccess
from nanobot.webui.ws_http import GatewayHTTPHandler


def _json(response: Any) -> dict[str, Any]:
    return json.loads(response.body)


def _request(path: str, headers: dict[str, str]) -> WsRequest:
    return WsRequest(path, Headers(headers))


def _connection(request: WsRequest) -> SimpleNamespace:
    return SimpleNamespace(request=request, remote_address=("127.0.0.1", 8765))


def _local_connection(path: str = "/") -> SimpleNamespace:
    return _connection(_request(path, {"Authorization": "Bearer local-token"}))


def _proxy_connection(subject: str, path: str = "/api/collaboration") -> SimpleNamespace:
    return _connection(
        _request(
            path,
            {
                "X-Proxy-Authenticated": "yes",
                "X-Proxy-Subject": subject,
            },
        )
    )


async def _handler(tmp_path) -> GatewayHTTPHandler:
    sessions = SessionManager(tmp_path / "sessions")
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = WebSocketConfig(
        host="127.0.0.1",
        token="local-token",
        trusted_proxy_auth=TrustedProxyAuthConfig(
            trusted_peer_cidrs=["127.0.0.1/32"],
            assertion_header="X-Proxy-Authenticated",
            subject_header="X-Proxy-Subject",
        ),
    )
    handler.collaboration = AsyncLocalCollaborationRepository(
        CollaborationStore(tmp_path / "collaboration")
    )
    handler._collaboration_init_lock = asyncio.Lock()
    handler._collaboration_initialized = False
    handler._collaboration_closed = False
    handler.session_manager = sessions
    handler.tokens = SimpleNamespace(
        check_api_token=lambda request: request.headers.get("Authorization") == "Bearer local-token"
    )
    handler.oidc = OidcAuthenticator(handler.config.oidc_auth)
    handler._oidc_connection_revoker = None
    scope = SimpleNamespace(payload=lambda: {"access_mode": "full"})
    handler.workspaces = SimpleNamespace(
        default_scope=lambda: scope,
        scope_for_indexed_metadata=lambda *_args, **_kwargs: scope,
        scope_for_session_key=lambda _key: scope,
    )
    handler.settings_routes = SimpleNamespace(
        dispatch=AsyncMock(return_value=None),
        is_mutation_path=lambda _path: False,
    )
    handler.skills_workspace_path = tmp_path / "workspace"
    handler.static_dist_path = None
    handler.cron_service = None
    handler.local_trigger_store = None
    handler.cron_pending_job_ids = None
    webui_connections: set[Any] = set()
    handler._webui_connections = webui_connections
    handler.endpoint = SimpleNamespace(webui_connections=webui_connections)
    handler.local_trigger_pending_ids = None
    handler.recovery_action = None
    handler.media = MagicMock()
    handler._log = MagicMock()
    handler._collaboration_available = lambda: {"skills": [], "mcp_servers": []}
    await handler.initialize_collaboration()
    return handler


def _save_webui_session(
    sessions: SessionManager,
    key: str,
    *,
    owner_id: str | None,
    title: str,
) -> None:
    session = sessions.get_or_create(key)
    session.metadata.update({"webui": True, "title": title})
    if owner_id is not None:
        session.metadata[COLLABORATION_USER_METADATA_KEY] = owner_id
    session.add_message("user", f"{title} history")
    sessions.save(session)

@pytest.mark.asyncio
async def test_local_websocket_handshake_auth_authorizes_collaboration_mutation_without_http_token(
    tmp_path,
) -> None:
    """A WebUI socket authenticated during its handshake may mutate its local project."""
    handler = await _handler(tmp_path)
    connection = MagicMock(request=_request("/", {}), remote_address=("127.0.0.1", 8765))
    handler.endpoint.webui_connections.add(connection)
    owner, project = await handler.collaboration.ensure_identity_user(
        "websocket", "webui-http", handler.skills_workspace_path, local_owner=True
    )

    response = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.task_list.create",
        {"project_id": project.id, "name": "Inbox", "user_id": owner.id},
    )

    assert response.status_code == 200
    assert [
        item.name
        for item in await handler.collaboration.list_task_lists(owner.id, project.id)
    ] == ["Inbox"]



@pytest.mark.asyncio
async def test_local_owner_uses_authenticated_mutation_dispatcher_without_secret_descriptors(
    tmp_path,
) -> None:
    """Authenticated local WebUI mutations create one owner's project surface only."""
    handler = await _handler(tmp_path)
    handler._collaboration_available = lambda: {
        "skills": [{"id": "research"}],
        "mcp_servers": [{"id": "docs"}],
    }
    connection = _local_connection()
    owner, _ = await handler.collaboration.ensure_identity_user(
        "websocket", "webui-http", handler.skills_workspace_path, local_owner=True
    )
    other, _ = await handler.collaboration.ensure_identity_user(
        "websocket", "other-client", handler.skills_workspace_path, local_owner=False
    )

    project_response = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.project.create",
        {"name": "Owner project", "user_id": other.id},
    )
    assert project_response.status_code == 200
    project_id = _json(project_response)["project"]["id"]
    assert await handler.collaboration.get_project(owner.id, project_id) is not None
    assert await handler.collaboration.get_project(other.id, project_id) is None

    task_list_response = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.task_list.create",
        {"project_id": project_id, "name": "Inbox", "user_id": other.id},
    )
    task_list_id = _json(task_list_response)["task_list"]["id"]
    task_response = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.task.create",
        {
            "project_id": project_id,
            "task_list_id": task_list_id,
            "title": "Keep this task private",
            "user_id": other.id,
        },
    )
    assert task_response.status_code == 200

    source_response = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.context_source.create",
        {
            "project_id": project_id,
            "name": "Private notes",
            "kind": "custom",
            "config": {"path": "notes.md", "token": "must-not-leak"},
            "user_id": other.id,
        },
    )
    assert source_response.status_code == 200


    details_request = _request(
        f"/api/collaboration/projects/{project_id}", {"Authorization": "Bearer local-token"}
    )
    details = await handler.dispatch(_connection(details_request), details_request)
    assert details is not None
    assert details.status_code == 200
    payload = _json(details)
    assert payload["tasks"][0]["title"] == "Keep this task private"
    assert payload["context_sources"][0]["config"] == {"path": "notes.md"}
    assert "workspace_path" not in payload["project"]

    profile_response = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.extensions.update",
        {
            "project_id": project_id,
            "revision": 0,
            "settings": {"skills": ["research"], "mcpServers": ["docs"]},
        },
    )
    assert profile_response.status_code == 200
    assert _json(profile_response)["extension_profile"]["revision"] == 1

    stale_profile_response = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.extensions.update",
        {"project_id": project_id, "revision": 0, "settings": {}},
    )
    raw_mutation_request = _request(
        "/api/collaboration/mutations/project/create", {"Authorization": "Bearer local-token"}
    )
    raw_mutation_response = await handler.dispatch(
        _connection(raw_mutation_request), raw_mutation_request
    )
    assert raw_mutation_response is not None
    assert raw_mutation_response.status_code == 405
    assert stale_profile_response.status_code == 409
    unauthenticated = _connection(_request("/", {}))
    unauthenticated_response = await handler.dispatch_webui_mutation(
        unauthenticated,
        "collaboration.project.create",
        {"name": "Unauthorized"},
    )
    assert unauthenticated_response.status_code == 401


@pytest.mark.asyncio
async def test_collaboration_extension_profile_rejects_plugins_and_accepts_available_selections(
    tmp_path,
) -> None:
    """Extension profiles reject plugins but retain selected available skills and MCP servers."""
    handler = await _handler(tmp_path)
    handler._collaboration_available = lambda: {
        "skills": [{"id": "research"}],
        "mcp_servers": [{"id": "docs"}],
    }
    connection = _local_connection()
    project_response = await handler.dispatch_webui_mutation(
        connection, "collaboration.project.create", {"name": "Extensions"}
    )
    project_id = _json(project_response)["project"]["id"]

    rejected = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.extensions.update",
        {
            "project_id": project_id,
            "revision": 0,
            "settings": {"plugins": ["reviewer"]},
        },
    )
    assert rejected.status_code == 400

    accepted = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.extensions.update",
        {
            "project_id": project_id,
            "revision": 0,
            "settings": {"skills": ["research"], "mcpServers": ["docs"]},
        },
    )
    assert accepted.status_code == 200
    assert _json(accepted)["extension_profile"] == {
        "revision": 1,
        "settings": {"skills": ["research"], "mcpServers": ["docs"]},
    }

    details_request = _request(
        f"/api/collaboration/projects/{project_id}", {"Authorization": "Bearer local-token"}
    )
    details = await handler.dispatch(_connection(details_request), details_request)
    assert details is not None
    assert _json(details)["extension_profile"] == {
        "revision": 1,
        "settings": {"skills": ["research"], "mcpServers": ["docs"]},
    }


@pytest.mark.asyncio
async def test_collaboration_http_exposes_selected_bot_summary_detail_and_owner_mutations(tmp_path) -> None:
    """The authenticated owner can select a created bot while another user cannot discover its detail."""
    handler = await _handler(tmp_path)
    owner = _local_connection()
    index_request = _request("/api/collaboration", {"Authorization": "Bearer local-token"})
    index = await handler.dispatch(_connection(index_request), index_request)
    assert index is not None and index.status_code == 200
    organization_id = _json(index)["organizations"][0]["id"]

    created = await handler.dispatch_webui_mutation(
        owner, "collaboration.bot.create",
        {"organization_id": organization_id, "name": "Release bot"},
    )
    assert created.status_code == 200
    bot = _json(created)["bot"]
    selected = await handler.dispatch_webui_mutation(
        owner, "collaboration.user.defaults",
        {"organization_id": organization_id, "bot_id": bot["id"], "project_id": None},
    )
    assert selected.status_code == 200
    assert _json(selected)["user"]["default_bot_id"] == bot["id"]

    refreshed_request = _request("/api/collaboration", {"Authorization": "Bearer local-token"})
    refreshed = await handler.dispatch(_connection(refreshed_request), refreshed_request)
    assert refreshed is not None and refreshed.status_code == 200
    summary = _json(refreshed)
    assert summary["active_bot_id"] == bot["id"]
    assert {item["id"] for item in summary["bots"]} >= {bot["id"]}

    detail_request = _request(
        f"/api/collaboration/bots/{bot['id']}", {"Authorization": "Bearer local-token"}
    )
    detail = await handler.dispatch(_connection(detail_request), detail_request)
    assert detail is not None and detail.status_code == 200
    assert _json(detail)["bot"] == bot
    assert _json(detail)["channels"] == []
    assert _json(detail)["projects"] == []

    foreign = _proxy_connection("unrelated-user", f"/api/collaboration/bots/{bot['id']}")
    hidden = await handler.dispatch(foreign, foreign.request)
    assert hidden is not None and hidden.status_code == 404



@pytest.mark.asyncio
async def test_proxy_user_cannot_read_or_mutate_foreign_collaboration_resources(tmp_path) -> None:
    """Authenticated proxy identity, not payload IDs, scopes every collaboration resource."""
    handler = await _handler(tmp_path)
    alice = _proxy_connection("alice")
    bob = _proxy_connection("bob")

    alice_index = await handler.dispatch(alice, alice.request)
    bob_index = await handler.dispatch(bob, bob.request)
    assert alice_index is not None and bob_index is not None
    alice_id = _json(alice_index)["user"]["id"]
    bob_id = _json(bob_index)["user"]["id"]

    alice_project = await handler.dispatch_webui_mutation(
        alice, "collaboration.project.create", {"name": "Alice private", "user_id": bob_id}
    )
    project_id = _json(alice_project)["project"]["id"]
    task_list = await handler.dispatch_webui_mutation(
        alice,
        "collaboration.task_list.create",
        {"project_id": project_id, "name": "Private list"},
    )
    task_list_id = _json(task_list)["task_list"]["id"]
    task = await handler.dispatch_webui_mutation(
        alice,
        "collaboration.task.create",
        {"project_id": project_id, "task_list_id": task_list_id, "title": "Alice task"},
    )
    task_id = _json(task)["task"]["id"]
    source = await handler.dispatch_webui_mutation(
        alice,
        "collaboration.context_source.create",
        {"project_id": project_id, "name": "Alice source", "kind": "custom"},
    )
    source_id = _json(source)["context_source"]["id"]

    foreign_details = _request(f"/api/collaboration/projects/{project_id}", bob.request.headers)
    foreign_response = await handler.dispatch(_connection(foreign_details), foreign_details)
    assert foreign_response is not None
    assert foreign_response.status_code == 404

    rejected = await handler.dispatch_webui_mutation(
        bob,
        "collaboration.task.create",
        {
            "project_id": project_id,
            "task_list_id": task_list_id,
            "title": "Injected task",
            "user_id": alice_id,
        },
    )
    assert rejected.status_code == 404
    for action, payload in (
        (
            "collaboration.task.update",
            {
                "project_id": project_id,
                "task_id": task_id,
                "values": {"title": "stolen"},
                "user_id": alice_id,
            },
        ),
        (
            "collaboration.context_source.update",
            {
                "project_id": project_id,
                "source_id": source_id,
                "values": {"name": "stolen"},
                "user_id": alice_id,
            },
        ),
        (
            "collaboration.context_source.delete",
            {"project_id": project_id, "source_id": source_id, "user_id": alice_id},
        ),
        (
            "collaboration.task.delete",
            {"project_id": project_id, "task_id": task_id, "user_id": alice_id},
        ),
    ):
        response = await handler.dispatch_webui_mutation(bob, action, payload)
        assert response.status_code == 404

    assert (await handler.collaboration.get_task(alice_id, task_id)).title == "Alice task"
    assert (await handler.collaboration.get_context_source(alice_id, source_id)).name == "Alice source"


@pytest.mark.asyncio
async def test_proxy_session_boundary_hides_foreign_legacy_and_automation_resources(tmp_path) -> None:
    """Proxy WebUI users cannot attach, fork, mention, preview, delete, or automate another owner’s session."""
    handler = await _handler(tmp_path)
    alice = _proxy_connection("alice")
    bob = _proxy_connection("bob")
    alice_index = await handler.dispatch(alice, alice.request)
    bob_index = await handler.dispatch(bob, bob.request)
    assert alice_index is not None and bob_index is not None
    alice_id = _json(alice_index)["user"]["id"]
    bob_id = _json(bob_index)["user"]["id"]
    await handler.collaboration.ensure_identity_user(
        "websocket", "webui-http", handler.skills_workspace_path, local_owner=True
    )

    _save_webui_session(handler.session_manager, "websocket:alice", owner_id=alice_id, title="Alice")
    _save_webui_session(handler.session_manager, "websocket:bob", owner_id=bob_id, title="Bob")
    _save_webui_session(handler.session_manager, "websocket:legacy", owner_id=None, title="Legacy")

    bob_list_request = _request("/api/sessions", bob.request.headers)
    bob_list = await handler.dispatch(_connection(bob_list_request), bob_list_request)
    assert bob_list is not None
    assert [row["key"] for row in _json(bob_list)["sessions"]] == ["websocket:bob"]

    local_list_request = _request("/api/sessions", {"Authorization": "Bearer local-token"})
    local_list = await handler.dispatch(_connection(local_list_request), local_list_request)
    assert local_list is not None
    assert {row["key"] for row in _json(local_list)["sessions"]} == {"websocket:legacy"}
    assert await handler.can_access_webui_session(bob, "websocket:alice") is False
    assert await handler.can_access_webui_session(_local_connection(), "websocket:legacy") is True
    assert await handler.can_access_webui_session(_local_connection(), "websocket:bob") is False
    attach_transport = SimpleNamespace(
        webui_send_event=AsyncMock(),
        webui_attach=MagicMock(),
        webui_hydrate=AsyncMock(),
    )
    attach_router = object.__new__(WebUICommandRouter)
    attach_router.gateway = SimpleNamespace(
        can_access_webui_session=handler.can_access_webui_session,
    )
    attach_router._transport = attach_transport
    attach_router._temporary_chats = SimpleNamespace(validate_attach=lambda _chat_id: None)
    await attach_router.dispatch(bob, "bob", {"type": "attach", "chat_id": "alice"})
    attach_transport.webui_send_event.assert_awaited_once_with(
        bob,
        "error",
        detail="session_not_found",
        chat_id="alice",
    )
    attach_transport.webui_attach.assert_not_called()
    attach_transport.webui_hydrate.assert_not_awaited()

    for path in (
        "/api/sessions/websocket%3Aalice/context",
        "/api/sessions/websocket%3Aalice/file-preview?probe=1",
        "/api/sessions/websocket%3Aalice/automations",
    ):
        request = _request(path, bob.request.headers)
        response = await handler.dispatch(_connection(request), request)
        assert response is not None
        assert response.status_code == 404

    deleted = await handler.dispatch_webui_mutation(bob, "session.delete", {"key": "websocket:alice"})
    assert deleted.status_code == 404
    assert handler.session_manager.read_session_metadata("websocket:alice") is not None

    job = CronJob(
        id="alice-job",
        name="Alice automation",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        payload=CronPayload(session_key="websocket:alice"),
    )
    handler.cron_service = SimpleNamespace(
        list_jobs=lambda **_kwargs: [job],
        get_job=lambda job_id: job if job_id == job.id else None,
        enable_job=MagicMock(),
        remove_job=MagicMock(),
    )
    automations_request = _request("/api/webui/automations", bob.request.headers)
    automations = await handler.dispatch(_connection(automations_request), automations_request)
    assert automations is not None
    assert _json(automations)["jobs"] == []

    action_request = _request("/api/webui/automations/disable?id=alice-job", bob.request.headers)
    setattr(action_request, "_nanobot_connection", bob)
    setattr(action_request, "_nanobot_trusted_proxy_authenticated", True)
    denied_automation = await handler._handle_webui_automation_action(action_request, "disable")
    assert denied_automation.status_code == 404
    handler.cron_service.enable_job.assert_not_called()

    accessible_to_bob = {
        "websocket:alice": await handler.can_access_webui_session(bob, "websocket:alice")
    }
    mention = WebuiSessionAccess(handler.session_manager).normalize_mentions(
        [{"name": "Alice", "session_key": "websocket:alice"}],
        can_access=accessible_to_bob.__getitem__,
    )
    assert mention == []

    fork_host = SimpleNamespace(
        gateway=SimpleNamespace(
            http=handler,
            session_manager=handler.session_manager,
            can_access_webui_session=handler.can_access_webui_session,
        ),
        send_webui_protocol_error=AsyncMock(),
        attach_webui_fork=AsyncMock(),
    )
    await handle_webui_fork_chat(
        fork_host,
        bob,
        {"source_chat_id": "alice", "before_user_index": 0},
    )
    fork_host.send_webui_protocol_error.assert_awaited_once_with(bob, "fork source not found")
    fork_host.attach_webui_fork.assert_not_awaited()


async def _claim_channel_instance(
    handler: GatewayHTTPHandler, connection: Any, instance_id: str
) -> None:
    """Claim one channel instance through the local repository's pairing flow."""
    index = await handler.dispatch(connection, connection.request)
    assert index is not None and index.status_code == 200
    actor_id = _json(index)["user"]["id"]
    bot = (await handler.collaboration.list_bots(actor_id))[0]
    challenge, code = await handler.collaboration.create_pairing_challenge(
        actor_id,
        purpose=PairingPurpose.CLAIM_CHANNEL,
        organization_id=bot.organization_id,
        bot_id=bot.id,
        channel_type="weixin",
        instance_id=instance_id,
    )
    await handler.collaboration.verify_pairing_challenge(
        code,
        channel_type="weixin",
        instance_id=instance_id,
        sender_id=f"{instance_id}-sender",
    )
    await handler.collaboration.consume_pairing_challenge(actor_id, challenge.id)


@pytest.mark.asyncio
async def test_ordinary_claimed_channel_owner_can_use_channel_control_mutation(tmp_path) -> None:
    """A claimed channel owner may reach channel control without system-admin privileges."""
    handler = await _handler(tmp_path)
    connection = _proxy_connection("ordinary-channel-owner")
    async def settings_dispatch(_connection, _request, path):
        if path == "/api/settings/channels/configure":
            return http_json_response({"status": "ok"})
        return None

    handler.settings_routes = SimpleNamespace(
        dispatch=settings_dispatch,
        is_mutation_path=lambda _path: False,
    )

    await _claim_channel_instance(handler, connection, "claimed")
    response = await handler.dispatch_webui_mutation(
        connection,
        "settings.channel.configure",
        {"name": "weixin", "instance_id": "claimed", "values": {}},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ordinary_user_cannot_use_channel_control_mutation_for_unclaimed_instance(
    tmp_path,
) -> None:
    """An ordinary collaboration user cannot mutate an unclaimed channel instance."""
    handler = await _handler(tmp_path)
    connection = _proxy_connection("ordinary-channel-user")
    async def settings_dispatch(_connection, _request, path):
        if path == "/api/settings/channels/configure":
            return http_json_response({"status": "unexpected"})
        return None

    handler.settings_routes = SimpleNamespace(
        dispatch=settings_dispatch,
        is_mutation_path=lambda _path: False,
    )

    index = await handler.dispatch(connection, connection.request)
    assert index is not None and index.status_code == 200
    response = await handler.dispatch_webui_mutation(
        connection,
        "settings.channel.configure",
        {"name": "weixin", "instance_id": "unclaimed", "values": {}},
    )

    assert response.status_code == 403
