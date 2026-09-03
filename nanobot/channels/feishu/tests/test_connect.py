from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from nanobot.channels.connect import ChannelConnectError
from nanobot.channels.feishu import runtime as feishu
from nanobot.channels.feishu.connect import FeishuConnectStore


@pytest.mark.asyncio
async def test_feishu_connect_session_rejects_other_actor_poll_and_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connector session can only be polled or cancelled by its creating actor."""
    monkeypatch.setattr(feishu, "_init_registration", lambda _domain: None)
    monkeypatch.setattr(
        feishu,
        "_begin_registration",
        lambda _domain: {
            "device_code": "actor-device",
            "qr_url": "https://qr.example/actor",
            "expire_in": 600,
            "interval": 2,
        },
    )
    store = FeishuConnectStore()
    started = await store.handle(
        "start",
        {
            "instance_id": ["actor-instance"],
            "_actor_user_id": ["owner-user"],
        },
    )
    query = {
        "session_id": [started["session_id"]],
        "_actor_user_id": ["other-user"],
    }

    with pytest.raises(ChannelConnectError) as poll_error:
        await store.handle("poll", query)
    assert poll_error.value.status == 403

    with pytest.raises(ChannelConnectError) as cancel_error:
        await store.handle("cancel", query)
    assert cancel_error.value.status == 403
    assert started["session_id"] in store._sessions

    cancelled = await store.handle(
        "cancel",
        {
            "session_id": [started["session_id"]],
            "_actor_user_id": ["owner-user"],
        },
    )
    assert cancelled["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "requested_instance", "expected_instance"),
    [
        ("replace", "named-target", "named-target"),
        ("create", "client-controlled", "assistant-server-assigned"),
    ],
)
async def test_feishu_connect_completion_keeps_server_authorized_target_and_mode(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    requested_instance: str,
    expected_instance: str,
) -> None:
    """A successful scan may only save and report the target authorized at session start."""
    monkeypatch.setattr(feishu, "_init_registration", lambda _domain: None)
    monkeypatch.setattr(
        feishu,
        "_begin_registration",
        lambda _domain: {
            "device_code": "bound-device",
            "qr_url": "https://qr.example/bound",
            "expire_in": 600,
            "interval": 2,
        },
    )
    monkeypatch.setattr(
        "nanobot.channels.feishu.connect.secrets.token_hex",
        lambda _size: "server-assigned",
    )
    monkeypatch.setattr(
        feishu,
        "poll_registration_once",
        lambda **_kwargs: {
            "status": "succeeded",
            "domain": "feishu",
            "app_id": "private-app-id",
            "app_secret": "private-app-secret",
        },
    )
    save_calls: list[dict[str, Any]] = []

    def save_result(_result: dict[str, Any], **kwargs: Any) -> str:
        save_calls.append(kwargs)
        return "wrong-instance-must-not-remap-session"

    monkeypatch.setattr(feishu, "save_registration_result", save_result)
    store = FeishuConnectStore()

    started = await store.handle(
        "start",
        {"mode": [mode], "instance_id": [requested_instance]},
    )
    completed = await store.handle("poll", {"session_id": [started["session_id"]]})

    assert started["instance_id"] == expected_instance
    assert completed["instance_id"] == expected_instance
    assert save_calls == [
        {
            "instance_id": expected_instance,
            "name": f"nanobot {expected_instance}",
            "mode": mode,
        }
    ]
    assert {"app_id", "app_secret"}.isdisjoint(completed)
    assert "private-app-id" not in repr(completed)
    assert "private-app-secret" not in repr(completed)

@pytest.mark.asyncio
async def test_feishu_cancel_wins_over_inflight_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poll_started = threading.Event()
    release_poll = threading.Event()
    saved_results: list[dict[str, Any]] = []

    monkeypatch.setattr(feishu, "_init_registration", lambda _domain: None)
    monkeypatch.setattr(
        feishu,
        "_begin_registration",
        lambda _domain: {
            "device_code": "device-cancel",
            "qr_url": "https://qr.example/cancel",
            "expire_in": 600,
            "interval": 2,
        },
    )

    def fake_poll_registration_once(**_kwargs: Any) -> dict[str, str]:
        poll_started.set()
        assert release_poll.wait(timeout=5)
        return {
            "status": "succeeded",
            "domain": "feishu",
            "app_id": "late-app",
            "app_secret": "late-secret",
        }

    def fake_save_registration_result(
        result: dict[str, Any],
        **_kwargs: Any,
    ) -> str:
        saved_results.append(result)
        return "default"

    monkeypatch.setattr(feishu, "poll_registration_once", fake_poll_registration_once)
    monkeypatch.setattr(feishu, "save_registration_result", fake_save_registration_result)

    store = FeishuConnectStore()
    started = await store.handle("start", {})
    query = {"session_id": [started["session_id"]]}
    poll_task = asyncio.create_task(store.handle("poll", query))
    assert await asyncio.to_thread(poll_started.wait, 5)

    cancelled = await store.handle("cancel", query)
    release_poll.set()
    completed = await poll_task

    assert cancelled["status"] == "cancelled"
    assert completed["status"] == "cancelled"
    assert saved_results == []


@pytest.mark.asyncio
async def test_feishu_cancel_does_not_interleave_with_registration_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_started = threading.Event()
    release_save = threading.Event()

    monkeypatch.setattr(feishu, "_init_registration", lambda _domain: None)
    monkeypatch.setattr(
        feishu,
        "_begin_registration",
        lambda _domain: {
            "device_code": "device-lock",
            "qr_url": "https://qr.example/lock",
            "expire_in": 600,
            "interval": 2,
        },
    )
    monkeypatch.setattr(
        feishu,
        "poll_registration_once",
        lambda **_kwargs: {
            "status": "succeeded",
            "domain": "feishu",
            "app_id": "saved-app",
            "app_secret": "saved-secret",
        },
    )

    def fake_save_registration_result(
        _result: dict[str, Any],
        **_kwargs: Any,
    ) -> str:
        save_started.set()
        assert release_save.wait(timeout=5)
        return "default"

    monkeypatch.setattr(feishu, "save_registration_result", fake_save_registration_result)

    store = FeishuConnectStore()
    started = await store.handle("start", {})
    query = {"session_id": [started["session_id"]]}
    poll_task = asyncio.create_task(store.handle("poll", query))
    assert await asyncio.to_thread(save_started.wait, 5)

    cancel_task = asyncio.create_task(store.handle("cancel", query))
    await asyncio.sleep(0)
    assert not cancel_task.done()

    release_save.set()
    completed = await poll_task
    cancelled = await cancel_task

    assert completed["status"] == "succeeded"
    assert cancelled["status"] == "cancelled"


def test_feishu_connect_sessions_are_bounded_and_replaced_per_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thirty-two distinct QR sessions fit; a replacement for one instance does not consume another slot."""
    monkeypatch.setattr(feishu, "_init_registration", lambda _domain: None)
    monkeypatch.setattr(
        feishu, "_begin_registration",
        lambda _domain: {
            "device_code": "device", "qr_url": "https://qr.example/session",
            "expire_in": 600, "interval": 2,
        },
    )
    store = FeishuConnectStore()
    for index in range(32):
        store.start(instance_id=f"instance-{index}")
    with pytest.raises(ChannelConnectError) as rejected:
        store.start(instance_id="instance-over-limit")
    assert rejected.value.status == 429

    replaced = store.start(instance_id="instance-0")
    assert replaced["instance_id"] == "instance-0"
    assert len(store._sessions) == 32


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["CREATE", "Replace", " create", "replace ", "delete"],
)
async def test_feishu_connect_rejects_nonexact_modes(mode: str) -> None:
    """Only the documented lowercase create and replace modes may start a flow."""
    store = FeishuConnectStore()

    with pytest.raises(ChannelConnectError) as error:
        await store.handle("start", {"mode": [mode]})

    assert error.value.status == 400
    assert str(error.value) == "invalid Feishu connect mode"


