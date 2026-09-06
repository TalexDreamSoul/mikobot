from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from nanobot.channels.websocket.runtime import TrustedProxyAuthConfig, WebSocketConfig
from nanobot.collaboration import AsyncLocalCollaborationRepository, CollaborationStore
from nanobot.collaboration.models import COLLABORATION_USER_METADATA_KEY
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
    handler._member_connect_sessions = {}
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
    handler.settings = SimpleNamespace(extensions=None)
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


def _channel_instances_snapshot(
    channel_type: str,
    display_name: str,
    instances: tuple[tuple[str, str], ...],
) -> Any:
    """Build one channel package exposing several named instances with a lifecycle."""
    from nanobot.extensions.contracts import (
        ExtensionAction,
        ExtensionComponentDescriptor,
        ExtensionComponentKind,
        ExtensionExecution,
        ExtensionLifecycle,
        ExtensionPackageDescriptor,
        ExtensionSnapshot,
        ExtensionSource,
        ExtensionTrust,
        extension_component_id,
        extension_package_id,
    )

    package_id = extension_package_id(ExtensionSource.CHANNEL_PACKAGE, channel_type)
    return ExtensionSnapshot(
        packages=(
            ExtensionPackageDescriptor(
                id=package_id,
                name=channel_type,
                display_name=display_name,
                source=ExtensionSource.CHANNEL_PACKAGE,
                trust=ExtensionTrust.FIRST_PARTY,
                execution=ExtensionExecution.IN_PROCESS,
                lifecycle=ExtensionLifecycle.DISABLED,
                components=tuple(
                    ExtensionComponentDescriptor(
                        id=extension_component_id(
                            package_id, ExtensionComponentKind.CHANNEL, instance_id
                        ),
                        package_id=package_id,
                        kind=ExtensionComponentKind.CHANNEL,
                        name=instance_id,
                        display_name=instance_id,
                        execution=ExtensionExecution.IN_PROCESS,
                        lifecycle=ExtensionLifecycle(lifecycle),
                        revision=f"{instance_id}-revision",
                        actions=frozenset({ExtensionAction.ENABLE}),
                    )
                    for instance_id, lifecycle in instances
                ),
            ),
        )
    )


def _install_registry(handler: GatewayHTTPHandler, snapshot: Any) -> Any:
    registry = SimpleNamespace(snapshot=MagicMock(return_value=snapshot))
    handler.settings = SimpleNamespace(extensions=registry)
    return registry


async def _identity(handler: GatewayHTTPHandler, connection: Any) -> dict[str, Any]:
    index = await handler.dispatch(connection, connection.request)
    assert index is not None and index.status_code == 200
    return _json(index)


async def _create_pairing(
    handler: GatewayHTTPHandler,
    connection: Any,
    *,
    project_id: str,
    channel_type: str,
    instance_id: str,
    assignee_user_id: str | None = None,
) -> tuple[str, str]:
    """Create a pairing challenge through the WebUI mutation boundary."""
    response = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.pairing.create",
        {
            "project_id": project_id,
            "channel_type": channel_type,
            "instance_id": instance_id,
            **({"assignee_user_id": assignee_user_id} if assignee_user_id else {}),
        },
    )
    assert response.status_code == 200, response.body
    pairing = _json(response)["pairing"]
    return pairing["id"], pairing["code"]


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
        "collaboration.project.update",
        {"project_id": project.id, "name": "Renamed"},
    )

    assert response.status_code == 200
    assert (await handler.collaboration.get_project(owner.id, project.id)).name == "Renamed"


