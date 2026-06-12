"""Running cost estimator, computed from the model routing table.

Shown before every paid step. In mock mode the real spend is $0, but the
estimate still reflects what a real run would cost.
"""
from __future__ import annotations

from . import models_config as mc
from .config import settings
from .models import CostEntry, Project, Scene
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


# ---------------------------------------------------------------------------
# Actual-spend ledger (recorded as paid steps run)
# ---------------------------------------------------------------------------

def add_entry(db, project_id: str, job_id: str | None, step: str, label: str,
              detail: str, amount: float) -> None:
    db.add(CostEntry(
        project_id=project_id, job_id=job_id, step=step, label=label,
        detail=detail, amount=round(amount, 4), mock=settings.mock_generation,
    ))


def record_keyframes(db, project_id, job_id, scene_number) -> None:
    kf = mc.route(mc.DEFAULT_KEYFRAME_MODEL)
    add_entry(db, project_id, job_id, "keyframes", f"Scene {scene_number} keyframes",
              f"{KEYFRAME_VARIANTS}× {kf.label}", kf.price_per_image * KEYFRAME_VARIANTS)


def record_clip(db, project_id, job_id, scene_number, model_id, duration, step="video") -> None:
    m = mc.route(model_id)
    add_entry(db, project_id, job_id, step, f"Scene {scene_number} clip",
              f"{duration:.0f}s × {m.label}", m.price_per_second * duration)


def record_narration(db, project_id, job_id, scene_number, chars) -> None:
    amount = (chars / 1000.0) * mc.TTS_PRICE_PER_1K_CHARS
    add_entry(db, project_id, job_id, "audio", f"Scene {scene_number} narration",
              f"{chars} chars (ElevenLabs)", amount)


def dashboard(project: Project) -> dict:
    """Estimated (full project, premium) vs actual ledger, grouped by step."""
    estimated = estimate_full_project(project, Tier.PREMIUM)
    by_step: dict[str, float] = {}
    entries = []
    for e in sorted(project.cost_entries, key=lambda x: x.created_at):
        by_step[e.step] = round(by_step.get(e.step, 0.0) + e.amount, 4)
        entries.append({"step": e.step, "label": e.label, "detail": e.detail,
                        "amount": e.amount, "mock": e.mock})
    actual_total = round(sum(by_step.values()), 4)
    return {
        "currency": "USD",
        "mock": settings.mock_generation,
        "estimated": {"total": estimated.total,
                      "line_items": [li.model_dump() for li in estimated.line_items]},
        "actual": {"total": actual_total, "by_step": by_step, "entries": entries},
    }


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
