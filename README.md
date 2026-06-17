# StoryForge

Turn a single text prompt into a finished short film with audio, through an AI
pipeline. Fully dockerized — one `docker compose up` brings up everything.

> **Status: all 6 phases complete + a Phase-7 pivot** — the **full pipeline runs end to
> end**: storyboard + review UI (+ optional **multi-agent CrewAI "Refine with AI"**) →
> one FLUX.2 keyframe/scene → **photo-to-video** clips (the keyframe is animated by Kling;
> image-to-video only, no lip-sync) + quality gate → audio build (one continuous narration
> track + music bed + **librosa** beat grid) → AI editor (EDL) → real FFmpeg **480p draft /
> 1080p final** render with preview, export, and a **cost dashboard**.
> See [docs/ROADMAP.md](docs/ROADMAP.md) — incl. the open **real-music** TODO.

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

1. You describe an idea (length, aspect ratio, style preset) and pick the **writer
   LLM** — `gpt-5.4-nano` (OpenAI) or `claude-haiku-4-6` (Anthropic).
2. That LLM writes a **style bible** (locked palette, lighting, lens, character
   sheet) and a shot-by-shot **storyboard**.
3. You **review and edit** the storyboard — edit any field, reorder, add/delete
   scenes, pick a model per scene, or revise conversationally ("make scene 3 moodier").
   Optionally hit **✨ Refine with AI** for a multi-agent (CrewAI) critique + rewrite of
   the storyboard and narration. **Nothing costs money until you approve.**
4. On approval, FLUX.2 renders **one keyframe per scene** (with the style reference
   images attached for consistency).
5. Each keyframe is **animated into a clip** via an image-to-video model (Kling —
   photo-to-video); native audio is demuxed per clip and a **vision quality gate** flags
   artifacts for one-click regeneration. Clips play right in the browser.
6. The **audio build** adds ElevenLabs narration in one locked voice, a music bed
   with a librosa **beat grid**, and a mix plan that ducks native audio under
   narration. All narration is one continuous voiceover track (no per-scene overlap).
7. The **AI editor** assembles an Edit Decision List (trims, transitions, captions,
   beat-snap, mix); **FFmpeg renders** a 480p watermarked draft, then a 1080p final
   (hero shots regenerated at premium). Preview and **download** in the browser.
8. A **cost dashboard** tracks estimated vs actual spend per step (re-runs included);
   a pipeline stepper and project history tie it all together.

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
OpenAI + Anthropic (selectable LLM), ElevenLabs. Assembly: FFmpeg. Storage: MinIO.

## Testing

```bash
# Self-contained unit + API integration tests (SQLite + eager Celery, no infra)
docker compose exec api python -m pytest -q          # 75 passed

# Live smoke test against the running stack (95 checks across every endpoint)
python scripts/smoke_test.py

# Frontend type-check + production build
docker compose exec frontend npm run build
```

See [docs/TESTING.md](docs/TESTING.md) for the full picture.

## License

Internal project scaffold. Add a license before distributing.
