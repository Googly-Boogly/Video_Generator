"""Central application settings, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Core ---
    app_name: str = "StoryForge"
    environment: str = "development"

    # When true, every generation task returns instant placeholder assets and
    # never spends a cent on a provider. This is the default for local dev.
    mock_generation: bool = True

    # --- Database ---
    database_url: str = "postgresql+psycopg2://storyforge:storyforge@postgres:5432/storyforge"

    # --- Redis / Celery ---
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # --- MinIO / S3 ---
    minio_endpoint: str = "minio:9000"
    minio_public_endpoint: str = "http://localhost:9000"  # what the browser sees
    minio_access_key: str = "storyforge"
    minio_secret_key: str = "storyforge-secret"
    minio_bucket: str = "storyforge"
    minio_secure: bool = False

    # --- Provider keys (server-side only) ---
    fal_key: str = ""
    anthropic_api_key: str = ""
    elevenlabs_api_key: str = ""

    # --- Model defaults ---
    anthropic_model: str = "claude-opus-4-8"
    anthropic_vision_model: str = "claude-opus-4-8"

    # --- Frontend origin for CORS ---
    frontend_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
