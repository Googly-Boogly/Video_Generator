"""Google (Veo) video generation — direct, not via fal. Only invoked when
MOCK_GENERATION is false and a model route has provider == "google".

Uses the unified google-genai SDK. Veo returns a long-running operation that we
poll, then download the resulting video bytes. Model ids + the exact response
shape are best-effort against the spec and should be verified on the first live
call (see the cost-aware rollout plan).
"""
from __future__ import annotations

import time

from ..config import settings
from ..models_config import route

# Veo supports 16:9 and 9:16; map square to landscape.
_ASPECT = {"16:9": "16:9", "9:16": "9:16", "1:1": "16:9"}


def generate_video(
    *, model_id: str, prompt: str, duration: float, aspect_ratio: str = "16:9",
    image_bytes: bytes | None = None, image_mime: str = "image/png",
) -> bytes:
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY not set (and mock mode is off).")

    from google import genai
    from google.genai import types

    model = route(model_id)
    client = genai.Client(api_key=settings.google_api_key)

    kwargs: dict = {
        "model": model.google_id,
        "prompt": prompt,
        "config": types.GenerateVideosConfig(
            aspect_ratio=_ASPECT.get(aspect_ratio, "16:9"),
            number_of_videos=1,
            duration_seconds=int(min(duration, model.max_clip_seconds)),
        ),
    }
    if image_bytes is not None:
        # Image-to-video: drive the clip from the winning keyframe.
        kwargs["image"] = types.Image(image_bytes=image_bytes, mime_type=image_mime)

    operation = client.models.generate_videos(**kwargs)
    while not operation.done:
        time.sleep(10)
        operation = client.operations.get(operation)

    videos = operation.response.generated_videos
    if not videos:
        raise RuntimeError("Veo returned no video")
    video = videos[0].video
    # Prefer inline bytes; otherwise download via the files API.
    data = getattr(video, "video_bytes", None)
    if not data:
        data = client.files.download(file=video)
    return bytes(data)
