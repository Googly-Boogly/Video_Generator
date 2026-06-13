"""Shared helper: put bytes in MinIO and create the Asset row pointing at them.

Used by both Celery tasks and request handlers (e.g. music upload).
"""
from __future__ import annotations

import uuid

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

    # Assign the id explicitly: the column `default` only fires at flush, so a
    # storage key built from `asset.id` before flush would be `.../None.ext` and
    # every asset of a kind would collide on one object.
    asset_id = str(uuid.uuid4())
    key = f"projects/{project_id}/{kind}/{asset_id}.{_ext_for(content_type)}"
    put_bytes(key, data, content_type)
    asset = Asset(
        id=asset_id, project_id=project_id, scene_id=scene_id, kind=kind,
        content_type=content_type, storage_key=key, meta=meta or {},
    )
    db.add(asset)
    db.flush()
    return asset
