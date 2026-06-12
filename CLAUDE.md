# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo. Keep it current when
behavior or structure changes.

## What this is

**StoryForge** turns one text prompt into a finished short film through an AI
pipeline. Fully dockerized: `docker compose up` brings up six services
(`frontend`, `api`, `worker`, `redis`, `postgres`, `minio`).

**Status:** Phases 1–4 done (storyboard + review UI; FLUX.2 best-of-N keyframes;
video generation + quality gate; audio build — ElevenLabs narration + music bed +
librosa beat grid + mix plan). Phases 5–6 scaffolded.
See [docs/ROADMAP.md](docs/ROADMAP.md).

## Ports (IMPORTANT — non-default)

The dev host already runs another `infra-*` stack on the defaults, so StoryForge
host ports are remapped. Inside the Docker network services use normal ports.

| Service | URL |
| --- | --- |
| Frontend (UI) | http://localhost:5273 |
| API (Swagger) | http://localhost:8800/docs |
| MinIO console | http://localhost:9001 (`storyforge` / `storyforge-secret`) |
| Postgres / Redis | host 5433 / 6380 |

If you add a service, pick a non-default host port and check `docker ps` first.
Frontend → API via `VITE_API_BASE=http://localhost:8800`; keep the CORS allowlist
in `backend/app/main.py` in sync.

## Mock mode

`MOCK_GENERATION=true` (default in `.env`) makes every generation stage return
instant placeholder assets (solid-color PNGs, silent WAVs) with **zero API spend**.
This is how the whole pipeline + UI run without keys. Real provider calls
(`fal-client`, Anthropic, ElevenLabs) only fire when it's `false` + keys are set.
**Every new stage must keep a mock path.**

Note: "mock" means *don't pay an AI model* — local media work still runs for real.
Mock video clips are genuinely playable MP4s that FFmpeg encodes from the winning
keyframe (and quality-gate frames are really extracted), so the UI plays video and
the FFmpeg path is exercised. FFmpeg is in the backend image; Phase 3 tests use it.

## Common commands

```bash
docker compose up --build              # start everything
docker compose restart worker          # REQUIRED after editing tasks.py (Celery has no hot-reload)
docker compose exec api python -m pytest -q     # 49 tests, FFmpeg+librosa in image
docker compose exec frontend npm run build      # tsc type-check + prod build
python scripts/smoke_test.py           # 49 live checks against the running stack
docker compose logs -f api|worker      # tail logs
docker compose down -v                 # stop + wipe DB/MinIO/assets
```

`api` and `worker` hot-reload (uvicorn `--reload` / Vite HMR). The **Celery worker
does not** — restart it after changing `tasks.py` or anything it imports.

## Architecture rules (don't break these)

- **Keys are server-side only.** The browser talks only to FastAPI; it loads media
  via the asset proxy `GET /api/assets/{id}/content`, never MinIO directly.
- **Generation runs only in Celery tasks** (`app/tasks.py`). HTTP handlers create a
  `Job` row and `.delay()` — never block on generation. Clients poll
  `GET /api/jobs/{id}` or stream `/stream` (SSE).
- **Model facts are config.** `app/models_config.py` is the single source of truth
  for model routing, pricing, and capabilities. `resolve_video_model()` decides a
  scene's model (override > premium suggestion > draft default). Nothing else
  hardcodes model ids or prices.
- **Pipeline stages are isolated.** One module per stage in `app/pipeline/`, each
  independently testable. A failed scene is isolated (marked `failed`) and never
  kills the project.
- **State lives in Postgres.** Project state machine: `draft → styled →
  storyboarded → keyframes → clips → audio → edited → draft_rendered → rendered`.
  Restarts never lose progress. Scene numbers stay contiguous (1..N).

## Where things live

```
backend/app/
  config.py models.py schemas.py state.py models_config.py cost.py
  llm.py            Anthropic: complete_json + rank_images (vision)
  storage.py        MinIO/S3 helper
  asset_store.py    store_asset(): put bytes in MinIO + create the Asset row
  media.py          FFmpeg: encode clip, demux native audio, extract frames, synth music
  providers/        fal_provider (image/video) + elevenlabs_provider (TTS) — mock-gated
  celery_app.py tasks.py            the ONLY place generation runs
  pipeline/         style_bible storyboard keyframes video quality audio editor
                    assemble + prompts.py (per-model translator) + mock.py
  routers/          config projects storyboard keyframes video audio assets jobs
  main.py
frontend/src/
  pages/  Home NewProject StoryboardReview Keyframes Clips Audio
  components/SceneCard.tsx   lib/api.ts   types.ts
scripts/smoke_test.py        docs/        docker-compose.yml  .env.example
```

## Testing expectations

Keep all three green under `MOCK_GENERATION=true` (CI must never spend money):

- **pytest** (`backend/tests/`) — unit (`test_pipeline_mock.py`) + API integration
  (`test_api_integration.py`, SQLite + eager Celery + in-memory storage shim).
  Currently **49 passed**.
- **smoke** (`scripts/smoke_test.py`) — **74 checks** against the live stack.
- **frontend** — `npm run build` must type-check clean (dev mode hides TS errors).

Add a regression test for every behavior you add or bug you fix. See
[docs/TESTING.md](docs/TESTING.md).

## Conventions when extending

- New paid stage → add to `pipeline/`, keep a mock path, wire the real provider in
  `providers/`, add cost to `models_config.py`, expose via a router that enqueues a
  Celery task, then add pytest + smoke coverage.
- New asset type → store via `asset_store.store_asset` (MinIO + `Asset` row), serve
  via the asset proxy. Reference the `kind` consistently
  (`reference|keyframe|clip|native_audio|narration|music|draft|final|frame`).
- **Clearing prior assets/scenes? Use `project.assets.remove(a)` /
  `project.scenes.clear()`, NOT `db.delete(a)`.** The relationships are
  `cascade="all, delete-orphan"`; calling `db.delete()` on a child that's still in
  the loaded collection trips the cascade on re-run/rebuild paths (a bug we've hit
  three times). Removing through the collection keeps it consistent.
- Keep `frontend/src/types.ts` in sync with `backend/app/schemas.py`.

## Docs index

[ARCHITECTURE](docs/ARCHITECTURE.md) · [PIPELINE](docs/PIPELINE.md) ·
[MODELS](docs/MODELS.md) · [API](docs/API.md) · [DEVELOPMENT](docs/DEVELOPMENT.md) ·
[TESTING](docs/TESTING.md) · [ROADMAP](docs/ROADMAP.md)
