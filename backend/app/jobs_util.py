"""Guard against concurrent jobs of the same kind racing on one project."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job
from .state import JobStatus

_ACTIVE = {JobStatus.QUEUED.value, JobStatus.RUNNING.value}


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
