"""ElevenLabs TTS — project narration. Only invoked when MOCK_GENERATION is false.

One locked voice id per project carries the narration; native model audio is
never used for narration (voice identity can't persist across generation calls).
"""
from __future__ import annotations

from ..config import settings

# Default narration model. Voice id is per-project (Project.voice_id).
TTS_MODEL = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"


def synth(*, text: str, voice_id: str) -> bytes:
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set (and mock mode is off).")

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    audio = client.text_to_speech.convert(
        voice_id=voice_id, model_id=TTS_MODEL, text=text, output_format=OUTPUT_FORMAT,
    )
    return b"".join(audio)


def synth_with_timestamps(*, text: str, voice_id: str) -> tuple[bytes, dict | None]:
    """Synthesize narration and return (mp3_bytes, alignment).

    `alignment` is ElevenLabs' character-level timing
    (`{characters, character_start_times_seconds, character_end_times_seconds}`),
    used for caption sync. Degrades to plain `synth()` + None if the timestamped
    endpoint is unavailable in the installed SDK or errors out — narration is far
    more important than caption timing, so this must never fail the audio build.
    """
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set (and mock mode is off).")

    import base64

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    try:
        res = client.text_to_speech.convert_with_timestamps(
            voice_id=voice_id, model_id=TTS_MODEL, text=text, output_format=OUTPUT_FORMAT,
        )
        audio = base64.b64decode(res.audio_base_64)
        al = getattr(res, "alignment", None)
        alignment = {
            "characters": getattr(al, "characters", None),
            "character_start_times_seconds": getattr(al, "character_start_times_seconds", None),
            "character_end_times_seconds": getattr(al, "character_end_times_seconds", None),
        } if al else None
        return audio, alignment
    except Exception:  # noqa: BLE001 — fall back to untimed narration
        return synth(text=text, voice_id=voice_id), None


def list_voices() -> list[dict]:
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set (and mock mode is off).")

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    res = client.voices.get_all()
    return [
        {"voice_id": v.voice_id, "name": v.name, "labels": getattr(v, "labels", {}) or {}}
        for v in res.voices
    ]
