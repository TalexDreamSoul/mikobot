"""Bounded, request-scoped access to Feishu/Lark conversation history.

This module deliberately has no background work or cache.  A caller supplies the
credentials for the Feishu instance which received the current request, then one
bounded API request sequence is made for that request's chat or thread.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

import httpx

_MAX_LIMIT = 50
_MAX_PAGE_SIZE = 50
_MAX_PAGES = 5
_REQUEST_TIMEOUT_SECONDS = 10.0
_TOTAL_TIMEOUT_SECONDS = 20.0
_MESSAGE_TEXT_LIMIT = 1_200
_RESULT_CHAR_LIMIT = 12_000


class FeishuHistoryError(RuntimeError):
    """A user-safe failure while retrieving conversation history."""


@dataclass(frozen=True)
class FeishuHistoryCredentials:
    """Credentials and API domain for one exact runtime Feishu instance."""

    app_id: str
    app_secret: str
    domain: Literal["feishu", "lark"]


@dataclass(frozen=True)
class FeishuConversationScope:
    """The immutable conversation boundary from the active request."""

    chat_id: str
    thread_id: str | None = None
    root_id: str | None = None


def parse_history_time(value: str | None, *, parameter: str) -> int | None:
    """Parse an optional RFC 3339 time into the API's Unix-seconds value."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeishuHistoryError(
            f"{parameter} must be an ISO 8601 timestamp such as 2026-08-31T12:00:00Z"
        ) from exc
    if parsed.tzinfo is None:
        raise FeishuHistoryError(f"{parameter} must include a timezone, for example a trailing Z")
    return math.floor(parsed.astimezone(UTC).timestamp())


def _api_base(domain: str) -> str:
    return "https://open.larksuite.com" if domain == "lark" else "https://open.feishu.cn"


