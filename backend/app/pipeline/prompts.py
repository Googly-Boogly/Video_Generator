"""Prompt engineering: LLM system prompts + the per-model prompt translator.

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
- Every scene is audio_mode "narrated": narration carries all spoken content as voiceover.
  Always write narration_text, set audio_mode "narrated", and leave dialogue_text null.
  Never use "dialogue" — this pipeline animates a still keyframe (photo-to-video) and has
  no lip-sync, so there is no on-camera speech.
- All scenes' narration_text is concatenated into ONE continuous voiceover track (it is
  NOT timed per scene), so write it to flow as a single cohesive narration from the first
  scene to the last. Pace it to the film length: about 2.5 spoken words per second of the
  TOTAL target duration (≈38 words for a 15s film), with each scene's line roughly its
  share of that, so the voiceover never runs longer than the video.
- image_prompt describes a single still keyframe. video_prompt describes the motion.
- Keep image_prompt and video_prompt concise: one to two sentences of scene content
  plus the embedded style descriptors. Never write multi-paragraph prompts.
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
}

--- WORKED EXAMPLE (study the format and quality; do NOT reuse its content) ---
EXAMPLE INPUT:
IDEA: A deep-sea diver discovers a sunken city
TARGET LENGTH: 12 seconds
ASPECT RATIO: 16:9
STYLE PRESET: cinematic
STYLE BIBLE (locked):
STYLE: desaturated teal noir, volumetric haze
PALETTE: teal, charcoal, amber
LIGHTING: shafting caustic god-rays from above
LENS: 24mm wide, shallow depth of field
CHARACTERS (locked): Mara: weathered diver, scarred dry-suit, copper helmet
AVAILABLE MODEL IDS for suggested_model: kling-3-pro, veo-31, seedance-2

EXAMPLE OUTPUT:
{
  "scenes": [
    {
      "scene_number": 1,
      "duration_seconds": 6,
      "shot_description": "Mara sinks past a colossal drowned archway into a ruined plaza",
      "camera_movement": "slow vertical descent, slight push in",
      "image_prompt": "Wide underwater shot of a diver descending past a colossal stone archway into a sunken plaza. desaturated teal noir, volumetric haze; palette teal, charcoal, amber; shafting caustic god-rays from above; 24mm wide, shallow depth of field. Mara: weathered diver, scarred dry-suit, copper helmet.",
      "video_prompt": "Diver drifts downward through suspended silt as god-rays sweep over crumbling columns; gentle current sways kelp. desaturated teal noir, volumetric haze; palette teal, charcoal, amber; shafting caustic god-rays from above; 24mm wide.",
      "narration_text": "Three hundred meters down, the city no one believed in was waiting.",
      "audio_mode": "narrated",
      "dialogue_text": null,
      "suggested_model": "kling-3-pro"
    },
    {
      "scene_number": 2,
      "duration_seconds": 6,
      "shot_description": "Mara's helmet lamp catches a carved face as she edges closer through the murk",
      "camera_movement": "handheld tracking right, slow",
      "image_prompt": "Close on a diver's copper helmet, beam raking across a barnacled carved stone face. desaturated teal noir, volumetric haze; palette teal, charcoal, amber; shafting caustic god-rays from above; 24mm wide, shallow depth of field. Mara: weathered diver, scarred dry-suit, copper helmet.",
      "video_prompt": "Helmet lamp sweeps over the carving, particulate drifting through the beam as Mara edges closer. desaturated teal noir, volumetric haze; palette teal, charcoal, amber; shafting caustic god-rays from above; 24mm wide.",
      "narration_text": "Something here had been waiting a very long time to be found.",
      "audio_mode": "narrated",
      "dialogue_text": null,
      "suggested_model": "kling-25-turbo"
    }
  ]
}
Note how the example: keeps total duration within 2s of target (6+6=12); embeds every
style descriptor verbatim into both image_prompt and video_prompt; keeps every scene
narrated (narration_text set, audio_mode "narrated", dialogue_text null); and picks
suggested_model per scene only from the provided ids. Match this rigor for the REAL idea below.
--- END EXAMPLE ---"""


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
