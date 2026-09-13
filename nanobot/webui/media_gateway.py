"""Media gateway services shared by WebUI HTTP routes and WebSocket frames."""

from __future__ import annotations

import hashlib
import hmac
import secrets
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
from nanobot.security.workspace_access import WORKSPACE_SCOPE_METADATA_KEY
from nanobot.security.workspace_policy import is_path_within
from nanobot.session.manager import SessionManager
from nanobot.webui.attachment_ingress import (
    AttachmentIngressResult,
    store_inbound_attachments,
)
from nanobot.webui.ingress_policy import AttachmentIngressLimits
from nanobot.webui.media_api import (
    serve_signed_media,
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
        attachment_limits: AttachmentIngressLimits | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.logger = logger
        self._media_dir: Callable[[str | None], Path] = media_dir or _default_media_dir
        self.secret = secret or secrets.token_bytes(32)
        self.attachment_limits = attachment_limits or AttachmentIngressLimits()
        self._sessions = session_manager

    def _session_policy(self, session_key: str | None) -> tuple[bytes, Path, Path] | None:
        """Resolve only server-owned media roots; incomplete member state fails closed."""
        if session_key is None or self._sessions is None:
            return None
        session = self._sessions.peek(session_key)
        if session is None:
            return None
        owner = session.metadata.get(COLLABORATION_USER_METADATA_KEY)
        project_id = session.metadata.get(COLLABORATION_PROJECT_METADATA_KEY)
        if owner is None and project_id is None:
            return None
        if (
            not isinstance(owner, str) or not owner
            or not isinstance(project_id, str) or not project_id
            or Path(project_id).name != project_id or project_id in {".", ".."}
            or "/" in project_id or "\\" in project_id
        ):
            raise ValueError("member media ownership is unavailable")
        workspace = session.metadata.get(WORKSPACE_SCOPE_METADATA_KEY)
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

    def store_inbound_attachments(
        self, media: list[Any], *, session_key: str | None = None
    ) -> AttachmentIngressResult:
        """Validate and persist attachments from an inbound WebUI message."""
        policy = self._session_policy(session_key)
        return store_inbound_attachments(
            media,
            media_dir=policy[1] if policy is not None else self._media_dir("websocket"),
            logger=self.logger,
            limits=self.attachment_limits,
        )

    def serve_signed_media(
        self,
        sig: str,
        payload: str,
        *,
        request: WsRequest | None = None,
        session_key: str | None = None,
    ) -> Response:
        policy = self._session_policy(session_key)
        response = serve_signed_media(
            sig,
            payload,
            secret=policy[0] if policy is not None else self.secret,
            request=request,
            media_dir=(lambda _channel: policy[1]) if policy is not None else self._media_dir,
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

    def augment_transcript_user_media(
        self, paths: list[str], *, session_key: str | None = None
    ) -> list[dict[str, Any]]:
        return self.augment_transcript_media(paths, session_key=session_key)
