"""Session tools read full canonical history independently of WebUI replay."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.sessions import ReadSessionTool, SearchSessionsTool
from nanobot.session.history import SessionHistoryReader
from nanobot.session.manager import SessionManager
from nanobot.session.sqlite_store import SqliteSessionStore


@pytest.mark.asyncio
@pytest.mark.parametrize("compacted", [False, True])
@pytest.mark.parametrize("tool_name", ["search", "read"])
async def test_session_tools_find_old_matches_in_full_session(
    tmp_path, compacted, tool_name,
):
    manager = SessionManager(tmp_path)
    key = "sdk:history"
    session = manager.get_or_create(key)
    session.metadata.update({"title": "Project notes", "title_user_edited": True})
    for index in range(180):
        text = f"launch decision {index}" if index < 10 else f"unrelated update {index}"
        session.add_message("user", text)
        session.add_message("assistant", f"acknowledged {index}")
    if compacted:
        session.commit_summary_checkpoint("Project updates are complete.")
        assert session.get_history() == []
    manager.save(session)
    manager = SessionManager(tmp_path)

    with request_context(RequestContext(
        channel="sdk", chat_id="current", session_key="sdk:current",
    )):
        if tool_name == "search":
            output = json.loads(await SearchSessionsTool(manager).execute(query="launch decision"))
            assert [item["session_key"] for item in output["results"]] == [key]
            matches = output["results"][0]["excerpts"]
            expected = range(8, 10)
        else:
            output = json.loads(await ReadSessionTool(manager).execute(
                session_key=key, query="launch decision",
            ))
            matches = output["messages"]
            expected = range(2, 10)
            latest = json.loads(await ReadSessionTool(manager).execute(session_key=key))
            assert len(latest["messages"]) == 8
            assert [item["message_index"] for item in latest["messages"]] == list(range(352, 360))
            assert latest["messages"][-1]["content"] == "acknowledged 179"

    assert [item["content"] for item in matches] == [f"launch decision {i}" for i in expected]
    assert [item["message_index"] for item in matches] == [i * 2 for i in expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["canonical", "transcript", "mixed"])
async def test_session_tools_use_canonical_records_after_restart(history, tmp_path, monkeypatch, state):
    webui_dir = tmp_path / "webui"
    monkeypatch.setattr("nanobot.webui.history.get_webui_dir", lambda: webui_dir)
    manager = history.sessions
    key = "websocket:history"
    if state != "transcript":
        session = manager.get_or_create(key)
        session.metadata.update({"title": "Project notes", "title_user_edited": True})
        session.add_message("user", "canonical needle")
        manager.save(session)
    if state != "canonical":
        history.append(key, {
            "event": "user", "chat_id": "history", "text": "display-only needle",
        })
        history.append(key, {"event": "turn_end", "chat_id": "history"})

    def durable_files():
        return {
            path: path.read_bytes()
            for root in (manager.sessions_dir, webui_dir)
            for path in root.rglob("*.jsonl")
        }

    original = durable_files()
    for _ in range(2):
        manager = history.sessions
        with request_context(RequestContext(
            channel="websocket", chat_id="current", session_key="websocket:current",
        )):
            search = json.loads(await SearchSessionsTool(manager).execute(query="needle"))
            read = await ReadSessionTool(manager).execute(session_key=key, query="needle")
            display_search = json.loads(await SearchSessionsTool(manager).execute(query="display-only"))
        if state == "transcript":
            assert search["results"] == []
            assert json.loads(read)["messages"] == []
        else:
            assert [row["session_key"] for row in search["results"]] == [key]
            assert search["results"][0]["excerpts"][0]["content"] == "canonical needle"
            assert [item["content"] for item in json.loads(read)["messages"]] == ["canonical needle"]
        assert display_search["results"] == []
        assert durable_files() == original


@pytest.mark.parametrize("operation", ["search", "read"])
def test_history_authorizes_and_excludes_before_opening_transcripts(
    tmp_path, monkeypatch, operation,
):
    manager = SessionManager(tmp_path)
    for key in ("sdk:allowed", "sdk:denied", "sdk:current"):
        session = manager.get_or_create(key)
        session.metadata.update({"title": "needle", "title_user_edited": True})
        session.add_message("user", f"needle private body for {key}")
        manager.save(session)
    protected = {"sdk:denied", "sdk:current"}

    def guarded(name):
        real = getattr(SqliteSessionStore, name)

        def read(self, key, *args, **kwargs):
            if key in protected:
                pytest.fail("Denied or current-session transcript was opened")
            return real(self, key, *args, **kwargs)

        return read

    for name in ("load", "read", "read_metadata"):
        monkeypatch.setattr(SqliteSessionStore, name, guarded(name))
    reader = SessionHistoryReader(SessionManager(tmp_path))
    options = {
        "exclude_session_key": "sdk:current",
        "can_access": lambda key: key != "sdk:denied",
    }
    if operation == "search":
        result = reader.search("needle", 5, **options)
        assert [(row["session_key"], row["title"]) for row in result] == [
            ("sdk:allowed", "needle"),
        ]
    else:
        for key in ("sdk:denied", "sdk:current"):
            assert reader.read(key, query="needle", limit=5, **options) is None
        result = reader.read("sdk:allowed", query="needle", limit=5, **options)
        assert result["messages"][0]["content"] == "needle private body for sdk:allowed"


def test_history_title_rank_ties_use_persisted_recency(tmp_path):
    manager = SessionManager(tmp_path)
    for key, title, day in (
        ("sdk:old", "needle", 1),
        ("sdk:new", "needle", 2),
        ("sdk:prefix", "needle notes", 3),
    ):
        session = manager.get_or_create(key)
        session.metadata.update({"title": title, "title_user_edited": True})
        session.add_message("user", "unrelated body")
        session.updated_at = datetime(2026, 10, day)
        manager.save(session)
    matches = SessionHistoryReader(SessionManager(tmp_path)).search("needle", 5)
    assert [row["session_key"] for row in matches] == [
        "sdk:new", "sdk:old", "sdk:prefix",
    ]


@pytest.mark.asyncio
async def test_read_scoped_session_without_context_reads_only_header(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:scoped")
    session.metadata.update({
        "collaboration_user_id": "private-user",
        "collaboration_project_id": "private-project",
    })
    session.add_message("user", "private transcript must not be read")
    manager.save(session)
    real_load = SqliteSessionStore.load
    real_read = SqliteSessionStore.read

    def guarded_load(self, key, *args, history=True, **kwargs):
        if key == session.key and history:
            pytest.fail("Authorization parsed the scoped transcript body")
        return real_load(self, key, *args, history=history, **kwargs)

    def guarded_read(self, key, *args, **kwargs):
        if key == session.key:
            pytest.fail("Authorization read the full scoped transcript")
        return real_read(self, key, *args, **kwargs)

    monkeypatch.setattr(SqliteSessionStore, "load", guarded_load)
    monkeypatch.setattr(SqliteSessionStore, "read", guarded_read)
    result = await ReadSessionTool(SessionManager(tmp_path)).execute(session_key=session.key)
    assert result == "Error: scoped session access requires a request context"
