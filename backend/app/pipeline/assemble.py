"""Stage 10: FFmpeg executes the EDL. Draft = 480p watermarked (budget tiers),
final = 1080p H.264/AAC with the full audio mix. Both stored in MinIO.

Phase 5 wires the real FFmpeg graph. Phase 1 ships a stub render artifact.
"""
from __future__ import annotations

from ..config import settings
from . import mock


def render(*, project: dict, edl: dict, draft: bool) -> bytes:
    if settings.mock_generation:
        tag = "draft-480p" if draft else "final-1080p"
        return b"STORYFORGE_MOCK_RENDER:" + tag.encode() + b":" + str(
            edl.get("total_duration", 0)
        ).encode()
    raise NotImplementedError("Real FFmpeg assembly lands in Phase 5.")
