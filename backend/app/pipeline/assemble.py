"""Stage 10: FFmpeg executes the EDL.

Draft = 480p watermarked; final = 1080p H.264/AAC with the full audio mix. Both
stored in MinIO. Assembly is deterministic FFmpeg in either generation mode — the
inputs (clips/narration/music) are already real, so there's no mock branch here.
"""
from __future__ import annotations

from .. import media
from . import audio as a_stage


def render(*, draft: bool, aspect_ratio: str, scenes: list[dict],
           music_bytes: bytes | None) -> bytes:
    """`scenes`: ordered render dicts (see media.assemble_video)."""
    return media.assemble_video(
        draft=draft, aspect_ratio=aspect_ratio, scenes=scenes,
        music_bytes=music_bytes, music_db=a_stage.MUSIC_BED_DB,
    )
