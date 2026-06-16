"""Guard against concurrent jobs of the same kind racing on one project."""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job
from .state import JobStatus

log = logging.getLogger("storyforge")

_ACTIVE = {JobStatus.QUEUED.value, JobStatus.RUNNING.value}

# A queued/running job older than this is genuinely stuck (executor died, or the
# broker queue was lost on a full restart). Must exceed the longest real stage —
# premium video across many scenes is the worst case — so we never kill live work.
_ORPHAN_AFTER_MINUTES = 30


def fail_orphaned_jobs(db: Session) -> int:
    """Mark only STALE queued/running jobs as failed; return how many were cleared.

    Called once when the Celery worker boots. A worker-only restart (e.g. deploying
    a code change) keeps the redis broker, so freshly-queued jobs are still pending
    and will be picked up — failing them would kill live work (a bug we hit). Only
    jobs queued/running longer than `_ORPHAN_AFTER_MINUTES` are genuinely stuck and
    safe to fail; that frees the `ensure_*` guards without touching in-flight runs.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=_ORPHAN_AFTER_MINUTES)
    orphaned = db.scalars(
        select(Job).where(Job.status.in_(_ACTIVE), Job.created_at < cutoff)
    ).all()
    for job in orphaned:
        job.status = JobStatus.FAILED.value
        job.error = (
            f"orphaned (queued/running > {_ORPHAN_AFTER_MINUTES} min); "
            "marked failed on worker startup"
        )
    if orphaned:
        db.commit()
        log.warning("recovered %d stale orphaned job(s) at worker startup", len(orphaned))
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


def ensure_project_idle(db: Session, project_id: str) -> None:
    """Raise 409 if ANY generation job is queued/running for the project.

    Every pipeline stage mutates the project's scenes/assets, so two running at once
    (e.g. keyframes while a video job runs, or a revise mid-generation) race on the
    same rows and deadlock in Postgres — which leaves the project in an inconsistent,
    half-generated state. Stages are sequential per project, so we require the project
    to be idle before starting any new generation job.
    """
    active = db.scalars(
        select(Job).where(Job.project_id == project_id, Job.status.in_(_ACTIVE))
    ).first()
    if active:
        raise HTTPException(
            409,
            f"a {active.type} job is already running for this project — "
            "wait for it to finish before starting another stage",
        )
