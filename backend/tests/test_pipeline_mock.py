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
    Modality,
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


def _fake_segment(n_scenes=3):
    def _fn(*, system, user, max_tokens, llm=None):
        return {"scenes": [
            {"scene_number": i, "duration_seconds": 5, "shot_description": f"shot {i}",
             "camera_movement": "static", "image_prompt": "x", "video_prompt": "x",
             "narration_text": "hello there", "audio_mode": "narrated",
             "dialogue_text": None, "suggested_model": "kling-3-pro"}
            for i in range(1, n_scenes + 1)]}
    return _fn


def test_long_storyboard_fills_full_target_length(monkeypatch):
    # A 10-minute film can't be one giant LLM call (the connection drops), so it's built
    # from sequential segments. Crucially, segments UNDER-produce, so the generator must
    # keep requesting until the accumulated duration reaches the target — otherwise the
    # film comes out half-length (the bug: 50 scenes ≈ 5 min for a 10-min request).
    from app.pipeline import storyboard as sb
    calls = {"n": 0}

    def fake(*, system, user, max_tokens, llm=None):
        calls["n"] += 1
        return _fake_segment(5)(system=system, user=user, max_tokens=max_tokens)  # ~25s/call

    monkeypatch.setattr(sb.settings, "mock_generation", False)
    monkeypatch.setattr(sb, "complete_json", fake)
    board = sb.generate_storyboard(idea="alexander", target_length=600, aspect_ratio="16:9",
                                   style_preset="cinematic", style_bible=None)
    total = sum(s.duration_seconds for s in board.scenes)
    assert total >= 600 - 5                      # reaches full length despite under-producing
    assert calls["n"] >= 2                       # segmented, not a single call
    assert [s.scene_number for s in board.scenes] == list(range(1, len(board.scenes) + 1))


def test_short_storyboard_uses_single_call(monkeypatch):
    from app.pipeline import storyboard as sb
    calls = {"n": 0}

    def fake(*, system, user, max_tokens, llm=None):
        calls["n"] += 1
        return _fake_segment(4)(system=system, user=user, max_tokens=max_tokens)

    monkeypatch.setattr(sb.settings, "mock_generation", False)
    monkeypatch.setattr(sb, "complete_json", fake)
    sb.generate_storyboard(idea="x", target_length=30, aspect_ratio="16:9",
                           style_preset="cinematic", style_bible=None)
    assert calls["n"] == 1  # short films stay a single call


def test_refine_chunked_apply_never_drops_scenes(monkeypatch):
    # Refining a long board applies notes in chunks; if the model under-returns or errors,
    # the chunk keeps its originals — so a 10-min film can't collapse to 3 min.
    from app.pipeline import refine
    scenes = [{"scene_number": i, "duration_seconds": 5, "shot_description": f"s{i}",
               "narration_text": f"n{i}", "audio_mode": "narrated"} for i in range(1, 41)]

    def under_returns(*, system, user, max_tokens, llm=None):
        return {"scenes": [{"scene_number": 1, "shot_description": "x"}]}  # too few

    monkeypatch.setattr(refine, "complete_json", under_returns)
    out = refine._apply_notes_chunked(scenes, notes="tighten", llm=None)
    assert len(out) == 40  # every scene preserved despite the model returning fewer


def test_refine_chunked_apply_uses_rewrites(monkeypatch):
    import json as _json
    from app.pipeline import refine
    scenes = [{"scene_number": i, "duration_seconds": 5, "shot_description": f"s{i}",
               "narration_text": f"n{i}", "audio_mode": "narrated"} for i in range(1, 31)]

    def rewrite(*, system, user, max_tokens, llm=None):
        batch = _json.loads(user.split("SCENES TO REWRITE (JSON):\n", 1)[1])["scenes"]
        for s in batch:
            s["shot_description"] = "REWRITTEN"
        return {"scenes": batch}

    monkeypatch.setattr(refine, "complete_json", rewrite)
    out = refine._apply_notes_chunked(scenes, notes="x", llm=None)
    assert len(out) == 30 and all(s["shot_description"] == "REWRITTEN" for s in out)


def test_refine_guard_rejects_collapsed_board():
    import pytest
    from app.pipeline import refine
    original = [{"scene_number": i} for i in range(1, 21)]  # 20 scenes
    with pytest.raises(ValueError):
        refine._guard_length([{"scene_number": 1} for _ in range(5)], original)  # collapse
    refine._guard_length([{"scene_number": i} for i in range(1, 19)], original)  # 18 ok, no raise


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


