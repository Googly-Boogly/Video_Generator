"""Stage 7: quality gate.

Extract 4 frames per finished clip; the vision model flags artifacts (warped hands,
melted faces, mushy motion, identity drift vs the character sheet). An audio check
flags garbled native speech → that clip's native track is auto-muted. Flagged
clips get one-click regenerate in the UI.

Frame extraction is real (FFmpeg) in both modes; the vision verdict is mocked
deterministically when MOCK_GENERATION is on.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from .. import media
from . import mock


@dataclass
class QualityResult:
    frames: list[bytes]            # extracted JPEG frames (stored as assets)
    report: dict                   # {flagged, reasons, native_audio_muted, identity_drift}


QUALITY_SYSTEM = """You are a strict QC reviewer for AI-generated video frames.
Given the shot intent, the locked character sheet, and 4 frames sampled from one
clip, flag any of: warped/extra hands or fingers, melted or distorted faces,
mushy/unstable motion artifacts, and identity drift from the locked character
descriptors. Return ONLY this JSON:
{"flagged": bool, "reasons": ["short", ...], "identity_drift": bool}"""


def _vision_report(scene: dict, frames: list[bytes], character_sheet, llm: str | None = None) -> dict:
    from ..llm import vision_json

    char = ""
    if character_sheet:
        char = "; ".join(
            f"{c.get('name','character')}: {c.get('physical_descriptors','')}" for c in character_sheet
        )
    text = f"SHOT: {scene.get('shot_description','')}\nLOCKED CHARACTERS: {char}\nThe frames follow."
    report = vision_json(
        system=QUALITY_SYSTEM, text=text,
        images=[(f, "image/jpeg") for f in frames], max_tokens=512, llm=llm,
    )
    report.setdefault("reasons", [])
    report.setdefault("flagged", bool(report.get("reasons")))
    return report


def check_clip(
    *, clip_bytes: bytes, scene: dict, character_sheet: list[dict] | None = None,
    native_audio_bytes: bytes | None = None, llm: str | None = None,
) -> QualityResult:
    frames = media.extract_frames(video_bytes=clip_bytes, n=4)

    if settings.mock_generation:
        report = mock.mock_quality_report(scene.get("scene_number", 1))
        return QualityResult(frames=frames, report=report)

    report = _vision_report(scene, frames, character_sheet, llm=llm)
    # Audio garble check → auto-mute native track on this clip.
    report["native_audio_muted"] = _audio_has_garbled_speech(native_audio_bytes)
    return QualityResult(frames=frames, report=report)


def _audio_has_garbled_speech(audio_bytes: bytes | None) -> bool:
    """Placeholder garble detector. Phase 4 can wire real ASR/heuristics; for now
    native audio is assumed clean (default storyboards avoid on-screen speech).
    """
    return False
