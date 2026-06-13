"""Config-driven model routing table (SOTA as of June 2026).

Models WILL change. Swapping a model is a config change here, never a refactor.
Each route is keyed by a stable internal id; the pipeline only ever references
these ids. Pricing feeds the cost estimator.

`fal_id` is the slug passed to fal-client. `prompt_style` selects which
per-model prompt translator to use (Kling / Veo / Seedance respond to very
different prompt phrasing).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Modality(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_VIDEO = "text_to_video"


class Tier(str, Enum):
    DRAFT = "draft"      # budget tiers — used for draft renders
    PREMIUM = "premium"  # premium tiers — used for final renders


@dataclass(frozen=True)
class ModelRoute:
    id: str
    label: str
    provider: str            # "fal" | "google"
    fal_id: Optional[str]    # slug for fal-client, when provider == "fal"
    modality: Modality
    tier: Tier
    prompt_style: str        # which prompt translator to use
    # Pricing (used by cost estimator)
    price_per_image: float = 0.0
    price_per_second: float = 0.0
    # Capabilities
    max_reference_images: int = 0
    native_audio: bool = False
    lip_sync: bool = False
    max_clip_seconds: float = 10.0
    notes: str = ""
    google_id: Optional[str] = None  # model id when provider == "google"


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

MODEL_ROUTES: dict[str, ModelRoute] = {
    # --- Text-to-image (keyframes) ---
    "flux2-dev": ModelRoute(
        id="flux2-dev",
        label="FLUX.2 [dev]",
        provider="fal",
        fal_id="fal-ai/flux-2/dev",
        modality=Modality.TEXT_TO_IMAGE,
        tier=Tier.DRAFT,
        prompt_style="flux",
        price_per_image=0.025,
        max_reference_images=10,
        notes="Up to 10 reference images — enforces character/style consistency.",
    ),
    "seedream-5": ModelRoute(
        id="seedream-5",
        label="Seedream 5.0",
        provider="fal",
        fal_id="fal-ai/seedream/v5",
        modality=Modality.TEXT_TO_IMAGE,
        tier=Tier.PREMIUM,
        prompt_style="flux",
        price_per_image=0.06,
        max_reference_images=8,
        notes="Stronger cinematic composition. Premium keyframe fallback.",
    ),

    # --- Image-to-video ---
    "kling-3-pro": ModelRoute(
        id="kling-3-pro",
        label="Kling 3.0 Pro",
        provider="fal",
        fal_id="fal-ai/kling-video/v3/pro/image-to-video",
        modality=Modality.IMAGE_TO_VIDEO,
        tier=Tier.PREMIUM,
        prompt_style="kling",
        price_per_second=0.11,
        max_reference_images=1,
        native_audio=True,
        max_clip_seconds=10.0,
        notes="Strong motion + subject consistency across angles, native audio.",
    ),
    "kling-25-turbo": ModelRoute(
        id="kling-25-turbo",
        label="Kling 2.5 Turbo Pro",
        provider="fal",
        fal_id="fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
        modality=Modality.IMAGE_TO_VIDEO,
        tier=Tier.DRAFT,
        prompt_style="kling",
        price_per_second=0.07,
        max_reference_images=1,
        native_audio=True,
        max_clip_seconds=10.0,
        notes="Budget tier for draft passes.",
    ),

    # --- Text-to-video / hero shots ---
    "veo-31": ModelRoute(
        id="veo-31",
        label="Veo 3.1",
        provider="google",
        fal_id=None,
        google_id="veo-3.1-generate-preview",
        modality=Modality.TEXT_TO_VIDEO,
        tier=Tier.PREMIUM,
        prompt_style="veo",
        price_per_second=0.15,
        max_reference_images=3,
        native_audio=True,
        lip_sync=True,
        max_clip_seconds=8.0,
        notes="Google direct. Native synchronized audio + lip-synced speech, 4K. Dialogue scenes.",
    ),
    "veo-31-lite": ModelRoute(
        id="veo-31-lite",
        label="Veo 3.1 Lite",
        provider="google",
        fal_id=None,
        google_id="veo-3.1-fast-generate-preview",
        modality=Modality.TEXT_TO_VIDEO,
        tier=Tier.DRAFT,
        prompt_style="veo",
        price_per_second=0.05,
        native_audio=True,
        lip_sync=True,
        max_clip_seconds=8.0,
        notes="Google direct, draft tier hero shots.",
    ),
    "seedance-2": ModelRoute(
        id="seedance-2",
        label="Seedance 2.0",
        provider="fal",
        fal_id="fal-ai/seedance/v2",
        modality=Modality.TEXT_TO_VIDEO,
        tier=Tier.PREMIUM,
        prompt_style="seedance",
        price_per_second=0.12,
        max_reference_images=9,
        native_audio=True,
        max_clip_seconds=15.0,
        notes="Up to 9 reference images + audio inputs, multi-shot up to 15s.",
    ),
}

# ElevenLabs TTS pricing (per 1k characters) — used by the cost estimator.
TTS_PRICE_PER_1K_CHARS = 0.30

# ---------------------------------------------------------------------------
# Default routing rules
# ---------------------------------------------------------------------------

DEFAULT_KEYFRAME_MODEL = "flux2-dev"

# Default image-to-video model by render tier.
DEFAULT_VIDEO_BY_TIER: dict[Tier, str] = {
    Tier.DRAFT: "kling-25-turbo",
    Tier.PREMIUM: "kling-3-pro",
}

# Dialogue scenes must route to a lip-sync capable model.
DEFAULT_DIALOGUE_BY_TIER: dict[Tier, str] = {
    Tier.DRAFT: "veo-31-lite",
    Tier.PREMIUM: "veo-31",
}


def route(model_id: str) -> ModelRoute:
    if model_id not in MODEL_ROUTES:
        raise KeyError(f"Unknown model route: {model_id!r}")
    return MODEL_ROUTES[model_id]


def default_video_model(tier: Tier, audio_mode: str) -> str:
    """Pick the default video model for a scene given render tier + audio mode."""
    if audio_mode == "dialogue":
        return DEFAULT_DIALOGUE_BY_TIER[tier]
    return DEFAULT_VIDEO_BY_TIER[tier]


def resolve_video_model(
    *, model_override: str | None, suggested_model: str | None,
    audio_mode: str, tier: Tier,
) -> str:
    """Single source of truth for which video model a scene renders on.

    Resolution order (matches the spec's routing rules):
      1. An explicit per-scene `model_override` always wins (both tiers).
      2. Premium renders use the storyboard's `suggested_model` (a premium pick).
      3. Draft renders use the budget-tier default for the audio mode, so draft
         passes are genuinely cheaper than finals.
    """
    if model_override and model_override in MODEL_ROUTES:
        return model_override
    if tier == Tier.PREMIUM and suggested_model in MODEL_ROUTES:
        return suggested_model
    return default_video_model(tier, audio_mode)


def models_for_modality(modality: Modality) -> list[ModelRoute]:
    return [m for m in MODEL_ROUTES.values() if m.modality == modality]


def video_models() -> list[ModelRoute]:
    return [
        m
        for m in MODEL_ROUTES.values()
        if m.modality in (Modality.IMAGE_TO_VIDEO, Modality.TEXT_TO_VIDEO)
    ]