def test_feishu_connect_start_redacts_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QR start failures expose a fixed gateway error instead of SDK diagnostics."""
    raw_error = (
        "upstream errmsg: secret=feishu-token "
        "https://feishu.example/connect /private/config/feishu.json"
    )

    def fail_registration(_domain: str) -> None:
        raise OSError(raw_error)

    monkeypatch.setattr(feishu, "_init_registration", fail_registration)

    with pytest.raises(ChannelConnectError) as error:
        FeishuConnectStore().start()

    assert error.value.status == 502
    assert str(error.value) == "Unable to start Feishu/Lark connection."
    for leaked in (
        "secret=feishu-token",
        "upstream errmsg",
        "https://feishu.example",
        "/private/config",
    ):
        assert leaked not in str(error.value)


def test_feishu_connect_poll_keeps_session_pending_without_leaking_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retryable QR polling failures retain the actor-bound session with a safe message."""
    raw_error = (
        "upstream errmsg: secret=feishu-token "
        "https://feishu.example/poll /private/config/feishu.json"
    )
    monkeypatch.setattr(feishu, "_init_registration", lambda _domain: None)
    monkeypatch.setattr(
        feishu,
        "_begin_registration",
        lambda _domain: {
            "device_code": "redaction-device",
            "qr_url": "https://qr.example/redaction",
            "expire_in": 600,
            "interval": 2,
        },
    )

    def fail_poll(**_kwargs: Any) -> dict[str, str]:
        raise OSError(raw_error)

    monkeypatch.setattr(feishu, "poll_registration_once", fail_poll)
    store = FeishuConnectStore()
    started = store.start(actor_user_id="owner-user")

    pending = store.poll(started["session_id"], actor_user_id="owner-user")

    assert pending["status"] == "pending"
    assert pending["message"] == "Waiting for authorization."
    assert started["session_id"] in store._sessions
    for leaked in (
        "secret=feishu-token",
        "upstream errmsg",
        "https://feishu.example",
        "/private/config",
    ):
        assert leaked not in repr(pending)
