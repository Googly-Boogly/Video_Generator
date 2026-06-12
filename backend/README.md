# StoryForge — backend

FastAPI + SQLAlchemy + Celery. Runs as two services off one image: `api`
(uvicorn) and `worker` (Celery). See the top-level [docs/](../docs) for the full
picture — this is a quick orientation.

## Layout

```
app/
  config.py        Settings from env/.env
  database.py      engine, SessionLocal, Base, init_db()
  models.py        ORM: Project, Scene, Asset, Job
  schemas.py       Pydantic I/O + validated Storyboard
  state.py         status enums + ordering
  models_config.py model routing table + resolve_video_model()
  cost.py          pre-flight estimate + actual-spend ledger (CostEntry)
  llm.py           Anthropic wrapper (complete_json, rank_images vision)
  storage.py       MinIO/S3 helper
  asset_store.py   store_asset(): put bytes in MinIO + create the Asset row
  media.py         FFmpeg: encode/demux/extract, synth music, assemble_video (render EDL)
  providers/       fal_provider (image/video) + elevenlabs_provider (TTS)
  celery_app.py    Celery app
  tasks.py         Celery tasks (only place generation runs)
  pipeline/        one module per stage (+ prompts.py, mock.py)
  routers/         config, projects, storyboard, keyframes, video, audio, render, assets, jobs
  main.py          app assembly, CORS, lifespan
alembic/           migrations (env reads DATABASE_URL)
tests/             unit + API integration (SQLite + eager Celery)
```

## Run / test

```bash
# Via compose (recommended): see ../README.md
docker compose exec api python -m pytest -q       # 60 tests (uses FFmpeg + librosa)

# Standalone uvicorn (needs Postgres/Redis/MinIO reachable):
uvicorn app.main:app --reload
```

## Conventions

- HTTP handlers never block on generation — they create a `Job` and `.delay()` a
  task. Poll `GET /api/jobs/{id}` or stream `/stream`.
- All model/pricing facts live in `models_config.py`; nothing else hardcodes them.
- Respect `settings.mock_generation`: every stage must have a mock path.
- After editing task code, `docker compose restart worker` (no hot-reload).
