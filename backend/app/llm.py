"""Provider-agnostic LLM wrapper for storyboarding, conversational revision,
vision-based keyframe ranking, and the quality/editor vision calls.

Two providers are supported and selected by LLM id (see app/llm_config.py):
gpt-5.4-nano (OpenAI) and claude-haiku-4-6 (Anthropic). Every entry point takes
an optional `llm` id so a project can route its prompts to either model. Honors
mock mode so the full pipeline runs offline.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional

from .config import settings
from .llm_config import LLMRoute, llm_route


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model response."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if not match:
            raise LLMError(f"No JSON found in LLM response: {text[:200]}")
        return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def _openai_json(route: LLMRoute, *, system: str, user_parts: list[dict], max_tokens: int) -> Any:
    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY not set (and mock mode is off).")
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, max_retries=5)
    resp = client.chat.completions.create(
        model=route.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_parts},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=max_tokens,
    )
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise LLMError(
            f"LLM response hit max_completion_tokens={max_tokens} and was truncated "
            "(JSON incomplete). Raise the token budget or shorten the prompt output."
        )
    return _extract_json(choice.message.content or "")


def _anthropic_json(route: LLMRoute, *, system: str, user_parts: list[dict], max_tokens: int) -> Any:
    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY not set (and mock mode is off).")
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=5)
    resp = client.messages.create(
        model=route.model,
        max_tokens=max_tokens,
        system=system + "\n\nReturn ONLY valid JSON, no prose.",
        messages=[{"role": "user", "content": _to_anthropic_parts(user_parts)}],
    )
    if resp.stop_reason == "max_tokens":
        raise LLMError(
            f"LLM response hit max_tokens={max_tokens} and was truncated "
            "(JSON incomplete). Raise the token budget or shorten the prompt output."
        )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _extract_json(text)


def _to_anthropic_parts(openai_parts: list[dict]) -> list[dict]:
    """Convert OpenAI-style content parts to Anthropic's content blocks."""
    out: list[dict] = []
    for p in openai_parts:
        if p["type"] == "text":
            out.append({"type": "text", "text": p["text"]})
        elif p["type"] == "image_url":
            url = p["image_url"]["url"]  # data:<media>;base64,<data>
            header, b64 = url.split(",", 1)
            media_type = header.split(";")[0].removeprefix("data:")
            out.append({"type": "image", "source": {
                "type": "base64", "media_type": media_type, "data": b64}})
    return out


def _dispatch_json(*, llm: Optional[str], system: str, user_parts: list[dict], max_tokens: int) -> Any:
    route = llm_route(llm)
    if route.provider == "openai":
        return _openai_json(route, system=system, user_parts=user_parts, max_tokens=max_tokens)
    if route.provider == "anthropic":
        return _anthropic_json(route, system=system, user_parts=user_parts, max_tokens=max_tokens)
    raise LLMError(f"unsupported LLM provider: {route.provider}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def complete_json(system: str, user: str, *, max_tokens: int = 4096, llm: Optional[str] = None) -> Any:
    """Text → JSON via the selected LLM."""
    return _dispatch_json(
        llm=llm, system=system, user_parts=[{"type": "text", "text": user}], max_tokens=max_tokens,
    )


def _image_part(data: bytes, media_type: str) -> dict:
    b64 = base64.standard_b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}


def vision_json(
    *, system: str, text: str, images: list[tuple[bytes, str]],
    max_tokens: int = 1024, llm: Optional[str] = None,
) -> Any:
    """Vision + JSON: system prompt, a text lead-in, and (bytes, media_type) images."""
    parts: list[dict] = [{"type": "text", "text": text}]
    parts += [_image_part(d, mt) for d, mt in images]
    return _dispatch_json(llm=llm, system=system, user_parts=parts, max_tokens=max_tokens)


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
    images: list[tuple[bytes, str]], llm: Optional[str] = None,
) -> dict:
    """Vision-rank candidate keyframes. Returns {"winner": int, "scores": [...]}."""
    char = ""
    if character_sheet:
        char = "; ".join(
            f"{c.get('name', 'character')}: {c.get('physical_descriptors', '')}"
            for c in character_sheet
        )
    text = (
        f"SHOT: {shot_description}\nLOCKED CHARACTERS: {char}\n\n"
        f"Candidates are the {len(images)} images below, in order (index 0..{len(images) - 1})."
    )
    return vision_json(system=RANK_SYSTEM, text=text, images=images, max_tokens=1024, llm=llm)
