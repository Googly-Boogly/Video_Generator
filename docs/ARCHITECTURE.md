# Architecture

## Service topology

```
                       ┌─────────────────────────┐
                       │  frontend (React/Vite)   │  :5273
                       │  TypeScript + Tailwind   │
                       └────────────┬────────────┘
                                    │ REST + SSE (JSON)
                                    ▼
                       ┌─────────────────────────┐
                       │     api (FastAPI)        │  :8800
                       │  routers + cost + state  │
                       └───┬───────────┬──────────┘
              enqueue .delay()         │ read/write
                       ▼               ▼
              ┌──────────────┐   ┌──────────────┐
              │ redis (broker│   │  postgres    │  :5433
              │  + backend)  │   │ project/scene│
              └──────┬───────┘   │  /job/asset  │
                     │           └──────┬───────┘
                     ▼ consume           │
              ┌──────────────┐           │
              │ worker       │───────────┘ read/write
              │ (Celery)     │
              │ pipeline/*   │──────────┐ put/get assets
              └──────────────┘          ▼
                                 ┌──────────────┐
                                 │ minio (S3)   │  :9000/:9001
                                 │  bucket:     │
                                 │  storyforge  │
                                 └──────────────┘
```

Six Compose services: `frontend`, `api`, `worker`, `redis`, `postgres`, `minio`.
`api` and `worker` share the same image (`backend/`).

## Core principles

- **Keys are server-side only.** The browser talks exclusively to FastAPI; the
  `FAL_KEY` / `ANTHROPIC_API_KEY` / `ELEVENLABS_API_KEY` never leave the backend.
- **Every generation step is an async Celery task** — the HTTP layer only creates
  a `Job` row and enqueues. No blocking generation in a request.
- **Config-driven model routing.** `app/models_config.py` is the single source of
  truth for which model handles which modality, its price, and its capabilities.
  Swapping a model is a config edit, not a refactor.
- **Restarts never lose progress.** All durable state is in Postgres; the project
  state machine lets any stage resume from where it left off.

## Request → task flow

```
POST /api/projects/{id}/storyboard
   │
   ├─ api: create Job(status=queued), generate_storyboard_task.delay(pid, jid)
   │       returns 202 + Job
   ▼
worker: generate_storyboard_task
   ├─ Job -> running
   ├─ pipeline.style_bible.generate_style_bible()  → project.style_bible, status=styled
   ├─ pipeline.storyboard.generate_storyboard()    → validated Storyboard
   ├─ replace project.scenes, status=storyboarded
   └─ Job -> success {scene_count}
   ▲
frontend: polls GET /api/jobs/{jid}  (or SSE /api/jobs/{jid}/stream)
          then GET /api/projects/{id} to render the board
```

In mock mode the pipeline functions return instantly, so the job is already in a
terminal state by the time the client first polls.

## Data model (`app/models.py`)

| Table      | Key columns | Purpose |
| ---------- | ----------- | ------- |
| `projects` | `idea`, `target_length`, `aspect_ratio`, `style_preset`, `status`, `voice_id`, `style_bible` (JSON), `edl` (JSON) | One short film. Holds the locked style bible and final EDL. |
| `scenes`   | `scene_number`, `duration_seconds`, `shot_description`, `camera_movement`, `image_prompt`, `video_prompt`, `narration_text`, `audio_mode`, `dialogue_text`, `suggested_model`, `model_override`, `status`, `*_asset_id`, `quality` (JSON) | One shot. Editable in the review UI. |
| `assets`   | `kind`, `scene_id`, `storage_key`, `content_type`, `meta` (JSON) | Pointer to a MinIO object (keyframe / clip / native_audio / narration / music / draft / final / frame / reference). |
| `jobs`     | `type`, `status`, `progress`, `scene_id`, `celery_task_id`, `result` (JSON), `error` | One async unit of work; what the UI polls. |

`projects → scenes / jobs / assets` cascade delete. Scenes are ordered by
`scene_number`, which is kept contiguous (1..N) on every add/delete/reorder.

## State machines (`app/state.py`)

**Project status** (linear, resumable):

```
draft → styled → storyboarded → keyframes → clips → audio
      → edited → draft_rendered → rendered
```

Re-running a stage moves the project back to that stage's state; progress already
persisted (assets, scene rows) is never discarded.

**Scene status:** `pending → queued → generating → done` (with `failed` and
`flagged` branches). A failed scene is isolated — it never kills the project.

**Job status:** `queued → running → success | failed`.

## Module map (`backend/app/`)

| Module | Responsibility |
| ------ | -------------- |
| `config.py` | `Settings` (pydantic-settings) from env / `.env` |
| `database.py` | Engine, `SessionLocal`, `Base`, `init_db()` |
| `models.py` | SQLAlchemy ORM models |
| `schemas.py` | Pydantic I/O + the validated `Storyboard` structure |
| `state.py` | Status enums + ordering |
| `models_config.py` | Model routing table, pricing, `resolve_video_model()` |
| `cost.py` | Cost estimator (per step + full project) |
| `llm.py` | Anthropic wrapper: `complete_json`, `rank_images` (vision) |
| `storage.py` | MinIO/S3 helper (`put_bytes`, `get_bytes`, `public_url`) |
| `media.py` | FFmpeg helpers: encode clip, demux native audio, extract frames |
| `providers/` | External provider calls (`fal_provider.generate_image` / `generate_video`) behind the mock flag |
| `celery_app.py` | Celery app + config |
| `tasks.py` | Celery tasks (the only place generation runs) |
| `pipeline/` | One module per stage — independently testable |
| `routers/` | FastAPI routers: `config`, `projects`, `storyboard`, `keyframes`, `video`, `assets`, `jobs` |
| `main.py` | App assembly, CORS, lifespan (DB + bucket init) |

See [PIPELINE.md](PIPELINE.md) for the pipeline modules and [API.md](API.md) for
the HTTP surface.
