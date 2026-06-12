"""Celery tasks. Every generation step is an async task; the API only enqueues.

A failed scene never kills the project — scene-level tasks isolate failures and
mark the individual scene FAILED while the rest proceed.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from .celery_app import celery_app
from .database import SessionLocal
from .models import Asset, Job, Project, Scene
from .state import JobStatus, ProjectStatus, SceneStatus

log = logging.getLogger("storyforge.tasks")


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _set_job(db, job_id: str, *, status: str | None = None, progress: float | None = None,
             result: dict | None = None, error: str | None = None) -> None:
    job = db.get(Job, job_id)
    if not job:
        return
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = progress
    if result is not None:
        job.result = result
    if error is not None:
        job.error = error
    db.add(job)
    db.flush()


# ---------------------------------------------------------------------------
# Phase 1: style bible + storyboard
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="storyforge.generate_storyboard")
def generate_storyboard_task(self, project_id: str, job_id: str) -> dict:
    from .pipeline import storyboard as sb_stage
    from .pipeline import style_bible as style_stage

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="project not found")
            return {"ok": False}

        _set_job(db, job_id, status=JobStatus.RUNNING.value, progress=0.1)

        try:
            # 1) Style bible (locked style document).
            if not project.style_bible:
                project.style_bible = style_stage.generate_style_bible(
                    idea=project.idea,
                    style_preset=project.style_preset,
                    aspect_ratio=project.aspect_ratio,
                )
                project.status = ProjectStatus.STYLED.value
                db.add(project)
                db.flush()
            _set_job(db, job_id, progress=0.4)

            # 2) Storyboard (validated).
            board = sb_stage.generate_storyboard(
                idea=project.idea,
                target_length=project.target_length,
                aspect_ratio=project.aspect_ratio,
                style_preset=project.style_preset,
                style_bible=project.style_bible,
            )

            # 3) Replace scenes.
            _write_scenes(db, project, board)
            project.status = ProjectStatus.STORYBOARDED.value
            db.add(project)
            db.flush()

            _set_job(db, job_id, status=JobStatus.SUCCESS.value, progress=1.0,
                     result={"scene_count": len(board.scenes)})
            return {"ok": True, "scene_count": len(board.scenes)}
        except Exception as exc:  # noqa: BLE001
            log.exception("storyboard generation failed")
            _set_job(db, job_id, status=JobStatus.FAILED.value, error=str(exc))
            return {"ok": False, "error": str(exc)}


@celery_app.task(bind=True, name="storyforge.revise_storyboard")
def revise_storyboard_task(self, project_id: str, job_id: str, instruction: str) -> dict:
    from .pipeline import storyboard as sb_stage

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="project not found")
            return {"ok": False}

        _set_job(db, job_id, status=JobStatus.RUNNING.value, progress=0.2)
        try:
            current = _scenes_to_dict(project)
            board = sb_stage.revise_storyboard(
                instruction=instruction,
                storyboard=current,
                style_bible=project.style_bible,
            )
            _write_scenes(db, project, board)
            project.status = ProjectStatus.STORYBOARDED.value
            db.add(project)
            db.flush()
            _set_job(db, job_id, status=JobStatus.SUCCESS.value, progress=1.0,
                     result={"scene_count": len(board.scenes)})
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            log.exception("storyboard revision failed")
            _set_job(db, job_id, status=JobStatus.FAILED.value, error=str(exc))
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 2: master reference images + best-of-N keyframes
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="storyforge.generate_keyframes")
def generate_keyframes_task(self, project_id: str, job_id: str, scene_id: str | None = None) -> dict:
    """Generate master reference images (once) + best-of-N keyframes per scene.

    If `scene_id` is given, only that scene is (re)generated. A failed scene is
    isolated — it is marked FAILED and the rest proceed.
    """
    from .pipeline import keyframes as kf_stage
    from .pipeline import style_bible as style_stage

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="project not found")
            return {"ok": False}
        if not project.style_bible:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="no style bible yet")
            return {"ok": False}

        _set_job(db, job_id, status=JobStatus.RUNNING.value, progress=0.05)

        # 1) Master reference images — generated once, reused for every keyframe.
        reference_urls = _ensure_reference_images(db, project, style_stage)
        char_sheet = (project.style_bible or {}).get("character_sheet")
        _set_job(db, job_id, progress=0.15)

        # 2) Keyframes per scene.
        scenes = (
            [s for s in project.scenes if s.id == scene_id]
            if scene_id else sorted(project.scenes, key=lambda s: s.scene_number)
        )
        if not scenes:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="no scenes to process")
            return {"ok": False}

        done = failed = 0
        for i, scene in enumerate(scenes):
            try:
                _keyframes_for_scene(db, project, scene, kf_stage, reference_urls, char_sheet)
                done += 1
            except Exception as exc:  # noqa: BLE001 — isolate per-scene failures
                log.exception("keyframes failed for scene %s", scene.scene_number)
                scene.status = SceneStatus.FAILED.value
                scene.error = str(exc)
                db.add(scene)
                failed += 1
            db.flush()
            _set_job(db, job_id, progress=0.15 + 0.8 * (i + 1) / len(scenes))

        # Project advances to keyframes once at least one scene has a winner.
        if not scene_id and any(s.keyframe_asset_id for s in project.scenes):
            project.status = ProjectStatus.KEYFRAMES.value
            db.add(project)

        _set_job(db, job_id, status=JobStatus.SUCCESS.value, progress=1.0,
                 result={"scenes_done": done, "scenes_failed": failed})
        return {"ok": True, "scenes_done": done, "scenes_failed": failed}


def _ensure_reference_images(db, project: Project, style_stage) -> list[str]:
    """Generate master reference images if absent; return their public URLs."""
    from .storage import public_url

    existing = [a for a in project.assets if a.kind == "reference"]
    if existing:
        return [public_url(a.storage_key) for a in existing]

    refs = style_stage.generate_reference_images(
        style_bible=project.style_bible, aspect_ratio=project.aspect_ratio,
    )
    urls: list[str] = []
    for ref in refs:
        asset = _store_asset(
            db, project.id, None, "reference", ref.image_bytes, ref.media_type,
            meta={"role": ref.role, "prompt": ref.prompt, "seed": ref.seed},
        )
        urls.append(public_url(asset.storage_key))
    db.flush()
    return urls


def _keyframes_for_scene(db, project: Project, scene: Scene, kf_stage, reference_urls, char_sheet) -> None:
    scene.status = SceneStatus.GENERATING.value
    scene.error = None
    db.add(scene)
    db.flush()

    # Clear any previous keyframe assets for this scene (idempotent re-runs).
    for a in [a for a in project.assets if a.kind == "keyframe" and a.scene_id == scene.id]:
        db.delete(a)
    db.flush()

    scene_dict = {
        "scene_number": scene.scene_number,
        "shot_description": scene.shot_description,
        "image_prompt": scene.image_prompt,
    }
    variants = kf_stage.generate_keyframes(
        scene=scene_dict, style_bible=project.style_bible,
        aspect_ratio=project.aspect_ratio, reference_urls=reference_urls,
    )
    ranking = kf_stage.rank_keyframes(variants, scene=scene_dict, character_sheet=char_sheet)
    score_by_index = {s["index"]: s for s in ranking.get("scores", [])}
    winner_index = ranking.get("winner", 0)

    winner_asset_id = None
    for v in variants:
        info = score_by_index.get(v.index, {})
        asset = _store_asset(
            db, project.id, scene.id, "keyframe", v.image_bytes, v.media_type,
            meta={
                "variant_index": v.index,
                "seed": v.seed,
                "score": info.get("score"),
                "reason": info.get("reason"),
                "is_winner": v.index == winner_index,
                "auto_winner": v.index == winner_index,
            },
        )
        if v.index == winner_index:
            winner_asset_id = asset.id

    scene.keyframe_asset_id = winner_asset_id
    scene.status = SceneStatus.DONE.value
    db.add(scene)
    db.flush()


def _store_asset(db, project_id, scene_id, kind, data: bytes, content_type, meta=None) -> Asset:
    """Put bytes in MinIO and create the Asset row pointing at them."""
    from .storage import put_bytes

    asset = Asset(
        project_id=project_id, scene_id=scene_id, kind=kind,
        content_type=content_type, meta=meta or {},
    )
    ext = "png" if "png" in content_type else ("jpg" if "jpeg" in content_type else "bin")
    key = f"projects/{project_id}/{kind}/{asset.id}.{ext}"
    put_bytes(key, data, content_type)
    asset.storage_key = key
    db.add(asset)
    db.flush()
    return asset


# ---------------------------------------------------------------------------
# Phase 3: video generation + quality gate
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="storyforge.generate_video")
def generate_video_task(self, project_id: str, job_id: str, tier: str = "draft",
                        scene_id: str | None = None) -> dict:
    """Animate winning keyframes into clips, demux native audio, run the quality
    gate. Per-scene failure is isolated. `scene_id` regenerates one scene."""
    from .models_config import Tier
    from .pipeline import quality as q_stage
    from .pipeline import video as v_stage
    from .storage import get_bytes, public_url

    tier_enum = Tier.PREMIUM if tier == "premium" else Tier.DRAFT

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="project not found")
            return {"ok": False}

        _set_job(db, job_id, status=JobStatus.RUNNING.value, progress=0.05)
        char_sheet = (project.style_bible or {}).get("character_sheet")
        ref_urls = [public_url(a.storage_key) for a in project.assets if a.kind == "reference"]

        scenes = (
            [s for s in project.scenes if s.id == scene_id]
            if scene_id else sorted(project.scenes, key=lambda s: s.scene_number)
        )
        if not scenes:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="no scenes to process")
            return {"ok": False}

        done = failed = flagged = 0
        for i, scene in enumerate(scenes):
            try:
                state = _clip_for_scene(
                    db, project, scene, v_stage, q_stage, tier_enum,
                    char_sheet, ref_urls, get_bytes, public_url,
                )
                done += 1
                flagged += 1 if state == "flagged" else 0
            except Exception as exc:  # noqa: BLE001 — isolate per-scene failures
                log.exception("video failed for scene %s", scene.scene_number)
                scene.status = SceneStatus.FAILED.value
                scene.error = str(exc)
                db.add(scene)
                failed += 1
            db.flush()
            _set_job(db, job_id, progress=0.05 + 0.9 * (i + 1) / len(scenes))

        if not scene_id and any(s.clip_asset_id for s in project.scenes):
            project.status = ProjectStatus.CLIPS.value
            db.add(project)

        _set_job(db, job_id, status=JobStatus.SUCCESS.value, progress=1.0,
                 result={"scenes_done": done, "scenes_failed": failed, "scenes_flagged": flagged})
        return {"ok": True, "scenes_done": done, "scenes_failed": failed, "scenes_flagged": flagged}


def _clip_for_scene(db, project, scene, v_stage, q_stage, tier, char_sheet, ref_urls,
                    get_bytes, public_url) -> str:
    if not scene.keyframe_asset_id:
        raise RuntimeError("no winning keyframe — generate keyframes first")
    keyframe = db.get(Asset, scene.keyframe_asset_id)
    if not keyframe:
        raise RuntimeError("winning keyframe asset missing")

    scene.status = SceneStatus.GENERATING.value
    scene.error = None
    db.add(scene)
    db.flush()

    # Clear previous clip/native/frame assets for idempotent re-runs.
    for a in [a for a in project.assets
              if a.scene_id == scene.id and a.kind in ("clip", "native_audio", "frame")]:
        db.delete(a)
    db.flush()

    keyframe_bytes = get_bytes(keyframe.storage_key)
    scene_dict = {
        "scene_number": scene.scene_number,
        "shot_description": scene.shot_description,
        "video_prompt": scene.video_prompt,
        "camera_movement": scene.camera_movement,
        "audio_mode": scene.audio_mode,
        "dialogue_text": scene.dialogue_text,
        "duration_seconds": scene.duration_seconds,
        "suggested_model": scene.suggested_model,
        "model_override": scene.model_override,
    }

    clip = v_stage.generate_clip(
        scene=scene_dict, style_bible=project.style_bible, tier=tier,
        keyframe_bytes=keyframe_bytes, keyframe_url=public_url(keyframe.storage_key),
        reference_urls=ref_urls, aspect_ratio=project.aspect_ratio,
    )

    clip_asset = _store_asset(
        db, project.id, scene.id, "clip", clip.clip_bytes, clip.clip_content_type,
        meta={"model_id": clip.model_id, "duration": scene.duration_seconds},
    )
    native_id = None
    if clip.native_audio_bytes:
        native = _store_asset(
            db, project.id, scene.id, "native_audio",
            clip.native_audio_bytes, clip.native_audio_content_type,
            meta={"muted": False},
        )
        native_id = native.id

    # Quality gate.
    qr = q_stage.check_clip(
        clip_bytes=clip.clip_bytes, scene=scene_dict, character_sheet=char_sheet,
        native_audio_bytes=clip.native_audio_bytes or None,
    )
    for idx, frame in enumerate(qr.frames):
        _store_asset(db, project.id, scene.id, "frame", frame, "image/jpeg",
                     meta={"frame_index": idx})

    # Auto-mute native track if the audio check flagged garbled speech.
    if qr.report.get("native_audio_muted") and native_id:
        native = db.get(Asset, native_id)
        native.meta = {**(native.meta or {}), "muted": True}
        db.add(native)

    scene.clip_asset_id = clip_asset.id
    scene.native_audio_asset_id = native_id
    scene.quality = qr.report
    scene.status = SceneStatus.FLAGGED.value if qr.report.get("flagged") else SceneStatus.DONE.value
    db.add(scene)
    db.flush()
    return scene.status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scenes_to_dict(project: Project) -> dict:
    return {
        "scenes": [
            {
                "scene_number": s.scene_number,
                "duration_seconds": s.duration_seconds,
                "shot_description": s.shot_description,
                "camera_movement": s.camera_movement,
                "image_prompt": s.image_prompt,
                "video_prompt": s.video_prompt,
                "narration_text": s.narration_text,
                "audio_mode": s.audio_mode,
                "dialogue_text": s.dialogue_text,
                "suggested_model": s.suggested_model,
            }
            for s in project.scenes
        ]
    }


def _write_scenes(db, project: Project, board) -> None:
    """Replace the project's scenes with the (validated) storyboard.

    Mutate through the relationship collection so the delete-orphan cascade and
    the in-memory collection stay consistent (clearing then appending avoids
    leaving deleted instances referenced by project.scenes).
    """
    project.scenes.clear()  # delete-orphan removes the old rows on flush
    db.flush()
    for sc in board.scenes:
        project.scenes.append(
            Scene(
                scene_number=sc.scene_number,
                duration_seconds=sc.duration_seconds,
                shot_description=sc.shot_description,
                camera_movement=sc.camera_movement,
                image_prompt=sc.image_prompt,
                video_prompt=sc.video_prompt,
                narration_text=sc.narration_text,
                audio_mode=sc.audio_mode,
                dialogue_text=sc.dialogue_text,
                suggested_model=sc.suggested_model,
                status=SceneStatus.PENDING.value,
            )
        )
    db.flush()
