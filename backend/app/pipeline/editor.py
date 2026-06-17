"""Stage 9: vision-equipped AI editor → Edit Decision List.

The vision model receives the storyboard + extracted frames + narration durations + beat
grid + audio_modes and returns an EDL: clip order, in/out trims (cut mushy clip
starts/ends), a transition per cut, captions with timestamps, and a per-scene mix
plan (narration / music / native levels, ducking, narration pauses for dialogue).

**Narration-led timeline:** narration is one continuous track, so each scene's on-screen
time (`screen_time`) equals its narration duration — the render fits the clip to that
(trim if longer, clone-pad if shorter). Captions are split into sentence-level events
(`captions[{text,start,end}]`) via `audio.caption_segments`, timed from the ElevenLabs
character alignment when available, so the burned text tracks the spoken voiceover.

Mock mode produces a deterministic EDL from the same real signals (durations,
narration lengths/alignment, beat grid); the vision path is used when live, then run
through `_normalize_timeline` so the render is narration-led regardless of source.
"""
from __future__ import annotations

from ..config import settings
from . import audio as a_stage

DEFAULT_TRIM = 0.15  # trim mushy clip starts/ends


def build_edl(*, project: dict, scenes: list[dict], beat_grid: dict | None = None,
              frames: list[dict] | None = None, llm: str | None = None) -> dict:
    """Return an Edit Decision List.

    `scenes`: ordered dicts {scene_number, duration, audio_mode, native_muted,
    narration_text, narration_duration}. `frames`: optional per-scene frame bytes
    for the live vision path.
    """
    if not settings.mock_generation and frames:
        edl = _vision_edl(project=project, scenes=scenes, beat_grid=beat_grid, frames=frames, llm=llm)
        return _normalize_timeline(edl, scenes)

    cuts, t = [], 0.0
    beats = (beat_grid or {}).get("beats") or []
    first = scenes[0]["scene_number"] if scenes else None
    for s in scenes:
        clip_dur = float(s.get("duration", 5.0))
        th = tt = min(DEFAULT_TRIM, clip_dur / 6)
        clip_trimmed = max(0.3, clip_dur - th - tt)
        text = (s.get("narration_text") or "").strip()
        narr_dur = s.get("narration_duration")
        narrated = s.get("audio_mode", "narrated") != "dialogue" and bool(text) and bool(narr_dur)

        # Narration-led timeline: each scene is on screen for exactly its narration's
        # duration, so the burned caption and the single continuous voiceover stay
        # locked scene-for-scene (the clip is looped/clone-padded or trimmed to fit).
        # Dialogue / no-narration scenes fall back to the trimmed clip length.
        screen_time = max(0.3, float(narr_dur)) if narrated else clip_trimmed
        if narrated:
            caps = a_stage.caption_segments(
                text=text, duration=screen_time, alignment=s.get("narration_alignment"),
            )
        elif text:
            caps = [{"text": text[:120], "start": 0.0, "end": round(screen_time, 3)}]
        else:
            caps = []

        mix = a_stage.mix_plan(
            audio_mode=s.get("audio_mode", "narrated"),
            native_muted=bool(s.get("native_muted")),
        )
        cuts.append({
            "scene_number": s["scene_number"],
            "in": round(t, 2),
            "out": round(t + screen_time, 2),
            "screen_time": round(screen_time, 3),
            "trim_head": round(th, 2),
            "trim_tail": round(tt, 2),
            "transition": "cut" if s["scene_number"] == first else "crossfade",
            "caption": " ".join(c["text"].replace("\n", " ") for c in caps)[:120],
            "captions": caps,
            "on_beat": _nearest_beat(t, beats),
            "mix": mix,
        })
        t += screen_time

    return {
        "total_duration": round(t, 2),
        "cuts": cuts,
        "beat_grid": {"bpm": (beat_grid or {}).get("bpm"), "beats": len(beats)} if beat_grid else None,
        "levels": {"narration_db": a_stage.NARRATION_GAIN_DB,
                   "native_db": a_stage.NATIVE_DUCK_DB, "music_db": a_stage.MUSIC_BED_DB},
        "engine": "mock" if settings.mock_generation else "rules",
    }


def _nearest_beat(t: float, beats: list[float]) -> float | None:
    if not beats:
        return None
    return round(min(beats, key=lambda b: abs(b - t)), 3)


def _normalize_timeline(edl: dict, scenes: list[dict]) -> dict:
    """Ensure every cut carries `screen_time` + `captions` and a narration-led
    timeline, regardless of EDL source (the vision model may omit either). Keeps the
    render path identical for mock, rules, and vision EDLs."""
    by_num = {s["scene_number"]: s for s in scenes}
    t = 0.0
    for cut in edl.get("cuts", []):
        s = by_num.get(cut.get("scene_number"), {})
        st = cut.get("screen_time")
        if st is None:
            st = s.get("narration_duration") or (cut.get("out", 0) - cut.get("in", 0)) \
                or float(s.get("duration", 5.0))
        st = max(0.3, float(st))
        cut["screen_time"] = round(st, 3)
        cut["in"], cut["out"] = round(t, 2), round(t + st, 2)
        if not cut.get("captions"):
            text = (s.get("narration_text") or "").strip()
            if text and s.get("audio_mode", "narrated") != "dialogue":
                cut["captions"] = a_stage.caption_segments(
                    text=text, duration=st, alignment=s.get("narration_alignment"))
            else:
                cap = (cut.get("caption") or "").strip()
                cut["captions"] = [{"text": cap[:120], "start": 0.0, "end": round(st, 3)}] if cap else []
        t += st
    edl["total_duration"] = round(t, 2)
    return edl


def _vision_edl(*, project, scenes, beat_grid, frames, llm=None) -> dict:
    """Live path: vision model proposes trims/transitions/captions from frames."""
    from ..llm import vision_json

    system = (
        "You are a film editor. Given the storyboard, sampled frames per scene, "
        "narration durations, the music beat grid, and each scene's audio_mode, "
        "output an Edit Decision List as STRICT JSON with the shape: "
        '{"total_duration": float, "cuts": [{"scene_number": int, "in": float, '
        '"out": float, "trim_head": float, "trim_tail": float, "transition": '
        '"cut|crossfade|dip", "caption": str, "mix": {"narration_db": float|null, '
        '"music_db": float, "native_db": float|null, "duck_music_under_narration": '
        'bool, "pause_narration_for_dialogue": bool}}]}. Trim mushy clip starts/ends, '
        "snap cuts near beats, and pause narration on dialogue scenes."
    )
    text = (
        f"SCENES: {[{k: s.get(k) for k in ('scene_number','duration','audio_mode','narration_text','narration_duration')} for s in scenes]}\n"
        f"BEAT GRID bpm={(beat_grid or {}).get('bpm')} beats={(beat_grid or {}).get('beats')}\n"
        "The frames (in scene order) follow."
    )
    images = [(fr["bytes"], "image/jpeg") for fr in (frames or [])]
    edl = vision_json(system=system, text=text, images=images, max_tokens=4096, llm=llm)
    edl["engine"] = "vision"
    return edl
