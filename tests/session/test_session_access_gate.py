"""Decision matrix for cross-session access between persisted sessions."""

from __future__ import annotations

import pytest

from nanobot.session.keys import is_host_private_session_key
from nanobot.session.privacy import (
    SESSION_ACCESS_KIND_METADATA_KEY,
    SESSION_ACCESS_PROJECT_METADATA_KEY,
    SESSION_ACCESS_USER_METADATA_KEY,
    SessionAccessScope,
    session_access_allowed,
    session_access_scope,
)

_METADATA = "session metadata"


def _record(
    *,
    kind: str = "direct",
    user_id: str | None = "alice",
    project_id: str | None = "alpha",
) -> dict[str, object]:
    """Build the per-turn access record the agent loop stamps on a session."""
    return {
        SESSION_ACCESS_KIND_METADATA_KEY: kind,
        SESSION_ACCESS_USER_METADATA_KEY: user_id,
        SESSION_ACCESS_PROJECT_METADATA_KEY: project_id,
    }


def _legacy(user_id: str, project_id: str | None = "alpha") -> dict[str, object]:
    """Build member capability provenance written before access records existed."""
    metadata: dict[str, object] = {"collaboration_user_id": user_id}
    if project_id is not None:
        metadata["collaboration_project_id"] = project_id
    return metadata


@pytest.mark.parametrize(
    ("source_metadata", "source_key", "target_metadata", "target_key", "expected"),
    [
        pytest.param(_record(), "telegram:a", _record(), "telegram:b", True, id="same-member-same-project"),
        pytest.param(
            _record(project_id="alpha"),
            "telegram:a",
            _record(project_id="beta"),
            "telegram:b",
            False,
            id="same-member-other-project",
        ),
        pytest.param(
            _record(user_id="alice"),
            "telegram:a",
            _record(user_id="bob"),
            "telegram:b",
            False,
            id="other-member-same-project",
        ),
        pytest.param(
            _record(user_id=None),
            "telegram:a",
            _record(),
            "telegram:b",
            False,
            id="source-without-member",
        ),
        pytest.param(
            _record(),
            "telegram:a",
            _record(user_id=None),
            "telegram:b",
            False,
            id="target-without-member",
        ),
        pytest.param(
            _record(),
            "telegram:a",
            _record(user_id="alice", project_id=None),
            "telegram:b",
            False,
            id="same-member-one-record-without-project",
        ),
        pytest.param(
            _record(project_id=None),
            "telegram:a",
            _record(project_id=None),
            "telegram:b",
            True,
            id="same-member-both-records-without-project",
        ),
        pytest.param(
            _record(kind="isolated", user_id="alice", project_id=None),
            "telegram:a",
            _record(),
            "telegram:b",
            False,
            id="isolated-source",
        ),
        pytest.param(
            _record(),
            "telegram:a",
            _record(kind="isolated", user_id="alice", project_id=None),
            "telegram:b",
            False,
            id="isolated-target",
        ),
        pytest.param(
            _record(kind="isolated", user_id="alice", project_id=None),
            "telegram:a",
            _record(kind="isolated", user_id="alice", project_id=None),
            "telegram:b",
            False,
            id="both-isolated",
        ),
        pytest.param(
            _record(kind="isolated", user_id="alice"),
            "telegram:a",
            _record(),
            "telegram:b",
            False,
            id="isolation-outranks-a-matching-project",
        ),
        pytest.param(
            {**_record(kind="isolated", user_id="alice", project_id=None), **_legacy("alice")},
            "feishu:group",
            _legacy("alice"),
            "user:alice:project:alpha:telegram:peer",
            False,
            id="isolation-outranks-stale-member-provenance",
        ),
        pytest.param(
            _legacy("alice"),
            "user:alice:project:alpha:telegram:peer",
            {**_record(kind="isolated", user_id="alice", project_id=None), **_legacy("alice")},
            "feishu:group",
            False,
            id="stale-provenance-cannot-revive-an-isolated-target",
        ),
        pytest.param(_record(), "telegram:a", None, "telegram:b", False, id="marked-to-unmarked-channel"),
        pytest.param(_record(), "telegram:a", None, "websocket:b", False, id="marked-to-unmarked-host"),
        pytest.param(None, "telegram:a", _record(), "telegram:b", False, id="unmarked-channel-source"),
        pytest.param(None, "telegram:a", None, "telegram:b", False, id="both-unmarked-channels"),
        pytest.param(
            None,
            "feishu:team-chat",
            None,
            "weixin:o9cq800LLcqRyeZsSjdtO9vSpqMI@im.wechat",
            False,
            id="reported-feishu-to-weixin-leak",
        ),
        pytest.param(None, "websocket:a", _record(), "telegram:b", True, id="host-source-reads-marked"),
        pytest.param(None, "heartbeat", None, "telegram:b", True, id="host-source-reads-channel"),
        pytest.param(None, "cli:a", None, "websocket:b", True, id="host-source-reads-host"),
        pytest.param(
            None,
            "websocket:cockpit",
            _record(kind="isolated", user_id="alice", project_id=None),
            "feishu:team-group",
            True,
            id="host-source-reaches-an-isolated-group",
        ),
        pytest.param(
            None,
            "user:alice:project:alpha:telegram:a",
            None,
            "user:alice:project:alpha:websocket:b",
            True,
            id="same-qualified-project-key",
        ),
        pytest.param(
            None,
            "unified:alice:project:alpha",
            None,
            "user:alice:project:alpha:websocket:b",
            True,
            id="unified-and-user-qualified-agree",
        ),
        pytest.param(
            None,
            "user:alice:project:alpha:telegram:a",
            None,
            "user:alice:project:beta:telegram:b",
            False,
            id="cross-qualified-project-key",
        ),
        pytest.param(
            None,
            "user:alice:project:alpha:telegram:a",
            None,
            "user:alice:telegram:b",
            False,
            id="qualified-key-versus-project-less-key",
        ),
        pytest.param(
            None,
            "user:alice:project:alpha:telegram:a",
            None,
            "slack:b",
            False,
            id="qualified-key-versus-unkeyed-channel",
        ),
        pytest.param(
            None,
            "user:alice:project:alpha:telegram:a",
            _record(user_id="alice", project_id="alpha"),
            "telegram:b",
            True,
            id="qualified-key-matches-access-record",
        ),
        pytest.param(_legacy("alice"), "telegram:a", _legacy("alice"), "telegram:b", True, id="legacy-same-project"),
        pytest.param(
            _legacy("alice", "alpha"),
            "telegram:a",
            _legacy("alice", "beta"),
            "telegram:b",
            False,
            id="legacy-cross-project",
        ),
        pytest.param(
            _legacy("alice", None),
            "telegram:a",
            _legacy("alice", None),
            "telegram:b",
            True,
            id="legacy-without-project-pairs",
        ),
        pytest.param(
            _legacy("alice", "alpha"),
            "telegram:a",
            _legacy("bob", "alpha"),
            "telegram:b",
            False,
            id="legacy-cross-member",
        ),
        pytest.param(
            {**_record(), **_legacy("bob", "beta")},
            "user:bob:project:beta:telegram:a",
            _record(),
            "telegram:b",
            True,
            id="access-record-outranks-legacy-and-key",
        ),
    ],
)
def test_session_access_decisions(
    source_metadata: dict[str, object] | None,
    source_key: str,
    target_metadata: dict[str, object] | None,
    target_key: str,
    expected: bool,
) -> None:
    """Every source/target row of the documented table resolves to its verdict."""
    assert (
        session_access_allowed(source_metadata, source_key, target_metadata, target_key) is expected
    )


