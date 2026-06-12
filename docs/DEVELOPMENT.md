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
imports, run `docker compose restart worker`.

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
| `FAL_KEY` / `ANTHROPIC_API_KEY` / `ELEVENLABS_API_KEY` | empty | required only when live |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | storyboard / revision / editor model |
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
2. Fill in `FAL_KEY`, `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`.
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
