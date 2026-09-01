"""Behavioral coverage for vault-scoped recording journals."""

from datetime import UTC, datetime
from pathlib import Path

from nanobot.personal.digests import RecordingJournal, RecordingNote


def test_journal_returns_only_requested_owner_vault_notes_from_same_utc_day(
    tmp_path: Path, monkeypatch
) -> None:
    """A day query never crosses owner/vault boundaries or calendar-day bounds."""
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(
        "nanobot.personal.digests.get_runtime_subdir", lambda name: runtime_root / name
    )
    journal = RecordingJournal()
    day = datetime(2026, 9, 1, tzinfo=UTC)
    authorized = RecordingNote(
        owner_user_id="owner-a",
        vault_id="vault-a",
        source_chat_id="chat-a",
        message_id="same-day",
        recorded_at_ms=int(datetime(2026, 9, 1, 12, tzinfo=UTC).timestamp() * 1000),
        transcript="authorized recording",
    )
    other_day = RecordingNote(
        owner_user_id="owner-a",
        vault_id="vault-a",
        source_chat_id="chat-a",
        message_id="next-day",
        recorded_at_ms=int(datetime(2026, 9, 2, tzinfo=UTC).timestamp() * 1000),
        transcript="tomorrow's recording",
    )
    other_vault = RecordingNote(
        owner_user_id="owner-a",
        vault_id="vault-b",
        source_chat_id="chat-b",
        message_id="other-vault",
        recorded_at_ms=int(datetime(2026, 9, 1, 12, tzinfo=UTC).timestamp() * 1000),
        transcript="another vault's recording",
    )
    other_owner = RecordingNote(
        owner_user_id="owner-b",
        vault_id="vault-a",
        source_chat_id="chat-c",
        message_id="other-owner",
        recorded_at_ms=int(datetime(2026, 9, 1, 12, tzinfo=UTC).timestamp() * 1000),
        transcript="another owner's recording",
    )

    for note in (authorized, other_day, other_vault, other_owner):
        journal.append(note)

    assert journal.for_local_day("owner-a", "vault-a", day) == [authorized]
    assert journal.for_local_day("owner-a", "vault-b", day) == [other_vault]
    assert journal.for_local_day("owner-b", "vault-a", day) == [other_owner]
