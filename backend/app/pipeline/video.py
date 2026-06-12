"""Stage 6: image-to-video via the routed model + native-audio demux.

Dialogue scenes route to a lip-sync model (Veo) with dialogue_text embedded.
Native audio is demuxed from every clip into its own asset. In mock mode the
clip is encoded with FFmpeg from the winning keyframe (real, playable video),
just without paying an AI model.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..models_config import Tier, resolve_video_model, route
from .. import media
from . import prompts


@dataclass
class ClipResult:
    clip_bytes: bytes
    clip_content_type: str
    native_audio_bytes: bytes
    native_audio_content_type: str
    model_id: str


def resolve_model(*, scene: dict, tier: Tier) -> str:
    return resolve_video_model(
        model_override=scene.get("model_override"),
        suggested_model=scene.get("suggested_model"),
        audio_mode=scene.get("audio_mode", "narrated"),
        tier=tier,
    )


def generate_clip(
    *, scene: dict, style_bible: dict | None, tier: Tier,
    keyframe_bytes: bytes | None, keyframe_url: str | None = None,
    reference_urls: list[str] | None = None, aspect_ratio: str = "16:9",
) -> ClipResult:
    model_id = resolve_model(scene=scene, tier=tier)
    prompt = prompts.translate_video_prompt(model_id=model_id, scene=scene, style_bible=style_bible)
    duration = float(scene.get("duration_seconds", 5.0))

    if settings.mock_generation:
        if not keyframe_bytes:
            raise RuntimeError("missing winning keyframe for clip")
        clip = media.image_to_clip(
            image_bytes=keyframe_bytes, duration=duration, aspect_ratio=aspect_ratio
        )
    else:
        from ..providers import fal_provider

        clip = fal_provider.generate_video(
            model_id=model_id, prompt=prompt, duration=duration,
            aspect_ratio=aspect_ratio, image_url=keyframe_url, reference_urls=reference_urls,
        )

    # Demux native audio (ambience/Foley) into its own track.
    try:
        native = media.demux_audio(video_bytes=clip)
        native_ct = "audio/mp4"
    except media.FFmpegError:
        native = b""
        native_ct = "audio/mp4"

    return ClipResult(
        clip_bytes=clip, clip_content_type="video/mp4",
        native_audio_bytes=native, native_audio_content_type=native_ct,
        model_id=model_id,
    )
