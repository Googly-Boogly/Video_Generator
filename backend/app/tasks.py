"""Celery tasks. Every generation step is an async task; the API only enqueues.

A failed scene never kills the project — scene-level tasks isolate failures and
mark the individual scene FAILED while the rest proceed.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from . import cost
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
                    llm=project.llm_model,
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
                llm=project.llm_model,
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


@celery_app.task(bind=True, name="storyforge.refine_storyboard")
def refine_storyboard_task(self, project_id: str, job_id: str,
                           agents: list[str] | None = None) -> dict:
    """Multi-agent (CrewAI) critique + refine of the storyboard and narration.
    Replaces the scenes with the crew's corrected storyboard; no-op in mock mode."""
    from . import cost
    from .pipeline import refine as refine_stage
    from .pipeline import storyboard as sb_stage

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="project not found")
            return {"ok": False}

        _set_job(db, job_id, status=JobStatus.RUNNING.value, progress=0.1)
        try:
            refined = refine_stage.refine_storyboard(
                idea=project.idea,
                target_length=project.target_length,
                style_bible=project.style_bible,
                storyboard=_scenes_to_dict(project),
                llm=project.llm_model,
                agents=agents,
            )
            mocked = bool(refined.pop("_refined_mock", False))
            music = refined.pop("music_suggestion", None)

            board = sb_stage._validate(refined)  # validate + clamp durations + backfill
            _write_scenes(db, project, board)

            # Stash the Music Director's pick on the style bible (read by the audio stage/UI).
            if music:
                sb = dict(project.style_bible or {})
                sb["music_suggestion"] = music
                project.style_bible = sb
            project.status = ProjectStatus.STORYBOARDED.value
            db.add(project)
            db.flush()

            if not mocked:
                cost.add_entry(
                    db, project.id, job_id, "refine", "AI storyboard refine",
                    f"{len(agents or refine_stage.ALL_AGENTS) + 2} agents (estimated)", 0.05)

            _set_job(db, job_id, status=JobStatus.SUCCESS.value, progress=1.0,
                     result={"scene_count": len(board.scenes),
                             "music_suggestion": music, "mock": mocked})
            return {"ok": True, "scene_count": len(board.scenes)}
        except Exception as exc:  # noqa: BLE001
            log.exception("storyboard refine failed")
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
                llm=project.llm_model,
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
                cost.record_keyframes(db, project.id, job_id, scene.scene_number)
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
    # Remove via the relationship (delete-orphan) so the in-memory collection
    # stays consistent — db.delete() here would trip the cascade on re-runs.
    for a in [a for a in project.assets if a.kind == "keyframe" and a.scene_id == scene.id]:
        project.assets.remove(a)
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
    ranking = kf_stage.rank_keyframes(variants, scene=scene_dict, character_sheet=char_sheet,
                                      llm=project.llm_model)
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


def _record_clip_cost(db, project, scene, job_id, step) -> None:
    """Record the actual clip cost from the model recorded on the clip asset."""
    clip = db.get(Asset, scene.clip_asset_id) if scene.clip_asset_id else None
    model_id = (clip.meta or {}).get("model_id") if clip else None
    if model_id:
        cost.record_clip(db, project.id, job_id, scene.scene_number, model_id,
                         scene.duration_seconds, step=step)


