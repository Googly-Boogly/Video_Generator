"""Stage 5: FLUX.2 keyframes, best-of-N with style references attached.

Per scene we render N variants (style reference images attached for consistency),
then a vision model auto-ranks them. The user can override the winner in the
UI; only the winner gets animated in Phase 3.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..models_config import DEFAULT_KEYFRAME_MODEL
from . import mock, prompts

KEYFRAME_VARIANTS = 1  # one keyframe per scene (set >1 to re-enable best-of-N + vision ranking)


@dataclass
class KeyframeVariant:
    index: int
    image_bytes: bytes
    media_type: str
    seed: int


def generate_keyframes(
    *, scene: dict, style_bible: dict | None, aspect_ratio: str = "16:9",
    n: int = KEYFRAME_VARIANTS, reference_urls: list[str] | None = None,
    model_id: str = DEFAULT_KEYFRAME_MODEL,
) -> list[KeyframeVariant]:
    """Render N keyframe variants for a scene, style references attached."""
    prompt = prompts.translate_image_prompt(scene=scene, style_bible=style_bible)
    variants: list[KeyframeVariant] = []
    for i in range(n):
        seed = 1000 + scene.get("scene_number", 0) * 10 + i
        if settings.mock_generation:
            variants.append(
                KeyframeVariant(
                    index=i,
                    image_bytes=mock.placeholder_png(f"{prompt}:{i}", width=32, height=18),
                    media_type="image/png",
                    seed=seed,
                )
            )
        else:
            from ..providers import fal_provider

            data, used = fal_provider.generate_image(
                model_id=model_id, prompt=prompt, aspect_ratio=aspect_ratio,
                seed=seed, reference_urls=reference_urls,
            )
            variants.append(KeyframeVariant(index=i, image_bytes=data, media_type="image/jpeg", seed=used))
    return variants


def rank_keyframes(
    variants: list[KeyframeVariant], *, scene: dict, character_sheet=None, llm: str | None = None
) -> dict:
    """Auto-rank variants. Returns {winner, scores:[{index,score,reason}]}."""
    if len(variants) <= 1:
        # Single keyframe — it's the winner; skip the (paid) vision ranking call.
        return {
            "winner": 0,
            "scores": [{"index": 0, "score": 1.0, "reason": "only keyframe"}] if variants else [],
        }
    if settings.mock_generation:
        # Deterministic mock ranking: first variant wins, descending synthetic scores.
        return {
            "winner": 0,
            "scores": [
                {"index": v.index, "score": round(0.9 - 0.1 * v.index, 2),
                 "reason": "mock ranking" if v.index else "best composition (mock)"}
                for v in variants
            ],
            "_mock": True,
        }

    from ..llm import rank_images

    result = rank_images(
        shot_description=scene.get("shot_description", ""),
        character_sheet=character_sheet,
        images=[(v.image_bytes, v.media_type) for v in variants],
        llm=llm,
    )
    # Clamp winner to a valid index.
    winner = int(result.get("winner", 0))
    result["winner"] = winner if 0 <= winner < len(variants) else 0
    return result
