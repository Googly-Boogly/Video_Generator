"""Mock-mode pipeline tests — run with no providers, no network, no DB.

    cd backend && MOCK_GENERATION=true python -m pytest
"""
import os

os.environ.setdefault("MOCK_GENERATION", "true")

from app.pipeline import keyframes as kf_stage
from app.pipeline import storyboard as sb_stage
from app.pipeline import style_bible as style_stage
from app.pipeline import editor, mock, prompts
from app.models_config import (
    MODEL_ROUTES,
    Tier,
    default_video_model,
    resolve_video_model,
)
from app.schemas import Storyboard


def test_style_bible_has_locked_character_descriptors():
    sb = style_stage.generate_style_bible(idea="a fox", style_preset="anime", aspect_ratio="16:9")
    assert sb["character_sheet"][0]["physical_descriptors"]
    assert sb["reference_image_prompts"]


def test_storyboard_validates_and_fits_target_length():
    board = sb_stage.generate_storyboard(
        idea="a lighthouse keeper", target_length=30, aspect_ratio="16:9",
        style_preset="cinematic", style_bible=mock.mock_style_bible("x", "cinematic"),
    )
    assert isinstance(board, Storyboard)
    total = sum(s.duration_seconds for s in board.scenes)
    assert abs(total - 30) <= 6
    # scene_number is contiguous from 1
    assert [s.scene_number for s in board.scenes] == list(range(1, len(board.scenes) + 1))
    # every scene routes to a known model
    for s in board.scenes:
        assert s.suggested_model in MODEL_ROUTES


def test_revision_targets_named_scene():
    board = sb_stage.generate_storyboard(
        idea="a fox", target_length=15, aspect_ratio="9:16", style_preset="anime", style_bible=None
    )
    as_dict = {"scenes": [s.model_dump() for s in board.scenes]}
    revised = sb_stage.revise_storyboard(
        instruction="make scene 2 moodier", storyboard=as_dict, style_bible=None
    )
    s2 = next(s for s in revised.scenes if s.scene_number == 2)
    assert "revised" in s2.shot_description


def test_dialogue_routes_to_lip_sync_model():
    assert default_video_model(Tier.PREMIUM, "dialogue") == "veo-31"
    assert MODEL_ROUTES["veo-31"].lip_sync is True


def test_draft_tier_is_cheaper_than_premium():
    # No override: premium uses the suggested premium model, draft drops to budget.
    premium = resolve_video_model(
        model_override=None, suggested_model="kling-3-pro",
        audio_mode="narrated", tier=Tier.PREMIUM,
    )
    draft = resolve_video_model(
        model_override=None, suggested_model="kling-3-pro",
        audio_mode="narrated", tier=Tier.DRAFT,
    )
    assert premium == "kling-3-pro"
    assert draft == "kling-25-turbo"
    assert MODEL_ROUTES[draft].price_per_second < MODEL_ROUTES[premium].price_per_second


def test_override_wins_on_both_tiers():
    for tier in (Tier.DRAFT, Tier.PREMIUM):
        got = resolve_video_model(
            model_override="seedance-2", suggested_model="kling-3-pro",
            audio_mode="narrated", tier=tier,
        )
        assert got == "seedance-2"


def test_prompt_translator_dialects_differ():
    scene = {
        "shot_description": "hero on a cliff", "video_prompt": "hero on a cliff at dawn",
        "camera_movement": "slow push in", "audio_mode": "dialogue", "dialogue_text": "We made it.",
    }
    veo = prompts.translate_video_prompt(model_id="veo-31", scene=scene, style_bible=None)
    kling = prompts.translate_video_prompt(model_id="kling-3-pro", scene=scene, style_bible=None)
    assert "lip-sync" in veo.lower()
    assert veo != kling


def test_reference_images_cover_prompts_with_roles():
    sb = mock.mock_style_bible("a fox", "anime")
    refs = style_stage.generate_reference_images(style_bible=sb, aspect_ratio="16:9")
    assert len(refs) == len(sb["reference_image_prompts"])
    assert refs[0].role == "character"
    assert all(r.image_bytes.startswith(b"\x89PNG") for r in refs)


def test_keyframes_best_of_n_and_ranking():
    scene = {"scene_number": 2, "shot_description": "hero on a cliff", "image_prompt": "hero"}
    variants = kf_stage.generate_keyframes(scene=scene, style_bible=None, n=3)
    assert len(variants) == 3
    assert len({v.seed for v in variants}) == 3  # distinct seeds
    ranking = kf_stage.rank_keyframes(variants, scene=scene)
    assert ranking["winner"] == 0
    assert len(ranking["scores"]) == 3
    # scores are descending in the mock ranking
    scores = [s["score"] for s in ranking["scores"]]
    assert scores == sorted(scores, reverse=True)


def test_video_clip_is_playable_mp4_and_demuxes_audio():
    # Encode a clip from a mock keyframe (real FFmpeg), demux native audio,
    # extract quality-gate frames — all without paying a model.
    from app.pipeline import video as v_stage
    from app.pipeline import quality as q_stage
    from app.models_config import Tier

    keyframe = mock.placeholder_png("kf", width=32, height=18)
    scene = {"scene_number": 1, "shot_description": "hero", "video_prompt": "hero",
             "audio_mode": "narrated", "duration_seconds": 2.0}
    clip = v_stage.generate_clip(
        scene=scene, style_bible=None, tier=Tier.DRAFT,
        keyframe_bytes=keyframe, aspect_ratio="16:9",
    )
    assert clip.clip_content_type == "video/mp4"
    assert clip.clip_bytes[4:8] == b"ftyp"          # valid MP4 box
    assert clip.model_id == "kling-25-turbo"         # draft narrated default
    assert len(clip.native_audio_bytes) > 0          # native audio demuxed

    qr = q_stage.check_clip(clip_bytes=clip.clip_bytes, scene=scene)
    assert len(qr.frames) == 4                        # 4 frames extracted
    assert all(f[:2] == b"\xff\xd8" for f in qr.frames)  # JPEGs
    assert "flagged" in qr.report


def test_dialogue_scene_routes_to_veo():
    from app.pipeline import video as v_stage
    from app.models_config import Tier
    scene = {"audio_mode": "dialogue", "model_override": None, "suggested_model": "veo-31"}
    assert v_stage.resolve_model(scene=scene, tier=Tier.DRAFT) == "veo-31-lite"
    assert v_stage.resolve_model(scene=scene, tier=Tier.PREMIUM) == "veo-31"


def test_mock_edl_pauses_narration_for_dialogue():
    scenes = [
        {"scene_number": 1, "duration_seconds": 5, "audio_mode": "narrated", "narration_text": "hi"},
        {"scene_number": 2, "duration_seconds": 5, "audio_mode": "dialogue", "narration_text": ""},
    ]
    edl = editor.build_edl(project={}, scenes=scenes, beat_grid=None)
    assert edl["cuts"][1]["mix"]["pause_narration_for_dialogue"] is True
    assert edl["cuts"][0]["mix"]["pause_narration_for_dialogue"] is False
