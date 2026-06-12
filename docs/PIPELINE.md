# Pipeline

Each stage lives in `backend/app/pipeline/` as an independently testable module.
Real provider calls run only when `MOCK_GENERATION=false`; otherwise each stage
returns instant placeholder output (see [Mock mode](#mock-mode)).

```
style_bible → storyboard → keyframes → video → quality → audio → editor → assemble
```

## Stages

| # | Stage | Module | Phase | What it produces |
| - | ----- | ------ | ----- | ---------------- |
| 1 | Prompt intake | (API) | ✅ 1 | Project row: idea, length, aspect, style preset |
| 2 | Style bible + reference images | `style_bible.py` | ✅ 1–2 | Locked palette/lighting/lens + character sheet; 3–5 master reference images (character/environment/color-key) via FLUX.2 |
| 3 | Storyboard | `storyboard.py` | ✅ 1 | Validated `scenes[]` (see schema below) |
| 4 | Storyboard review | (API + UI) | ✅ 1 | Human-edited storyboard; conversational revision |
| 5 | Keyframes (best-of-N) | `keyframes.py` | ✅ 2 | 3× FLUX.2 variants/scene with refs attached + Claude-vision ranked winner; user can override |
| 6 | Video generation | `video.py` | ✅ 3 | One clip/scene via the routed model (tier-aware) + demuxed native audio |
| 7 | Quality gate | `quality.py` | ✅ 3 | 4 frames/clip + Claude-vision artifact/identity flags + garbled-speech auto-mute |
| 8 | Audio build | `audio.py` | ✅ 4 | ElevenLabs narration (locked voice), music bed + librosa beat grid, native-track mix plan |
| 9 | AI editor | `editor.py` | ✅ 5 | Edit Decision List (order, trims, transitions, captions, beat-snap, mix plan) |
| 10 | Draft → final render | `assemble.py` + `media.py` | ✅ 5 | Real FFmpeg: 480p watermarked draft → 1080p H.264/AAC final with hybrid audio mix |
| 11 | Preview & export | (API + UI) | ✅ 5 | In-browser player, download (Content-Disposition), history with "▶ watch" |

✅ = implemented in Phase 1 · 🔜 = scaffolded with a working mock path; real
provider integration arrives in the noted phase (`NotImplementedError` is raised
if called with `MOCK_GENERATION=false` before then).

## Storyboard schema (validated)

`pipeline/storyboard.py` validates every storyboard — from Claude or from the mock
generator — against `schemas.Storyboard` before the rest of the system trusts it.

```jsonc
{
  "scenes": [
    {
      "scene_number": 1,            // contiguous from 1
      "duration_seconds": 5,        // 0 < d <= 15
      "shot_description": "...",
      "camera_movement": "slow push in",
      "image_prompt": "...",        // single keyframe
      "video_prompt": "...",        // motion
      "narration_text": "...",
      "audio_mode": "narrated",     // "narrated" | "dialogue"
      "dialogue_text": null,        // set only when audio_mode == "dialogue"
      "suggested_model": "kling-3-pro"
    }
  ]
}
```

Validation also re-numbers scenes contiguously and backfills `suggested_model`.

## Keyframes: reference images + best-of-N (Phase 2)

```
generate_keyframes_task(project_id, [scene_id])
  ├─ ensure master reference images (once per project)
  │     style_bible.reference_image_prompts → FLUX.2 → MinIO assets (kind=reference)
  │     roles: character / environment / colorkey
  ├─ per scene (failure isolated → scene.status=failed, others continue):
  │     ├─ scene.status=generating
  │     ├─ 3× FLUX.2 variants, reference images attached  (kind=keyframe assets)
  │     ├─ Claude-vision ranks → winner + per-variant {score, reason}
  │     ├─ scene.keyframe_asset_id = winner, scene.status=done
  └─ project.status = keyframes
```

- **Reference images are the consistency mechanism:** the master references are
  passed as reference-image inputs to every FLUX.2 keyframe (and, later, every
  Seedance reference-to-video call), so characters/style stay locked across shots.
- **Best-of-N:** only the winner is animated in Phase 3. The auto-rank is a
  starting point — the user can pick a different variant in the selection UI
  (`POST …/scenes/{id}/keyframe/select`), which flips the `is_winner` flags.
- **Regenerate** re-runs a single scene (fresh variants), or the whole project.
- Assets are served to the browser via a backend proxy
  (`GET /api/assets/{id}/content`) — no MinIO credentials reach the client.

## Video generation + quality gate (Phase 3)

```
generate_video_task(project_id, tier="draft", [scene_id])
  ├─ per scene (failure isolated → scene.status=failed, others continue):
  │     ├─ require winning keyframe (else fail this scene)
  │     ├─ scene.status=generating; clear old clip/native/frame assets
  │     ├─ resolve model (override > premium suggestion > draft default);
  │     │     dialogue → lip-sync model with dialogue_text in the prompt
  │     ├─ generate clip (kind=clip)  ── mock: FFmpeg-encode the keyframe
  │     ├─ demux native audio (kind=native_audio)
  │     ├─ quality gate: extract 4 frames (kind=frame) → Claude-vision verdict
  │     │     {flagged, reasons, identity_drift}; garble check → auto-mute native
  │     └─ scene.clip_asset_id / native_audio_asset_id / quality;
  │           status = flagged | done
  └─ project.status = clips
```

- **Tier-aware routing:** `?tier=draft` uses budget models, `?tier=premium` uses
  the premium suggestion. Per-scene `model_override` always wins. (Phase 5 will
  re-render hero scenes at premium for the final cut.)
- **Native audio per clip:** every clip's audio track is demuxed into its own
  asset so it can be leveled independently in Phase 4 (15–30% under narration).
  If the garble check trips, the native track is auto-muted (`meta.muted=true`).
- **One-click regenerate:** `POST …/scenes/{id}/video` re-runs a single scene
  (used for flagged clips); a fresh clip + frames replace the old ones.
- Clips and frames are served via the asset proxy and play directly in the UI.

## Audio build (Phase 4)

```
build_audio_task(project_id, [scene_id])
  ├─ ensure the music bed's beat grid (librosa) if a bed is chosen
  └─ per narrated scene (dialogue scenes skipped — native audio carries speech):
        ElevenLabs TTS with Project.voice_id  ── mock: silent WAV sized to text
        → narration asset (kind=narration) {voice_id, duration, chars}
  → project.status = audio
```

- **Voice is locked per project** (`Project.voice_id`, default `voice_aria`). Set
  via `POST …/voice`. Narration carries the words; native model audio is never the
  voice (identity can't persist across generation calls).
- **Music bed** is one continuous track: `POST …/music` (upload) or
  `POST …/music/library` (a built-in bed synthesized by FFmpeg). On either, a
  **librosa beat grid** `{bpm, beats[], duration, engine}` is detected on the real
  audio and stored in the music asset's `meta` (falls back to a synthetic grid only
  if librosa/codec is unavailable).
- **Mix plan** (`GET …/mix-plan`) renders the per-scene levels the editor/render
  will apply: narration 0 dB, native −16 dB (ducked, or muted if garble-flagged),
  music −18 dB; dialogue scenes set `narration_db=None` + `pause_narration_for_dialogue`.
- Narration is one asset per narrated scene; rebuilding replaces (never duplicates)
  them.

## AI editor + render (Phase 5)

```
build_edl_task → editor.build_edl(scenes, beat_grid)
   EDL = { total_duration, cuts[{scene_number, in, out, trim_head, trim_tail,
           transition, caption, on_beat, mix}], beat_grid, levels, engine }
   → project.edl, status = edited

render_task(final) → assemble.render → media.assemble_video (FFmpeg)
   ├─ final only: regenerate hero scenes (dialogue + flagged) at premium tier
   ├─ video: trim each clip, scale to 480p/1080p, burn caption, concat, (draft) watermark
   ├─ audio: native (trim+level, concat) + narration (delay per scene, mix)
   │         + music bed (pad/trim/level) → amix → limiter
   └─ store draft|final asset (replaces prior of that tier); status → draft_rendered|rendered
```

- The render is **real FFmpeg in both modes** — the inputs (clips, narration,
  music) are already real, so there's no mock branch.
- **Hybrid mix realized:** narration at 0 dB delayed to each scene's offset, native
  audio ducked to −16 dB (0 dB on dialogue, silenced if garble-muted), music bed at
  −18 dB; a final `alimiter` prevents clipping.
- **Editor signals:** clip durations, narration durations, the librosa beat grid,
  and audio modes. Cuts are beat-snapped (`on_beat`). The live path sends sampled
  frames to Claude vision; mock is a deterministic rules EDL from the same signals.
- **v1 simplifications (documented):** transitions are recorded per cut but the
  render uses hard cuts; ducking is static leveling (music −18 dB under narration).

## Hybrid audio strategy

Deliberately mixed — implemented as designed (levels live in `pipeline/audio.py`
and the mix plan in `pipeline/editor.py`):

- **Narration** — ElevenLabs TTS, **one locked voice id per project**. Native model
  audio is never used for narration (voice identity can't persist across separate
  generation calls).
- **Music** — one continuous bed for the whole video, beat-detected with librosa so
  the editor can cut on beat. Never per-clip.
- **Native model audio** (ambience/Foley/SFX) — demuxed from every generated clip
  into its own track, mixed **15–30% under narration** (`NATIVE_DUCK_DB = -16 dB`).
  It's the only way to get Foley that matches on-screen motion.
- **On-screen dialogue** — the only case native audio carries speech. That scene
  uses Veo 3.1 lip-sync (`audio_mode = "dialogue"`), and **narration pauses** over
  it (`pause_narration_for_dialogue` in the EDL mix plan). Default storyboards
  avoid on-screen speaking; narration carries the words.
- **Quality gate** flags clips whose native audio contains stray/garbled speech →
  that clip's native track is auto-muted.

## Cost gating

A cost estimate (`app/cost.py`, computed from the routing table) is available
before every paid step via `GET /api/projects/{id}/cost?tier=...`. Draft tier uses
budget models, premium uses the suggested/premium models, and any per-scene
`model_override` always wins — so the estimate reflects exactly what a real run
would charge. See [MODELS.md](MODELS.md).

## Mock mode

`MOCK_GENERATION=true` (the default) makes every stage return instant placeholders
so the whole UI and pipeline run with **zero API spend**:

| Output | Mock artifact (`pipeline/mock.py`) |
| ------ | ---------------------------------- |
| Style bible | Plausible JSON with a locked character sheet |
| Storyboard | Beat-structured scenes sized to the target length |
| Keyframe | Tiny solid-color PNG, color seeded per scene |
| Clip | **Real, playable** H.264/AAC MP4 — FFmpeg-encoded from the winning keyframe |
| Native audio | Demuxed from the clip (silent track in mock) |
| Quality frames | **Really extracted** from the clip via FFmpeg (4 JPEGs) |
| Narration audio | Valid **silent** WAV of the right duration (ElevenLabs when live) |
| Music bed | **Real** FFmpeg-synthesized beat track; **librosa** detects its grid for real |
| Draft / final render | **Real** FFmpeg-assembled MP4 (480p / 1080p) with the full audio mix |
| Quality report | Passes most clips; deterministically flags a subset |
| EDL | Full decision list with a per-scene mix plan |

This is what lets Phases 2–6 be built and demoed before wiring any real provider.
