"""Stage 6: video generation via the routed model + native-audio demux.

Default routing is photo-to-video: the routed image-to-video model (Kling) animates
the winning keyframe. A scene can instead select a text-to-video model (Veo) as its
`model_override` — that generates the clip straight from the prompt and **overrides
the keyframe** (no image is sent). Native audio is demuxed from every clip into its
own asset. In mock mode the clip is encoded with FFmpeg from the keyframe (real,
playable video), just without paying an AI model.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..models_config import Modality, Tier, resolve_video_model, route
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
    # Text-to-video (Veo) generates from the prompt and overrides the keyframe — no
    # image is sent. Image-to-video (Kling) animates the keyframe as before.
    text_to_video = route(model_id).modality == Modality.TEXT_TO_VIDEO

    if settings.mock_generation:
        # Mock always encodes a playable placeholder from the keyframe (even for t2v),
        # so the FFmpeg path + UI preview are exercised without paying a model.
        if not keyframe_bytes:
            raise RuntimeError("missing winning keyframe for clip")
        clip = media.image_to_clip(
            image_bytes=keyframe_bytes, duration=duration, aspect_ratio=aspect_ratio
        )
    else:
        from ..providers import generation

        clip = generation.generate_video(
            model_id=model_id, prompt=prompt, duration=duration, aspect_ratio=aspect_ratio,
            image_url=None if text_to_video else keyframe_url,
            image_bytes=None if text_to_video else keyframe_bytes,
            reference_urls=reference_urls,
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
