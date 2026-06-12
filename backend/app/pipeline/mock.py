"""Mock-mode asset + content generators.

When MOCK_GENERATION=true every generation task returns instant placeholder
assets (and silent audio stubs) so the full UI and pipeline can be exercised
with zero API spend.
"""
from __future__ import annotations

import hashlib
import struct
import zlib

# --- Deterministic colors so mock keyframes look distinct per scene ----------

_PALETTE = [
    (37, 99, 235), (220, 38, 38), (22, 163, 74), (217, 119, 6),
    (147, 51, 234), (8, 145, 178), (190, 24, 93), (101, 163, 13),
]


def _color_for(seed: str) -> tuple[int, int, int]:
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return _PALETTE[h % len(_PALETTE)]


def placeholder_png(seed: str, width: int = 16, height: int = 9) -> bytes:
    """A tiny solid-color PNG. Color is derived from the seed so each scene's
    placeholder is visually distinguishable.
    """
    r, g, b = _color_for(seed)
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # filter byte per scanline
        for _ in range(width):
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def silent_wav(seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """A valid silent mono 16-bit PCM WAV."""
    n = max(1, int(seconds * sample_rate))
    data = b"\x00\x00" * n
    byte_rate = sample_rate * 2
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


def placeholder_mp4_stub(seed: str) -> bytes:
    """Phase 1 stub: a marker payload standing in for a generated clip.

    Real clips arrive in Phase 3. This keeps the asset pipeline exercised
    without bundling a video encoder into mock mode.
    """
    return b"STORYFORGE_MOCK_CLIP:" + seed.encode()


# --- Mock structured content -------------------------------------------------

def mock_style_bible(idea: str, style_preset: str) -> dict:
    return {
        "style_summary": f"A {style_preset} short film interpretation of: {idea}",
        "palette": ["#1f2937", "#f59e0b", "#e5e7eb", "#0ea5e9"],
        "lighting": "soft key with warm rim light, gentle volumetric haze",
        "lens": "35mm anamorphic, shallow depth of field",
        "mood": "contemplative, hopeful",
        "character_sheet": [
            {
                "name": "Protagonist",
                "physical_descriptors": (
                    "late 20s, short dark curly hair, weathered green jacket, "
                    "small scar over left eyebrow, amber eyes"
                ),
            }
        ],
        "reference_image_prompts": [
            "character turnaround, neutral studio lighting, full body",
            "key environment establishing shot, golden hour",
            "color key mood board, dominant teal and amber",
        ],
        "_mock": True,
    }


def mock_storyboard(idea: str, target_length: int, default_model: str) -> dict:
    """Build a believable storyboard sized to the target length."""
    seg = 5.0
    count = max(3, round(target_length / seg))
    seg = round(target_length / count, 1)
    beats = [
        ("Establishing shot", "slow push in", "narrated"),
        ("Inciting detail close-up", "rack focus", "narrated"),
        ("Rising action wide", "handheld tracking", "narrated"),
        ("Turning point", "crane up", "narrated"),
        ("Climax", "fast dolly in", "narrated"),
        ("Resolution", "slow pull back", "narrated"),
        ("Final image", "static lock-off", "narrated"),
    ]
    scenes = []
    for i in range(count):
        beat, cam, mode = beats[i % len(beats)]
        scenes.append(
            {
                "scene_number": i + 1,
                "duration_seconds": seg,
                "shot_description": f"{beat} — {idea}",
                "camera_movement": cam,
                "image_prompt": f"{beat}: {idea}, cinematic keyframe",
                "video_prompt": f"{beat}: {idea}, {cam}",
                "narration_text": f"In this moment, {idea.lower()} unfolds — scene {i + 1}.",
                "audio_mode": mode,
                "dialogue_text": None,
                "suggested_model": default_model,
            }
        )
    return {"scenes": scenes, "_mock": True}


def mock_quality_report(scene_number: int) -> dict:
    """Most clips pass; flag a deterministic subset to exercise the UI."""
    flagged = scene_number % 4 == 0
    return {
        "flagged": flagged,
        "reasons": ["possible warped hand in frame 3"] if flagged else [],
        "native_audio_muted": False,
        "_mock": True,
    }