def _store_asset(db, project_id, scene_id, kind, data: bytes, content_type, meta=None) -> Asset:
    """Put bytes in MinIO and create the Asset row pointing at them."""
    from .asset_store import store_asset

    return store_asset(db, project_id, scene_id, kind, data, content_type, meta)


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
                _record_clip_cost(db, project, scene, job_id, "video")
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
    from .config import settings
    from .models_config import Modality, route

    # A text-to-video override (Veo) generates from the prompt and overrides the
    # keyframe, so it needs no winning keyframe in live mode. Mock still encodes a
    # placeholder from the keyframe, and image-to-video (Kling) animates it, so both
    # still require one.
    model_id = v_stage.resolve_model(scene={
        "audio_mode": scene.audio_mode, "model_override": scene.model_override,
        "suggested_model": scene.suggested_model,
    }, tier=tier)
    needs_keyframe = settings.mock_generation or route(model_id).modality == Modality.IMAGE_TO_VIDEO

    keyframe = db.get(Asset, scene.keyframe_asset_id) if scene.keyframe_asset_id else None
    if needs_keyframe and not keyframe:
        raise RuntimeError("no winning keyframe — generate keyframes first")

    scene.status = SceneStatus.GENERATING.value
    scene.error = None
    db.add(scene)
    db.flush()

    keyframe_bytes = get_bytes(keyframe.storage_key) if keyframe else None
    keyframe_url = public_url(keyframe.storage_key) if keyframe else None
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

    # Generate FIRST, then replace. If generation (or the keyframe fetch above) fails,
    # we must not have already deleted the existing clip — otherwise a failed regenerate
    # wipes good content. So only clear the previous clip/native/frame assets once we
    # hold a new clip (remove via the relationship so the cascade + MinIO before_delete
    # stay consistent on re-runs).
    clip = v_stage.generate_clip(
        scene=scene_dict, style_bible=project.style_bible, tier=tier,
        keyframe_bytes=keyframe_bytes, keyframe_url=keyframe_url,
        reference_urls=ref_urls, aspect_ratio=project.aspect_ratio,
    )

    for a in [a for a in project.assets
              if a.scene_id == scene.id and a.kind in ("clip", "native_audio", "frame")]:
        project.assets.remove(a)
    db.flush()

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
        native_audio_bytes=clip.native_audio_bytes or None, llm=project.llm_model,
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
# Phase 4: audio build (narration + music beat grid + native leveling)
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="storyforge.build_audio")
def build_audio_task(self, project_id: str, job_id: str, scene_id: str | None = None) -> dict:
    """Synthesize ElevenLabs narration per narrated scene with the locked project
    voice, ensure the music bed's beat grid, and record the native-track levels.
    Dialogue scenes are skipped (native audio carries the speech)."""
    from .pipeline import audio as a_stage

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="project not found")
            return {"ok": False}

        _set_job(db, job_id, status=JobStatus.RUNNING.value, progress=0.05)
        voice_id = project.voice_id or a_stage.DEFAULT_VOICE_ID

        # 1) Ensure the music bed's beat grid (if a bed has been chosen).
        _ensure_beat_grid(db, project, a_stage)
        _set_job(db, job_id, progress=0.15)

        # 2) Narration per narrated scene.
        scenes = (
            [s for s in project.scenes if s.id == scene_id]
            if scene_id else sorted(project.scenes, key=lambda s: s.scene_number)
        )
        narrated = skipped = failed = 0
        for i, scene in enumerate(scenes):
            try:
                if scene.audio_mode == "dialogue" or not (scene.narration_text or "").strip():
                    skipped += 1
                else:
                    _narration_for_scene(db, project, scene, voice_id, a_stage)
                    cost.record_narration(db, project.id, job_id, scene.scene_number,
                                          len(scene.narration_text))
                    narrated += 1
            except Exception as exc:  # noqa: BLE001 — isolate per-scene failures
                log.exception("narration failed for scene %s", scene.scene_number)
                failed += 1
            db.flush()
            _set_job(db, job_id, progress=0.15 + 0.8 * (i + 1) / len(scenes))

        # Advance when a full build produced narration, OR the project has clips
        # (so an all-dialogue project — no narration to synthesize — still moves on,
        # since native audio carries the speech).
        if not scene_id and (narrated or any(s.clip_asset_id for s in project.scenes)):
            project.status = ProjectStatus.AUDIO.value
            db.add(project)

        _set_job(db, job_id, status=JobStatus.SUCCESS.value, progress=1.0,
                 result={"narrated": narrated, "skipped": skipped, "failed": failed})
        return {"ok": True, "narrated": narrated, "skipped": skipped, "failed": failed}


