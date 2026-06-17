"""Stage 3: Storyboard generation + conversational revision.

Output is validated against the Pydantic Storyboard schema before it is
trusted by the rest of the pipeline.
"""
import math

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

# Long films can't be written in one LLM call: a ~10-minute storyboard is 100+ scenes
# of JSON, and the request runs for minutes and the connection drops mid-generation.
# Above this length we generate the storyboard in sequential time-windowed segments
# (each a small, fast, reliable call) and stitch them together.
_SINGLE_CALL_MAX_SECONDS = 90
_SEGMENT_SECONDS = 60
_SEGMENT_MAX_TOKENS = 16_000
_AVG_SCENE_SECONDS = 5.0   # used to tell each segment how many scenes to write


def _available_video_model_ids() -> list[str]:
    return [m.id for m in video_models()]


def generate_storyboard(
    *, idea: str, target_length: int, aspect_ratio: str, style_preset: str,
    style_bible: dict | None, llm: str | None = None,
) -> Storyboard:
    if settings.mock_generation:
        default = default_video_model(Tier.PREMIUM, "narrated")
        return _validate(mock.mock_storyboard(idea, target_length, default))

    models = _available_video_model_ids()
    if target_length > _SINGLE_CALL_MAX_SECONDS:
        return _generate_segmented(
            idea=idea, target_length=target_length, aspect_ratio=aspect_ratio,
            style_preset=style_preset, style_bible=style_bible, models=models, llm=llm,
        )

    raw = complete_json(
        system=prompts.STORYBOARD_SYSTEM,
        user=prompts.storyboard_user_prompt(
            idea=idea, target_length=target_length, aspect_ratio=aspect_ratio,
            style_preset=style_preset, style_bible=style_bible, available_models=models,
        ),
        max_tokens=_SEGMENT_MAX_TOKENS,
        llm=llm,
    )
    return _validate(raw)


def _generate_segmented(
    *, idea: str, target_length: int, aspect_ratio: str, style_preset: str,
    style_bible: dict | None, models: list[str], llm: str | None,
) -> Storyboard:
    """Write a long storyboard as sequential time-windowed segments, then stitch.

    Each segment is its own small LLM call (fast + reliable), told the running progress,
    an explicit scene count to write, and a recap of the prior segment for continuity.
    We keep requesting segments until the accumulated duration reaches the target — the
    model under-produces per window, so a fixed segment count leaves the film too short.
    Scenes are concatenated and renumbered contiguously in `_validate`.
    """
    all_scenes: list[dict] = []
    prev_recap = ""
    accumulated = 0.0
    # Safety cap so a chronically under-producing (or empty) model can't loop forever.
    max_segments = math.ceil(target_length / _SEGMENT_SECONDS) * 3
    seg = 0
    while accumulated < target_length - _AVG_SCENE_SECONDS and seg < max_segments:
        seg += 1
        remaining = target_length - accumulated
        seg_len = int(min(_SEGMENT_SECONDS, remaining))
        scenes_hint = max(2, round(seg_len / _AVG_SCENE_SECONDS))
        raw = complete_json(
            system=prompts.STORYBOARD_SYSTEM,
            user=prompts.storyboard_segment_user_prompt(
                idea=idea, target_length=target_length, aspect_ratio=aspect_ratio,
                style_preset=style_preset, style_bible=style_bible, available_models=models,
                seconds_done=int(accumulated), seg_len=seg_len, scenes_hint=scenes_hint,
                is_final=remaining <= _SEGMENT_SECONDS, prev_recap=prev_recap,
            ),
            max_tokens=_SEGMENT_MAX_TOKENS,
            llm=llm,
        )
        seg_scenes = raw.get("scenes", []) if isinstance(raw, dict) else []
        if not seg_scenes:
            break  # model returned nothing — stop rather than spin
        for s in seg_scenes:
            try:
                d = float(s.get("duration_seconds"))
            except (TypeError, ValueError):
                d = 4.0
            s["duration_seconds"] = min(max(d, 2.0), 8.0)
            accumulated += s["duration_seconds"]
        all_scenes.extend(seg_scenes)
        last = seg_scenes[-1]
        prev_recap = f"{last.get('shot_description', '')} | {last.get('narration_text', '')}"[:400]
    return _validate({"scenes": all_scenes})


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
