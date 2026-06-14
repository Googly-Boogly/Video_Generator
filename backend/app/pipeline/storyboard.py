"""Stage 3: Storyboard generation + conversational revision.

Output is validated against the Pydantic Storyboard schema before it is
trusted by the rest of the pipeline.
"""
from __future__ import annotations

from ..config import settings
from ..llm import complete_json
from ..models_config import (
    DEFAULT_KEYFRAME_MODEL,
    Tier,
    default_video_model,
    video_models,
)
from ..schemas import Storyboard
from . import mock, prompts


def _available_video_model_ids() -> list[str]:
    return [m.id for m in video_models()]


def generate_storyboard(
    *, idea: str, target_length: int, aspect_ratio: str, style_preset: str,
    style_bible: dict | None, llm: str | None = None,
) -> Storyboard:
    if settings.mock_generation:
        default = default_video_model(Tier.PREMIUM, "narrated")
        raw = mock.mock_storyboard(idea, target_length, default)
    else:
        raw = complete_json(
            system=prompts.STORYBOARD_SYSTEM,
            user=prompts.storyboard_user_prompt(
                idea=idea,
                target_length=target_length,
                aspect_ratio=aspect_ratio,
                style_preset=style_preset,
                style_bible=style_bible,
                available_models=_available_video_model_ids(),
            ),
            max_tokens=100_000,
            llm=llm,
        )
    return _validate(raw)


def revise_storyboard(
    *, instruction: str, storyboard: dict, style_bible: dict | None, llm: str | None = None
) -> Storyboard:
    if settings.mock_generation:
        # Deterministic mock revision: tag the affected scene's description so
        # the UI visibly reflects the change.
        raw = _mock_revise(instruction, storyboard)
    else:
        raw = complete_json(
            system=prompts.REVISE_SYSTEM,
            user=prompts.revise_user_prompt(
                instruction=instruction, storyboard=storyboard, style_bible=style_bible
            ),
            max_tokens=100_000,
            llm=llm,
        )
    return _validate(raw)


def _validate(raw: dict) -> Storyboard:
    # LLMs occasionally emit out-of-range or non-numeric durations (e.g. 0). Clamp
    # each scene into the allowed per-scene range BEFORE validation so one bad number
    # never fails the whole storyboard.
    scenes = raw.get("scenes", []) if isinstance(raw, dict) else []
    for s in scenes:
        if isinstance(s, dict):
            try:
                d = float(s.get("duration_seconds"))
            except (TypeError, ValueError):
                d = 4.0
            s["duration_seconds"] = min(max(d, 2.0), 8.0)
    sb = Storyboard.model_validate(raw)
    # Re-number contiguously and backfill suggested_model.
    for i, scene in enumerate(sb.scenes, start=1):
        scene.scene_number = i
        if not scene.suggested_model:
            scene.suggested_model = default_video_model(Tier.PREMIUM, scene.audio_mode)
    return sb


def _mock_revise(instruction: str, storyboard: dict) -> dict:
    import re

    scenes = [dict(s) for s in storyboard.get("scenes", [])]
    note = instruction.strip()
    m = re.search(r"scene\s+(\d+)", instruction, re.IGNORECASE)
    if m:
        target = int(m.group(1))
        for s in scenes:
            if s.get("scene_number") == target:
                s["shot_description"] = f"{s.get('shot_description', '')} [revised: {note}]"
                s["video_prompt"] = f"{s.get('video_prompt', '')} ({note})"
    else:
        for s in scenes:
            s["shot_description"] = f"{s.get('shot_description', '')} [revised: {note}]"
    return {"scenes": scenes, "_mock": True}
