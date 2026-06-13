"""Phase 3: video generation (animate winners) + quality gate + frames."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset, Job, Project, Scene
from ..schemas import AssetOut, JobOut
from ..state import JobStatus, JobType

router = APIRouter(prefix="/api/projects/{project_id}", tags=["video"])


def _project(db: Session, project_id: str) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def _scene(db: Session, project_id: str, scene_id: str) -> Scene:
    s = db.get(Scene, scene_id)
    if not s or s.project_id != project_id:
        raise HTTPException(404, "scene not found")
    return s


def _asset_out(a: Asset) -> AssetOut:
    return AssetOut(
        id=a.id, project_id=a.project_id, scene_id=a.scene_id, kind=a.kind,
        content_type=a.content_type, meta=a.meta, url=f"/api/assets/{a.id}/content",
    )


def _enqueue(db: Session, project: Project, tier: str, scene_id: str | None) -> Job:
    from ..tasks import generate_video_task
    from ..jobs_util import ensure_no_active_job

    ensure_no_active_job(db, project.id, [JobType.VIDEO.value])
    job = Job(project_id=project.id, type=JobType.VIDEO.value,
              status=JobStatus.QUEUED.value, scene_id=scene_id)
    db.add(job)
    db.commit()
    db.refresh(job)
    res = generate_video_task.delay(project.id, job.id, tier, scene_id)
    job.celery_task_id = res.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/video", response_model=JobOut, status_code=202)
def generate_video(project_id: str, tier: str = "draft", db: Session = Depends(get_db)):
    """Animate every scene's winning keyframe into a clip (+ quality gate)."""
    project = _project(db, project_id)
    if not any(s.keyframe_asset_id for s in project.scenes):
        raise HTTPException(400, "no winning keyframes — generate keyframes first")
    return _enqueue(db, project, tier, None)


@router.post("/scenes/{scene_id}/video", response_model=JobOut, status_code=202)
def regenerate_scene_video(project_id: str, scene_id: str, tier: str = "draft",
                           db: Session = Depends(get_db)):
    """Regenerate one scene's clip — used by 'one-click regenerate' on flagged clips."""
    project = _project(db, project_id)
    scene = _scene(db, project_id, scene_id)
    if not scene.keyframe_asset_id:
        raise HTTPException(400, "scene has no winning keyframe")
    return _enqueue(db, project, tier, scene_id)


@router.get("/scenes/{scene_id}/frames", response_model=list[AssetOut])
def list_scene_frames(project_id: str, scene_id: str, db: Session = Depends(get_db)):
    """The quality-gate frames extracted from the scene's clip."""
    project = _project(db, project_id)
    frames = [a for a in project.assets if a.kind == "frame" and a.scene_id == scene_id]
    frames.sort(key=lambda a: (a.meta or {}).get("frame_index", 0))
    return [_asset_out(a) for a in frames]
