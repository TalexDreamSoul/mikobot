"""Standards-compatible iCalendar export for personal tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from nanobot.collaboration.models import PersonalTask


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).strftime("%Y%m%dT%H%M%SZ")


def export_tasks_ics(tasks: list[PersonalTask], *, calendar_name: str) -> str:
    """Export confirmed tasks as VTODO records without credentials or secrets."""
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nanobot//Personal Assistant//EN",
        "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_escape(calendar_name)}",
    ]
    for task in tasks:
        if task.review_state.value != "confirmed":
            continue
        uid = sha256(task.id.encode()).hexdigest()[:32] + "@nanobot"
        lines.extend(["BEGIN:VTODO", f"UID:{uid}", f"DTSTAMP:{_timestamp(task.updated_at_ms)}", f"SUMMARY:{_escape(task.title)}", f"STATUS:{'COMPLETED' if task.status.value == 'done' else 'NEEDS-ACTION'}"])
        if task.note:
            lines.append(f"DESCRIPTION:{_escape(task.note)}")
        if task.due_at_ms is not None:
            lines.append(f"DUE:{_timestamp(task.due_at_ms)}")
        if task.status.value == "done":
            lines.append("PERCENT-COMPLETE:100")
        lines.append("END:VTODO")
    lines.extend(["END:VCALENDAR", ""])
    return "\r\n".join(lines)