def _narration_for_scene(db, project, scene, voice_id, a_stage) -> None:
    for a in [a for a in project.assets if a.kind == "narration" and a.scene_id == scene.id]:
        project.assets.remove(a)  # delete-orphan; keeps the collection consistent
    db.flush()
    data, content_type, duration, alignment = a_stage.synth_narration(
        text=scene.narration_text, voice_id=voice_id
    )
    _store_asset(
        db, project.id, scene.id, "narration", data, content_type,
        meta={"voice_id": voice_id, "duration": duration, "chars": len(scene.narration_text),
              "alignment": alignment},
    )


def _ensure_beat_grid(db, project, a_stage) -> None:
    from .storage import get_bytes

    music = next((a for a in project.assets if a.kind == "music"), None)
    if not music:
        return
    meta = dict(music.meta or {})
    if meta.get("beat_grid"):
        return
    suffix = ".mp3" if "mpeg" in (music.content_type or "") or "mp3" in (music.content_type or "") else ".wav"
    grid = a_stage.beat_grid(
        audio_bytes=get_bytes(music.storage_key), suffix=suffix, bpm_hint=meta.get("bpm"),
    )
    meta["beat_grid"] = grid
    music.meta = meta
    db.add(music)
    db.flush()


# ---------------------------------------------------------------------------
# Phase 5: AI editor (EDL) + draft/final render
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="storyforge.build_edl")
def build_edl_task(self, project_id: str, job_id: str) -> dict:
    """Assemble the Edit Decision List from the storyboard + real signals."""
    from .pipeline import editor as e_stage

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="project not found")
            return {"ok": False}
        _set_job(db, job_id, status=JobStatus.RUNNING.value, progress=0.2)

        scenes = _edl_scene_inputs(project)
        if not scenes:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="no clips to edit yet")
            return {"ok": False}
        music = next((a for a in project.assets if a.kind == "music"), None)
        beat_grid = (music.meta or {}).get("beat_grid") if music else None

        edl = e_stage.build_edl(
            project={"aspect_ratio": project.aspect_ratio, "idea": project.idea},
            scenes=scenes, beat_grid=beat_grid, frames=None, llm=project.llm_model,
        )
        project.edl = edl
        project.status = ProjectStatus.EDITED.value
        db.add(project)
        db.flush()
        _set_job(db, job_id, status=JobStatus.SUCCESS.value, progress=1.0,
                 result={"cuts": len(edl["cuts"]), "total_duration": edl["total_duration"]})
        return {"ok": True, "cuts": len(edl["cuts"])}


@celery_app.task(bind=True, name="storyforge.render")
def render_task(self, project_id: str, job_id: str, final: bool = False) -> dict:
    """Render the EDL. Draft = 480p watermarked; final regenerates hero scenes at
    premium then renders 1080p with the full audio mix. Output stored in MinIO."""
    from .models_config import Tier
    from .pipeline import assemble as as_stage
    from .pipeline import quality as q_stage
    from .pipeline import video as v_stage
    from .storage import get_bytes, public_url

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="project not found")
            return {"ok": False}
        if not project.edl:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="no EDL — run the editor first")
            return {"ok": False}

        _set_job(db, job_id, status=JobStatus.RUNNING.value, progress=0.05)
        regenerated = 0

        # Final: regenerate hero scenes (dialogue or quality-flagged) at premium.
        if final:
            char_sheet = (project.style_bible or {}).get("character_sheet")
            ref_urls = [public_url(a.storage_key) for a in project.assets if a.kind == "reference"]
            hero = [s for s in project.scenes
                    if (s.audio_mode == "dialogue" or s.status == SceneStatus.FLAGGED.value)
                    and s.keyframe_asset_id]
            for s in hero:
                try:
                    _clip_for_scene(db, project, s, v_stage, q_stage, Tier.PREMIUM,
                                    char_sheet, ref_urls, get_bytes, public_url)
                    _record_clip_cost(db, project, s, job_id, "render")
                    regenerated += 1
                except Exception:  # noqa: BLE001
                    log.exception("hero regen failed for scene %s", s.scene_number)
                db.flush()
            _set_job(db, job_id, progress=0.4)

        scenes = _render_scene_inputs(db, project, get_bytes)
        if not scenes:
            _set_job(db, job_id, status=JobStatus.FAILED.value, error="no renderable clips")
            return {"ok": False}
        music = next((a for a in project.assets if a.kind == "music"), None)
        music_bytes = get_bytes(music.storage_key) if music else None
        _set_job(db, job_id, progress=0.6)

        data = as_stage.render(draft=not final, aspect_ratio=project.aspect_ratio,
                               scenes=scenes, music_bytes=music_bytes)

        kind = "final" if final else "draft"
        for a in [a for a in project.assets if a.kind == kind]:
            project.assets.remove(a)  # replace previous render of this tier
        db.flush()
        asset = _store_asset(db, project.id, None, kind, data, "video/mp4",
                             meta={"resolution": "1080p" if final else "480p",
                                   "duration": project.edl.get("total_duration")})
        project.status = (ProjectStatus.RENDERED if final else ProjectStatus.DRAFT_RENDERED).value
        db.add(project)
        db.flush()
        _set_job(db, job_id, status=JobStatus.SUCCESS.value, progress=1.0,
                 result={"asset_id": asset.id, "kind": kind, "regenerated": regenerated})
        return {"ok": True, "asset_id": asset.id, "kind": kind}


