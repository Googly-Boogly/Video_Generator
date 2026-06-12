# HTTP API reference

Base URL (dev): `http://localhost:8800`. Interactive docs: `/docs` (Swagger),
`/redoc`. All bodies are JSON. CORS is open to the frontend origins.

Money is never spent by an edit endpoint — generation happens only in Celery tasks
kicked off by the storyboard/revise endpoints (and, in later phases, render
endpoints). Mock mode keeps even those free.

## Conventions

- IDs are UUID strings.
- Async actions return **202** with a `Job`; poll `GET /api/jobs/{id}` or stream
  `GET /api/jobs/{id}/stream` until `status` is `success` or `failed`.
- Validation errors → **422**; unknown id → **404**; bad references → **400**.

---

## Config

### `GET /api/config`
Model table + presets for the UI.
```jsonc
{
  "mock_generation": true,
  "style_presets": ["cinematic", "anime", ...],
  "target_lengths": [15, 30, 60],
  "aspect_ratios": ["16:9", "9:16", "1:1"],
  "models": [{ "id": "kling-3-pro", "label": "...", "tier": "premium",
               "price_per_second": 0.11, "lip_sync": false, ... }],
  "video_models": ["kling-3-pro", "kling-25-turbo", "veo-31", ...]
}
```

### `GET /health`
`{ "status": "ok", "mock_generation": true }`

---

## Projects

### `GET /api/projects`
List projects (newest first).

### `POST /api/projects` → 201
```jsonc
{ "idea": "A lonely lighthouse keeper…",   // required, ≥3 chars
  "title": "optional",
  "target_length": 30,                      // 15 | 30 | 60
  "aspect_ratio": "16:9",                   // 16:9 | 9:16 | 1:1
  "style_preset": "cinematic" }
```
Returns the project (`status: "draft"`, no scenes yet).

### `GET /api/projects/{id}`
Project **with** `scenes[]` and `style_bible`.

### `DELETE /api/projects/{id}` → 204
Cascades to scenes, jobs, assets.

### `POST /api/projects/{id}/storyboard` → 202
Kicks off the style-bible + storyboard generation task. Returns a `Job`. On
success the project advances to `storyboarded` and gains scenes. Calling it again
regenerates the storyboard.

### `GET /api/projects/{id}/cost?tier=premium|draft`
Cost estimate for the whole project at the given render tier.
```jsonc
{ "step": "full_project", "currency": "USD", "total": 4.71,
  "line_items": [{ "label": "Keyframes", "detail": "best-of-N", "amount": 0.45 }, ...] }
```

---

## Scenes (storyboard review)

All under `/api/projects/{project_id}/scenes`. These only edit stored data.

### `GET …/scenes`
Ordered scene list.

### `GET …/scenes/{scene_id}`
A single scene (includes `clip_asset_id`, `native_audio_asset_id`, `quality`, …).

### `PATCH …/scenes/{scene_id}`
Partial update; any subset of:
`duration_seconds, shot_description, camera_movement, image_prompt, video_prompt,
narration_text, audio_mode, dialogue_text, model_override`.
- Unknown `model_override` → **400**.
- Setting `audio_mode: "dialogue"` (without an override) auto-suggests a lip-sync
  model.

### `POST …/scenes` → 201
`{ "after_scene_number": 2 }` (or `null`/omit to append). Inserts a blank scene and
re-numbers contiguously.

### `DELETE …/scenes/{scene_id}` → 204
Removes the scene and re-numbers contiguously.

### `POST …/scenes/reorder`
`{ "scene_ids": ["id3", "id1", "id2"] }` — must be exactly the project's scene ids
(else **400**). Returns the re-numbered list.

### `POST …/scenes/revise` → 202
`{ "instruction": "make scene 3 moodier" }` (≥2 chars). Kicks off a Claude revision
task that patches the whole storyboard. Returns a `Job`.

---

## Keyframes & reference images (Phase 2)

### `POST /api/projects/{id}/keyframes` → 202
Generates master reference images (once) + best-of-N keyframes for **all** scenes.
Returns a `Job`. On success the project advances to `keyframes`.
**400** if the storyboard has no scenes.

### `POST /api/projects/{id}/scenes/{scene_id}/keyframes` → 202
Regenerate the best-of-N keyframes for a **single** scene (fresh variants).
Returns a `Job`.

### `GET /api/projects/{id}/references`
The master reference images (`kind: "reference"`), `meta.role` ∈
`character | environment | colorkey | extra`.

### `GET /api/projects/{id}/scenes/{scene_id}/keyframes`
The scene's keyframe variants, ordered by `meta.variant_index`. Each asset's
`meta` carries `{ variant_index, seed, score, reason, is_winner, auto_winner }`.

### `POST /api/projects/{id}/scenes/{scene_id}/keyframe/select`
`{ "asset_id": "..." }` — user override of the auto-ranked winner. Flips
`is_winner` across the scene's variants and sets `scene.keyframe_asset_id`.
Returns the updated `Scene`. **400** if the asset isn't a keyframe for that scene.

---

## Video & quality gate (Phase 3)

### `POST /api/projects/{id}/video?tier=draft|premium` → 202
Animate every scene's winning keyframe into a clip, demux native audio, and run
the quality gate. `tier` selects budget vs premium models (default `draft`).
Returns a `Job`. On success the project advances to `clips`.
**400** if no scene has a winning keyframe yet.

### `POST /api/projects/{id}/scenes/{scene_id}/video?tier=draft|premium` → 202
Regenerate a **single** scene's clip (the "one-click regenerate" for flagged
clips). Returns a `Job`. **400** if the scene has no winning keyframe.

### `GET /api/projects/{id}/scenes/{scene_id}/frames`
The quality-gate frames (`kind: "frame"`, ordered by `meta.frame_index`).

After a clip job, each scene carries:
- `clip_asset_id` → an `video/mp4` asset,
- `native_audio_asset_id` → an `audio/mp4` asset (`meta.muted` if garble-flagged),
- `quality` → `{ flagged, reasons[], identity_drift, native_audio_muted }`,
- `status` → `done` or `flagged` (or `failed`, isolated).

---

## Assets

Generated media live in MinIO; the API proxies them so the browser needs no
credentials.

### `GET /api/assets/{id}`
Asset metadata:
```jsonc
{ "id": "...", "scene_id": "...", "kind": "keyframe",
  "content_type": "image/png", "meta": { ... },
  "url": "/api/assets/{id}/content" }
```

### `GET /api/assets/{id}/content`
The raw bytes with the right `Content-Type` (cacheable). Use directly in
`<img src>` (prefix with the API host).

---

## Jobs

### `GET /api/jobs/{id}`
```jsonc
{ "id": "...", "type": "storyboard", "status": "success",
  "progress": 1.0, "result": { "scene_count": 6 }, "error": null }
```

### `GET /api/jobs/project/{project_id}`
All jobs for a project (newest first).

### `GET /api/jobs/{id}/stream`  (Server-Sent Events)
Emits `event: status` frames (~1/s) until the job is terminal, then closes.
```
event: status
data: {"id":"…","status":"running","progress":0.4, ...}
```

---

## Status enums

- **Project:** `draft → styled → storyboarded → keyframes → clips → audio →
  edited → draft_rendered → rendered`
- **Scene:** `pending → queued → generating → done` (+ `failed`, `flagged`)
- **Job:** `queued → running → success | failed`
