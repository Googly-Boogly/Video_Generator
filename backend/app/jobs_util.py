"""Guard against concurrent jobs of the same kind racing on one project."""
from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job
from .state import JobStatus

log = logging.getLogger("storyforge")

_ACTIVE = {JobStatus.QUEUED.value, JobStatus.RUNNING.value}


def fail_orphaned_jobs(db: Session) -> int:
    """Mark every queued/running job as failed and return how many were cleared.

    Called once when the Celery worker boots. The broker (redis) is in-memory with
    no volume, so a fresh worker means the queue is empty — any job still marked
    queued/running was orphaned by a crash/restart and will never be processed. Left
    alone it blocks its stage forever via `ensure_no_active_job` (HTTP 409). Clearing
    it releases the guard so the stage can simply be retried.
    """
    orphaned = db.scalars(select(Job).where(Job.status.in_(_ACTIVE))).all()
    for job in orphaned:
        job.status = JobStatus.FAILED.value
        job.error = "orphaned by a restart (broker queue lost); marked failed on worker startup"
    if orphaned:
        db.commit()
        log.warning("recovered %d orphaned job(s) at worker startup", len(orphaned))
    return len(orphaned)


def ensure_no_active_job(db: Session, project_id: str, types: list[str]) -> None:
    """Raise 409 if a job of one of `types` is already queued/running for the
    project — prevents double-kick races (e.g. two video jobs on the same scenes).
    """
    active = db.scalars(
        select(Job).where(
            Job.project_id == project_id,
            Job.type.in_(types),
            Job.status.in_(_ACTIVE),
        )
    ).first()
    if active:
        raise HTTPException(409, f"a {active.type} job is already in progress for this project")
