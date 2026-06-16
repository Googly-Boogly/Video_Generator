"""Provider dispatch for video generation.

Images are fal-only (FLUX/Seedream), so the keyframe stage calls fal_provider
directly. Video can route to fal (Kling/Seedance) or Google direct (Veo), so the
video stage goes through here.
"""
from __future__ import annotations

from .. import models_config as mc


def generate_video(
    *, model_id: str, prompt: str, duration: float, aspect_ratio: str,
    image_url: str | None = None, image_bytes: bytes | None = None,
    reference_urls: list[str] | None = None,
) -> bytes:
    """Dispatch to the right provider based on the model's route."""
    provider = mc.route(model_id).provider
    if provider == "fal":
        from . import fal_provider

        return fal_provider.generate_video(
            model_id=model_id, prompt=prompt, duration=duration,
            aspect_ratio=aspect_ratio, image_url=image_url, image_bytes=image_bytes,
            reference_urls=reference_urls,
        )
    if provider == "google":
        from . import google_provider

        return google_provider.generate_video(
            model_id=model_id, prompt=prompt, duration=duration,
            aspect_ratio=aspect_ratio, image_bytes=image_bytes,
        )
    raise RuntimeError(f"no video provider for {provider!r} (model {model_id})")
