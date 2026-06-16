"""Phase 4: audio build — voices, music bed + beat grid, narration, mix plan."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..asset_store import store_asset
from ..database import get_db
from ..models import Asset, Job, Project
from ..pipeline import audio as a_stage
from ..schemas import AssetOut, JobOut, MusicLibrarySelect, VoiceSelect
from ..state import JobStatus, JobType

router = APIRouter(prefix="/api", tags=["audio"])


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


# --- Catalogs ---------------------------------------------------------------

@router.get("/voices")
def list_voices():
    return {"voices": a_stage.list_voices(), "default": a_stage.DEFAULT_VOICE_ID}


@router.get("/music/library")
def music_library():
    return {"tracks": a_stage.MUSIC_LIBRARY}


# --- Project voice ----------------------------------------------------------

@router.post("/projects/{project_id}/voice")
def set_voice(project_id: str, payload: VoiceSelect, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    valid = {v["voice_id"] for v in a_stage.list_voices()}
    if payload.voice_id not in valid:
        raise HTTPException(400, "unknown voice_id")
    project.voice_id = payload.voice_id
    db.add(project)
    db.commit()
    return {"voice_id": project.voice_id}


# --- Music bed --------------------------------------------------------------

def _replace_music(db: Session, project: Project, data: bytes, content_type: str, meta: dict) -> Asset:
    for a in [a for a in project.assets if a.kind == "music"]:
        project.assets.remove(a)  # delete-orphan; keeps the collection consistent
    db.flush()
    suffix = ".mp3" if ("mpeg" in content_type or "mp3" in content_type) else ".wav"
    grid = a_stage.beat_grid(audio_bytes=data, suffix=suffix, bpm_hint=meta.get("bpm"))
    meta = {**meta, "beat_grid": grid}
    asset = store_asset(db, project.id, None, "music", data, content_type, meta=meta)
    db.commit()
    return asset


@router.get("/projects/{project_id}/music", response_model=AssetOut | None)
def get_music(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    music = next((a for a in project.assets if a.kind == "music"), None)
    return _asset_out(music) if music else None


@router.post("/projects/{project_id}/music", response_model=AssetOut, status_code=201)
async def upload_music(project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a music bed; beat grid is detected (librosa) immediately."""
    project = _project(db, project_id)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    content_type = file.content_type or "audio/mpeg"
    asset = _replace_music(db, project, data, content_type, {"source": "upload", "name": file.filename})
    return _asset_out(asset)


@router.post("/projects/{project_id}/music/library", response_model=AssetOut, status_code=201)
def pick_library_music(project_id: str, payload: MusicLibrarySelect, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    try:
        data, track = a_stage.synth_library_bed(track_id=payload.track_id)
    except KeyError:
        raise HTTPException(404, "unknown library track")
    asset = _replace_music(
        db, project, data, "audio/mpeg",
        {"source": "library", "name": track["name"], "bpm": track["bpm"], "style": track["style"]},
    )
    return _asset_out(asset)


@router.delete("/projects/{project_id}/music", status_code=204)
def remove_music(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    for a in [a for a in project.assets if a.kind == "music"]:
        project.assets.remove(a)  # delete-orphan
    db.commit()


# --- Narration / build ------------------------------------------------------

@router.get("/projects/{project_id}/narration", response_model=list[AssetOut])
def list_narration(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    narr = [a for a in project.assets if a.kind == "narration"]
    return [_asset_out(a) for a in narr]


@router.get("/projects/{project_id}/mix-plan")
def mix_plan(project_id: str, db: Session = Depends(get_db)):
    """The per-scene narration/music/native mix the render will apply."""
    project = _project(db, project_id)
    plans = []
    for s in sorted(project.scenes, key=lambda s: s.scene_number):
        native = next((a for a in project.assets if a.kind == "native_audio" and a.scene_id == s.id), None)
        muted = bool((native.meta or {}).get("muted")) if native else False
        plans.append({
            "scene_number": s.scene_number,
            "audio_mode": s.audio_mode,
            "mix": a_stage.mix_plan(audio_mode=s.audio_mode, native_muted=muted),
        })
    return {
        "levels": {"narration_db": a_stage.NARRATION_GAIN_DB,
                   "native_db": a_stage.NATIVE_DUCK_DB, "music_db": a_stage.MUSIC_BED_DB},
        "scenes": plans,
    }


def _enqueue(db: Session, project: Project, scene_id: str | None) -> Job:
    from ..tasks import build_audio_task
    from ..jobs_util import ensure_project_idle

    ensure_project_idle(db, project.id)
    job = Job(project_id=project.id, type=JobType.AUDIO.value,
              status=JobStatus.QUEUED.value, scene_id=scene_id)
    db.add(job)
    db.commit()
    db.refresh(job)
    res = build_audio_task.delay(project.id, job.id, scene_id)
    job.celery_task_id = res.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/projects/{project_id}/audio", response_model=JobOut, status_code=202)
def build_audio(project_id: str, db: Session = Depends(get_db)):
    """Synthesize narration for every narrated scene + ensure the beat grid."""
    project = _project(db, project_id)
    if not project.scenes:
        raise HTTPException(400, "no scenes")
    return _enqueue(db, project, None)


@router.post("/projects/{project_id}/scenes/{scene_id}/narration", response_model=JobOut, status_code=202)
def regenerate_scene_narration(project_id: str, scene_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    scene = next((s for s in project.scenes if s.id == scene_id), None)
    if not scene:
        raise HTTPException(404, "scene not found")
    return _enqueue(db, project, scene_id)
