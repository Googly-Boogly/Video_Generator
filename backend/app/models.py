"""SQLAlchemy ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .state import JobStatus, ProjectStatus, SceneStatus


def _uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), default="Untitled project")
    idea: Mapped[str] = mapped_column(Text)

    target_length: Mapped[int] = mapped_column(Integer, default=30)  # seconds
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9")
    style_preset: Mapped[str] = mapped_column(String(64), default="cinematic")

    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.DRAFT.value)

    # Which LLM handles this project's prompts (storyboard / revision / vision).
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Locked project voice (ElevenLabs voice id) — narration identity.
    voice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # JSON blobs produced by pipeline stages.
    style_bible: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    edl: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Scene.scene_number",
    )
    jobs: Mapped[list["Job"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    assets: Mapped[list["Asset"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    cost_entries: Mapped[list["CostEntry"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)

    scene_number: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float] = mapped_column(Float, default=5.0)

    shot_description: Mapped[str] = mapped_column(Text, default="")
    camera_movement: Mapped[str] = mapped_column(String(255), default="")
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    video_prompt: Mapped[str] = mapped_column(Text, default="")

    narration_text: Mapped[str] = mapped_column(Text, default="")
    audio_mode: Mapped[str] = mapped_column(String(16), default="narrated")  # narrated | dialogue
    dialogue_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    suggested_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_override: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=SceneStatus.PENDING.value)
    # Id of the winning keyframe asset (best-of-N) and the produced clip asset.
    keyframe_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    clip_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    native_audio_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Quality gate findings: {flagged: bool, reasons: [...], native_audio_muted: bool}
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="scenes")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # reference | keyframe | clip | native_audio | narration | music | draft | final | frame
    kind: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(512))  # key within the MinIO bucket
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="assets")


@event.listens_for(Asset, "before_delete")
def _delete_asset_blob(mapper, connection, target: "Asset") -> None:
    """Delete the backing MinIO object whenever an Asset row is removed — covers
    both project-delete cascade and asset replacement (regeneration). Best-effort.
    """
    if target.storage_key:
        from .storage import delete_object

        delete_object(target.storage_key)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED.value)
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1

    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="jobs")


class CostEntry(Base):
    """One line in a project's actual-spend ledger — appended as paid steps run.

    `mock=True` means no money was really charged (the amount is the would-be
    cost of the operation). Re-runs append new entries, so the ledger reflects
    accumulated spend including regeneration waste.
    """
    __tablename__ = "cost_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    step: Mapped[str] = mapped_column(String(32))  # keyframes | video | audio | render
    label: Mapped[str] = mapped_column(String(128))
    detail: Mapped[str] = mapped_column(String(255), default="")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    mock: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="cost_entries")