@pytest.mark.asyncio
async def test_local_owner_is_administrator_and_sees_every_project(tmp_path) -> None:
    """The local owner administers the host: every project is listed and manageable."""
    handler = await _handler(tmp_path)
    handler._collaboration_available = lambda: {
        "skills": [{"id": "research"}],
        "mcp_servers": [{"id": "docs"}],
    }
    connection = _local_connection()
    alice = _proxy_connection("alice")
    alice_id = (await _identity(handler, alice))["user"]["id"]
    alice_project = await handler.dispatch_webui_mutation(
        alice, "collaboration.project.create", {"name": "Alice private"}
    )
    alice_project_id = _json(alice_project)["project"]["id"]

    index = await _identity(handler, _local_connection("/api/collaboration"))
    assert index["is_admin"] is True
    assert alice_project_id in {project["id"] for project in index["projects"]}

    capabilities = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.project.update",
        {
            "project_id": alice_project_id,
            "capabilities": {"allowed_skills": ["research"], "allowed_mcp_servers": ["docs"]},
        },
    )
    assert capabilities.status_code == 200
    assert _json(capabilities)["project"]["allowed_skills"] == ["research"]

    rejected = await handler.dispatch_webui_mutation(
        connection,
        "collaboration.project.update",
        {"project_id": alice_project_id, "capabilities": {"plugins": ["reviewer"]}},
    )
    assert rejected.status_code == 400

    details_request = _request(
        f"/api/collaboration/projects/{alice_project_id}", {"Authorization": "Bearer local-token"}
    )
    details = await handler.dispatch(_connection(details_request), details_request)
    assert details is not None and details.status_code == 200
    payload = _json(details)
    assert payload["can_manage"] is True
    assert "workspace_path" not in payload["project"]
    assert payload["project"]["allowed_mcp_servers"] == ["docs"]
    assert await handler.collaboration.get_project(alice_id, alice_project_id) is not None

    raw_mutation_request = _request(
        "/api/collaboration/mutations/project/create", {"Authorization": "Bearer local-token"}
    )
    raw_mutation_response = await handler.dispatch(
        _connection(raw_mutation_request), raw_mutation_request
    )
    assert raw_mutation_response is not None and raw_mutation_response.status_code == 405
    unauthenticated = _connection(_request("/", {}))
    unauthenticated_response = await handler.dispatch_webui_mutation(
        unauthenticated, "collaboration.project.create", {"name": "Unauthorized"}
    )
    assert unauthenticated_response.status_code == 401


@pytest.mark.asyncio
async def test_proxy_user_cannot_read_or_mutate_foreign_collaboration_resources(tmp_path) -> None:
    """Authenticated proxy identity, not payload IDs, scopes every collaboration resource."""
    handler = await _handler(tmp_path)
    alice = _proxy_connection("alice")
    bob = _proxy_connection("bob")
    alice_index = await _identity(handler, alice)
    bob_index = await _identity(handler, bob)
    alice_id = alice_index["user"]["id"]
    bob_id = bob_index["user"]["id"]
    assert alice_index["is_admin"] is False

    alice_project = await handler.dispatch_webui_mutation(
        alice, "collaboration.project.create", {"name": "Alice private", "user_id": bob_id}
    )
    project_id = _json(alice_project)["project"]["id"]

    foreign_details = _request(f"/api/collaboration/projects/{project_id}", bob.request.headers)
    foreign_response = await handler.dispatch(_connection(foreign_details), foreign_details)
    assert foreign_response is not None and foreign_response.status_code == 404

    for action, payload in (
        ("collaboration.project.update", {"project_id": project_id, "name": "stolen", "user_id": alice_id}),
        ("collaboration.project.member.add", {"project_id": project_id, "member_user_id": bob_id}),
        ("collaboration.project.delete", {"project_id": project_id}),
    ):
        response = await handler.dispatch_webui_mutation(bob, action, payload)
        assert response.status_code == 404, action

    bob_index_after = await _identity(handler, _proxy_connection("bob"))
    assert project_id not in {project["id"] for project in bob_index_after["projects"]}
    assert (await handler.collaboration.get_project(alice_id, project_id)).name == "Alice private"


@pytest.mark.asyncio
async def test_proxy_session_boundary_hides_foreign_legacy_and_automation_resources(tmp_path) -> None:
    """Proxy WebUI users cannot attach, fork, mention, preview, delete, or automate another owner’s session."""
    handler = await _handler(tmp_path)
    alice = _proxy_connection("alice")
    bob = _proxy_connection("bob")
    alice_id = (await _identity(handler, alice))["user"]["id"]
    bob_id = (await _identity(handler, bob))["user"]["id"]
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


async def _assign_instance(
    handler: GatewayHTTPHandler, connection: Any, instance_id: str
) -> tuple[str, str]:
    """Assign one instance to the caller's default project through the pairing flow."""
    identity = await _identity(handler, connection)
    actor_id = identity["user"]["id"]
    project_id = identity["active_project_id"]
    await handler.collaboration.record_channel_provision(
        actor_id, channel_type="weixin", instance_id=instance_id
    )
    challenge, code = await handler.collaboration.create_pairing_challenge(
        actor_id, project_id=project_id, channel_type="weixin", instance_id=instance_id
    )
    await handler.collaboration.verify_pairing_challenge(
        code, channel_type="weixin", instance_id=instance_id, sender_id=f"{instance_id}-sender"
    )
    await handler.collaboration.consume_pairing_challenge(actor_id, challenge.id)
    return actor_id, project_id


