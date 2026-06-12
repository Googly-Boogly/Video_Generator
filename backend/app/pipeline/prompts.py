"""Prompt engineering: Claude system prompts + the per-model prompt translator.

Kling, Veo, and Seedance respond to very different prompt phrasing. The
translator rewrites a neutral scene description into the dialect each target
model expects, always embedding the locked style block verbatim.
"""
from __future__ import annotations

from ..models_config import ModelRoute, route


def style_block(style_bible: dict | None) -> str:
    """Render the locked style block that gets embedded verbatim into prompts."""
    if not style_bible:
        return ""
    sb = style_bible
    parts = [
        f"STYLE: {sb.get('style_summary', '')}",
        f"PALETTE: {', '.join(sb.get('palette', []))}",
        f"LIGHTING: {sb.get('lighting', '')}",
        f"LENS: {sb.get('lens', '')}",
    ]
    chars = sb.get("character_sheet", [])
    if chars:
        char_lines = "; ".join(
            f"{c.get('name', 'character')}: {c.get('physical_descriptors', '')}" for c in chars
        )
        parts.append(f"CHARACTERS (locked): {char_lines}")
    return "\n".join(p for p in parts if p.split(": ", 1)[-1])


# --- Storyboard system prompt ------------------------------------------------

STORYBOARD_SYSTEM = """You are a cinematic storyboard director for an AI video pipeline.
Given a creative idea, a target length, an aspect ratio, a style preset, and a
locked STYLE BIBLE, produce a shot-by-shot storyboard as STRICT JSON.

Rules:
- Total of all scene durations must be within ±2s of the target length.
- Each scene's duration_seconds must be between 2 and 8 seconds.
- Embed the locked style descriptors verbatim into every image_prompt and video_prompt.
- Default to audio_mode "narrated": narration carries the words, scenes avoid on-screen
  speaking. Only use audio_mode "dialogue" when a character must visibly speak on camera;
  then set dialogue_text and keep narration_text empty for that scene.
- image_prompt describes a single still keyframe. video_prompt describes the motion.
- camera_movement is a short phrase (e.g. "slow push in", "handheld tracking left").
- suggested_model is one of the provided model ids, chosen per scene.

Output ONLY this JSON shape, no prose:
{
  "scenes": [
    {
      "scene_number": 1,
      "duration_seconds": 5,
      "shot_description": "...",
      "camera_movement": "...",
      "image_prompt": "...",
      "video_prompt": "...",
      "narration_text": "...",
      "audio_mode": "narrated",
      "dialogue_text": null,
      "suggested_model": "kling-3-pro"
    }
  ]
}"""


def storyboard_user_prompt(
    *, idea: str, target_length: int, aspect_ratio: str, style_preset: str,
    style_bible: dict | None, available_models: list[str],
) -> str:
    return (
        f"IDEA: {idea}\n"
        f"TARGET LENGTH: {target_length} seconds\n"
        f"ASPECT RATIO: {aspect_ratio}\n"
        f"STYLE PRESET: {style_preset}\n\n"
        f"STYLE BIBLE (locked — embed verbatim):\n{style_block(style_bible)}\n\n"
        f"AVAILABLE MODEL IDS for suggested_model: {', '.join(available_models)}\n\n"
        f"Produce the storyboard JSON now."
    )


# --- Style bible system prompt ----------------------------------------------

STYLE_BIBLE_SYSTEM = """You are an art director. From a creative idea and a style preset,
produce a STYLE BIBLE as STRICT JSON describing a consistent visual language and a
character sheet with LOCKED physical descriptors (so the same character renders
identically across shots).

Output ONLY this JSON shape:
{
  "style_summary": "one or two sentences",
  "palette": ["#hex or color name", "..."],
  "lighting": "...",
  "lens": "...",
  "mood": "...",
  "character_sheet": [
    {"name": "...", "physical_descriptors": "locked, specific, repeatable descriptors"}
  ],
  "reference_image_prompts": [
    "character turnaround prompt",
    "key environment prompt",
    "color key / mood board prompt"
  ]
}"""


def style_bible_user_prompt(*, idea: str, style_preset: str, aspect_ratio: str) -> str:
    return (
        f"IDEA: {idea}\n"
        f"STYLE PRESET: {style_preset}\n"
        f"ASPECT RATIO: {aspect_ratio}\n\n"
        f"Produce the style bible JSON now. Include 3-5 reference_image_prompts."
    )


# --- Conversational revision -------------------------------------------------

REVISE_SYSTEM = """You revise an existing storyboard JSON given a natural-language
instruction (e.g. "make scene 3 moodier", "add a transition shot before the ending",
"cut scene 2"). Apply the instruction and return the COMPLETE updated storyboard JSON
in the same schema. Keep scene_number contiguous starting at 1. Preserve unaffected
scenes exactly. Keep embedding the locked style descriptors. Output ONLY JSON."""


def revise_user_prompt(*, instruction: str, storyboard: dict, style_bible: dict | None) -> str:
    import json

    return (
        f"STYLE BIBLE (locked):\n{style_block(style_bible)}\n\n"
        f"CURRENT STORYBOARD JSON:\n{json.dumps(storyboard, indent=2)}\n\n"
        f"INSTRUCTION: {instruction}\n\n"
        f"Return the full updated storyboard JSON."
    )


# ---------------------------------------------------------------------------
# Per-model prompt translator
# ---------------------------------------------------------------------------

def translate_video_prompt(
    *, model_id: str, scene: dict, style_bible: dict | None
) -> str:
    """Rewrite a scene's video prompt into the dialect of the target model."""
    model: ModelRoute = route(model_id)
    base = scene.get("video_prompt") or scene.get("shot_description", "")
    camera = scene.get("camera_movement", "")
    style = style_block(style_bible)
    dialogue = scene.get("dialogue_text")

    if model.prompt_style == "kling":
        # Kling: concise, motion-forward, camera direction inline.
        out = f"{base}. Camera: {camera}." if camera else base
        if style:
            out += f"\nStyle: {style}"
        return out

    if model.prompt_style == "veo":
        # Veo: rich cinematic description; supports synchronized audio + lip sync.
        out = f"{base}\nCamera movement: {camera}".strip()
        if dialogue and scene.get("audio_mode") == "dialogue":
            out += f'\nThe character says, with natural lip-sync: "{dialogue}"'
        else:
            out += "\nAmbient sound only, no spoken dialogue."
        if style:
            out += f"\n{style}"
        return out

    if model.prompt_style == "seedance":
        # Seedance: reference-driven, multi-shot; emphasize consistency to refs.
        out = (
            f"{base}. Maintain strict consistency with the provided reference images. "
            f"Camera: {camera}."
        )
        if style:
            out += f"\n{style}"
        return out

    # Default / flux-style fallthrough.
    return f"{base}\n{style}".strip()


def translate_image_prompt(*, scene: dict, style_bible: dict | None) -> str:
    """FLUX.2 keyframe prompt with the style block appended."""
    base = scene.get("image_prompt") or scene.get("shot_description", "")
    style = style_block(style_bible)
    return f"{base}\n{style}".strip()
