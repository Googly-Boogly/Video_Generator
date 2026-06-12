"""Stage 9: vision-equipped AI editor. Claude receives storyboard + frames +
narration durations + beat grid + audio_modes and outputs an Edit Decision List.

Phase 5 wires real vision + Claude. Phase 1 ships a deterministic mock EDL.
"""
from __future__ import annotations

from ..config import settings


def build_edl(*, project: dict, scenes: list[dict], beat_grid: dict | None) -> dict:
    """Return an Edit Decision List: clip order, trims, transitions, captions,
    and a per-scene mix plan (narration / music / native levels + ducking).
    """
    if settings.mock_generation:
        cuts = []
        t = 0.0
        for s in scenes:
            dur = s.get("duration_seconds", 5.0)
            is_dialogue = s.get("audio_mode") == "dialogue"
            cuts.append(
                {
                    "scene_number": s.get("scene_number"),
                    "in": round(t, 2),
                    "out": round(t + dur, 2),
                    "trim_head": 0.15,  # cut mushy clip starts
                    "trim_tail": 0.15,
                    "transition": "crossfade",
                    "caption": s.get("narration_text", "")[:120],
                    "mix": {
                        "narration_db": None if is_dialogue else 0.0,
                        "music_db": -18.0,
                        "native_db": 0.0 if is_dialogue else -16.0,
                        "duck_music_under_narration": True,
                        "pause_narration_for_dialogue": is_dialogue,
                    },
                }
            )
            t += dur
        return {"total_duration": round(t, 2), "cuts": cuts, "_mock": True}
    raise NotImplementedError("Real vision EDL editor lands in Phase 5.")
