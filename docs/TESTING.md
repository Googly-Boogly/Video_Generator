# Testing

Three layers, all runnable with **zero API spend** (mock mode):

| Layer | File | Infra needed | What it covers |
| ----- | ---- | ------------ | -------------- |
| Unit (pipeline) | `backend/tests/test_pipeline_mock.py` | FFmpeg (in the image) | Storyboard validation, prompt-translator dialects, model/tier resolution, reference images + best-of-N ranking, **playable-clip encode + demux + frame extract**, EDL mix plan |
| Integration (API) | `backend/tests/test_api_integration.py` | none (SQLite + eager Celery + in-memory storage shim) | Every HTTP endpoint incl. keyframes/assets/**video+quality**, validation/error paths, async job lifecycle |
| Smoke (live) | `scripts/smoke_test.py` | running stack | 59 checks against the real API + worker + Postgres + Redis + **MinIO** |

> The integration harness (`conftest.py`) also patches the storage helpers with an
> in-memory shim, so keyframe/asset tests need no MinIO.

## Run the pytest suite (unit + integration)

Self-contained — `tests/conftest.py` points the app at a temp **SQLite** DB and
runs Celery tasks **eagerly** (in-process), so no Postgres/Redis/worker/MinIO are
required.

```bash
# In the container (has all deps):
docker compose exec api python -m pytest -q

# Or on the host:
cd backend && MOCK_GENERATION=true python -m pytest -q
```

Expected: **29 passed** (12 unit + 17 integration).

> Phase 3 tests invoke real FFmpeg (present in the backend image), so run them in
> the container — encoding/demux/frame-extraction happen for real, just with mock
> AI output.

> Host run needs the backend deps installed (`pip install -r backend/requirements.txt`),
> or at minimum `fastapi sqlalchemy celery httpx sse-starlette pydantic
> pydantic-settings pytest`.

## Run the live smoke test

Requires the stack up (`docker compose up`) in mock mode.

```bash
python scripts/smoke_test.py           # defaults to http://localhost:8800
BASE=http://localhost:8800 python scripts/smoke_test.py
```

It creates a project, generates a storyboard, edits/reorders/adds/deletes scenes,
revises conversationally, checks tier-based costs and error paths, then deletes the
project. It also runs the Phase 2 keyframe flow (reference images, best-of-N,
asset content, winner override, single-scene regenerate) and the Phase 3 video
flow (clip generation, playable-mp4 check, native audio, quality-gate frames,
single-scene regenerate). Prints `PASS`/`FAIL` per check and exits non-zero on any
failure. Expected: **59 passed, 0 failed**.

## Frontend

```bash
docker compose exec frontend npm run build   # tsc type-check + production build
```

Type errors (which the dev server's esbuild transform ignores) fail here, so run
it before considering a frontend change done. Vite also transforms every TS/TSX
module on request in dev, surfacing import/parse errors immediately.

## What's verified end to end

- Stack health: all six services up; Postgres/Redis/MinIO healthcheck `healthy`;
  the `storyforge` MinIO bucket is auto-created on API startup.
- Full mock pipeline: `create → style bible → storyboard → review edits → revise`,
  with project state advancing `draft → styled → storyboarded`.
- Async correctness: jobs go `queued → running → success`; SSE stream emits status
  and closes on terminal state.
- Routing/cost: dialogue scenes route to a lip-sync model; draft cost < premium
  cost; explicit per-scene overrides win.
- Frontend: every TS/TSX module transforms cleanly through Vite; UI served at
  `:5273`.

## Regression notes

Two bugs were caught by these tests and fixed:

1. **Revise cascade** — replacing scenes via `db.delete()` while the relationship
   collection still held them tripped SQLAlchemy's delete-orphan cascade on
   populated projects. Fixed by mutating through `project.scenes` (clear → append).
   Guarded by `test_conversational_revision` + the smoke test.
2. **Tier cost equality** — premium and draft estimates were identical because the
   premium `suggested_model` won regardless of tier. Fixed with a shared
   `resolve_video_model()`. Guarded by `test_draft_tier_is_cheaper_than_premium`
   and `test_cost_premium_vs_draft`.
3. **Untyped `import.meta.env`** (Phase 2) — the frontend dev server transformed
   fine but `npm run build` (tsc) failed on `import.meta.env`. Fixed by adding
   `frontend/src/vite-env.d.ts`. Caught by running the production build, which is
   now part of the frontend check.

## Adding tests

- Pipeline logic → add to `test_pipeline_mock.py` (pure functions, no DB).
- New endpoint → add to `test_api_integration.py` using the `client` fixture; in
  eager mode a kicked-off job is already terminal when the POST returns.
- Keep everything green under `MOCK_GENERATION=true` so CI never spends money.