def test_video_routing_is_image_to_video_only():
    # Photo-to-video pivot: audio_mode no longer affects routing — the default is
    # always the tier's image-to-video model (lip-sync routing was removed).
    assert default_video_model(Tier.PREMIUM, "dialogue") == default_video_model(Tier.PREMIUM)
    assert default_video_model(Tier.PREMIUM) == "kling-3-pro"
    assert default_video_model(Tier.DRAFT) == "kling-25-turbo"
    assert MODEL_ROUTES[default_video_model(Tier.PREMIUM)].modality == Modality.IMAGE_TO_VIDEO
    assert MODEL_ROUTES[default_video_model(Tier.DRAFT)].modality == Modality.IMAGE_TO_VIDEO


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
    # A valid image-to-video override wins on both tiers.
    for tier in (Tier.DRAFT, Tier.PREMIUM):
        got = resolve_video_model(
            model_override="kling-3-pro", suggested_model="kling-25-turbo",
            audio_mode="narrated", tier=tier,
        )
        assert got == "kling-3-pro"


def test_text_to_video_suggestion_falls_back_but_explicit_override_wins():
    # An auto/suggested text-to-video pick (a Veo hero shot the user didn't choose)
    # falls back to the tier's image-to-video default — t2v is never automatic.
    assert resolve_video_model(
        model_override=None, suggested_model="veo-31",
        audio_mode="narrated", tier=Tier.PREMIUM,
    ) == "kling-3-pro"
    # But an EXPLICIT per-scene override into text-to-video (Veo) is honored on both
    # tiers — this is how a scene opts into text-to-video over photo-to-video.
    for tier in (Tier.DRAFT, Tier.PREMIUM):
        assert resolve_video_model(
            model_override="veo-31", suggested_model="kling-3-pro",
            audio_mode="narrated", tier=tier,
        ) == "veo-31"
        assert MODEL_ROUTES["veo-31"].modality == Modality.TEXT_TO_VIDEO


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


def test_text_to_video_override_overrides_keyframe(monkeypatch):
    # A Veo (text-to-video) override generates from the prompt and overrides the
    # keyframe — no image is sent to the provider. An image-to-video override still
    # forwards the keyframe so Kling can animate it.
    from app.pipeline import video as v_stage
    from app.models_config import Tier
    from app import media
    from app.providers import generation

    real_clip = media.image_to_clip(
        image_bytes=mock.placeholder_png("x", width=32, height=18),
        duration=1.0, aspect_ratio="16:9",
    )
    captured: dict = {}

    def fake_generate_video(**kw):
        captured.clear()
        captured.update(kw)
        return real_clip

    monkeypatch.setattr(generation, "generate_video", fake_generate_video)
    monkeypatch.setattr(v_stage.settings, "mock_generation", False)

    scene = {"scene_number": 1, "video_prompt": "a phalanx advances", "audio_mode": "narrated",
             "duration_seconds": 1.0, "model_override": "veo-31", "suggested_model": "kling-3-pro"}
    clip = v_stage.generate_clip(
        scene=scene, style_bible=None, tier=Tier.PREMIUM,
        keyframe_bytes=b"KEYFRAMEBYTES", keyframe_url="https://x/kf.png", aspect_ratio="16:9",
    )
    assert clip.model_id == "veo-31"
    assert captured["image_bytes"] is None and captured["image_url"] is None  # photo overridden

    scene["model_override"] = "kling-3-pro"
    v_stage.generate_clip(
        scene=scene, style_bible=None, tier=Tier.PREMIUM,
        keyframe_bytes=b"KEYFRAMEBYTES", keyframe_url="https://x/kf.png", aspect_ratio="16:9",
    )
    assert captured["image_bytes"] == b"KEYFRAMEBYTES" and captured["image_url"] == "https://x/kf.png"


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


def test_video_resolve_model_forces_image_to_video():
    from app.pipeline import video as v_stage
    from app.models_config import Tier
    # A suggested text-to-video hero shot (Veo) still animates the keyframe via i2v.
    scene = {"audio_mode": "narrated", "model_override": None, "suggested_model": "veo-31"}
    assert v_stage.resolve_model(scene=scene, tier=Tier.DRAFT) == "kling-25-turbo"
    assert v_stage.resolve_model(scene=scene, tier=Tier.PREMIUM) == "kling-3-pro"


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
    data, ct, dur, alignment = a.synth_narration(text="hello there friend " * 3, voice_id="voice_aria")
    assert ct == "audio/wav" and data[:4] == b"RIFF" and dur > 0
    assert alignment is None  # no timestamps in mock mode


