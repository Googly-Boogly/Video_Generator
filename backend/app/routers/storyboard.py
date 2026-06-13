"""Storyboard review UI endpoints: edit / reorder / add / delete / regenerate /
per-scene model + audio_mode / conversational revision.

Nothing here costs money — it only edits the stored storyboard.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Job, Project, Scene
from ..models_config import MODEL_ROUTES, Tier, default_video_model, route
from ..schemas import (
    AddSceneRequest,
    JobOut,
    ReorderRequest,
    ReviseRequest,
    SceneOut,
    SceneUpdate,
)
from ..state import JobStatus, JobType, SceneStatus

router = APIRouter(prefix="/api/projects/{project_id}/scenes", tags=["storyboard"])


def _get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    return project


def _get_scene(db: Session, project_id: str, scene_id: str) -> Scene:
    scene = db.get(Scene, scene_id)
    if not scene or scene.project_id != project_id:
        raise HTTPException(404, "scene not found")
    return scene


def _renumber(project: Project) -> None:
    for i, sc in enumerate(sorted(project.scenes, key=lambda s: s.scene_number), start=1):
        sc.scene_number = i


@router.get("", response_model=list[SceneOut])
def list_scenes(project_id: str, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    return sorted(project.scenes, key=lambda s: s.scene_number)


@router.get("/{scene_id}", response_model=SceneOut)
def get_scene(project_id: str, scene_id: str, db: Session = Depends(get_db)):
    return _get_scene(db, project_id, scene_id)


@router.patch("/{scene_id}", response_model=SceneOut)
def update_scene(project_id: str, scene_id: str, payload: SceneUpdate, db: Session = Depends(get_db)):
    scene = _get_scene(db, project_id, scene_id)
    data = payload.model_dump(exclude_unset=True)

    if "model_override" in data and data["model_override"]:
        if data["model_override"] not in MODEL_ROUTES:
            raise HTTPException(400, f"unknown model: {data['model_override']}")

    for key, value in data.items():
        setattr(scene, key, value)

    # If audio_mode flipped to dialogue and no override is set, suggest a
    # lip-sync capable model.
    if data.get("audio_mode") == "dialogue" and not scene.model_override:
        m = route(scene.suggested_model) if scene.suggested_model in MODEL_ROUTES else None
        if not m or not m.lip_sync:
            scene.suggested_model = default_video_model(Tier.PREMIUM, "dialogue")

    db.add(scene)
    db.commit()
    db.refresh(scene)
    return scene


@router.delete("/{scene_id}", status_code=204)
def delete_scene(project_id: str, scene_id: str, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    scene = _get_scene(db, project_id, scene_id)
    db.delete(scene)
    db.flush()
    db.refresh(project)
    _renumber(project)
    db.commit()


@router.post("/reorder", response_model=list[SceneOut])
def reorder_scenes(project_id: str, payload: ReorderRequest, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    by_id = {s.id: s for s in project.scenes}
    if set(payload.scene_ids) != set(by_id):
        raise HTTPException(400, "scene_ids must be exactly the project's scenes")
    for i, sid in enumerate(payload.scene_ids, start=1):
        by_id[sid].scene_number = i
    db.commit()
    db.refresh(project)
    return sorted(project.scenes, key=lambda s: s.scene_number)


@router.post("", response_model=SceneOut, status_code=201)
def add_scene(project_id: str, payload: AddSceneRequest, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    scenes = sorted(project.scenes, key=lambda s: s.scene_number)
    insert_after = payload.after_scene_number if payload.after_scene_number is not None else len(scenes)

    # Shift later scenes down to make room.
    for sc in scenes:
        if sc.scene_number > insert_after:
            sc.scene_number += 1

    new_scene = Scene(
        project_id=project.id,
        scene_number=insert_after + 1,
        duration_seconds=5.0,
        shot_description="New scene",
        camera_movement="static",
        image_prompt="",
        video_prompt="",
        narration_text="",
        audio_mode="narrated",
        suggested_model=default_video_model(Tier.PREMIUM, "narrated"),
        status=SceneStatus.PENDING.value,
    )
    db.add(new_scene)
    db.commit()
    db.refresh(new_scene)
    return new_scene


@router.post("/revise", response_model=JobOut, status_code=202)
def revise_storyboard(project_id: str, payload: ReviseRequest, db: Session = Depends(get_db)):
    """Conversational revision ('make scene 3 moodier') — patches via the LLM."""
    from ..tasks import revise_storyboard_task
    from ..jobs_util import ensure_no_active_job

    project = _get_project(db, project_id)
    ensure_no_active_job(db, project.id, [JobType.STORYBOARD.value, JobType.STORYBOARD_REVISE.value])
    job = Job(
        project_id=project.id,
        type=JobType.STORYBOARD_REVISE.value,
        status=JobStatus.QUEUED.value,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    async_result = revise_storyboard_task.delay(project.id, job.id, payload.instruction)
    job.celery_task_id = async_result.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