def _edl_scene_inputs(project: Project) -> list[dict]:
    """Per-scene signals the editor needs (only scenes that have a clip)."""
    out = []
    for s in sorted(project.scenes, key=lambda s: s.scene_number):
        if not s.clip_asset_id:
            continue
        native = next((a for a in project.assets
                       if a.kind == "native_audio" and a.scene_id == s.id), None)
        narr = next((a for a in project.assets
                     if a.kind == "narration" and a.scene_id == s.id), None)
        out.append({
            "scene_number": s.scene_number,
            "duration": s.duration_seconds,
            "audio_mode": s.audio_mode,
            "native_muted": bool((native.meta or {}).get("muted")) if native else False,
            "narration_text": s.narration_text,
            "narration_duration": (narr.meta or {}).get("duration") if narr else None,
            "narration_alignment": (narr.meta or {}).get("alignment") if narr else None,
        })
    return out


def _render_scene_inputs(db, project: Project, get_bytes) -> list[dict]:
    """Gather clip/narration bytes + per-scene mix from the EDL, in cut order."""
    cuts = {c["scene_number"]: c for c in (project.edl or {}).get("cuts", [])}
    scenes_by_num = {s.scene_number: s for s in project.scenes}
    out = []
    for num in sorted(cuts):
        scene = scenes_by_num.get(num)
        if not scene or not scene.clip_asset_id:
            continue
        clip = db.get(Asset, scene.clip_asset_id)
        if not clip:
            continue
        cut = cuts[num]
        mix = cut.get("mix", {})
        narr = next((a for a in project.assets
                     if a.kind == "narration" and a.scene_id == scene.id), None)
        out.append({
            "clip_bytes": get_bytes(clip.storage_key),
            "narration_bytes": get_bytes(narr.storage_key) if narr else None,
            "trim_head": cut.get("trim_head", 0.0),
            "trim_tail": cut.get("trim_tail", 0.0),
            "screen_time": cut.get("screen_time"),
            "caption": cut.get("caption", ""),
            "captions": cut.get("captions"),
            "transition": cut.get("transition", "cut"),
            "audio_mode": scene.audio_mode,
            "narration_db": mix.get("narration_db"),
            "native_db": mix.get("native_db"),
            "duration": scene.duration_seconds,
        })
    return out


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

    Replacing the scenes invalidates everything derived from them, so remove those
    assets too — otherwise keyframes/clips/etc. survive pointing at deleted scenes
    (stranded in MinIO + DB). Remove through the collection so the before_delete
    blob cleanup + cascade stay consistent (see CLAUDE.md). Reference images live at
    the style-bible level (scene_id is None) and stay valid.
    """
    _DERIVED_KINDS = {"keyframe", "clip", "native_audio", "frame", "narration"}
    for a in [a for a in project.assets if a.kind in _DERIVED_KINDS]:
        project.assets.remove(a)
    db.flush()
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
