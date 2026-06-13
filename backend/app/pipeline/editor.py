"""Stage 9: vision-equipped AI editor → Edit Decision List.

The vision model receives the storyboard + extracted frames + narration durations + beat
grid + audio_modes and returns an EDL: clip order, in/out trims (cut mushy clip
starts/ends), a transition per cut, captions with timestamps, and a per-scene mix
plan (narration / music / native levels, ducking, narration pauses for dialogue).

Mock mode produces a deterministic EDL from the same real signals (durations,
narration lengths, beat grid); the vision path is used when live.
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
        return _vision_edl(project=project, scenes=scenes, beat_grid=beat_grid, frames=frames, llm=llm)

    cuts, t = [], 0.0
    beats = (beat_grid or {}).get("beats") or []
    for s in scenes:
        dur = float(s.get("duration", 5.0))
        th = tt = min(DEFAULT_TRIM, dur / 6)
        tdur = max(0.3, dur - th - tt)
        mix = a_stage.mix_plan(
            audio_mode=s.get("audio_mode", "narrated"),
            native_muted=bool(s.get("native_muted")),
        )
        cuts.append({
            "scene_number": s["scene_number"],
            "in": round(t, 2),
            "out": round(t + tdur, 2),
            "trim_head": round(th, 2),
            "trim_tail": round(tt, 2),
            "transition": "cut" if s["scene_number"] == scenes[0]["scene_number"] else "crossfade",
            "caption": (s.get("narration_text") or "").strip()[:120],
            "on_beat": _nearest_beat(t, beats),
            "mix": mix,
        })
        t += tdur

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