@pytest.mark.asyncio
async def test_assigned_channel_owner_can_use_channel_control_mutation(tmp_path) -> None:
    """The member an instance was handed to may reach channel control without host admin."""
    handler = await _handler(tmp_path)
    connection = _proxy_connection("ordinary-channel-owner")
    seen: list[tuple[object, object]] = []

    async def settings_dispatch(_connection, request, path):
        if path == "/api/settings/channels/configure":
            seen.append((
                getattr(request, "_nanobot_settings_system_admin", None),
                getattr(request, "_nanobot_settings_actor_user_id", None),
            ))
            return http_json_response({"status": "ok"})
        return None

    handler.settings_routes = SimpleNamespace(
        dispatch=settings_dispatch,
        is_mutation_path=lambda _path: False,
    )

    await _assign_instance(handler, connection, "claimed")
    response = await handler.dispatch_webui_mutation(
        connection,
        "settings.channel.configure",
        {"name": "weixin", "instance_id": "claimed", "values": {}},
    )

    assert response.status_code == 200
    assert len(seen) == 1
    system_admin, actor_user_id = seen[0]
    assert system_admin is True
    assert isinstance(actor_user_id, str) and actor_user_id.strip()


@pytest.mark.asyncio
async def test_ordinary_user_cannot_use_channel_control_mutation_for_unassigned_instance(
    tmp_path,
) -> None:
    """An ordinary collaboration user cannot mutate an instance nobody handed to them."""
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

    await _identity(handler, connection)
    response = await handler.dispatch_webui_mutation(
        connection,
        "settings.channel.configure",
        {"name": "weixin", "instance_id": "unclaimed", "values": {}},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proxy_subject", "expected_admin"),
    [(None, True), ("ordinary-member", False)],
)
async def test_features_read_derives_administration_only_for_the_local_owner(
    tmp_path,
    proxy_subject: str | None,
    expected_admin: bool,
) -> None:
    """The host inventory read derives identity and administration, nothing more."""
    handler = await _handler(tmp_path)
    path = "/api/settings/nanobot-features"
    connection = (
        _local_connection(path)
        if proxy_subject is None
        else _proxy_connection(proxy_subject, path)
    )
    seen: list[tuple[object, object]] = []

    async def settings_dispatch(_connection, request, _path):
        seen.append((
            getattr(request, "_nanobot_settings_system_admin", None),
            getattr(request, "_nanobot_settings_actor_user_id", None),
        ))
        return http_json_response({"features": []})

    handler.settings_routes = SimpleNamespace(
        dispatch=settings_dispatch,
        is_mutation_path=lambda _path: False,
    )

    response = await handler.dispatch(connection, connection.request)

    assert response is not None and response.status_code == 200
    assert len(seen) == 1
    system_admin, actor_user_id = seen[0]
    assert system_admin is expected_admin
    assert isinstance(actor_user_id, str) and actor_user_id.strip()


