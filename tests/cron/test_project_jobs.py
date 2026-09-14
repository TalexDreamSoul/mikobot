"""Tests for the built-in automations every project owns."""

from __future__ import annotations

import json
from pathlib import Path

from nanobot.cron.project_jobs import (
    DREAM_JOB,
    HEARTBEAT_JOB,
    ensure_project_heartbeat_file,
    project_job_id,
    project_job_specs,
    sync_project_jobs,
)
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronPayload, CronSchedule


def _service(tmp_path: Path) -> CronService:
    return CronService(tmp_path / "cron" / "jobs.json")


def _schedules() -> tuple[CronSchedule, CronSchedule]:
    return (
        CronSchedule(kind="every", every_ms=60_000, tz="UTC"),
        CronSchedule(kind="every", every_ms=7_200_000, tz="UTC"),
    )


def _sync(service: CronService, project_ids: list[str | None]) -> None:
    heartbeat, dream = _schedules()
    sync_project_jobs(service, project_job_specs(
        project_ids, heartbeat_schedule=heartbeat, dream_schedule=dream,
    ))


def test_every_project_gets_its_own_heartbeat_and_dream(tmp_path: Path) -> None:
    service = _service(tmp_path)

    _sync(service, ["project-a", "project-b"])

    jobs = {job.id: job for job in service.list_jobs(include_disabled=True)}
    assert set(jobs) == {
        project_job_id(HEARTBEAT_JOB, "project-a"),
        project_job_id(DREAM_JOB, "project-a"),
        project_job_id(HEARTBEAT_JOB, "project-b"),
        project_job_id(DREAM_JOB, "project-b"),
    }
    heartbeat = jobs[project_job_id(HEARTBEAT_JOB, "project-a")]
    assert (heartbeat.name, heartbeat.payload.kind) == (HEARTBEAT_JOB, "system_event")
    assert heartbeat.payload.project_id == "project-a"


def test_a_deleted_project_loses_its_automations(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _sync(service, ["project-a", "project-b"])

    _sync(service, ["project-a"])

    remaining = [job.payload.project_id for job in service.list_jobs(include_disabled=True)]
    assert remaining == ["project-a", "project-a"]


def test_disabled_automation_leaves_no_job_behind(tmp_path: Path) -> None:
    """A disabled Heartbeat must not keep running from a stale job."""
    service = _service(tmp_path)
    _sync(service, ["project-a"])
    heartbeat, _dream = _schedules()

    sync_project_jobs(service, project_job_specs(
        ["project-a"], heartbeat_schedule=None, dream_schedule=None,
    ))

    assert service.list_jobs(include_disabled=True) == []
    assert heartbeat is not None


def test_an_existing_job_keeps_its_next_run(tmp_path: Path) -> None:
    """Reconciling on every start must not reschedule an unchanged automation."""
    service = _service(tmp_path)
    _sync(service, ["project-a"])
    job_id = project_job_id(DREAM_JOB, "project-a")
    first = service.get_job(job_id)
    assert first is not None

    _sync(service, ["project-a"])

    again = service.get_job(job_id)
    assert again is not None
    assert again.state.next_run_at_ms == first.state.next_run_at_ms


def test_a_changed_schedule_is_applied(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _sync(service, ["project-a"])
    job_id = project_job_id(HEARTBEAT_JOB, "project-a")
    before = service.get_job(job_id)
    assert before is not None
    assert before.schedule.every_ms == 60_000

    _heartbeat, dream = _schedules()
    sync_project_jobs(service, project_job_specs(
        ["project-a"],
        heartbeat_schedule=CronSchedule(kind="every", every_ms=5_000, tz="UTC"),
        dream_schedule=dream,
    ))

    after = service.get_job(job_id)
    assert after is not None
    assert after.schedule.every_ms == 5_000


def test_instance_scope_reuses_the_bare_job_ids(tmp_path: Path) -> None:
    """An install without projects keeps the ids it already had."""
    service = _service(tmp_path)

    _sync(service, [None])

    jobs = {job.id: job for job in service.list_jobs(include_disabled=True)}
    assert set(jobs) == {HEARTBEAT_JOB, DREAM_JOB}
    assert jobs[HEARTBEAT_JOB].payload.project_id is None


def test_project_scope_replaces_the_instance_jobs(tmp_path: Path) -> None:
    """Upgrading must not leave two Heartbeats running."""
    service = _service(tmp_path)
    _sync(service, [None])

    _sync(service, ["project-a"])

    ids = {job.id for job in service.list_jobs(include_disabled=True)}
    assert ids == {
        project_job_id(HEARTBEAT_JOB, "project-a"),
        project_job_id(DREAM_JOB, "project-a"),
    }


def test_reconciliation_leaves_user_automations_alone(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.add_job(
        name="Morning summary",
        schedule=CronSchedule(kind="every", every_ms=3_600_000),
        message="summarize",
    )

    _sync(service, ["project-a"])

    names = {job.name for job in service.list_jobs(include_disabled=True)}
    assert "Morning summary" in names


def test_project_heartbeat_file_is_scaffolded_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = ensure_project_heartbeat_file(workspace)

    assert created is not None and created.name == "HEARTBEAT.md"
    assert "Mikobot" in created.read_text(encoding="utf-8")

    created.write_text("## Active Tasks\n\n- watch the queue\n", encoding="utf-8")
    ensure_project_heartbeat_file(workspace)
    assert created.read_text(encoding="utf-8") == "## Active Tasks\n\n- watch the queue\n"


def test_heartbeat_jobs_persist_their_project(tmp_path: Path) -> None:
    """A restart must route each automation back to its project."""
    service = _service(tmp_path)
    _sync(service, ["project-a"])
    service.stop()

    reloaded = _service(tmp_path)
    reloaded._running = True
    try:
        reloaded._load_store()
    finally:
        reloaded._running = False

    persisted = json.loads((tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8"))
    payloads = {
        job["id"]: job["payload"].get("projectId") for job in persisted["jobs"]
    }
    assert payloads[project_job_id(HEARTBEAT_JOB, "project-a")] == "project-a"
    dream = reloaded.get_job(project_job_id(DREAM_JOB, "project-a"))
    assert dream is not None
    assert dream.payload.project_id == "project-a"


def test_user_jobs_keep_an_empty_project(tmp_path: Path) -> None:
    """Only built-in automations carry a project."""
    service = _service(tmp_path)
    job: CronJob = service.add_job(
        name="Morning summary",
        schedule=CronSchedule(kind="every", every_ms=3_600_000),
        message="summarize",
    )

    assert job.payload.project_id is None
    stored = service.get_job(job.id)
    assert stored is not None
    assert stored.payload == CronPayload(kind="agent_turn", message="summarize")
