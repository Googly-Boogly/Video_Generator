# StoryForge

Turn a single text prompt into a finished short film with audio, through an AI
pipeline. Fully dockerized — one `docker compose up` brings up everything.

> **Status:** Phases 1–3 complete — storyboard + review UI, style-bible reference
> images + FLUX.2 best-of-N keyframes, and video generation + quality gate (mock
> mode produces genuinely playable clips). Phases 4–6 scaffolded.
> See [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Quick start

```bash
cp .env.example .env          # MOCK_GENERATION=true by default — no API keys needed
docker compose up --build
```

Then open:

| Service            | URL                                   | Notes |
| ------------------ | ------------------------------------- | ----- |
| **Frontend (UI)**  | http://localhost:5273                 | The app |
| API (Swagger docs) | http://localhost:8800/docs            | FastAPI interactive docs |
| MinIO console      | http://localhost:9001                 | login `storyforge` / `storyforge-secret` |

> **⚠️ Non-default host ports.** The defaults (5173/8000/5432/6379) collided with
> another stack on the dev host, so host ports are remapped: **UI 5273, API 8800,
> Postgres 5433, Redis 6380**. Inside the Docker network services still use their
> normal ports. Change the left-hand side of each `ports:` mapping in
> `docker-compose.yml` if you want the defaults back.

In **mock mode** every generation step returns instant placeholder assets and
silent audio stubs, so you can drive the entire UI and pipeline with **zero API
spend**. Flip `MOCK_GENERATION=false` and supply keys to go live (see
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#going-live)).

## What it does

1. You describe an idea (length, aspect ratio, style preset).
2. Claude writes a **style bible** (locked palette, lighting, lens, character
   sheet) and a shot-by-shot **storyboard**.
3. You **review and edit** the storyboard — edit any field, reorder, add/delete
   scenes, pick a model per scene, toggle narrated/dialogue audio, or revise
   conversationally ("make scene 3 moodier"). **Nothing costs money until you
   approve.**
4. On approval, FLUX.2 renders **3 keyframe variants per scene** (with the style
   reference images attached for consistency); Claude-vision ranks them and you
   pick the winner in a **best-of-N selection UI**.
5. The winning keyframes are **animated into clips** by the routed model; native
   audio is demuxed per clip and a **vision quality gate** flags artifacts for
   one-click regeneration. Clips play right in the browser.
6. (Phases 4–5) Audio build → AI editor → draft/final render.

## Documentation

| Doc | What's in it |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Services, data flow, DB schema, state machine |
| [docs/PIPELINE.md](docs/PIPELINE.md)         | The 11 pipeline stages + hybrid audio strategy |
| [docs/MODELS.md](docs/MODELS.md)             | Model routing table, cost model, prompt translator |
| [docs/API.md](docs/API.md)                   | Full HTTP endpoint reference |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)   | Local setup, migrations, going live, troubleshooting |
| [docs/TESTING.md](docs/TESTING.md)           | Test strategy + how to run everything |
| [docs/ROADMAP.md](docs/ROADMAP.md)           | Phase-by-phase build plan and status |

## Tech stack

Backend: Python 3.12 · FastAPI · SQLAlchemy + Alembic · PostgreSQL · Celery + Redis.
Frontend: React (Vite) · TypeScript · Tailwind. Generation: fal.ai (`fal-client`),
Anthropic SDK, ElevenLabs. Assembly: FFmpeg. Storage: MinIO (S3-compatible).

## Testing

```bash
# Self-contained unit + API integration tests (SQLite + eager Celery, no infra)
docker compose exec api python -m pytest -q          # 29 passed

# Live smoke test against the running stack (59 checks across every endpoint)
python scripts/smoke_test.py

# Frontend type-check + production build
docker compose exec frontend npm run build
```

See [docs/TESTING.md](docs/TESTING.md) for the full picture.

## License

Internal project scaffold. Add a license before distributing.
