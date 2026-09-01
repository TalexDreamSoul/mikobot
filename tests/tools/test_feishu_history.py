from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import nanobot.channels.feishu.history as history
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.feishu_history import FeishuHistoryTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.channels.feishu.history import (
    FeishuConversationScope,
    FeishuHistoryCredentials,
    FeishuHistoryError,
    read_feishu_history,
)


def _install_mock_history_api(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    """Route all history API requests through a deterministic in-memory transport."""
    real_async_client = httpx.AsyncClient

    def mock_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(history.httpx, "AsyncClient", mock_async_client)


def _message(message_id: str, text: str, *, root_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "message_id": message_id,
        "msg_type": "text",
        "sender": {"id": "ou_sender"},
        "create_time": "1735689600000",
        "body": {"content": json.dumps({"text": text})},
    }
    if root_id is not None:
        result["root_id"] = root_id
    return result


def _credentials() -> FeishuHistoryCredentials:
    return FeishuHistoryCredentials(app_id="cli-app", app_secret="history-app-secret", domain="feishu")


@pytest.mark.asyncio
async def test_history_reads_only_current_chat_and_current_root_from_mocked_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request-derived chat/root boundary is sent to Feishu and filters unrelated messages."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"}, request=request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        _message("root-1", "current topic", root_id="root-1"),
                        _message("other-root", "another topic", root_id="other-root"),
                    ],
                    "has_more": False,
                },
            },
            request=request,
        )

    _install_mock_history_api(monkeypatch, handler)
    encoded = await read_feishu_history(
        credentials=_credentials(),
        scope=FeishuConversationScope(chat_id="oc_current", root_id="root-1"),
        limit=10,
        start_time=1_735_689_600,
        end_time=1_735_689_700,
        order="asc",
    )

    body = json.loads(encoded)
    request = next(request for request in seen if request.method == "GET")
    assert request.url.params["container_id_type"] == "chat"
    assert request.url.params["container_id"] == "oc_current"
    assert request.url.params["start_time"] == "1735689600"
    assert request.url.params["end_time"] == "1735689700"
    assert body["scope"] == "root"
    assert [message["message_id"] for message in body["messages"]] == ["root-1"]


@pytest.mark.asyncio
async def test_history_rejects_time_windows_for_current_topic_before_network_access() -> None:
    """Topic history cannot silently widen to a chat-wide time-filtered query."""
    with pytest.raises(FeishuHistoryError, match="does not support start_time or end_time"):
        await read_feishu_history(
            credentials=_credentials(),
            scope=FeishuConversationScope(chat_id="oc_current", thread_id="omt_topic"),
            limit=5,
            start_time=1,
            end_time=None,
            order="desc",
        )


@pytest.mark.asyncio
async def test_history_stops_after_bounded_pagination_when_no_current_root_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A noisy unrelated history cannot make the current-topic tool paginate indefinitely."""
    message_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal message_requests
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"}, request=request)
        message_requests += 1
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [_message(f"unrelated-{message_requests}", "not this topic", root_id="other")],
                    "has_more": True,
                    "page_token": f"page-{message_requests}",
                },
            },
            request=request,
        )

    _install_mock_history_api(monkeypatch, handler)
    encoded = await read_feishu_history(
        credentials=_credentials(),
        scope=FeishuConversationScope(chat_id="oc_current", root_id="root-current"),
        limit=50,
        start_time=None,
        end_time=None,
        order="desc",
    )

    body = json.loads(encoded)
    assert message_requests == 5
    assert body["messages"] == []
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_history_caps_serialized_output_even_when_the_api_returns_full_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History output remains bounded even if every message is individually valid and maximal."""
    large_text = "x" * 1_200

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"}, request=request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [_message(str(index), large_text) for index in range(50)],
                    "has_more": False,
                },
            },
            request=request,
        )

    _install_mock_history_api(monkeypatch, handler)
    encoded = await read_feishu_history(
        credentials=_credentials(),
        scope=FeishuConversationScope(chat_id="oc_current"),
        limit=50,
        start_time=None,
        end_time=None,
        order="desc",
    )

    body = json.loads(encoded)
    assert len(encoded) <= 12_000
    assert 0 < body["returned"] < 50
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_history_returns_safe_permission_error_without_secret_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A denied Feishu API request maps to actionable user-safe text without exposing configured secrets."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"}, request=request)
        return httpx.Response(403, json={"msg": "bot lacks history permission"}, request=request)

    _install_mock_history_api(monkeypatch, handler)
    with pytest.raises(FeishuHistoryError) as exc_info:
        await read_feishu_history(
            credentials=_credentials(),
            scope=FeishuConversationScope(chat_id="oc_current"),
            limit=5,
            start_time=None,
            end_time=None,
            order="desc",
        )

    error = str(exc_info.value)
    assert "permission" in error
    assert "history-app-secret" not in error
    assert "tenant-token" not in error


@pytest.mark.asyncio
async def test_history_tool_rejects_arbitrary_chat_id_before_it_can_be_requested() -> None:
    """The public tool interface cannot be used to query a model-supplied chat id."""
    registry = ToolRegistry()
    registry.register(FeishuHistoryTool())

    result = await registry.execute("read_feishu_history", {"chat_id": "oc_someone_else"})

    assert result.is_error is True
    assert "unexpected parameter chat_id" in result


@pytest.mark.asyncio
async def test_history_tool_selects_exact_runtime_instance_and_request_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named Feishu channel uses its own credentials and the active event's chat/topic, never another instance."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "nanobot.config.loader.load_config",
        lambda: SimpleNamespace(channels=SimpleNamespace(feishu={})),
    )
    monkeypatch.setattr("nanobot.config.loader.resolve_config_env_vars", lambda config: config)
    monkeypatch.setattr(
        "nanobot.agent.tools.feishu_history.feishu_instance_specs",
        lambda *_args: [
            SimpleNamespace(instance_id="default", config={"appId": "default-app", "appSecret": "default-secret"}),
            SimpleNamespace(
                instance_id="team-a",
                config={"appId": "team-app", "appSecret": "team-secret", "domain": "lark"},
            ),
        ],
    )

    async def fake_read(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "history"

    monkeypatch.setattr("nanobot.agent.tools.feishu_history.read_feishu_history", fake_read)
    tool = FeishuHistoryTool()
    with request_context(RequestContext(
        channel="feishu.team-a",
        chat_id="ou_dm_target",
        metadata={"source_chat_id": "oc_current", "thread_id": "omt_topic", "root_id": "root-topic"},
    )):
        result = await tool.execute(limit=7, order="asc")

    assert result == "history"
    assert captured["credentials"] == FeishuHistoryCredentials(
        app_id="team-app",
        app_secret="team-secret",
        domain="lark",
    )
    assert captured["scope"] == FeishuConversationScope(
        chat_id="oc_current",
        thread_id="omt_topic",
        root_id="root-topic",
    )
    assert captured["limit"] == 7
    assert captured["order"] == "asc"
