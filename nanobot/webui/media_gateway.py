"""Media gateway services shared by WebUI HTTP routes and WebSocket frames."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.collaboration.models import (
    COLLABORATION_PROJECT_METADATA_KEY,
    COLLABORATION_USER_METADATA_KEY,
)
from nanobot.config.paths import get_media_dir
from nanobot.security.private_media import user_private_media_root
from nanobot.security.workspace_access import WORKSPACE_SCOPE_METADATA_KEY, WorkspaceScope
from nanobot.security.workspace_policy import is_path_within
from nanobot.session.manager import SessionManager
from nanobot.webui.media_api import (
    media_attachment_kind,
    serve_signed_media,
    sign_local_attachment,
    sign_media_path,
    sign_or_stage_media_path,
    signed_media_attachments,
)
from nanobot.webui.transcript import rewrite_local_markdown_images


def _default_media_dir(channel: str | None) -> Path:
    return get_media_dir(channel)


class WebUIMediaGateway:
    """Own media URL signing and WebUI markdown/media augmentation."""

    def __init__(
        self,
        *,
        workspace_path: Path,
        logger: Any,
        media_dir: Callable[[str | None], Path] | None = None,
        secret: bytes | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.logger = logger
        self._media_dir: Callable[[str | None], Path] = media_dir or _default_media_dir
        self.secret = secret or secrets.token_bytes(32)
        self._sessions = session_manager

    def _session_metadata(self, session_key: str) -> dict[str, Any] | None:
        assert self._sessions is not None
        metadata = self._sessions.get_cached_metadata(session_key)
        if metadata is not None:
            return metadata
        stored = self._sessions.read_session_metadata(session_key)
        value = stored.get("metadata") if stored is not None else None
        return cast(dict[str, Any], value) if isinstance(value, dict) else None

    def _session_policy(self, session_key: str | None) -> tuple[bytes, Path, Path] | None:
        """Resolve only server-owned media roots; incomplete member state fails closed."""
        if session_key is None or self._sessions is None:
            return None
        metadata = self._session_metadata(session_key)
        if metadata is None:
            return None
        owner = metadata.get(COLLABORATION_USER_METADATA_KEY)
        project_id = metadata.get(COLLABORATION_PROJECT_METADATA_KEY)
        if owner is None and project_id is None:
            return None
        if (
            not isinstance(owner, str) or not owner
            or not isinstance(project_id, str) or not project_id
            or Path(project_id).name != project_id or project_id in {".", ".."}
            or "/" in project_id or "\\" in project_id
        ):
            raise ValueError("member media ownership is unavailable")
        workspace = metadata.get(WORKSPACE_SCOPE_METADATA_KEY)
        if not isinstance(workspace, dict):
            raise ValueError("member media workspace is unavailable")
        project_path = cast(dict[str, object], workspace).get("project_path")
        if not isinstance(project_path, str):
            raise ValueError("member media workspace is unavailable")
        project_root = Path(project_path).resolve()
        media_root = user_private_media_root(owner, project_id=project_id)
        audience = f"webui-media\0{session_key}\0{owner}\0{project_id}".encode("utf-8")
        secret = hmac.new(self.secret, audience, hashlib.sha256).digest()
        return secret, media_root, project_root

    def claim_uploaded_media(self, paths: list[str], *, session_key: str) -> list[str]:
        """Move accepted uploads of a member session into its private media root."""
        policy = self._session_policy(session_key)
        if policy is None or not paths:
            return paths
        claimed: list[str] = []
        try:
            managed_root = self._media_dir("websocket").resolve()
            policy[1].mkdir(mode=0o700, parents=True, exist_ok=True)
            for raw_path in paths:
                source = Path(raw_path).resolve(strict=True)
                if not is_path_within(source, managed_root) or not source.is_file():
                    raise ValueError("uploaded attachment is outside managed media")
                destination = policy[1] / source.name
                shutil.move(source, destination)
                destination.chmod(0o600)
                claimed.append(str(destination))
        except OSError as exc:
            raise ValueError("uploaded attachment is unavailable") from exc
        return claimed

    def serve_signed_media(
        self,
        sig: str,
        payload: str,
        *,
        request: WsRequest | None = None,
        session_key: str | None = None,
        scope_for_session: Callable[[str], WorkspaceScope | None] | None = None,
    ) -> Response:
        policy = self._session_policy(session_key)
        response = serve_signed_media(
            sig,
            payload,
            secret=policy[0] if policy is not None else self.secret,
            request=request,
            media_dir=(lambda _channel: policy[1]) if policy is not None else self._media_dir,
            scope_for_session=scope_for_session,
        )
        if policy is not None:
            if "Cache-Control" in response.headers:
                del response.headers["Cache-Control"]
            response.headers["Cache-Control"] = "private, no-store"
        return response

    def sign_or_stage_media_path(
        self, path: Path, *, session_key: str | None = None
    ) -> dict[str, str] | None:
        policy = self._session_policy(session_key)
        if policy is not None and not (
            is_path_within(path, policy[1]) or is_path_within(path, policy[2])
        ):
            return None
        if policy is not None:
            policy[1].mkdir(mode=0o700, parents=True, exist_ok=True)
        result = sign_or_stage_media_path(
            path,
            secret=policy[0] if policy is not None else self.secret,
            media_dir=(lambda _channel: policy[1]) if policy is not None else self._media_dir,
            logger=self.logger,
        )
        if result is not None and policy is not None:
            result["url"] += "?" + urlencode({"session_key": session_key})
        return result

    def rewrite_local_markdown_images(
        self,
        text: str,
        *,
        workspace_path: Path | None = None,
        session_key: str | None = None,
    ) -> str:
        policy = self._session_policy(session_key)
        return rewrite_local_markdown_images(
            text,
            workspace_path=policy[2] if policy is not None else (workspace_path or self.workspace_path),
            sign_path=lambda path: self.sign_or_stage_media_path(path, session_key=session_key),
        )

    def augment_transcript_media(
        self, paths: list[str], *, session_key: str | None = None
    ) -> list[dict[str, Any]]:
        return signed_media_attachments(
            paths,
            sign_path=lambda path: self.sign_or_stage_media_path(path, session_key=session_key),
        )

    def local_attachment_url(self, path: str, *, session_key: str) -> str:
        policy = self._session_policy(session_key)
        url = sign_local_attachment(
            path,
            session_key=session_key,
            secret=policy[0] if policy is not None else self.secret,
        )
        if policy is not None:
            url += "?" + urlencode({"session_key": session_key})
        return url

    def augment_transcript_user_media(
        self, paths: list[str], local_paths: list[str], *, session_key: str,
    ) -> list[dict[str, Any]]:
        local = set(local_paths)
        policy = self._session_policy(session_key)
        media: list[dict[str, Any]] = []
        for raw in paths:
            path = Path(raw)
            if raw in local:
                url = self.local_attachment_url(raw, session_key=session_key) if path.is_file() else ""
            elif policy is not None:
                signed = self.sign_or_stage_media_path(path, session_key=session_key)
                url = signed["url"] if signed is not None else ""
            else:
                url = sign_media_path(path, secret=self.secret, media_dir=self._media_dir) or ""
            media.append({"kind": media_attachment_kind(path.name), "name": path.name, "url": url})
        return media
