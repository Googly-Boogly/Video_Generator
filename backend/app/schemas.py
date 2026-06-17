"""Pydantic schemas: API I/O + validated pipeline structures."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict

AudioMode = Literal["narrated", "dialogue"]


# ---------------------------------------------------------------------------
# Storyboard (LLM output — strictly validated)
# ---------------------------------------------------------------------------

class StoryboardScene(BaseModel):
    """One scene as produced by the LLM during storyboarding."""

    scene_number: int = Field(ge=1)
    duration_seconds: float = Field(gt=0, le=15)
    shot_description: str
    camera_movement: str = ""
    image_prompt: str
    video_prompt: str
    narration_text: str = ""
    audio_mode: AudioMode = "narrated"
    dialogue_text: Optional[str] = None
    suggested_model: Optional[str] = None


class Storyboard(BaseModel):
    scenes: list[StoryboardScene]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    idea: str = Field(min_length=3)
    title: Optional[str] = None
    target_length: Literal[15, 30, 60, 600] = 30
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    style_preset: str = "cinematic"
    llm_model: Optional[str] = None  # which LLM handles this project's prompts


class SceneUpdate(BaseModel):
    """Partial update of a scene from the review UI."""
    model_config = ConfigDict(extra="ignore")

    duration_seconds: Optional[float] = Field(default=None, gt=0, le=15)
    shot_description: Optional[str] = None
    camera_movement: Optional[str] = None
    image_prompt: Optional[str] = None
    video_prompt: Optional[str] = None
    narration_text: Optional[str] = None
    audio_mode: Optional[AudioMode] = None
    dialogue_text: Optional[str] = None
    model_override: Optional[str] = None


class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    scene_number: int
    duration_seconds: float
    shot_description: str
    camera_movement: str
    image_prompt: str
    video_prompt: str
    narration_text: str
    audio_mode: str
    dialogue_text: Optional[str]
    suggested_model: Optional[str]
    model_override: Optional[str]
    status: str
    keyframe_asset_id: Optional[str]
    clip_asset_id: Optional[str]
    native_audio_asset_id: Optional[str]
    quality: Optional[dict]
    error: Optional[str]


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    idea: str
    target_length: int
    aspect_ratio: str
    style_preset: str
    status: str
    llm_model: Optional[str]
    voice_id: Optional[str]
    style_bible: Optional[dict]
    thumbnail_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    scenes: list[SceneOut] = []


class ReorderRequest(BaseModel):
    """Ordered list of scene ids defining the new sequence."""
    scene_ids: list[str]


class ReviseRequest(BaseModel):
    """Conversational storyboard revision, e.g. 'make scene 3 moodier'."""
    instruction: str = Field(min_length=2)


class AddSceneRequest(BaseModel):
    after_scene_number: Optional[int] = None  # None => append at end


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    scene_id: Optional[str]
    type: str
    status: str
    progress: float
    result: Optional[dict]
    error: Optional[str]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

class CostLineItem(BaseModel):
    label: str
    detail: str
    amount: float


class CostEstimate(BaseModel):
    step: str
    line_items: list[CostLineItem]
    total: float
    currency: str = "USD"


# ---------------------------------------------------------------------------
# Assets (keyframes, reference images, …)
# ---------------------------------------------------------------------------

class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    scene_id: Optional[str]
    kind: str
    content_type: str
    meta: Optional[dict]
    url: str  # content endpoint the browser can load directly


class SelectKeyframeRequest(BaseModel):
    asset_id: str


# ---------------------------------------------------------------------------
# Audio (Phase 4)
# ---------------------------------------------------------------------------

class VoiceSelect(BaseModel):
    voice_id: str = Field(min_length=1)


class MusicLibrarySelect(BaseModel):
    track_id: str = Field(min_length=1)
