"""Behavioral coverage for personal task iCalendar export."""

from nanobot.collaboration.models import PersonalTask, TaskReviewState, TaskStatus
from nanobot.personal.ics import export_tasks_ics


def _task(
    *,
    task_id: str,
    title: str,
    note: str,
    status: TaskStatus,
    review_state: TaskReviewState,
    due_at_ms: int | None,
    updated_at_ms: int,
) -> PersonalTask:
    return PersonalTask(
        id=task_id,
        owner_user_id="owner-a",
        vault_id="vault-a",
        title=title,
        note=note,
        status=status,
        priority=1,
        due_at_ms=due_at_ms,
        timezone="UTC",
        recurrence_rule=None,
        source_type="manual",
        source_ref=None,
        external_provider=None,
        external_id=None,
        external_version=None,
        review_state=review_state,
        created_at_ms=1_788_264_000_000,
        updated_at_ms=updated_at_ms,
    )


def test_export_tasks_ics_emits_confirmed_completed_task_with_due_time() -> None:
    """Only confirmed tasks become VTODOs, retaining due time and completion state."""
    confirmed = _task(
        task_id="confirmed",
        title="Send report",
        note="Share with finance",
        status=TaskStatus.DONE,
        review_state=TaskReviewState.CONFIRMED,
        due_at_ms=1_788_266_096_000,
        updated_at_ms=1_788_264_000_000,
    )
    proposed = _task(
        task_id="proposed",
        title="Do not export",
        note="Still needs approval",
        status=TaskStatus.TODO,
        review_state=TaskReviewState.PROPOSED,
        due_at_ms=1_788_266_096_000,
        updated_at_ms=1_788_264_000_000,
    )

    calendar = export_tasks_ics([confirmed, proposed], calendar_name="Work")

    assert calendar.count("BEGIN:VTODO") == 1
    assert "SUMMARY:Send report" in calendar
    assert "Do not export" not in calendar
    assert "DUE:20260901T123456Z" in calendar
    assert "STATUS:COMPLETED" in calendar
    assert "PERCENT-COMPLETE:100" in calendar


def test_export_tasks_ics_escapes_icalendar_summary_and_note_text() -> None:
    """Punctuation and line breaks in confirmed task text remain one valid iCalendar field."""
    task = _task(
        task_id="escaped",
        title="Title, notes; path\\line\nnext",
        note="Line one\nline two; list, C:\\work",
        status=TaskStatus.TODO,
        review_state=TaskReviewState.CONFIRMED,
        due_at_ms=None,
        updated_at_ms=1_788_264_000_000,
    )

    calendar = export_tasks_ics([task], calendar_name="Work")

    assert "SUMMARY:Title\\, notes\\; path\\\\line\\nnext" in calendar
    assert "DESCRIPTION:Line one\\nline two\\; list\\, C:\\\\work" in calendar
