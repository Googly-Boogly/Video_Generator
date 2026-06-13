"""MinIO / S3 storage helper. All generated assets live here."""
from __future__ import annotations

import io
from functools import lru_cache

import boto3
from botocore.client import Config

from .config import settings


@lru_cache
def _client():
    scheme = "https" if settings.minio_secure else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{settings.minio_endpoint}",
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket() -> None:
    client = _client()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if settings.minio_bucket not in existing:
        client.create_bucket(Bucket=settings.minio_bucket)


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    client = _client()
    client.put_object(
        Bucket=settings.minio_bucket,
        Key=key,
        Body=io.BytesIO(data),
        ContentType=content_type,
    )
    return key


def get_bytes(key: str) -> bytes:
    client = _client()
    obj = client.get_object(Bucket=settings.minio_bucket, Key=key)
    return obj["Body"].read()


def delete_object(key: str) -> None:
    """Best-effort delete of a single object (never raises)."""
    try:
        _client().delete_object(Bucket=settings.minio_bucket, Key=key)
    except Exception:  # noqa: BLE001 — storage cleanup must not break the txn
        pass


def public_url(key: str, expires: int = 3600) -> str:
    """Presigned URL the browser can hit directly.

    We sign against the *public* endpoint (localhost) so the URL works from the
    user's browser, not just from inside the docker network.
    """
    scheme = "https" if settings.minio_secure else "http"
    public_host = settings.minio_public_endpoint or f"{scheme}://{settings.minio_endpoint}"
    public_client = boto3.client(
        "s3",
        endpoint_url=public_host,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    return public_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.minio_bucket, "Key": key},
        ExpiresIn=expires,
    )
