"""Merge-forward expansion: a forwarded card is remote input, so it must stay bounded."""

from types import SimpleNamespace

from nanobot.channels.feishu.runtime import FeishuChannel


def _bare_channel() -> FeishuChannel:
    channel = FeishuChannel.__new__(FeishuChannel)
    channel.logger = SimpleNamespace(debug=lambda *_a, **_k: None)
    channel._client = object()
    return channel


def test_merge_forward_ids_read_the_shapes_feishu_actually_sends() -> None:
    """Feishu has used a flat list and nested message objects across API versions."""
    flat = FeishuChannel._merge_forward_message_ids({"message_id_list": ["om_a", "om_b"]})
    nested = FeishuChannel._merge_forward_message_ids(
        {"messages": [{"message_id": "om_c"}, {"content": {"message_id": "om_d"}}]}
    )

    assert flat == ["om_a", "om_b"]
    assert nested == ["om_c", "om_d"]


def test_merge_forward_ids_ignore_identifiers_that_are_not_messages() -> None:
    """Chat, user, and file IDs share the payload; fetching them would be wrong."""
    ids = FeishuChannel._merge_forward_message_ids(
        {
            "chat_id": "oc_chat",
            "user_id": "ou_user",
            "file_key": "file_v2",
            "messages": [{"message_id": "om_real", "sender_id": "ou_sender"}],
        }
    )

    assert ids == ["om_real"]


def test_merge_forward_ids_stop_at_the_message_cap() -> None:
    """The payload is remote input, so a huge card cannot turn into unbounded fetches."""
    ids = FeishuChannel._merge_forward_message_ids(
        {"message_id_list": [f"om_{i}" for i in range(500)]}
    )

    assert len(ids) == FeishuChannel._MERGE_FORWARD_MAX_MESSAGES


def test_merge_forward_expansion_renders_each_message_once() -> None:
    """A message reached again from a nested forward must not be fetched twice.

    The id extractor already drops duplicates inside one payload, so the case that
    exercises the visited set is a nested forward pointing back at an outer message.
    """
    channel = _bare_channel()
    fetched: list[str] = []

    def record(message_id: str) -> tuple[str, dict[str, object]] | None:
        fetched.append(message_id)
        if message_id == "om_nested":
            return "merge_forward", {"message_id_list": ["om_a"]}
        return "text", {"text": f"body of {message_id}"}

    channel._get_message_record_sync = record  # type: ignore[method-assign]

    text = channel._expand_merge_forward_sync({"message_id_list": ["om_a", "om_nested"]})

    assert fetched == ["om_a", "om_nested"]
    assert text == "body of om_a"


def test_merge_forward_expansion_stops_when_a_forward_references_itself() -> None:
    """A self-referencing forward would otherwise recurse until the worker dies."""
    channel = _bare_channel()
    calls: list[str] = []

    def record(message_id: str) -> tuple[str, dict[str, object]] | None:
        calls.append(message_id)
        # Every fetched message is itself a forward pointing at a fresh id.
        return "merge_forward", {"message_id_list": [f"{message_id}_deeper"]}

    channel._get_message_record_sync = record  # type: ignore[method-assign]

    text = channel._expand_merge_forward_sync({"message_id_list": ["om_root"]})

    assert text == ""
    assert len(calls) <= FeishuChannel._MERGE_FORWARD_MAX_DEPTH + 2


def test_merge_forward_expansion_skips_messages_it_cannot_fetch() -> None:
    """One unreadable message must not lose the rest of the forward."""
    channel = _bare_channel()

    def record(message_id: str) -> tuple[str, dict[str, object]] | None:
        if message_id == "om_gone":
            return None
        if message_id == "om_raises":
            raise RuntimeError("upstream refused")
        return "text", {"text": "kept"}

    channel._get_message_record_sync = record  # type: ignore[method-assign]

    text = channel._expand_merge_forward_sync(
        {"message_id_list": ["om_gone", "om_raises", "om_ok"]}
    )

    assert text == "kept"


def test_merge_forward_expansion_returns_empty_without_a_client() -> None:
    """The caller falls back to the placeholder, which needs an empty string to do so."""
    channel = _bare_channel()
    channel._client = None

    assert channel._expand_merge_forward_sync({"message_id_list": ["om_a"]}) == ""
