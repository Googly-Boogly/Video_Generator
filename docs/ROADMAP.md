# Roadmap

Build order. Each phase is demoable on its own; mock mode lets later phases be
built before any real provider is wired.

> **All six phases are complete** — the full pipeline runs end to end (prompt →
> downloadable film). Remaining items are refinements, listed under Phase 6.

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
- a vision model auto-ranks variants; the user can override the winner in the
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
- Quality gate: 4 frames/clip extracted (FFmpeg), the vision model flags artifacts +
  identity drift; garbled-speech check auto-mutes the native track. Flagged clips
  get one-click regenerate. Project advances `keyframes → clips`.
- **Mock mode produces genuinely playable clips:** FFmpeg encodes the winning
  keyframe into an H.264/AAC clip (real demux + frame extraction), so the Clips UI
  plays video without paying an AI model.
- Wires: `media.py` (FFmpeg), `providers/fal_provider.generate_video`,
  `pipeline/video.py`, `pipeline/quality.py`, `routers/video.py`, frontend
  `pages/Clips.tsx`.
- Tests: 29 pytest + 59-check live smoke.

## ✅ Phase 4 — Audio build (done)

- ElevenLabs narration per narrated scene with the locked project voice
  (`Project.voice_id`); dialogue scenes are skipped (native audio carries speech).
- One continuous music bed (upload or built-in library) with a **librosa beat
  grid** detected on the real audio (runs in mock mode too — no AI spend).
- Native tracks leveled per the hybrid strategy via a `mix-plan` (narration 0 dB,
  native −16 dB ducked, music −18 dB; dialogue pauses narration). Project advances
  `clips → audio`.
- Wires: `providers/elevenlabs_provider.py`, `pipeline/audio.py`, `media.synth_music_bed`,
  `asset_store.py`, `routers/audio.py`, frontend `pages/Audio.tsx`. librosa +
  soundfile enabled in `requirements.txt`; `libsndfile1` in the image.
- Tests: 49 pytest + 74-check live smoke. (Also fixed a latent delete-orphan
  cascade bug on asset re-runs — see TESTING.md.)

## ✅ Phase 5 — AI editor + renders (done)

- AI editor builds an **Edit Decision List** from the storyboard + real signals
  (clip durations, narration durations, beat grid, audio modes): per-cut in/out,
  trims (cut mushy starts/ends), transition, caption, beat-snap, and a per-scene
  mix plan. Shown for approval. Live path is the vision model over extracted frames.
- **FFmpeg renders the EDL for real:** concat (trimmed) clips, burn captions, and
  build the hybrid audio mix (narration delayed per scene + native ducked −16 dB +
  music bed −18 dB, limiter). Draft = **480p watermarked**; final regenerates hero
  scenes (dialogue + flagged) at premium then renders **1080p H.264/AAC**. Both in
  MinIO. Project advances `audio → edited → draft_rendered → rendered`.
- In-browser preview + **download/export** (Content-Disposition). Home shows a
  "▶ watch" link for rendered projects.
- Wires: `media.assemble_video` (FFmpeg filtergraph), `pipeline/editor.py`,
  `pipeline/assemble.py`, `routers/render.py`, frontend `pages/Editor.tsx`.
- Tests: 57 pytest + 89-check live smoke.

## ✅ Phase 6 — History, cost dashboard, polish (done)

- **Cost dashboard:** a per-project ledger (`cost_entries`) records what every paid
  step actually ran (keyframes, video, narration, premium hero regen), so the
  dashboard shows **estimated (full premium) vs actual** by step — re-runs append,
  surfacing regeneration waste. In mock mode the amounts are the would-be cost with
  a "$0 actually charged" banner. `GET /api/projects/{id}/costs`, `pages/Costs.tsx`.
- **Render polish:** fade (dip-to-black) transitions for non-`cut` cuts +
  intro/outro, and **sidechain ducking** of the music under narration.
- **Polish:** a `PipelineNav` stepper across project pages; in-browser player +
  download/export; project history (Home) with "▶ watch".
- Tests: 60 pytest + 95-check live smoke.

### Possible future refinements
- Overlapping crossfade (xfade) instead of dip-to-black; cost ceilings/alerts;
  multi-project cost rollup; richer history page.

---

### Conventions for picking this up

- Each stage already has a mock path and a typed signature; replace the
  `NotImplementedError` branch with the real provider call.
- Keep the cost table (`models_config.py`) accurate — the estimator and UI read it.
- Add a regression test per stage under `MOCK_GENERATION=true`.
