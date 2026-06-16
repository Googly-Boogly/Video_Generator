"""fal.ai provider calls. Only invoked when MOCK_GENERATION is false.

Kept deliberately thin and config-driven: the model slug comes from
`models_config`, so this module never hardcodes a model. Reference images are
passed as public URLs (fal fetches them), which is how FLUX.2 enforces
character/style consistency.
"""
from __future__ import annotations

import httpx

from ..config import settings
from ..models_config import route

# Aspect ratio -> FLUX image_size hint.
_IMAGE_SIZE = {
    "16:9": "landscape_16_9",
    "9:16": "portrait_16_9",
    "1:1": "square_hd",
}


def _fetch(url: str) -> bytes:
    with httpx.Client(timeout=120) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content


def generate_image(
    *, model_id: str, prompt: str, aspect_ratio: str = "16:9",
    seed: int | None = None, reference_urls: list[str] | None = None,
) -> tuple[bytes, int]:
    """Generate one image with a text-to-image model (FLUX.2 / Seedream).

    Returns (png_or_jpg_bytes, seed_used). `reference_urls` are passed as
    reference-image inputs (capped at the model's max_reference_images).
    """
    if not settings.fal_key:
        raise RuntimeError("FAL_KEY not set (and mock mode is off).")

    import fal_client

    model = route(model_id)
    refs = (reference_urls or [])[: model.max_reference_images]

    arguments: dict = {
        "prompt": prompt,
        "image_size": _IMAGE_SIZE.get(aspect_ratio, "landscape_16_9"),
        "num_images": 1,
    }
    if seed is not None:
        arguments["seed"] = seed
    if refs:
        # FLUX.2 accepts reference images here; param name is centralized so a
        # provider tweak is a one-line change.
        arguments["image_urls"] = refs

    result = fal_client.subscribe(model.fal_id, arguments=arguments, with_logs=False)
    images = result.get("images") or []
    if not images:
        raise RuntimeError(f"{model.label} returned no images")
    used_seed = result.get("seed", seed if seed is not None else 0)
    return _fetch(images[0]["url"]), int(used_seed)


def generate_video(
    *, model_id: str, prompt: str, duration: float, aspect_ratio: str = "16:9",
    image_url: str | None = None, image_bytes: bytes | None = None,
    reference_urls: list[str] | None = None,
) -> bytes:
    """Generate one clip with an image→video or text→video model.

    The winning keyframe drives image→video (and seeds text→video for consistency).
    Prefer `image_bytes`: fal is a remote service and cannot fetch our local MinIO
    presigned URLs (http://localhost:9000/...), so we upload the bytes to fal and use
    the URL it returns. Returns the raw clip bytes (with native audio).
    """
    if not settings.fal_key:
        raise RuntimeError("FAL_KEY not set (and mock mode is off).")

    import fal_client

    model = route(model_id)

    # Some models cap prompt length (e.g. Kling = 2500 chars). Truncate to stay valid;
    # the scene action leads the prompt, so the tail (style descriptors) is what trims.
    if model.max_prompt_chars and len(prompt) > model.max_prompt_chars:
        prompt = prompt[: model.max_prompt_chars]

    # Snap clip length to a value the model accepts; pass as the string the API expects.
    if model.allowed_durations:
        dur = min(model.allowed_durations, key=lambda d: abs(d - duration))
        duration_arg: object = str(dur)
    else:
        duration_arg = round(min(duration, model.max_clip_seconds))

    arguments: dict = {
        "prompt": prompt,
        "duration": duration_arg,
        "aspect_ratio": aspect_ratio,
    }
    # Upload local keyframe bytes to fal (reachable HTTPS URL); fall back to a URL.
    if image_bytes is not None:
        arguments["image_url"] = fal_client.upload(image_bytes, "image/jpeg")
    elif image_url:
        arguments["image_url"] = image_url
    # Only forward reference URLs fal can actually fetch — our MinIO presigned URLs
    # are http://localhost and unreachable from fal. The uploaded keyframe above is
    # the image→video consistency anchor, so dropping local refs is safe.
    refs = [u for u in (reference_urls or []) if u.startswith("https://")][
        : model.max_reference_images
    ]
    if refs:
        arguments["reference_image_urls"] = refs

    result = fal_client.subscribe(model.fal_id, arguments=arguments, with_logs=False)
    video = result.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        raise RuntimeError(f"{model.label} returned no video")
    return _fetch(url)
