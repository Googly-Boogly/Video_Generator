"""Asset listing + content proxy.

The frontend loads images via `/api/assets/{id}/content` (a backend proxy), so it
never needs MinIO credentials or a presigned-URL round trip.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset
from ..schemas import AssetOut

router = APIRouter(prefix="/api/assets", tags=["assets"])


def _to_out(a: Asset) -> AssetOut:
    return AssetOut(
        id=a.id, project_id=a.project_id, scene_id=a.scene_id, kind=a.kind,
        content_type=a.content_type, meta=a.meta, url=f"/api/assets/{a.id}/content",
    )


@router.get("/{asset_id}/content")
def asset_content(asset_id: str, download: bool = False, db: Session = Depends(get_db)):
    from ..storage import get_bytes

    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    try:
        data = get_bytes(asset.storage_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"storage error: {exc}")
    headers = {"Cache-Control": "public, max-age=86400"}
    if download:
        ext = asset.storage_key.rsplit(".", 1)[-1]
        headers["Content-Disposition"] = f'attachment; filename="storyforge-{asset.kind}-{asset.id[:8]}.{ext}"'
    return Response(content=data, media_type=asset.content_type, headers=headers)


@router.get("/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    return _to_out(asset)
