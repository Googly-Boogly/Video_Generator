"""Phase 5: AI editor (EDL) + draft/final render + preview/export."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset, Job, Project
from ..schemas import AssetOut, JobOut
from ..state import JobStatus, JobType

router = APIRouter(prefix="/api/projects/{project_id}", tags=["render"])


def _project(db: Session, project_id: str) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def _asset_out(a: Asset) -> AssetOut:
    return AssetOut(
        id=a.id, project_id=a.project_id, scene_id=a.scene_id, kind=a.kind,
        content_type=a.content_type, meta=a.meta, url=f"/api/assets/{a.id}/content",
    )


@router.post("/edl", response_model=JobOut, status_code=202)
def build_edl(project_id: str, db: Session = Depends(get_db)):
    """Generate the Edit Decision List from the storyboard + frames + audio."""
    from ..tasks import build_edl_task

    from ..jobs_util import ensure_project_idle

    project = _project(db, project_id)
    if not any(s.clip_asset_id for s in project.scenes):
        raise HTTPException(400, "no clips — generate video first")
    ensure_project_idle(db, project.id)

    job = Job(project_id=project.id, type=JobType.EDIT.value, status=JobStatus.QUEUED.value)
    db.add(job)
    db.commit()
    db.refresh(job)
    res = build_edl_task.delay(project.id, job.id)
    job.celery_task_id = res.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/edl")
def get_edl(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    if not project.edl:
        raise HTTPException(404, "no EDL yet")
    return project.edl


@router.post("/render", response_model=JobOut, status_code=202)
def render(project_id: str, final: bool = False, db: Session = Depends(get_db)):
    """Render the EDL. `final=false` → 480p watermarked draft; `final=true` →
    regenerate hero scenes at premium + render 1080p."""
    from ..tasks import render_task

    project = _project(db, project_id)
    if not project.edl:
        raise HTTPException(400, "no EDL — run the editor first")

    from ..jobs_util import ensure_project_idle
    ensure_project_idle(db, project.id)

    jtype = JobType.RENDER_FINAL.value if final else JobType.RENDER_DRAFT.value
    job = Job(project_id=project.id, type=jtype, status=JobStatus.QUEUED.value)
    db.add(job)
    db.commit()
    db.refresh(job)
    res = render_task.delay(project.id, job.id, final)
    job.celery_task_id = res.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/renders", response_model=list[AssetOut])
def list_renders(project_id: str, db: Session = Depends(get_db)):
    """Draft + final render outputs for the in-browser player and download."""
    project = _project(db, project_id)
    renders = [a for a in project.assets if a.kind in ("draft", "final")]
    renders.sort(key=lambda a: a.created_at)
    return [_asset_out(a) for a in renders]
