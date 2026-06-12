# Roadmap

Build order. Each phase is demoable on its own; mock mode lets later phases be
built before any real provider is wired.

## ✅ Phase 1 — Foundation + storyboard review (done)

- Compose stack scaffolded; all six services healthy on `docker compose up`.
- Mock mode wired end to end (zero API spend).
- Style bible + storyboard generation via Celery.
- **Storyboard review UI** (the heart): editable scene cards (every field,
  reorder, add/delete), per-scene model picker, narrated/dialogue toggle,
  conversational revision, live cost estimate. Nothing costs money pre-approval.
- Config-driven model routing table + cost estimator.
- Project state machine in Postgres; assets in MinIO.
- Tests: 16 pytest (unit + API integration) + 39-check live smoke test.

**Verified:** see [TESTING.md](TESTING.md).

## ✅ Phase 2 — Style bible + keyframes (done)

- Generate 3–5 master reference images via FLUX.2 from the style bible
  (character / environment / color-key roles), stored in MinIO and reused as
  reference-image inputs for every keyframe.
- Per scene: 3 FLUX.2 keyframe variants with style references attached.
- Claude-with-vision auto-ranks variants; the user can override the winner in the
  **best-of-N selection UI** (`/projects/:id/keyframes`). Only the winner proceeds.
- Per-scene regenerate; a failed scene is isolated and never kills the project.
- Project advances `storyboarded → keyframes`.
- Wires: `providers/fal_provider.py` (FLUX.2), `llm.rank_images` (vision),
  `pipeline/style_bible.generate_reference_images`, `pipeline/keyframes.py`,
  `routers/keyframes.py`, `routers/assets.py`, frontend `pages/Keyframes.tsx`.
- Tests: 23 pytest + 49-check live smoke. Real provider calls are wired behind
  `MOCK_GENERATION` (instant placeholder PNGs in mock mode).

## ✅ Phase 3 — Video generation + quality gate (done)

- Image→video via the routed model (tier-aware; `?tier=draft|premium`); dialogue
  scenes → Veo with `dialogue_text` for lip sync. Per-scene `model_override` wins.
- Per-scene Celery status (queued/generating/done/failed/flagged) + per-scene
  regenerate; a failed scene is isolated and never kills the project.
- Native audio demuxed from every clip into its own asset (FFmpeg).
- Quality gate: 4 frames/clip extracted (FFmpeg), Claude vision flags artifacts +
  identity drift; garbled-speech check auto-mutes the native track. Flagged clips
  get one-click regenerate. Project advances `keyframes → clips`.
- **Mock mode produces genuinely playable clips:** FFmpeg encodes the winning
  keyframe into an H.264/AAC clip (real demux + frame extraction), so the Clips UI
  plays video without paying an AI model.
- Wires: `media.py` (FFmpeg), `providers/fal_provider.generate_video`,
  `pipeline/video.py`, `pipeline/quality.py`, `routers/video.py`, frontend
  `pages/Clips.tsx`.
- Tests: 29 pytest + 59-check live smoke.

## 🔜 Phase 4 — Audio build

- ElevenLabs narration per narrated scene with the locked project voice.
- Music bed + librosa beat grid (cut-on-beat).
- Native tracks leveled per the hybrid audio strategy (15–30% under narration).
- Wires: `pipeline/audio.py`, ElevenLabs, librosa. (Enable the commented audio
  deps in `requirements.txt`.)

## 🔜 Phase 5 — AI editor + renders

- Vision-equipped editor: Claude takes storyboard + frames + narration durations +
  beat grid + audio modes → an Edit Decision List (order, trims, transitions,
  captions, per-scene mix plan + ducking + narration pauses). EDL shown for approval.
- FFmpeg executes the EDL: 480p watermarked draft (budget tiers) → on approval,
  regenerate hero scenes on premium tiers and render final 1080p H.264/AAC with the
  full audio mix. Both stored in MinIO.
- Wires: `pipeline/editor.py`, `pipeline/assemble.py`.

## 🔜 Phase 6 — History, cost dashboard, polish

- In-browser player, download, project history page.
- Cost dashboard (estimated vs actual).
- General polish.

---

### Conventions for picking this up

- Each stage already has a mock path and a typed signature; replace the
  `NotImplementedError` branch with the real provider call.
- Keep the cost table (`models_config.py`) accurate — the estimator and UI read it.
- Add a regression test per stage under `MOCK_GENERATION=true`.
