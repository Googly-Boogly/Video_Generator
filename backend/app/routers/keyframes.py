"""Phase 2: keyframe generation (best-of-N) + reference images + winner select."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset, Job, Project, Scene
from ..schemas import AssetOut, JobOut, SelectKeyframeRequest, SceneOut
from ..state import JobStatus, JobType

router = APIRouter(prefix="/api/projects/{project_id}", tags=["keyframes"])


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


def _enqueue_keyframes(db: Session, project: Project, scene_id: str | None) -> Job:
    from ..tasks import generate_keyframes_task
    from ..jobs_util import ensure_project_idle

    ensure_project_idle(db, project.id)
    job = Job(project_id=project.id, type=JobType.KEYFRAMES.value,
              status=JobStatus.QUEUED.value, scene_id=scene_id)
    db.add(job)
    db.commit()
    db.refresh(job)
    res = generate_keyframes_task.delay(project.id, job.id, scene_id)
    job.celery_task_id = res.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/keyframes", response_model=JobOut, status_code=202)
def generate_keyframes(project_id: str, db: Session = Depends(get_db)):
    """Generate reference images (once) + best-of-N keyframes for all scenes."""
    project = _project(db, project_id)
    if not project.scenes:
        raise HTTPException(400, "storyboard has no scenes")
    return _enqueue_keyframes(db, project, None)


@router.post("/scenes/{scene_id}/keyframes", response_model=JobOut, status_code=202)
def regenerate_scene_keyframes(project_id: str, scene_id: str, db: Session = Depends(get_db)):
    """Regenerate the best-of-N keyframes for a single scene."""
    project = _project(db, project_id)
    scene = db.get(Scene, scene_id)
    if not scene or scene.project_id != project_id:
        raise HTTPException(404, "scene not found")
    return _enqueue_keyframes(db, project, scene_id)


@router.get("/references", response_model=list[AssetOut])
def list_references(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    refs = [a for a in project.assets if a.kind == "reference"]
    return [_asset_out(a) for a in refs]


@router.get("/scenes/{scene_id}/keyframes", response_model=list[AssetOut])
def list_scene_keyframes(project_id: str, scene_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    kfs = [a for a in project.assets if a.kind == "keyframe" and a.scene_id == scene_id]
    kfs.sort(key=lambda a: (a.meta or {}).get("variant_index", 0))
    return [_asset_out(a) for a in kfs]


@router.post("/scenes/{scene_id}/keyframe/select", response_model=SceneOut)
def select_keyframe(project_id: str, scene_id: str, payload: SelectKeyframeRequest, db: Session = Depends(get_db)):
    """User overrides the auto-ranked winner for a scene."""
    project = _project(db, project_id)
    scene = db.get(Scene, scene_id)
    if not scene or scene.project_id != project_id:
        raise HTTPException(404, "scene not found")
    chosen = db.get(Asset, payload.asset_id)
    if not chosen or chosen.scene_id != scene_id or chosen.kind != "keyframe":
        raise HTTPException(400, "asset is not a keyframe for this scene")

    # Flip is_winner flags across the scene's keyframes.
    for a in [a for a in project.assets if a.kind == "keyframe" and a.scene_id == scene_id]:
        meta = dict(a.meta or {})
        meta["is_winner"] = a.id == chosen.id
        a.meta = meta
        db.add(a)
    scene.keyframe_asset_id = chosen.id
    db.add(scene)
    db.commit()
    db.refresh(scene)
    return scene
