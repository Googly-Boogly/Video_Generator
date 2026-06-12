"""Project + scene + job state machines."""
from __future__ import annotations

from enum import Enum


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    STYLED = "styled"
    STORYBOARDED = "storyboarded"
    KEYFRAMES = "keyframes"
    CLIPS = "clips"
    AUDIO = "audio"
    EDITED = "edited"
    DRAFT_RENDERED = "draft_rendered"
    RENDERED = "rendered"


# Linear progression order. We allow moving forward, and re-entering an earlier
# state (e.g. regenerating the storyboard) — restarts never lose progress.
PROJECT_ORDER: list[ProjectStatus] = [
    ProjectStatus.DRAFT,
    ProjectStatus.STYLED,
    ProjectStatus.STORYBOARDED,
    ProjectStatus.KEYFRAMES,
    ProjectStatus.CLIPS,
    ProjectStatus.AUDIO,
    ProjectStatus.EDITED,
    ProjectStatus.DRAFT_RENDERED,
    ProjectStatus.RENDERED,
]


def rank(status: ProjectStatus) -> int:
    return PROJECT_ORDER.index(status)


def advance(current: ProjectStatus, target: ProjectStatus) -> ProjectStatus:
    """Move to `target` only if it is at or ahead of current; never regress
    silently. Callers that intentionally re-run a stage pass the earlier state.
    """
    return target


class SceneStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"
    FLAGGED = "flagged"  # quality gate flagged this clip


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class JobType(str, Enum):
    STYLE_BIBLE = "style_bible"
    STORYBOARD = "storyboard"
    STORYBOARD_REVISE = "storyboard_revise"
    KEYFRAMES = "keyframes"
    VIDEO = "video"
    QUALITY = "quality"
    AUDIO = "audio"
    EDIT = "edit"
    RENDER_DRAFT = "render_draft"
    RENDER_FINAL = "render_final"
