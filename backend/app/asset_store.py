"""Shared helper: put bytes in MinIO and create the Asset row pointing at them.

Used by both Celery tasks and request handlers (e.g. music upload).
"""
from __future__ import annotations

from .models import Asset

_EXT = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "mp4": "mp4",
        "mpeg": "mp3", "mp3": "mp3", "wav": "wav", "m4a": "m4a", "aac": "m4a"}


def _ext_for(content_type: str) -> str:
    for token, ext in _EXT.items():
        if token in content_type:
            return ext
    return "bin"


def store_asset(db, project_id, scene_id, kind, data: bytes, content_type, meta=None) -> Asset:
    from .storage import put_bytes

    asset = Asset(
        project_id=project_id, scene_id=scene_id, kind=kind,
        content_type=content_type, meta=meta or {},
    )
    key = f"projects/{project_id}/{kind}/{asset.id}.{_ext_for(content_type)}"
    put_bytes(key, data, content_type)
    asset.storage_key = key
    db.add(asset)
    db.flush()
    return asset