@pytest.mark.asyncio
async def test_pairing_create_requires_a_known_instance_and_consume_activates_it(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Pair Code targets an instance the runtime knows; consuming it enables that instance."""
    from nanobot.extensions.contracts import (
        ExtensionAction,
        ExtensionActionResult,
        ExtensionLifecycle,
    )

    handler = await _handler(tmp_path)
    registry = _install_registry(
        handler, _channel_instances_snapshot("weixin", "WeChat", (("default", "disabled"),))
    )
    owner = _proxy_connection("pairing-owner")
    identity = await _identity(handler, owner)
    project_id = identity["active_project_id"]

    unknown = await handler.dispatch_webui_mutation(
        owner,
        "collaboration.pairing.create",
        {"project_id": project_id, "channel_type": "weixin", "instance_id": "ghost"},
    )
    assert unknown.status_code == 409

    # A member may only pair an instance they connected themselves.
    forbidden = await handler.dispatch_webui_mutation(
        owner,
        "collaboration.pairing.create",
        {"project_id": project_id, "channel_type": "weixin", "instance_id": "default"},
    )
    assert forbidden.status_code == 404
    await handler.collaboration.record_channel_provision(
        identity["user"]["id"], channel_type="weixin", instance_id="default"
    )
    challenge_id, code = await _create_pairing(
        handler, owner, project_id=project_id, channel_type="weixin", instance_id="default"
    )

    pending = await handler.dispatch_webui_mutation(
        owner, "collaboration.pairing.consume", {"challenge_id": challenge_id}
    )
    assert pending.status_code == 409

    await handler.collaboration.verify_pairing_challenge(
        code, channel_type="weixin", instance_id="default", sender_id="default-sender"
    )
    activation = AsyncMock(
        return_value=ExtensionActionResult(
            ok=True,
            action=ExtensionAction.ENABLE,
            package_id="ext:channel_package:weixin",
            target_id="ext:channel_package:weixin/channel:default",
            lifecycle=ExtensionLifecycle.ENABLED,
            message="Channel enabled.",
        )
    )
    monkeypatch.setattr("nanobot.webui.nanobot_features_api.execute_nanobot_extension_action", activation)

    response = await handler.dispatch_webui_mutation(
        owner, "collaboration.pairing.consume", {"challenge_id": challenge_id}
    )

    assert response.status_code == 200
    assert _json(response)["channel_activation"]["ok"] is True
    assert _json(response)["pairing"]["consumed"] is True
    activation.assert_awaited_once()
    assert activation.await_args.kwargs["instance_id"] == "default"
    assert activation.await_args.kwargs["expected_revision"] == "default-revision"
    assignment = await handler.collaboration.resolve_channel_assignment("weixin", "default")
    assert assignment is not None and assignment.project_id == project_id
    registry.snapshot.assert_called()

    index = await _identity(handler, _proxy_connection("pairing-owner"))
    assert [item["instance_id"] for item in index["assignments"]] == ["default"]


@pytest.mark.asyncio
async def test_administrator_can_hand_any_known_instance_to_a_member(tmp_path) -> None:
    """The host administrator assigns an instance nobody provisioned to another member's project."""
    handler = await _handler(tmp_path)
    _install_registry(
        handler,
        _channel_instances_snapshot("weixin", "WeChat", (("legacy", "enabled"), ("spare", "disabled"))),
    )
    admin = _local_connection()
    member = _proxy_connection("member")
    member_id = (await _identity(handler, member))["user"]["id"]
    shared = await handler.dispatch_webui_mutation(
        admin, "collaboration.project.create", {"name": "Shared"}
    )
    member_project = _json(shared)["project"]["id"]
    added = await handler.dispatch_webui_mutation(
        admin,
        "collaboration.project.member.add",
        {"project_id": member_project, "member_user_id": member_id},
    )
    assert added.status_code == 200

    claimable_request = _request(
        "/api/collaboration/claimable-channels", {"Authorization": "Bearer local-token"}
    )
    claimable = await handler.dispatch(_connection(claimable_request), claimable_request)
    assert claimable is not None and claimable.status_code == 200
    assert [item["instance_id"] for item in _json(claimable)["channels"]] == ["legacy", "spare"]

    member_claimable_request = _request(
        "/api/collaboration/claimable-channels", dict(member.request.headers)
    )
    member_claimable = await handler.dispatch(_connection(member_claimable_request), member_claimable_request)
    assert member_claimable is not None and _json(member_claimable)["channels"] == []

    challenge_id, code = await _create_pairing(
        handler, admin, project_id=member_project, channel_type="weixin",
        instance_id="spare", assignee_user_id=member_id,
    )
    await handler.collaboration.verify_pairing_challenge(
        code, channel_type="weixin", instance_id="spare", sender_id="member-sender"
    )
    handler.settings = SimpleNamespace(extensions=None)
    response = await handler.dispatch_webui_mutation(
        admin, "collaboration.pairing.consume", {"challenge_id": challenge_id}
    )
    assert response.status_code == 503

    _install_registry(
        handler,
        _channel_instances_snapshot("weixin", "WeChat", (("legacy", "enabled"), ("spare", "disabled"))),
    )
    response = await handler.dispatch_webui_mutation(
        admin, "collaboration.pairing.consume", {"challenge_id": challenge_id}
    )
    assert response.status_code == 200
    assignment = await handler.collaboration.resolve_channel_assignment("weixin", "spare")
    assert assignment is not None
    assert (assignment.project_id, assignment.assignee_user_id) == (member_project, member_id)
    assert (await handler.collaboration.resolve_identity("weixin.spare", "member-sender")).id == member_id

    disabled = await handler.dispatch_webui_mutation(
        admin,
        "collaboration.assignment.update",
        {"channel_type": "weixin", "instance_id": "spare", "enabled": False},
    )
    assert disabled.status_code == 200 and _json(disabled)["assignment"]["enabled"] is False
    member_attempt = await handler.dispatch_webui_mutation(
        member,
        "collaboration.assignment.delete",
        {"channel_type": "weixin", "instance_id": "spare"},
    )
    assert member_attempt.status_code == 404
    removed = await handler.dispatch_webui_mutation(
        admin,
        "collaboration.assignment.delete",
        {"channel_type": "weixin", "instance_id": "spare"},
    )
    assert removed.status_code == 200 and _json(removed)["deleted"] is True


def _connect_settings_dispatch(sessions: dict[str, str]) -> Any:
    """Stand in for the channel package: mint an instance, then report success on poll."""
    async def dispatch(_connection: Any, request: Any, path: str) -> Any:
        match = re.fullmatch(
            r"/api/settings/channels/([^/]+)/connect/(start|poll|cancel)", path
        )
        if match is None:
            return None
        payload = getattr(request, "_nanobot_webui_mutation_payload", {})
        if match.group(2) == "start":
            instance_id = str(payload["instance_id"])
            session_id = f"session-{instance_id}"
            sessions[session_id] = instance_id
            return http_json_response({
                "session_id": session_id,
                "instance_id": instance_id,
                "status": "pending",
                "qr_url": "https://example.invalid/qr",
            })
        session_id = str(payload["session_id"])
        return http_json_response({
            "session_id": session_id,
            "instance_id": sessions[session_id],
            "status": str(payload.get("outcome", "succeeded")),
            "pairing_required": True,
        })

    return dispatch


async def _provision_channel_instance(
    handler: GatewayHTTPHandler,
    connection: Any,
    sessions: dict[str, str],
    *,
    instance_id: str,
    outcome: str = "succeeded",
) -> None:
    """Run one member-owned create-mode connect session through the mutation boundary."""
    await _identity(handler, connection)
    start = await handler.dispatch_webui_mutation(
        connection,
        "settings.channel.connect.start",
        {"channel": "weixin", "mode": "create", "instance_id": instance_id},
    )
    assert start.status_code == 200
    session_id = _json(start)["session_id"]
    poll = await handler.dispatch_webui_mutation(
        connection,
        "settings.channel.connect.poll",
        {"channel": "weixin", "session_id": session_id, "outcome": outcome},
    )
    assert poll.status_code == 200
    assert sessions[session_id] == instance_id


@pytest.mark.asyncio
async def test_self_service_connect_makes_only_the_provisioner_instance_claimable(
    tmp_path,
) -> None:
    """A member pairs the instance they connected, and never a colleague's instance."""
    handler = await _handler(tmp_path)
    _install_registry(
        handler,
        _channel_instances_snapshot(
            "weixin", "WeChat", (("wechat-aaa111", "enabled"), ("wechat-bbb222", "disabled")),
        ),
    )
    sessions: dict[str, str] = {}
    handler.settings_routes = SimpleNamespace(
        dispatch=_connect_settings_dispatch(sessions),
        is_mutation_path=lambda _path: False,
    )
    first = _proxy_connection("connect-member-one")
    second = _proxy_connection("connect-member-two")
    await _provision_channel_instance(handler, first, sessions, instance_id="wechat-aaa111")
    await _provision_channel_instance(handler, second, sessions, instance_id="wechat-bbb222")

    listings: dict[str, list[dict[str, Any]]] = {}
    for label, connection in (("first", first), ("second", second)):
        request = _request(
            "/api/collaboration/claimable-channels", dict(connection.request.headers)
        )
        response = await handler.dispatch(_connection(request), request)
        assert response is not None and response.status_code == 200
        listings[label] = _json(response)["channels"]

    assert listings["first"] == [{
        "channel_type": "weixin",
        "channel_display_name": "WeChat",
        "instance_id": "wechat-aaa111",
        "display_name": "wechat-aaa111",
        "status": "running",
    }]
    assert [item["instance_id"] for item in listings["second"]] == ["wechat-bbb222"]
    assert listings["second"][0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_claimable_channels_omit_unattributed_abandoned_and_assigned_instances(
    tmp_path,
) -> None:
    """Only a completed, still-present, still-unassigned, self-provisioned instance is offered."""
    handler = await _handler(tmp_path)
    registry = _install_registry(
        handler,
        _channel_instances_snapshot(
            "weixin", "WeChat",
            (("legacy", "enabled"), ("wechat-aaa111", "enabled"), ("wechat-ccc333", "disabled")),
        ),
    )
    sessions: dict[str, str] = {}
    handler.settings_routes = SimpleNamespace(
        dispatch=_connect_settings_dispatch(sessions),
        is_mutation_path=lambda _path: False,
    )
    connection = _proxy_connection("connect-member-one")
    await _provision_channel_instance(handler, connection, sessions, instance_id="wechat-aaa111")
    await _provision_channel_instance(
        handler, connection, sessions, instance_id="wechat-ccc333", outcome="failed"
    )

    request = _request(
        "/api/collaboration/claimable-channels", dict(connection.request.headers)
    )
    before = await handler.dispatch(_connection(request), request)
    assert before is not None and before.status_code == 200
    assert [item["instance_id"] for item in _json(before)["channels"]] == ["wechat-aaa111"]

    registry.snapshot.return_value = _channel_instances_snapshot(
        "weixin", "WeChat", (("legacy", "enabled"),)
    )
    withdrawn = await handler.dispatch(_connection(request), request)
    assert withdrawn is not None and _json(withdrawn)["channels"] == []

    registry.snapshot.return_value = _channel_instances_snapshot(
        "weixin", "WeChat", (("legacy", "enabled"), ("wechat-aaa111", "enabled"))
    )
    await _assign_instance(handler, connection, "wechat-aaa111")
    after = await handler.dispatch(_connection(request), request)

    assert after is not None and after.status_code == 200
    assert _json(after)["channels"] == []


@pytest.mark.asyncio
async def test_claimable_channels_do_not_reopen_the_host_inventory_read(tmp_path) -> None:
    """The member listing is additive: the features read still derives no administration."""
    handler = await _handler(tmp_path)
    _install_registry(
        handler, _channel_instances_snapshot("weixin", "WeChat", (("wechat-aaa111", "enabled"),))
    )
    sessions: dict[str, str] = {}
    connect_dispatch = _connect_settings_dispatch(sessions)
    seen: list[tuple[object, object]] = []

    async def settings_dispatch(connection: Any, request: Any, path: str) -> Any:
        if path == "/api/settings/nanobot-features":
            seen.append((
                getattr(request, "_nanobot_settings_system_admin", None),
                getattr(request, "_nanobot_settings_host_admin", None),
            ))
            return http_json_response({"features": [], "restricted": True})
        return await connect_dispatch(connection, request, path)

    handler.settings_routes = SimpleNamespace(
        dispatch=settings_dispatch,
        is_mutation_path=lambda _path: False,
    )
    connection = _proxy_connection("connect-member-one")
    await _provision_channel_instance(handler, connection, sessions, instance_id="wechat-aaa111")

    features_request = _request(
        "/api/settings/nanobot-features", dict(connection.request.headers)
    )
    features = await handler.dispatch(_connection(features_request), features_request)
    claimable_request = _request(
        "/api/collaboration/claimable-channels", dict(connection.request.headers)
    )
    claimable = await handler.dispatch(_connection(claimable_request), claimable_request)

    assert features is not None and _json(features)["features"] == []
    assert seen == [(False, False)]
    assert claimable is not None and claimable.status_code == 200
    assert [item["instance_id"] for item in _json(claimable)["channels"]] == ["wechat-aaa111"]


@pytest.mark.asyncio
async def test_another_member_cannot_take_over_a_connect_session_provenance(tmp_path) -> None:
    """Provenance follows the member who opened the session, not whoever polls it."""
    handler = await _handler(tmp_path)
    _install_registry(
        handler, _channel_instances_snapshot("weixin", "WeChat", (("wechat-aaa111", "enabled"),))
    )
    sessions: dict[str, str] = {}
    handler.settings_routes = SimpleNamespace(
        dispatch=_connect_settings_dispatch(sessions),
        is_mutation_path=lambda _path: False,
    )
    owner = _proxy_connection("connect-member-one")
    intruder = _proxy_connection("connect-member-two")
    for connection in (owner, intruder):
        await _identity(handler, connection)

    start = await handler.dispatch_webui_mutation(
        owner,
        "settings.channel.connect.start",
        {"channel": "weixin", "mode": "create", "instance_id": "wechat-aaa111"},
    )
    assert start.status_code == 200
    stolen = await handler.dispatch_webui_mutation(
        intruder,
        "settings.channel.connect.poll",
        {"channel": "weixin", "session_id": _json(start)["session_id"]},
    )

    assert stolen.status_code == 200
    for connection in (owner, intruder):
        request = _request(
            "/api/collaboration/claimable-channels", dict(connection.request.headers)
        )
        response = await handler.dispatch(_connection(request), request)
        assert response is not None and response.status_code == 200
        assert _json(response)["channels"] == []