def test_resolve_live_voice_id_maps_mock_placeholders_to_real_ids():
    from app.pipeline import audio as a
    # Mock catalog ids ("voice_aria") are NOT real ElevenLabs voices — live narration
    # 404s unless they're translated. Each placeholder resolves to a real id, the
    # default voice resolves, an already-real id passes through, and empty falls back.
    assert a.resolve_live_voice_id("voice_aria") == a._LIVE_VOICE_IDS["voice_aria"]
    assert a.resolve_live_voice_id(a.DEFAULT_VOICE_ID) == a.DEFAULT_LIVE_VOICE_ID
    # Default narrator is Sarah (mature, reassuring, confident).
    assert a.DEFAULT_VOICE_ID == "voice_sarah"
    assert a.DEFAULT_LIVE_VOICE_ID == "EXAVITQu4vr4xnSDxMaL"
    assert not a.DEFAULT_LIVE_VOICE_ID.startswith("voice_")
    # Legacy placeholder ids still resolve to real voices (no 404 for old projects).
    assert a.resolve_live_voice_id("voice_sage") == "EXAVITQu4vr4xnSDxMaL"
    assert all(not v.startswith("voice_") for v in a._LIVE_VOICE_IDS.values())
    real = "9BWtsMINqrJLrRacOk9x"
    assert a.resolve_live_voice_id(real) == real  # real id passes through
    assert a.resolve_live_voice_id(None) == a.DEFAULT_LIVE_VOICE_ID
    assert a.resolve_live_voice_id("") == a.DEFAULT_LIVE_VOICE_ID


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


# --- Caption sync + narration-led timeline (Phase-7 continuous-narration fix) ---

def test_caption_segments_cover_full_duration_proportionally():
    from app.pipeline import audio as a
    segs = a.caption_segments(
        text="First sentence here. Second one follows. Third and last.", duration=6.0,
    )
    assert len(segs) == 3
    assert segs[0]["start"] == 0.0
    assert abs(segs[-1]["end"] - 6.0) < 0.01
    # monotonic, non-overlapping events
    for i in range(1, len(segs)):
        assert segs[i]["start"] >= segs[i - 1]["start"]
        assert segs[i - 1]["end"] <= segs[i]["start"] + 0.001
    # degenerate inputs produce nothing (never crash the editor)
    assert a.caption_segments(text="", duration=5.0) == []
    assert a.caption_segments(text="hi", duration=0) == []


def test_caption_segments_use_elevenlabs_alignment():
    from app.pipeline import audio as a
    text = "Hello world. Goodbye now."
    # Fabricate per-character timing at 0.1s/char (what ElevenLabs returns).
    starts = [round(0.1 * i, 3) for i in range(len(text))]
    ends = [round(0.1 * (i + 1), 3) for i in range(len(text))]
    alignment = {
        "characters": list(text),
        "character_start_times_seconds": starts,
        "character_end_times_seconds": ends,
    }
    segs = a.caption_segments(text=text, duration=2.6, alignment=alignment)
    assert len(segs) == 2
    assert segs[0]["start"] == 0.0
    # "Goodbye now." starts at char index 13 -> ~1.3s, not a proportional guess.
    assert abs(segs[1]["start"] - 1.3) < 0.2


def test_caption_segments_wrap_long_lines():
    from app.pipeline import audio as a
    segs = a.caption_segments(text="word " * 30, duration=5.0, wrap_width=20)
    assert segs and "\n" in segs[0]["text"]  # wrapped to fit on screen


def test_editor_narration_led_timeline_and_captions():
    # Each scene is on screen for exactly its narration duration (not the clip's),
    # so the burned caption tracks the single continuous voiceover scene-for-scene.
    scenes = [
        {"scene_number": 1, "duration": 5.0, "audio_mode": "narrated",
         "narration_text": "Alpha beta gamma.", "narration_duration": 3.0, "native_muted": False},
        {"scene_number": 2, "duration": 5.0, "audio_mode": "narrated",
         "narration_text": "Delta epsilon zeta.", "narration_duration": 7.0, "native_muted": False},
    ]
    edl = editor.build_edl(project={"aspect_ratio": "16:9"}, scenes=scenes, beat_grid=None)
    assert edl["cuts"][0]["screen_time"] == 3.0
    assert edl["cuts"][1]["screen_time"] == 7.0      # narration outlasting the clip is honored
    assert abs(edl["total_duration"] - 10.0) < 0.01
    assert edl["cuts"][1]["in"] == edl["cuts"][0]["out"]   # contiguous timeline
    for cut in edl["cuts"]:
        assert cut["captions"]
        assert cut["captions"][-1]["end"] <= cut["screen_time"] + 0.001
