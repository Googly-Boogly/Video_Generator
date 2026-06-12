"""Stage 2: Style bible generation + master reference images.

The style document (palette, lighting, lens, locked character sheet) is produced
in Phase 1. Phase 2 adds the 3–5 master reference images (character turnaround,
environment, color key) rendered via FLUX.2 — these are passed as reference-image
inputs to EVERY subsequent keyframe and reference-to-video call to enforce
character/style consistency.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..llm import complete_json
from ..models_config import DEFAULT_KEYFRAME_MODEL
from . import mock, prompts


@dataclass
class ReferenceImage:
    role: str          # character | environment | colorkey | extra
    prompt: str
    image_bytes: bytes
    media_type: str
    seed: int


_ROLES = ["character", "environment", "colorkey"]


def generate_style_bible(*, idea: str, style_preset: str, aspect_ratio: str) -> dict:
    """Produce the locked style document (reference-image *prompts* included)."""
    if settings.mock_generation:
        return mock.mock_style_bible(idea, style_preset)

    return complete_json(
        system=prompts.STYLE_BIBLE_SYSTEM,
        user=prompts.style_bible_user_prompt(
            idea=idea, style_preset=style_preset, aspect_ratio=aspect_ratio
        ),
    )


def generate_reference_images(
    *, style_bible: dict, aspect_ratio: str, model_id: str = DEFAULT_KEYFRAME_MODEL,
) -> list[ReferenceImage]:
    """Render the master reference images from the style bible's prompts."""
    ref_prompts: list[str] = style_bible.get("reference_image_prompts") or []
    out: list[ReferenceImage] = []
    for i, ref_prompt in enumerate(ref_prompts):
        role = _ROLES[i] if i < len(_ROLES) else "extra"
        full_prompt = f"{ref_prompt}\n{prompts.style_block(style_bible)}".strip()
        if settings.mock_generation:
            data = mock.placeholder_png(f"ref:{role}:{ref_prompt}", width=32, height=18)
            out.append(ReferenceImage(role, ref_prompt, data, "image/png", 7000 + i))
        else:
            from ..providers import fal_provider

            data, seed = fal_provider.generate_image(
                model_id=model_id, prompt=full_prompt, aspect_ratio=aspect_ratio,
                seed=7000 + i,
            )
            out.append(ReferenceImage(role, ref_prompt, data, "image/jpeg", seed))
    return out
