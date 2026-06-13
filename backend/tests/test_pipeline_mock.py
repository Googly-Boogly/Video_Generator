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


def test_veo_routes_to_google_others_to_fal():
    from app.models_config import route
    assert route("veo-31").provider == "google" and route("veo-31").google_id
    assert route("veo-31-lite").provider == "google"
    assert route("kling-3-pro").provider == "fal" and route("kling-3-pro").fal_id
    assert route("seedance-2").provider == "fal"
    assert route("flux2-dev").provider == "fal"


def test_video_dispatch_picks_provider(monkeypatch):
    from app.providers import generation, fal_provider, google_provider
    monkeypatch.setattr(fal_provider, "generate_video", lambda **k: b"FAL:" + k["model_id"].encode())
    monkeypatch.setattr(google_provider, "generate_video", lambda **k: b"GOOGLE:" + k["model_id"].encode())
    fal_out = generation.generate_video(model_id="kling-3-pro", prompt="x", duration=5, aspect_ratio="16:9")
    goog_out = generation.generate_video(model_id="veo-31", prompt="x", duration=5, aspect_ratio="16:9")
    assert fal_out == b"FAL:kling-3-pro"
    assert goog_out == b"GOOGLE:veo-31"


def test_dialogue_scene_routes_to_veo():
    from app.pipeline import video as v_stage
    from app.models_config import Tier
    scene = {"audio_mode": "dialogue", "model_override": None, "suggested_model": "veo-31"}
    assert v_stage.resolve_model(scene=scene, tier=Tier.DRAFT) == "veo-31-lite"
    assert v_stage.resolve_model(scene=scene, tier=Tier.PREMIUM) == "veo-31"


def test_audio_voices_and_mix_levels():
    from app.pipeline import audio as a
    voices = a.list_voices()
    assert any(v["voice_id"] == a.DEFAULT_VOICE_ID for v in voices)
    narrated = a.mix_plan(audio_mode="narrated", native_muted=False)
    assert narrated["narration_db"] == 0.0 and narrated["native_db"] == a.NATIVE_DUCK_DB
    assert narrated["pause_narration_for_dialogue"] is False
    dlg = a.mix_plan(audio_mode="dialogue", native_muted=False)
    assert dlg["narration_db"] is None and dlg["pause_narration_for_dialogue"] is True
    assert a.mix_plan(audio_mode="narrated", native_muted=True)["native_db"] is None


def test_narration_synth_is_silent_wav_in_mock():
    from app.pipeline import audio as a
    data, ct, dur = a.synth_narration(text="hello there friend " * 3, voice_id="voice_aria")
    assert ct == "audio/wav" and data[:4] == b"RIFF" and dur > 0


def test_beat_grid_detects_tempo_with_librosa():
    from app.pipeline import audio as a
    from app import media
    bed = media.synth_music_bed(bpm=128, seconds=10, style="upbeat")
    grid = a.beat_grid(audio_bytes=bed, suffix=".mp3", bpm_hint=128)
    assert grid["engine"] == "librosa"
    assert 100 <= grid["bpm"] <= 150          # recovers roughly the 128-bpm bed
    assert len(grid["beats"]) > 5


def test_build_edl_structure_and_beat_snap():
    from app.pipeline import editor as e
    scenes = [
        {"scene_number": 1, "duration": 5, "audio_mode": "narrated", "narration_text": "hi", "native_muted": False},
        {"scene_number": 2, "duration": 5, "audio_mode": "dialogue", "narration_text": "", "native_muted": False},
    ]
    edl = e.build_edl(project={"aspect_ratio": "16:9"}, scenes=scenes,
                      beat_grid={"bpm": 120, "beats": [0.0, 0.5, 1.0, 1.5, 2.0]})
    assert len(edl["cuts"]) == 2 and edl["total_duration"] > 0
    assert edl["cuts"][0]["trim_head"] > 0 and edl["cuts"][0]["trim_tail"] > 0
    assert edl["cuts"][0]["on_beat"] is not None
    assert edl["cuts"][1]["mix"]["pause_narration_for_dialogue"] is True
    assert edl["cuts"][1]["mix"]["narration_db"] is None
    assert edl["levels"]["music_db"] == -18.0


def test_llm_routing_and_anthropic_part_conversion():
    from app.llm_config import llm_route, DEFAULT_LLM, is_known
    assert llm_route(None).id == DEFAULT_LLM
    assert llm_route("claude-haiku-4-6").provider == "anthropic"
    assert llm_route("gpt-5.4-nano").provider == "openai"
    assert llm_route("nonsense").id == DEFAULT_LLM        # unknown → default
    assert is_known("claude-haiku-4-6") and not is_known("nope")
    # OpenAI-style image part → Anthropic content block
    from app.llm import _image_part, _to_anthropic_parts
    part = _image_part(b"\x89PNGdata", "image/png")
    blocks = _to_anthropic_parts([{"type": "text", "text": "hi"}, part])
    assert blocks[0] == {"type": "text", "text": "hi"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert blocks[1]["source"]["type"] == "base64"


def test_mock_edl_pauses_narration_for_dialogue():
    scenes = [
        {"scene_number": 1, "duration_seconds": 5, "audio_mode": "narrated", "narration_text": "hi"},
        {"scene_number": 2, "duration_seconds": 5, "audio_mode": "dialogue", "narration_text": ""},
    ]
    edl = editor.build_edl(project={}, scenes=scenes, beat_grid=None)
    assert edl["cuts"][1]["mix"]["pause_narration_for_dialogue"] is True
    assert edl["cuts"][0]["mix"]["pause_narration_for_dialogue"] is False
