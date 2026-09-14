"""Built-in automations that every project owns.

Heartbeat and Dream belong to a project rather than to the instance: each project
reads its own ``HEARTBEAT.md`` and consolidates its own memory. Both are protected
system jobs, so no caller can delete or edit them while the project exists, and
deleting a project deletes its jobs with it.

The schedule stays owned by configuration (``agents.defaults.dream`` and
``gateway.heartbeat``); this module only reconciles the stored jobs against the
projects that exist.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.collaboration.models import Project
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronPayload, CronSchedule

HEARTBEAT_JOB = "heartbeat"
DREAM_JOB = "dream"

HEARTBEAT_FILE = "HEARTBEAT.md"


@dataclass(frozen=True, slots=True)
class ProjectJobSpec:
    """One built-in automation.

    ``project_id`` is ``None`` for the instance itself, which is what an install
    without projects has always run.
    """

    kind: str
    project_id: str | None
    schedule: CronSchedule


def project_job_id(kind: str, project_id: str | None) -> str:
    """Return the stable id of a built-in automation.

    An instance-scoped automation keeps the bare name used before automations
    became project-owned, so an upgrade reuses the job instead of duplicating it.
    """
    return kind if project_id is None else f"{kind}:{project_id}"


async def projects_for_jobs(repository: object | None, workspace: str | Path) -> list[Project]:
    """Return every project the built-in automations must cover.

    The repository is optional: a gateway without collaboration still runs the
    instance's own automations, so the built-in project is created on demand.
    """
    ensure_owner = getattr(repository, "ensure_local_owner", None)
    list_projects = getattr(repository, "list_all_projects", None)
    if ensure_owner is None or list_projects is None:
        return []
    try:
        owner, _default_project = await ensure_owner(workspace)
        return list(await list_projects(owner.id))
    except Exception:
        logger.exception("Cron: could not list projects for the built-in automations")
        return []


def schedules_from_config(config: Any) -> tuple[CronSchedule | None, CronSchedule | None]:
    """Return the configured (heartbeat, dream) schedules; ``None`` disables one.

    The schedules belong to configuration, not to a project, so every project's
    built-in automation follows the same cadence.
    """
    heartbeat_cfg = config.gateway.heartbeat
    dream_cfg = config.agents.defaults.dream
    timezone = config.agents.defaults.timezone
    heartbeat_schedule = (
        CronSchedule(kind="every", every_ms=heartbeat_cfg.interval_s * 1000, tz=timezone)
        if heartbeat_cfg.enabled else None
    )
    dream_schedule = dream_cfg.build_schedule(timezone) if dream_cfg.enabled else None
    return heartbeat_schedule, dream_schedule


async def sync_projects_automations(
    cron: CronService,
    repository: object | None,
    workspace: str | Path,
    *,
    heartbeat_schedule: CronSchedule | None,
    dream_schedule: CronSchedule | None,
) -> list[Project]:
    """Give every project its built-in Heartbeat and Dream.

    Called on gateway start and after a project is created or deleted. Returns the
    projects that were reconciled.
    """
    projects = await projects_for_jobs(repository, workspace)
    # Without projects the instance itself is the only scope, which keeps a
    # single-user install's Heartbeat and Dream running as before.
    scopes: list[str | None] = [project.id for project in projects] or [None]
    sync_project_jobs(cron, project_job_specs(
        scopes,
        heartbeat_schedule=heartbeat_schedule,
        dream_schedule=dream_schedule,
    ))
    for project in projects:
        await asyncio.to_thread(ensure_project_heartbeat_file, project.workspace_path)
    if not projects:
        await asyncio.to_thread(ensure_project_heartbeat_file, workspace)
    return projects


def project_job_specs(
    project_ids: Iterable[str | None],
    *,
    heartbeat_schedule: CronSchedule | None,
    dream_schedule: CronSchedule | None,
) -> list[ProjectJobSpec]:
    """Return every built-in automation the given scopes should have."""
    specs: list[ProjectJobSpec] = []
    for project_id in project_ids:
        if heartbeat_schedule is not None:
            specs.append(ProjectJobSpec(HEARTBEAT_JOB, project_id, heartbeat_schedule))
        if dream_schedule is not None:
            specs.append(ProjectJobSpec(DREAM_JOB, project_id, dream_schedule))
    return specs


def sync_project_jobs(cron: CronService, specs: Sequence[ProjectJobSpec]) -> None:
    """Make the stored built-in automations match *specs*.

    Missing jobs are created, jobs whose schedule configuration changed are
    re-registered, and jobs of projects that no longer exist are removed. Jobs
    that already match are left untouched so their next run is not rescheduled.
    """
    wanted = {project_job_id(spec.kind, spec.project_id): spec for spec in specs}
    for job_id, spec in wanted.items():
        existing = cron.get_job(job_id)
        if existing is not None and _same_schedule(existing.schedule, spec.schedule):
            continue
        cron.register_system_job(CronJob(
            id=job_id,
            name=spec.kind,
            schedule=spec.schedule,
            payload=CronPayload(kind="system_event", project_id=spec.project_id),
        ))
    # The reconciler owns every protected system job: anything it does not want
    # right now is either a disabled automation or an id from the release that
    # stored automations before they became project-owned.
    for job in cron.list_jobs(include_disabled=True):
        if job.payload.kind != "system_event" or job.id in wanted:
            continue
        cron.remove_system_job(job.id)


def ensure_project_heartbeat_file(workspace: str | Path) -> Path | None:
    """Create the project's ``HEARTBEAT.md`` from the bundled default if missing.

    Returns the file path, or ``None`` when the bundled default is unavailable.
    """
    from nanobot.utils.helpers import load_bundled_template

    template = load_bundled_template(HEARTBEAT_FILE)
    if template is None:
        return None
    path = Path(workspace) / HEARTBEAT_FILE
    if path.exists():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template, encoding="utf-8")
    except OSError:
        logger.exception("Heartbeat: could not create {}", path)
        return None
    logger.info("Heartbeat: created {} for the project", path)
    return path


def _same_schedule(left: CronSchedule, right: CronSchedule) -> bool:
    return (
        left.kind == right.kind
        and left.at_ms == right.at_ms
        and left.every_ms == right.every_ms
        and left.expr == right.expr
        and left.tz == right.tz
    )