def test_access_scope_resolution_prefers_the_per_turn_record() -> None:
    """The per-turn record wins over member provenance and the project-qualified key."""
    metadata = {**_record(), **_legacy("bob", "beta")}

    assert session_access_scope(metadata, "user:bob:project:beta:telegram:one") == SessionAccessScope(
        "alice", "alpha", isolated=False
    )
    assert session_access_scope(_legacy("bob", "beta"), "telegram:one") == SessionAccessScope(
        "bob", "beta", isolated=False
    )
    assert session_access_scope({}, "user:carol:project:gamma:telegram:one") == SessionAccessScope(
        "carol", "gamma", isolated=False
    )
    assert session_access_scope({}, "unified:dave") == SessionAccessScope("dave", None)


def test_unmarked_sessions_have_no_access_scope() -> None:
    """A session with no record, no provenance and no scoped key is unmarked."""
    assert session_access_scope(None, None) is None
    assert session_access_scope(_METADATA, "telegram:one") is None
    assert session_access_scope({"title": "notes"}, "slack:channel") is None


def test_a_scope_kind_alone_still_marks_the_session() -> None:
    """A record that carries only its kind is marked, not silently treated as unmarked."""
    scope = session_access_scope({SESSION_ACCESS_KIND_METADATA_KEY: "isolated"}, "telegram:one")

    assert scope is not None
    assert scope.isolated
    assert not session_access_allowed(
        {SESSION_ACCESS_KIND_METADATA_KEY: "isolated"}, "telegram:one", None, "telegram:two"
    )


@pytest.mark.parametrize(
    ("session_key", "is_host_private"),
    [
        pytest.param("heartbeat", True, id="heartbeat"),
        pytest.param("heartbeat:tick", True, id="heartbeat-prefixed"),
        pytest.param("cron:job-1", True, id="cron"),
        pytest.param("dream:nightly", True, id="dream"),
        pytest.param("websocket:browser", True, id="websocket"),
        pytest.param("cli:direct", True, id="cli"),
        pytest.param("heartbeats", False, id="heartbeat-lookalike"),
        pytest.param("cron", False, id="cron-without-instance"),
        pytest.param("websockets", False, id="websocket-lookalike"),
        pytest.param("feishu:heartbeat:tick", False, id="channel-copying-a-host-prefix"),
        pytest.param("unified:default", False, id="unified"),
        pytest.param("telegram:one", False, id="channel"),
        pytest.param("", False, id="empty"),
        pytest.param(None, False, id="missing"),
    ],
)
def test_host_private_keys_are_an_exact_prefix_set(session_key: str | None, is_host_private: bool) -> None:
    """Only the host's own namespaces are reachable by an unmarked source."""
    assert is_host_private_session_key(session_key) is is_host_private
