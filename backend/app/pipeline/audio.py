"""Stage 8: audio build — narration (ElevenLabs), music bed + librosa beat grid,
and the native-track leveling that the hybrid audio strategy calls for.

- Narration is the paid part → silent WAV in mock mode, real ElevenLabs otherwise.
- Beat detection runs librosa for real on the actual music file (no AI spend), so
  it works the same in mock and live modes.
"""
from __future__ import annotations

import re

from ..config import settings
from .. import media
from . import mock

# --- Mix levels (the hybrid audio strategy) ---------------------------------
# Native ambience/Foley sits 15–30% under narration; music is a quiet bed.
NARRATION_GAIN_DB = 0.0
NATIVE_DUCK_DB = -16.0      # ~15–30% under narration
MUSIC_BED_DB = -18.0

# --- Locked narration voices (mock catalog; real list comes from ElevenLabs) -
# Sarah (mature, reassuring, confident) is the default narrator.
DEFAULT_VOICE_ID = "voice_sarah"
MOCK_VOICES = [
    {"voice_id": "voice_sarah", "name": "Sarah",
     "labels": {"gender": "female", "tone": "mature, reassuring, confident"}},
    {"voice_id": "voice_aria", "name": "Aria", "labels": {"gender": "female", "tone": "warm"}},
    {"voice_id": "voice_george", "name": "George", "labels": {"gender": "male", "tone": "cinematic"}},
    {"voice_id": "voice_lily", "name": "Lily", "labels": {"gender": "female", "tone": "bright"}},
]

# Real ElevenLabs public voice ids backing the mock-catalog placeholders. A live
# project that stored a mock voice id (or fell back to DEFAULT_VOICE_ID without a
# voice ever being assigned) would otherwise 404 — "voice_sarah" is not a real
# ElevenLabs voice. Live users normally pick a real voice from list_voices();
# this is the fallback so the built-in catalog names always narrate. The legacy
# "voice_atlas"/"voice_sage"/"voice_nova" keys are kept as aliases so older
# projects that stored them still resolve.
_LIVE_VOICE_IDS = {
    "voice_sarah": "EXAVITQu4vr4xnSDxMaL",  # Sarah  (mature, reassuring, confident)
    "voice_aria": "9BWtsMINqrJLrRacOk9x",   # Aria   (warm female)
    "voice_george": "JBFqnCBsd6RMkjVDRZzb",  # George (cinematic male)
    "voice_lily": "pFZP5JQG7iQjIQuC4Bku",   # Lily   (bright female)
    # legacy placeholder aliases
    "voice_atlas": "JBFqnCBsd6RMkjVDRZzb",  # -> George
    "voice_sage": "EXAVITQu4vr4xnSDxMaL",   # -> Sarah
    "voice_nova": "pFZP5JQG7iQjIQuC4Bku",   # -> Lily
}
DEFAULT_LIVE_VOICE_ID = _LIVE_VOICE_IDS["voice_sarah"]


def resolve_live_voice_id(voice_id: str | None) -> str:
    """Map a stored voice id to a real ElevenLabs id for live narration.

    Mock-catalog placeholders translate to their real backing voice; a real id
    (one the user picked from list_voices) passes through unchanged; empty falls
    back to the default real voice.
    """
    if not voice_id:
        return DEFAULT_LIVE_VOICE_ID
    return _LIVE_VOICE_IDS.get(voice_id, voice_id)

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


def synth_narration(
    *, text: str, voice_id: str
) -> tuple[bytes, str, float, dict | None]:
    """Return (audio_bytes, content_type, duration_seconds, alignment) for one line.

    `alignment` is the ElevenLabs character-timing map (or None in mock mode / when
    timestamps are unavailable). It feeds per-sentence caption sync in the editor.
    """
    if settings.mock_generation:
        seconds = max(1.0, len(text) / 15.0)  # ~15 chars/sec speaking rate
        data = mock.silent_wav(seconds)
        return data, "audio/wav", media.duration_of(audio_or_video_bytes=data, suffix=".wav"), None

    from ..providers import elevenlabs_provider

    data, alignment = elevenlabs_provider.synth_with_timestamps(
        text=text, voice_id=resolve_live_voice_id(voice_id)
    )
    return data, "audio/mpeg", media.duration_of(audio_or_video_bytes=data, suffix=".mp3"), alignment


# --- Caption sync -----------------------------------------------------------
# Narration is one continuous voiceover whose lines play back-to-back, so each
# scene's on-screen time equals its narration duration (see editor.build_edl).
# Within a scene we split the line into sentence-level caption events so the burned
# text tracks the spoken words — timed from ElevenLabs character timestamps when
# available, else proportional to sentence length.

_SENTENCE_RE = re.compile(r".+?(?:[.!?]+(?:\s+|$)|$)", re.S)


def _sentences(text: str) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []
    return [m.group(0).strip() for m in _SENTENCE_RE.finditer(text) if m.group(0).strip()]


def _wrap(text: str, width: int) -> str:
    """Word-wrap into ~`width`-char lines so a burned caption doesn't run off-screen."""
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def _align_segments(sentences: list[str], text: str, alignment: dict, duration: float):
    starts = (alignment or {}).get("character_start_times_seconds") or []
    ends = (alignment or {}).get("character_end_times_seconds") or []
    if not starts:
        return None
    segs, pos = [], 0
    for s in sentences:
        idx = text.find(s, pos)
        if idx < 0:
            idx = pos
        start = float(starts[idx]) if idx < len(starts) else (segs[-1]["end"] if segs else 0.0)
        endpos = min(idx + len(s) - 1, len(ends) - 1)
        end = float(ends[endpos]) if endpos >= 0 and ends else duration
        segs.append({"text": s, "start": start, "end": end})
        pos = idx + len(s)
    return segs


def caption_segments(
    *, text: str, duration: float, alignment: dict | None = None, wrap_width: int = 38
) -> list[dict]:
    """Split narration into time-coded caption events spanning [0, duration].

    Returns ``[{"text", "start", "end"}]`` with starts/ends relative to the scene's
    narration. One event per sentence, wrapped to ~`wrap_width`-char lines. Timing
    uses the ElevenLabs alignment when given, else is proportional to sentence length.
    """
    text = " ".join((text or "").split())
    if not text or duration <= 0:
        return []
    sentences = _sentences(text) or [text]

    segs = _align_segments(sentences, text, alignment, duration) if alignment else None
    if not segs:
        total = sum(len(s) for s in sentences) or 1
        segs, t = [], 0.0
        for i, s in enumerate(sentences):
            end = duration if i == len(sentences) - 1 else t + duration * len(s) / total
            segs.append({"text": s, "start": t, "end": end})
            t = end

    return [
        {
            "text": _wrap(s["text"], wrap_width),
            "start": round(max(0.0, s["start"]), 3),
            "end": round(min(duration, max(s["start"] + 0.1, s["end"])), 3),
        }
        for s in segs
    ]


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
