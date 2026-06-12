"""Anthropic wrapper for storyboarding, conversational revision, vision-based
best-of-N keyframe ranking, and (later) quality + editing. Honors mock mode so
the full pipeline runs offline.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional

from .config import settings


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model response."""
    text = text.strip()
    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} or [...].
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if not match:
            raise LLMError(f"No JSON found in LLM response: {text[:200]}")
        return json.loads(match.group(1))


def complete_json(
    system: str,
    user: str,
    *,
    max_tokens: int = 4096,
    model: Optional[str] = None,
) -> Any:
    """Call Claude and parse a JSON response. Raises LLMError on failure."""
    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY not set (and mock mode is off).")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=model or settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    return _extract_json(text)


RANK_SYSTEM = """You are a cinematography art director judging candidate keyframes
for one scene of a short film. You are given the shot's intent, the locked
character sheet, and several numbered candidate images. Rank them and pick the
best — favoring: faithfulness to the shot description, adherence to the locked
character descriptors (no identity drift), clean anatomy (no warped hands/faces),
strong composition, and consistency with the established style.

Return ONLY this JSON:
{"winner": <0-based index>,
 "scores": [{"index": 0, "score": 0.0-1.0, "reason": "short"}, ...]}"""


def rank_images(
    *, shot_description: str, character_sheet: list[dict] | None,
    images: list[tuple[bytes, str]],
) -> dict:
    """Vision-rank candidate keyframes. `images` is [(bytes, media_type), ...].

    Returns {"winner": int, "scores": [{index, score, reason}, ...]}.
    """
    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY not set (and mock mode is off).")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    char = ""
    if character_sheet:
        char = "; ".join(
            f"{c.get('name', 'character')}: {c.get('physical_descriptors', '')}"
            for c in character_sheet
        )

    content: list[dict] = [
        {"type": "text", "text": f"SHOT: {shot_description}\nLOCKED CHARACTERS: {char}\n\nCandidates:"}
    ]
    for i, (data, media_type) in enumerate(images):
        content.append({"type": "text", "text": f"Candidate {i}:"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(data).decode(),
                },
            }
        )

    resp = client.messages.create(
        model=settings.anthropic_vision_model,
        max_tokens=1024,
        system=RANK_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    return _extract_json(text)
