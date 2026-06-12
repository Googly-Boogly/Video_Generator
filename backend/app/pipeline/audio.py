"""Stage 8: audio build — ElevenLabs narration, music bed + librosa beat grid,
native track leveling per the hybrid audio strategy.

Phase 4 wires ElevenLabs + librosa. Phase 1 ships silent stubs.
"""
from __future__ import annotations

from ..config import settings
from . import mock

# Native ambience/Foley is mixed this far under narration.
NATIVE_DUCK_DB = -16.0  # ~15-30% under narration
NARRATION_GAIN_DB = 0.0
MUSIC_BED_DB = -18.0


def synth_narration(*, text: str, voice_id: str, seconds_hint: float = 3.0) -> bytes:
    if settings.mock_generation:
        return mock.silent_wav(max(seconds_hint, len(text) / 15.0))
    raise NotImplementedError("Real ElevenLabs TTS lands in Phase 4.")


def beat_grid(*, music_key: str) -> dict:
    """Beat-detected grid so the editor can cut on beat."""
    if settings.mock_generation:
        return {"bpm": 120.0, "beats": [i * 0.5 for i in range(64)], "_mock": True}
    raise NotImplementedError("librosa beat detection lands in Phase 4.")
