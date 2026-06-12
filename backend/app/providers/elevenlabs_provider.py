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
