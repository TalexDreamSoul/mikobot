"""Member previews reach their own media and what their session recorded, not the shared media root."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from nanobot.channels.websocket.runtime import TrustedProxyAuthConfig, WebSocketConfig
from nanobot.collaboration import AsyncLocalCollaborationRepository, CollaborationStore
from nanobot.collaboration.models import (
    COLLABORATION_PROJECT_METADATA_KEY,
    COLLABORATION_USER_METADATA_KEY,
)
from nanobot.runtime_admission import RuntimeAdmission
from nanobot.security.workspace_access import default_workspace_scope
from nanobot.session.manager import SessionManager
from nanobot.webui.file_preview import (
    PreviewMedia,
    WebUIFilePreviewError,
    file_preview_availability_payload,
    file_preview_payload,
    file_reference_payload,
)
from nanobot.webui.oidc_auth import OidcAuthenticator
from nanobot.webui.ws_http import GatewayHTTPHandler

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
)


class Layout:
    def __init__(self, root: Path) -> None:
        self.project = root / "project"
        self.shared = root / "media"
        self.private = root / "users" / "alice" / "media" / "p1"
        self.other_private = root / "users" / "bob" / "media" / "p1"
        for directory in (
            self.project,
            self.shared / "websocket",
            self.shared / "generated" / "2026-10-24",
            self.shared / "generated" / "2026-10-01",
            self.private,
            self.other_private,
        ):
            directory.mkdir(parents=True)
        self.notes = self.project / "notes.txt"
        self.notes.write_text("project notes", encoding="utf-8")
        self.other_upload = self.shared / "websocket" / "9f2c41d7-other.txt"
        self.other_upload.write_text("someone else's upload", encoding="utf-8")
        self.artifact = self.shared / "generated" / "2026-10-24" / "img_0123456789ab.png"
        self.artifact.write_bytes(PNG)
        self.older = self.shared / "generated" / "2026-10-01" / "older.txt"
        self.older.write_text("older generated file", encoding="utf-8")
        self.own_upload = self.private / "own-upload.txt"
        self.own_upload.write_text("alice upload", encoding="utf-8")
        self.other_member_upload = self.other_private / "bob-upload.txt"
        self.other_member_upload.write_text("bob upload", encoding="utf-8")
        self.outside = root / "outside.txt"
        self.outside.write_text("outside everything", encoding="utf-8")
        self.scope = default_workspace_scope(self.project, restrict_to_workspace=True)

    def member(self, messages: list[dict[str, Any]] | None = None) -> PreviewMedia:
        return PreviewMedia(roots=(self.private,), session_messages=lambda: messages or [])


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    built = Layout(tmp_path)
    monkeypatch.setattr("nanobot.webui.file_preview.get_media_dir", lambda: built.shared)
    return built


def generated_result(path: Path, prompt: str = "draw") -> dict[str, Any]:
    return {
        "role": "tool",
        "name": "generate_image",
        "tool_call_id": "call-1",
        "content": json.dumps({"artifacts": [{"id": "img_0123456789ab", "path": str(path), "prompt": prompt}]}),
    }


def denied(path: Path, scope: Any, media: PreviewMedia | None) -> int:
    statuses = set()
    for read in (file_preview_payload, file_preview_availability_payload, file_reference_payload):
        with pytest.raises(WebUIFilePreviewError) as error:
            read(str(path), scope=scope, media=media)
        statuses.add(error.value.status)
    assert len(statuses) == 1
    return statuses.pop()


def test_member_cannot_preview_unreferenced_shared_media(layout: Layout) -> None:
    assert denied(layout.other_upload, layout.scope, layout.member()) == 403
    assert denied(layout.artifact, layout.scope, layout.member()) == 403


def test_member_keeps_project_and_own_private_media(layout: Layout) -> None:
    media = layout.member()
    assert file_preview_payload(str(layout.notes), scope=layout.scope, media=media)["content"] == "project notes"
    assert file_preview_payload(str(layout.own_upload), scope=layout.scope, media=media)["content"] == "alice upload"
    assert file_preview_availability_payload(str(layout.own_upload), scope=layout.scope, media=media) == {
        "available": True,
    }


def test_member_cannot_preview_another_members_private_media(layout: Layout) -> None:
    assert denied(layout.other_member_upload, layout.scope, layout.member()) == 403


def test_member_can_preview_generated_artifact_of_own_session(layout: Layout) -> None:
    media = layout.member([{"role": "user", "content": "draw a cat"}, generated_result(layout.artifact)])

    payload = file_preview_payload(str(layout.artifact), scope=layout.scope, media=media)

    assert payload["kind"] == "image"
    assert payload["mime_type"] == "image/png"
    assert file_reference_payload(str(layout.artifact), scope=layout.scope, media=media)["path"] == str(
        layout.artifact
    )


def test_member_can_preview_older_shared_file_recorded_in_session_media(layout: Layout) -> None:
    media = layout.member([
        {"role": "user", "content": "see attached", "media": [str(layout.older)]},
        {"role": "assistant", "content": "done", "media": [str(layout.artifact)]},
    ])

    assert file_preview_payload(str(layout.older), scope=layout.scope, media=media)["content"] == (
        "older generated file"
    )
    assert file_preview_availability_payload(str(layout.artifact), scope=layout.scope, media=media) == {
        "available": True,
    }
    assert denied(layout.other_upload, layout.scope, media) == 403


def test_recorded_reference_covers_only_that_file(layout: Layout) -> None:
    media = layout.member([generated_result(layout.artifact)])

    assert denied(layout.older, layout.scope, media) == 403
    assert denied(layout.artifact.with_suffix(".json"), layout.scope, media) in {403, 404}
    assert denied(layout.shared / "generated", layout.scope, media) in {403, 404}


@pytest.mark.parametrize(
    "records",
    [
        pytest.param(lambda p: [{"role": "user", "content": f"please show {p}"}], id="user-text"),
        pytest.param(lambda p: [{"role": "assistant", "content": f"the file is {p}"}], id="assistant-text"),
        pytest.param(
            lambda p: [{"role": "tool", "name": "exec", "content": json.dumps({"artifacts": [{"path": str(p)}]})}],
            id="other-tool-result",
        ),
        pytest.param(lambda p: [generated_result(Path("/nowhere/x.png"), prompt=str(p))], id="artifact-prompt"),
        pytest.param(
            lambda p: [{"role": "tool", "name": "generate_image", "content": f"saved {p}"}],
            id="unparsable-tool-result",
        ),
        pytest.param(lambda p: [{"role": "user", "content": "x", "media": [p.name]}], id="relative-media"),
    ],
)
def test_free_text_and_foreign_records_never_grant_shared_media(layout: Layout, records: Any) -> None:
    media = layout.member(records(layout.other_upload))

    assert denied(layout.other_upload, layout.scope, media) == 403


def test_recorded_symlink_does_not_reach_its_target(layout: Layout) -> None:
    link = layout.shared / "generated" / "2026-10-24" / "img_link.png"
    link.symlink_to(layout.other_upload)
    media = layout.member([generated_result(link), {"role": "user", "content": "x", "media": [str(link)]}])

    assert denied(link, layout.scope, media) == 403
    assert denied(layout.other_upload, layout.scope, media) == 403


def test_recorded_media_outside_shared_root_is_ignored(layout: Layout) -> None:
    media = layout.member([
        {"role": "user", "content": "x", "media": [str(layout.outside)]},
        generated_result(layout.outside),
    ])

    assert denied(layout.outside, layout.scope, media) == 403


def test_recorded_file_that_was_deleted_is_not_found(layout: Layout) -> None:
    media = layout.member([generated_result(layout.artifact)])
    layout.artifact.unlink()

    assert denied(layout.artifact, layout.scope, media) == 404


def test_session_records_are_read_only_for_shared_media_paths(layout: Layout) -> None:
    calls: list[str] = []

    def messages() -> list[dict[str, Any]]:
        calls.append("read")
        return [generated_result(layout.artifact)]

    media = PreviewMedia(roots=(layout.private,), session_messages=messages)

    for allowed in (layout.notes, layout.own_upload):
        file_preview_availability_payload(str(allowed), scope=layout.scope, media=media)
    assert denied(layout.outside, layout.scope, media) == 403
    assert denied(layout.project / "missing.txt", layout.scope, media) == 404
    assert calls == []

    file_preview_availability_payload(str(layout.artifact), scope=layout.scope, media=media)
    assert calls == ["read"]


def test_member_without_stored_session_gets_no_shared_media(layout: Layout) -> None:
    media = PreviewMedia(roots=(layout.private,), session_messages=lambda: None)

    assert denied(layout.artifact, layout.scope, media) == 403
    assert file_preview_payload(str(layout.own_upload), scope=layout.scope, media=media)["content"] == "alice upload"


def test_host_owner_keeps_the_whole_shared_media_root(layout: Layout) -> None:
    for path in (layout.other_upload, layout.artifact, layout.older):
        assert file_preview_availability_payload(str(path), scope=layout.scope) == {"available": True}
        assert file_preview_availability_payload(str(path), scope=layout.scope, media=None) == {"available": True}
    assert file_preview_payload(str(layout.other_upload), scope=layout.scope)["content"] == (
        "someone else's upload"
    )
    assert denied(layout.other_member_upload, layout.scope, None) == 403
    assert denied(layout.outside, layout.scope, None) == 403


def test_unrestricted_scope_ignores_member_media(layout: Layout) -> None:
    full = default_workspace_scope(layout.project, restrict_to_workspace=False)

    assert file_preview_payload(str(layout.outside), scope=full)["content"] == "outside everything"
    assert file_preview_payload(str(layout.outside), scope=full, media=PreviewMedia())["content"] == (
        "outside everything"
    )


PROXY_HEADERS = {"X-Proxy-Authenticated": "yes", "X-Proxy-Subject": "alice"}
HOST_HEADERS = {"Authorization": "Bearer local-token"}


async def _handler(tmp_path: Path, scope: Any) -> GatewayHTTPHandler:
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
    handler.collaboration = AsyncLocalCollaborationRepository(CollaborationStore(tmp_path / "collaboration"))
    handler._collaboration_init_lock = asyncio.Lock()
    handler._collaboration_initialized = False
    handler._collaboration_closed = False
    handler._member_connect_sessions = {}
    handler.bus = SimpleNamespace(admission=RuntimeAdmission())
    handler.session_manager = SessionManager(tmp_path / "sessions")
    handler.tokens = SimpleNamespace(
        check_api_token=lambda request: request.headers.get("Authorization") == "Bearer local-token"
    )
    handler.oidc = OidcAuthenticator(handler.config.oidc_auth)
    handler._oidc_connection_revoker = None
    handler.workspaces = SimpleNamespace(
        default_scope=lambda: scope,
        scope_for_indexed_metadata=lambda *_args, **_kwargs: scope,
        scope_for_session_key=lambda _key: scope,
        existing_scope_for_session_key=lambda _key: scope,
    )
    handler.settings_routes = SimpleNamespace(
        dispatch=AsyncMock(return_value=None), is_mutation_path=lambda _path: False,
    )
    handler.settings = SimpleNamespace(extensions=None)
    handler.skills_workspace_path = tmp_path / "workspace"
    handler.static_dist_path = None
    handler.cron_service = None
    handler.local_trigger_store = None
    handler.cron_pending_job_ids = None
    handler._webui_connections = set()
    handler.endpoint = SimpleNamespace(webui_connections=handler._webui_connections)
    handler.local_trigger_pending_ids = None
    handler.recovery_action = None
    handler.media = MagicMock()
    handler._log = MagicMock()
    handler._collaboration_available = lambda: {"skills": [], "mcp_servers": []}
    await handler.initialize_collaboration()
    return handler


async def _get(handler: GatewayHTTPHandler, headers: dict[str, str], path: str) -> tuple[int, Any]:
    request = WsRequest(path, Headers(headers))
    connection = SimpleNamespace(request=request, remote_address=("127.0.0.1", 8765))
    response = await handler.dispatch(connection, request)
    assert response is not None
    try:
        return response.status_code, json.loads(response.body)
    except ValueError:
        return response.status_code, response.body.decode()


async def _preview(
    handler: GatewayHTTPHandler,
    headers: dict[str, str],
    key: str,
    path: Path,
    mode: str = "",
) -> tuple[int, Any]:
    suffix = f"&{mode}=1" if mode else ""
    return await _get(
        handler, headers, f"/api/sessions/{quote(key, safe='')}/file-preview?path={quote(str(path), safe='')}{suffix}",
    )


async def test_http_preview_narrows_members_and_keeps_the_host(
    tmp_path: Path, layout: Layout, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nanobot.security.private_media.get_runtime_subdir", lambda name: tmp_path / "runtime" / name,
    )
    handler = await _handler(tmp_path, layout.scope)
    alice = await _get(handler, PROXY_HEADERS, "/api/collaboration")
    alice_id, project_id = alice[1]["user"]["id"], alice[1]["active_project_id"]
    private = tmp_path / "runtime" / "users" / alice_id / "media" / project_id
    other_private = tmp_path / "runtime" / "users" / "someone-else" / "media" / project_id
    private.mkdir(parents=True)
    other_private.mkdir(parents=True)
    (private / "upload.txt").write_text("alice upload", encoding="utf-8")
    (other_private / "upload.txt").write_text("someone else", encoding="utf-8")

    chat = handler.session_manager.get_or_create("websocket:alice-chat")
    chat.metadata.update({
        "webui": True,
        COLLABORATION_USER_METADATA_KEY: alice_id,
        COLLABORATION_PROJECT_METADATA_KEY: project_id,
    })
    chat.add_message("user", "older work", media=[str(layout.older)])
    chat.add_message("assistant", "drawing", tool_calls=[
        {"id": "call-1", "type": "function", "function": {"name": "generate_image", "arguments": "{}"}},
    ])
    chat.messages.append(generated_result(layout.artifact))
    chat.add_message("user", f"also show {layout.other_upload}")
    handler.session_manager.save(chat)
    host_chat = handler.session_manager.get_or_create("websocket:host-chat")
    host_chat.metadata["webui"] = True
    host_chat.add_message("user", "hello")
    handler.session_manager.save(host_chat)

    async def member(path: Path, mode: str = "") -> tuple[int, Any]:
        return await _preview(handler, PROXY_HEADERS, "websocket:alice-chat", path, mode)

    assert (await member(layout.notes))[0] == 200
    assert (await member(private / "upload.txt"))[1]["content"] == "alice upload"
    assert (await member(layout.artifact))[1]["kind"] == "image"
    assert (await member(layout.older))[1]["content"] == "older generated file"
    assert (await member(layout.other_upload))[0] == 403
    assert await member(layout.other_upload, "probe") == (200, {"available": False})
    assert (await member(layout.other_upload, "metadata"))[0] == 403
    assert (await member(other_private / "upload.txt"))[0] == 403
    assert (await member(layout.outside))[0] == 403

    async def host(path: Path, mode: str = "") -> tuple[int, Any]:
        return await _preview(handler, HOST_HEADERS, "websocket:host-chat", path, mode)

    assert (await host(layout.other_upload))[1]["content"] == "someone else's upload"
    assert (await host(layout.artifact))[1]["kind"] == "image"
    assert await host(layout.older, "probe") == (200, {"available": True})
    assert (await host(layout.outside))[0] == 403
    assert (await host(private / "upload.txt"))[0] == 403
