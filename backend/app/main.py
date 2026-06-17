"""StoryForge FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import assets as assets_router
from .routers import audio as audio_router
from .routers import config as config_router
from .routers import jobs as jobs_router
from .routers import keyframes as keyframes_router
from .routers import projects as projects_router
from .routers import render as render_router
from .routers import storyboard as storyboard_router
from .routers import video as video_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("storyforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        from .storage import ensure_bucket

        ensure_bucket()
    except Exception as exc:  # noqa: BLE001
        log.warning("MinIO bucket init skipped: %s", exc)
    log.info("StoryForge up. mock_generation=%s", settings.mock_generation)
    yield


app = FastAPI(title="StoryForge API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_origin,
        "http://localhost:5273",
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    # Accept the dev frontend on either host alias (localhost OR 127.0.0.1) and any
    # port — the two are distinct browser origins, so loading the app at
    # 127.0.0.1:5273 was failing the preflight against a localhost-only allowlist.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router.router)
app.include_router(projects_router.router)
app.include_router(storyboard_router.router)
app.include_router(keyframes_router.router)
app.include_router(video_router.router)
app.include_router(audio_router.router)
app.include_router(render_router.router)
app.include_router(assets_router.router)
app.include_router(jobs_router.router)


@app.get("/health")
def health():
    return {"status": "ok", "mock_generation": settings.mock_generation}
