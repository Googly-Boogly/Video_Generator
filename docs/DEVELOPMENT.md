# Development guide

## Prerequisites

- Docker + Docker Compose v2 (`docker compose version`)
- For host-side backend work without containers: Python 3.12
- For host-side frontend work without containers: Node 20+

## Run the stack

```bash
cp .env.example .env        # MOCK_GENERATION=true by default
docker compose up --build   # first run builds images
```

| Service  | Container port | Host port | URL |
| -------- | -------------- | --------- | --- |
| frontend | 5173 | **5273** | http://localhost:5273 |
| api      | 8000 | **8800** | http://localhost:8800/docs |
| postgres | 5432 | **5433** | — |
| redis    | 6379 | **6380** | — |
| minio    | 9000 / 9001 | 9000 / 9001 | console: http://localhost:9001 |

> Host ports are remapped off the defaults because another stack on the dev host
> already used 5173/8000/5432/6379. To revert, edit the left-hand side of each
> `ports:` entry in `docker-compose.yml`. The frontend reaches the API via
> `VITE_API_BASE` (set to `http://localhost:8800` in compose) — keep these in sync.

Source is bind-mounted, so both servers hot-reload:
- API: `uvicorn --reload`
- Frontend: Vite HMR (polling enabled for Docker)

The **Celery worker does not hot-reload** — after editing `tasks.py` or anything it
imports, run `docker compose restart worker`. **But never restart the worker while a
generation job is running:** `task_acks_late` re-queues the in-flight task and the fresh
worker re-runs it (re-spending on providers). To truly stop a stuck/runaway task:
`docker compose kill worker` → `docker compose exec redis redis-cli FLUSHALL` →
`docker compose up -d worker`. Also: only one generation job runs per project at a time
(`jobs_util.ensure_project_idle` → HTTP 409); the worker clears jobs stuck >30 min on boot.

**Adding a Python dependency** (e.g. librosa, or **CrewAI** for the refine crew) requires
an image rebuild: `docker compose build api worker && docker compose up -d api worker`.
The backend image ships FFmpeg, `libsndfile1` (for librosa/soundfile), `crewai` (multi-agent
refine), and `pytest`. CrewAI is lazy-imported in `pipeline/refine.py`, so mock mode and the
test suite don't require it.

## Common commands

```bash
docker compose logs -f api          # tail a service
docker compose restart worker       # reload task code
docker compose exec api bash        # shell into the API container
docker compose exec api python -m pytest -q   # run the test suite
docker compose down                 # stop (keep volumes)
docker compose down -v              # stop and wipe DB + MinIO + assets
```

## Configuration

All settings come from env / `.env` via `app/config.py` (`Settings`). Key vars:

| Var | Default | Notes |
| --- | ------- | ----- |
| `MOCK_GENERATION` | `true` | `false` makes stages call real providers |
| `FAL_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `ELEVENLABS_API_KEY` | empty | required only when live (set the LLM key you use) |
| `DEFAULT_LLM` | `gpt-5.4-nano` | default LLM; a project can pick `gpt-5.4-nano` or `claude-haiku-4-6` |
| `DATABASE_URL` | postgres in compose | tests override to SQLite |
| `MINIO_PUBLIC_ENDPOINT` | `http://localhost:9000` | what presigned URLs point the browser at |

## Database & migrations

For dev convenience the API runs `Base.metadata.create_all()` on boot, so the
stack works with **zero manual migration steps**. Alembic is configured for real
schema evolution:

```bash
docker compose exec api alembic revision --autogenerate -m "describe change"
docker compose exec api alembic upgrade head
```

(`backend/alembic/env.py` reads `DATABASE_URL` and the ORM metadata.)

## Going live

1. Set `MOCK_GENERATION=false` in `.env`.
2. Fill in `FAL_KEY`, `ELEVENLABS_API_KEY`, and your LLM key(s) (`OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY`).
3. `docker compose up -d` (restart `api` + `worker` to pick up env).

Stages not yet wired for real (keyframes/video/quality/audio/editor/assemble in
Phase 1) raise `NotImplementedError` when called with mock off — see
[ROADMAP.md](ROADMAP.md) for which phase wires each.

> Going live spends money. Use the cost endpoint
> (`GET /api/projects/{id}/cost`) before kicking off paid steps.

## Project layout

```
backend/
  app/
    config.py models.py schemas.py state.py
    models_config.py cost.py llm.py storage.py
    celery_app.py tasks.py main.py
    pipeline/   style_bible.py storyboard.py keyframes.py video.py
                quality.py audio.py editor.py assemble.py prompts.py mock.py
    routers/    config.py projects.py storyboard.py jobs.py
  alembic/      env.py  versions/
  tests/        conftest.py test_pipeline_mock.py test_api_integration.py
  Dockerfile  entrypoint.sh  requirements.txt
frontend/
  src/
    pages/      Home.tsx NewProject.tsx StoryboardReview.tsx
    components/ SceneCard.tsx
    lib/api.ts  types.ts  App.tsx  main.tsx  index.css
  Dockerfile  package.json  vite/tailwind/postcss/tsconfig
scripts/        smoke_test.py
docs/           ARCHITECTURE PIPELINE MODELS API DEVELOPMENT TESTING ROADMAP
docker-compose.yml  .env.example
```

## Troubleshooting

| Symptom | Fix |
| ------- | --- |
| `port is already allocated` | Another process holds the host port. Change the mapping in `docker-compose.yml` or stop the other process. |
| `/app/entrypoint.sh: permission denied` | `chmod +x backend/entrypoint.sh` (it's bind-mounted). |
| Task code change has no effect | `docker compose restart worker` (Celery doesn't hot-reload). |
| Frontend can't reach API | Confirm `VITE_API_BASE` matches the API host port and the origin is in the CORS list in `app/main.py`. |
| MinIO links 404 in browser | `MINIO_PUBLIC_ENDPOINT` must be reachable from the browser (`localhost:9000`), not the in-network `minio:9000`. |
