# Testing

Three layers, all runnable with **zero API spend** (mock mode):

| Layer | File | Infra needed | What it covers |
| ----- | ---- | ------------ | -------------- |
| Unit (pipeline) | `backend/tests/test_pipeline_mock.py` | FFmpeg + librosa (in the image) | Storyboard validation, prompt dialects, model/tier resolution, best-of-N ranking, clip encode + demux + frame extract, voices/mix levels, narration synth, librosa beat grid, **EDL structure + beat-snap** |
| Unit (media) | `backend/tests/test_media.py` | FFmpeg (in the image) | `media.py` directly: encode, demux, frame extract, music-bed synth, **`assemble_video` draft 480p / final 1080p with audio** |
| Integration (API) | `backend/tests/test_api_integration.py` | none (SQLite + eager Celery + in-memory storage shim) | Every HTTP endpoint incl. keyframes/video/audio/EDL+render/cost dashboard, failure isolation, rebuild/regenerate (no cascade), hero regen, ledger accumulation, **robustness (storage cleanup, all-dialogue advance, concurrent-job guard, single-scene render)**, error paths |
| Smoke (live) | `scripts/smoke_test.py` | running stack | 95 checks against the real API + worker + Postgres + Redis + **MinIO** |

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

Expected: **75 passed** (35 unit + 40 integration).

> The media/audio/Phase 3 tests invoke real FFmpeg + librosa (present in the
> backend image), so run them in the container — encoding/demux/frame-extraction
> and beat detection happen for real, just with mock AI output. `pytest` is in
> `requirements.txt`; a freshly built image already has it.

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
project. It also runs Phase 2 (keyframes best-of-N, override, regenerate), Phase 3
(clip generation, playable-mp4, native audio, quality frames, regenerate),
Phase 4 (voices/library, library bed + **librosa** beat grid, narration build +
rebuild without duplication, mix plan), and Phase 5 (build EDL, **render draft 480p
+ final 1080p**, status transitions, download header), and Phase 6 (**cost
dashboard** — estimate vs actual ledger by step). Prints `PASS`/`FAIL` per check
and exits non-zero on any failure. Expected: **95 passed, 0 failed**.

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
- Keyframes: 3 variants/scene, exactly one winner, winner override, regenerate.
- Video: real playable H.264/AAC clips, native-audio demux (default unmuted),
  4 quality-gate frames, single-scene regenerate, premium tier → premium model.
- **Failure isolation:** a scene with no winning keyframe fails alone — it is
  marked `failed` with a reason, while every other scene still gets a clip and the
  project advances to `clips` (`test_failed_scene_is_isolated`).
- Media layer: `media.py` produces valid MP4s (h264+aac, correct duration),
  demuxes audio, extracts the right number of JPEG frames, synthesizes music beds,
  and raises `FFmpegError` on bad input (`test_media.py`).
- Audio: locked-voice narration per narrated scene (dialogue skipped), real
  **librosa** beat grid on the music bed (~128 bpm recovered), music upload/remove,
  the mix plan, and **rebuild without duplicating narration**.
- Editor/render: EDL with trims/beat-snap/mix; FFmpeg assembles a real **480p draft
  and 1080p final** (ffprobe-verified resolution + audio); status transitions
  `edited → draft_rendered → rendered`; hero-scene regen on final; render replaces
  the prior of its tier; export download header.
- Cost dashboard: the ledger records actual per-step spend (keyframes/video/audio/
  render), `by_step` sums to the total, **re-runs accumulate**, premium hero regen
  adds a `render` cost, entries are flagged `mock`, and delete cascades the ledger.
- Multi-LLM: `/api/config` lists both LLMs with providers; a project persists its
  `llm_model` (default when unset, 400 on unknown), and the OpenAI↔Anthropic image
  part conversion round-trips (`test_llm_routing_and_anthropic_part_conversion`,
  `test_llm_catalog_and_per_project_selection`).
- Frontend: every TS/TSX module transforms cleanly through Vite + `npm run build`
  type-checks; UI served at `:5273`.

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
4. **Delete-orphan cascade on asset re-runs** (Phase 4) — clearing prior assets
   with `db.delete()` while they were still in the loaded `project.assets`
   collection tripped SQLAlchemy's delete-orphan cascade — but only on the *rebuild*
   path (when assets already exist), which the first-run/single-scene tests didn't
   exercise. It also affected keyframes/video full-regenerate. Fixed by removing via
   the relationship (`project.assets.remove(a)`). Guarded by
   `test_audio_build_narration_and_rebuild` and `test_full_regenerate_keyframes_no_cascade`.
5. **Asset storage-key collision** (found in the Phase-6 robustness pass, latent
   since Phase 2) — `store_asset` built the MinIO key from `asset.id` *before* flush,
   but the `default=_uuid` only fires at flush, so every key was `…/{kind}/None.ext`
   and **all assets of a kind overwrote one object** (every keyframe served the
   last-written image). Masked because mock images are tiny solid colors. Fixed by
   assigning the id explicitly. Guarded by `test_regenerate_cleans_old_blobs` (which
   asserts distinct keys before/after a re-run).
6. **Orphaned storage + status/concurrency gaps** (robustness pass) — deleting a
   project (and every regenerate) left MinIO objects behind; an all-dialogue project
   couldn't advance past `clips`; and double-kicking a job raced. Fixed with a
   `before_delete` event that deletes the blob, a relaxed audio-advance rule, and a
   `ensure_no_active_job` 409 guard. Guarded by `test_delete_project_cleans_storage`,
   `test_all_dialogue_audio_advances_status`, `test_concurrent_job_guard`.

## Adding tests

- Pipeline logic → add to `test_pipeline_mock.py` (pure functions, no DB).
- New endpoint → add to `test_api_integration.py` using the `client` fixture; in
  eager mode a kicked-off job is already terminal when the POST returns.
- Keep everything green under `MOCK_GENERATION=true` so CI never spends money.
