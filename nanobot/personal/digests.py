"""Vault-scoped Feishu transcription journal used by daily digest jobs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from nanobot.config.paths import get_runtime_subdir


@dataclass(frozen=True, slots=True)
class RecordingNote:
    owner_user_id: str
    vault_id: str
    source_chat_id: str
    message_id: str
    recorded_at_ms: int
    transcript: str


class RecordingJournal:
    """Append-only private transcription journal; no cross-vault query exists."""

    def __init__(self) -> None:
        self._root = get_runtime_subdir("vaults")

    def _path(self, owner_user_id: str, vault_id: str) -> Path:
        return self._root / owner_user_id / vault_id / "recordings.jsonl"

    def append(self, note: RecordingNote) -> None:
        path = self._path(note.owner_user_id, note.vault_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(note), ensure_ascii=False) + "\n")

    def for_local_day(self, owner_user_id: str, vault_id: str, day: datetime) -> list[RecordingNote]:
        path = self._path(owner_user_id, vault_id)
        if not path.is_file():
            return []
        start = datetime(day.year, day.month, day.day, tzinfo=day.tzinfo or UTC)
        end_ms = int(start.timestamp() * 1000) + 86_400_000
        start_ms = int(start.timestamp() * 1000)
        notes: list[RecordingNote] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
                note = RecordingNote(**raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if start_ms <= note.recorded_at_ms < end_ms:
                notes.append(note)
        return notes

    def digest_prompt(self, owner_user_id: str, vault_id: str, day: datetime) -> str:
        notes = self.for_local_day(owner_user_id, vault_id, day)
        if not notes:
            return "No authorized Feishu recordings were received for this day."
        items = "\n\n".join(f"[{note.message_id}] {note.transcript}" for note in notes)
        return (
            "Summarize only the following authorized recordings. Extract decisions and proposed tasks; "
            "do not invent facts or disclose other vault data.\n\n" + items
        )
