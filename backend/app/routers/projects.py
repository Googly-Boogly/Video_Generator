"""Project CRUD + pipeline kickoff endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Job, Project
from ..schemas import (
    CostEstimate,
    JobOut,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
)
from ..state import JobStatus, JobType
from .. import cost as cost_mod
from ..models_config import Tier

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.scalars(select(Project).order_by(Project.created_at.desc())).all()


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(
        idea=payload.idea,
        title=payload.title or payload.idea[:60],
        target_length=payload.target_length,
        aspect_ratio=payload.aspect_ratio,
        style_preset=payload.style_preset,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    db.delete(project)
    db.commit()


@router.post("/{project_id}/storyboard", response_model=JobOut, status_code=202)
def kick_off_storyboard(project_id: str, db: Session = Depends(get_db)):
    """Generate (or regenerate) the style bible + storyboard asynchronously."""
    from ..tasks import generate_storyboard_task

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    job = Job(project_id=project.id, type=JobType.STORYBOARD.value, status=JobStatus.QUEUED.value)
    db.add(job)
    db.commit()
    db.refresh(job)

    async_result = generate_storyboard_task.delay(project.id, job.id)
    job.celery_task_id = async_result.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{project_id}/cost", response_model=CostEstimate)
def project_cost(project_id: str, tier: str = "premium", db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    tier_enum = Tier.PREMIUM if tier == "premium" else Tier.DRAFT
    return cost_mod.estimate_full_project(project, tier_enum)


@router.get("/{project_id}/costs")
def project_cost_dashboard(project_id: str, db: Session = Depends(get_db)):
    """Cost dashboard: pre-flight estimate vs the actual-run ledger, by step."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    return cost_mod.dashboard(project)
