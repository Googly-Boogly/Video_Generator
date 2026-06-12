"""Expose the model routing table + style presets to the frontend."""
from __future__ import annotations

from fastapi import APIRouter

from ..config import settings
from ..models_config import MODEL_ROUTES, Modality

router = APIRouter(prefix="/api/config", tags=["config"])

STYLE_PRESETS = [
    "cinematic", "anime", "documentary", "noir", "watercolor",
    "claymation", "retro 80s", "hyperreal",
]


@router.get("")
def get_config():
    return {
        "mock_generation": settings.mock_generation,
        "style_presets": STYLE_PRESETS,
        "target_lengths": [15, 30, 60],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "modality": m.modality.value,
                "tier": m.tier.value,
                "price_per_image": m.price_per_image,
                "price_per_second": m.price_per_second,
                "native_audio": m.native_audio,
                "lip_sync": m.lip_sync,
                "max_reference_images": m.max_reference_images,
                "notes": m.notes,
            }
            for m in MODEL_ROUTES.values()
        ],
        "video_models": [
            m.id
            for m in MODEL_ROUTES.values()
            if m.modality in (Modality.IMAGE_TO_VIDEO, Modality.TEXT_TO_VIDEO)
        ],
    }
