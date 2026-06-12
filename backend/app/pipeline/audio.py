"""Stage 8: audio build — narration (ElevenLabs), music bed + librosa beat grid,
and the native-track leveling that the hybrid audio strategy calls for.

- Narration is the paid part → silent WAV in mock mode, real ElevenLabs otherwise.
- Beat detection runs librosa for real on the actual music file (no AI spend), so
  it works the same in mock and live modes.
"""
from __future__ import annotations

from ..config import settings
from .. import media
from . import mock

# --- Mix levels (the hybrid audio strategy) ---------------------------------
# Native ambience/Foley sits 15–30% under narration; music is a quiet bed.
NARRATION_GAIN_DB = 0.0
NATIVE_DUCK_DB = -16.0      # ~15–30% under narration
MUSIC_BED_DB = -18.0

# --- Locked narration voices (mock catalog; real list comes from ElevenLabs) -
DEFAULT_VOICE_ID = "voice_aria"
MOCK_VOICES = [
    {"voice_id": "voice_aria", "name": "Aria", "labels": {"gender": "female", "tone": "warm"}},
    {"voice_id": "voice_atlas", "name": "Atlas", "labels": {"gender": "male", "tone": "cinematic"}},
    {"voice_id": "voice_sage", "name": "Sage", "labels": {"gender": "neutral", "tone": "calm"}},
    {"voice_id": "voice_nova", "name": "Nova", "labels": {"gender": "female", "tone": "bright"}},
]

# --- Built-in music library (synthesized on demand; real librosa analysis) ----
MUSIC_LIBRARY = [
    {"id": "ambient-80", "name": "Ambient Drift", "bpm": 80, "style": "ambient", "seconds": 60},
    {"id": "cinematic-100", "name": "Cinematic Rise", "bpm": 100, "style": "cinematic", "seconds": 60},
    {"id": "upbeat-128", "name": "Upbeat Pulse", "bpm": 128, "style": "upbeat", "seconds": 60},
]


def list_voices() -> list[dict]:
    if settings.mock_generation:
        return MOCK_VOICES
    from ..providers import elevenlabs_provider

    return elevenlabs_provider.list_voices()


def synth_narration(*, text: str, voice_id: str) -> tuple[bytes, str, float]:
    """Return (audio_bytes, content_type, duration_seconds) for one narration line."""
    if settings.mock_generation:
        seconds = max(1.0, len(text) / 15.0)  # ~15 chars/sec speaking rate
        data = mock.silent_wav(seconds)
        return data, "audio/wav", media.duration_of(audio_or_video_bytes=data, suffix=".wav")

    from ..providers import elevenlabs_provider

    data = elevenlabs_provider.synth(text=text, voice_id=voice_id)
    return data, "audio/mpeg", media.duration_of(audio_or_video_bytes=data, suffix=".mp3")


def synth_library_bed(*, track_id: str) -> tuple[bytes, dict]:
    """Synthesize a built-in library bed. Returns (mp3_bytes, track_meta)."""
    track = next((t for t in MUSIC_LIBRARY if t["id"] == track_id), None)
    if not track:
        raise KeyError(f"unknown library track: {track_id}")
    data = media.synth_music_bed(bpm=track["bpm"], seconds=track["seconds"], style=track["style"])
    return data, dict(track)


def beat_grid(*, audio_bytes: bytes, suffix: str = ".mp3", bpm_hint: int | None = None) -> dict:
    """Detect tempo + beat times with librosa. Falls back to a synthetic grid
    from `bpm_hint` if librosa is unavailable.
    """
    import tempfile
    from pathlib import Path

    try:
        import librosa

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"m{suffix}"
            p.write_bytes(audio_bytes)
            y, sr = librosa.load(str(p), mono=True)
            tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
            beats = librosa.frames_to_time(beat_frames, sr=sr).tolist()
            duration = float(librosa.get_duration(y=y, sr=sr))
        return {
            "bpm": round(float(tempo if not hasattr(tempo, "__len__") else tempo[0]), 1),
            "beats": [round(b, 3) for b in beats],
            "duration": round(duration, 2),
            "engine": "librosa",
        }
    except Exception:  # noqa: BLE001 — degrade gracefully if librosa/codec missing
        bpm = bpm_hint or 120
        duration = media.duration_of(audio_or_video_bytes=audio_bytes, suffix=suffix) or 60.0
        step = 60.0 / bpm
        n = int(duration / step)
        return {
            "bpm": float(bpm),
            "beats": [round(i * step, 3) for i in range(n)],
            "duration": round(duration, 2),
            "engine": "fallback",
        }


def mix_plan(*, audio_mode: str, native_muted: bool) -> dict:
    """Per-scene mix levels used by the editor/render."""
    is_dialogue = audio_mode == "dialogue"
    return {
        "narration_db": None if is_dialogue else NARRATION_GAIN_DB,
        "music_db": MUSIC_BED_DB,
        "native_db": (0.0 if is_dialogue else NATIVE_DUCK_DB) if not native_muted else None,
        "duck_music_under_narration": True,
        "pause_narration_for_dialogue": is_dialogue,
    }
