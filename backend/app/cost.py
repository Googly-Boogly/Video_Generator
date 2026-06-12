"""Running cost estimator, computed from the model routing table.

Shown before every paid step. In mock mode the real spend is $0, but the
estimate still reflects what a real run would cost.
"""
from __future__ import annotations

from . import models_config as mc
from .models import Project, Scene
from .schemas import CostEstimate, CostLineItem
from .models_config import Tier


KEYFRAME_VARIANTS = 3  # best-of-N


def _scene_video_model(scene: Scene, tier: Tier) -> mc.ModelRoute:
    model_id = mc.resolve_video_model(
        model_override=scene.model_override,
        suggested_model=scene.suggested_model,
        audio_mode=scene.audio_mode,
        tier=tier,
    )
    return mc.route(model_id)


def estimate_keyframes(project: Project) -> CostEstimate:
    items: list[CostLineItem] = []
    kf = mc.route(mc.DEFAULT_KEYFRAME_MODEL)
    total = 0.0
    for scene in project.scenes:
        amount = kf.price_per_image * KEYFRAME_VARIANTS
        total += amount
        items.append(
            CostLineItem(
                label=f"Scene {scene.scene_number} keyframes",
                detail=f"{KEYFRAME_VARIANTS}× {kf.label} @ ${kf.price_per_image:.3f}/img",
                amount=round(amount, 4),
            )
        )
    return CostEstimate(step="keyframes", line_items=items, total=round(total, 4))


def estimate_video(project: Project, tier: Tier = Tier.DRAFT) -> CostEstimate:
    items: list[CostLineItem] = []
    total = 0.0
    for scene in project.scenes:
        model = _scene_video_model(scene, tier)
        amount = model.price_per_second * scene.duration_seconds
        total += amount
        items.append(
            CostLineItem(
                label=f"Scene {scene.scene_number} clip",
                detail=f"{scene.duration_seconds:.0f}s × {model.label} @ ${model.price_per_second:.3f}/s",
                amount=round(amount, 4),
            )
        )
    return CostEstimate(step=f"video_{tier.value}", line_items=items, total=round(total, 4))


def estimate_audio(project: Project) -> CostEstimate:
    items: list[CostLineItem] = []
    total = 0.0
    for scene in project.scenes:
        if scene.audio_mode != "narrated" or not scene.narration_text:
            continue
        chars = len(scene.narration_text)
        amount = (chars / 1000.0) * mc.TTS_PRICE_PER_1K_CHARS
        total += amount
        items.append(
            CostLineItem(
                label=f"Scene {scene.scene_number} narration",
                detail=f"{chars} chars @ ${mc.TTS_PRICE_PER_1K_CHARS:.2f}/1k (ElevenLabs)",
                amount=round(amount, 4),
            )
        )
    return CostEstimate(step="audio", line_items=items, total=round(total, 4))


def estimate_full_project(project: Project, tier: Tier = Tier.PREMIUM) -> CostEstimate:
    kf = estimate_keyframes(project)
    vid = estimate_video(project, tier)
    aud = estimate_audio(project)
    items = (
        [CostLineItem(label="Keyframes", detail="best-of-N", amount=kf.total)]
        + [CostLineItem(label="Video", detail=tier.value, amount=vid.total)]
        + [CostLineItem(label="Narration", detail="ElevenLabs TTS", amount=aud.total)]
    )
    total = kf.total + vid.total + aud.total
    return CostEstimate(step="full_project", line_items=items, total=round(total, 4))