def _as_object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _bounded_text(value: object, limit: int = _MESSAGE_TEXT_LIMIT) -> str:
    text = re.sub(r"[ \t]+", " ", str(value or ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _content_text(value: object, message_type: str) -> str:
    """Turn an API message body into compact, model-safe plain text."""
    if not isinstance(value, str):
        return f"[{message_type}]"
    try:
        content = _as_object(json.loads(value))
    except json.JSONDecodeError:
        return _bounded_text(value) or f"[{message_type}]"

    content_text = content.get("text")
    if isinstance(content_text, str):
        return _bounded_text(content_text) or f"[{message_type}]"

    fragments: list[str] = []

    def collect(node: object) -> None:
        object_node = _as_object(node)
        if object_node:
            for key in ("text", "content", "title"):
                text = object_node.get(key)
                if isinstance(text, str) and text.strip():
                    fragments.append(text)
            for child in object_node.values():
                collect(child)
            return
        for child in _as_list(node):
            collect(child)

    collect(content)
    text = _bounded_text("\n".join(fragments))
    return text or f"[{message_type}]"


def _timestamp(value: object) -> str | None:
    """Render Feishu millisecond/second epoch fields as RFC 3339 where possible."""
    try:
        raw = int(str(value))
    except (TypeError, ValueError):
        return None
    seconds = raw / 1000 if raw > 10_000_000_000 else raw
    try:
        return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_message(item: Mapping[str, object]) -> dict[str, object] | None:
    message_id = _bounded_text(item.get("message_id"), 256)
    if not message_id:
        return None
    message_type = _bounded_text(item.get("msg_type"), 64) or "unknown"
    sender = _as_object(item.get("sender"))
    sender_id = _bounded_text(sender.get("id") or sender.get("sender_id") or item.get("sender_id"), 256)
    deleted = bool(item.get("deleted") or item.get("is_deleted"))
    body = _as_object(item.get("body"))
    message: dict[str, object] = {
        "message_id": message_id,
        "sender": sender_id or "unknown",
        "sender_type": _bounded_text(sender.get("sender_type"), 64) or None,
        "time": _timestamp(item.get("create_time")),
        "message_type": message_type,
        "root_id": _bounded_text(item.get("root_id"), 256) or None,
        "thread_id": _bounded_text(item.get("thread_id"), 256) or None,
        "deleted": deleted,
        "text": "[deleted message]" if deleted else _content_text(body.get("content"), message_type),
    }
    return {key: value for key, value in message.items() if value is not None}


def _matches_root(item: Mapping[str, object], root_id: str | None) -> bool:
    if not root_id:
        return True
    return root_id in {
        str(item.get("message_id") or ""),
        str(item.get("root_id") or ""),
    }


def _safe_api_error(response: httpx.Response) -> str:
    try:
        payload = _as_object(response.json())
    except (json.JSONDecodeError, ValueError):
        payload = {}
    message = _bounded_text(payload.get("msg") or payload.get("message"), 400)
    detail = f" ({message})" if message else ""
    if response.status_code in (401, 403):
        return "Feishu denied history access. The app may lack message-history permission, the bot may not be in this chat, or this is a restricted chat" + detail + "."
    return f"Feishu history API returned HTTP {response.status_code}" + detail + "."


async def _tenant_token(client: httpx.AsyncClient, credentials: FeishuHistoryCredentials) -> str:
    response = await client.post(
        "/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": credentials.app_id, "app_secret": credentials.app_secret},
    )
    if response.is_error:
        raise FeishuHistoryError(_safe_api_error(response))
    payload = _as_object(response.json())
    token = payload.get("tenant_access_token")
    if payload.get("code") not in (None, 0) or not isinstance(token, str) or not token:
        detail = _bounded_text(payload.get("msg") or payload.get("message"), 400)
        raise FeishuHistoryError(
            "Feishu could not issue a tenant access token"
            + (f" ({detail})." if detail else ". Check this runtime instance's app credentials.")
        )
    return token


async def read_feishu_history(
    *,
    credentials: FeishuHistoryCredentials,
    scope: FeishuConversationScope,
    limit: int,
    start_time: int | None,
    end_time: int | None,
    order: Literal["asc", "desc"],
) -> str:
    """Read a strictly bounded current-chat/current-thread history window."""
    if not scope.chat_id:
        raise FeishuHistoryError("The current Feishu request has no chat id.")
    if start_time is not None and end_time is not None and start_time > end_time:
        raise FeishuHistoryError("start_time must be earlier than or equal to end_time.")
    if scope.thread_id and (start_time is not None or end_time is not None):
        raise FeishuHistoryError(
            "Feishu does not support start_time or end_time when reading a topic thread."
        )

    requested_limit = min(max(limit, 1), _MAX_LIMIT)
    container_type = "thread" if scope.thread_id else "chat"
    container_id = scope.thread_id or scope.chat_id
    params: dict[str, str | int] = {
        "container_id_type": container_type,
        "container_id": container_id,
        "sort_type": "ByCreateTimeAsc" if order == "asc" else "ByCreateTimeDesc",
    }
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time

    records: list[dict[str, object]] = []
    exhausted = False
    page_token: str | None = None
    pages_read = 0
    try:
        async with asyncio.timeout(_TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                base_url=_api_base(credentials.domain),
                timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS),
                headers={"User-Agent": "nanobot-feishu-history/1"},
            ) as client:
                token = await _tenant_token(client, credentials)
                while len(records) < requested_limit and pages_read < _MAX_PAGES:
                    page_params = dict(params)
                    page_params["page_size"] = min(_MAX_PAGE_SIZE, requested_limit - len(records))
                    if page_token:
                        page_params["page_token"] = page_token
                    response = await client.get(
                        "/open-apis/im/v1/messages",
                        params=page_params,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if response.is_error:
                        raise FeishuHistoryError(_safe_api_error(response))
                    payload = _as_object(response.json())
                    if payload.get("code") not in (None, 0):
                        detail = _bounded_text(payload.get("msg") or payload.get("message"), 400)
                        raise FeishuHistoryError(
                            "Feishu denied or could not retrieve this conversation's history"
                            + (f" ({detail})." if detail else ".")
                        )
                    data = _as_object(payload.get("data"))
                    for raw in _as_list(data.get("items")):
                        item = _as_object(raw)
                        if not item or not _matches_root(item, scope.root_id):
                            continue
                        normalized = _normalize_message(item)
                        if normalized is not None:
                            records.append(normalized)
                            if len(records) >= requested_limit:
                                break
                    pages_read += 1
                    has_more = bool(data.get("has_more"))
                    next_token = data.get("page_token")
                    page_token = next_token if isinstance(next_token, str) and next_token else None
                    if not has_more or not page_token:
                        exhausted = True
                        break
    except TimeoutError as exc:
        raise FeishuHistoryError("Feishu history request timed out before the bounded window could be read.") from exc
    except httpx.TimeoutException as exc:
        raise FeishuHistoryError("Feishu history request timed out. Please try a smaller time window.") from exc
    except httpx.HTTPError as exc:
        raise FeishuHistoryError("Unable to reach Feishu history API. Please try again later.") from exc

    messages: list[dict[str, object]] = []
    result: dict[str, object] = {
        "messages": messages,
        "returned": 0,
        "requested_limit": requested_limit,
        "order": order,
        "scope": "thread" if scope.thread_id else ("root" if scope.root_id else "chat"),
        "truncated": not exhausted or len(records) >= requested_limit,
    }
    for record in records:
        candidate = [*messages, record]
        candidate_result = dict(result)
        candidate_result["messages"] = candidate
        candidate_result["returned"] = len(candidate)
        if len(json.dumps(candidate_result, ensure_ascii=False, separators=(",", ":"))) > _RESULT_CHAR_LIMIT:
            result["truncated"] = True
            break
        messages.append(record)
        result["returned"] = len(messages)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "FeishuConversationScope",
    "FeishuHistoryCredentials",
    "FeishuHistoryError",
    "parse_history_time",
    "read_feishu_history",
]
