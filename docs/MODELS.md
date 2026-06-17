# Model routing, pricing & prompt translation

Everything model-related is config in `backend/app/models_config.py` (generation
models) and `backend/app/llm_config.py` (LLMs). The pipeline references models only
by their **internal id**, so swapping providers/models is a config change, never a
refactor. The same tables feed the cost estimator and the UI pickers.

## LLM routing (`llm_config.py`)

The text/vision LLM is **provider-agnostic and selectable per project** — pick it on
the New Project form ("Writer model"); it handles the storyboard, conversational
revisions, keyframe ranking, the quality gate, and the editor EDL for that project.

| Internal id | Label | Provider | Model id | Vision |
| --- | --- | --- | --- | --- |
| `gpt-5.4-nano` | GPT-5.4 nano | openai | `gpt-5.4-nano` | ✓ |
| `claude-haiku-4-6` | Claude Haiku 4.6 | anthropic | `claude-haiku-4-6` | ✓ |

- Default is `DEFAULT_LLM` (env, defaults to `gpt-5.4-nano`); a project stores its
  choice in `projects.llm_model`.
- `llm.py` dispatches by provider: OpenAI uses Chat Completions JSON mode; Anthropic
  uses the Messages API. Image parts are converted between the two formats
  automatically, so vision calls work the same on either.
- Add a model by adding an `LLMRoute` — no pipeline changes needed.
- Going live needs the matching key(s): `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY`.

## Generation routing table (SOTA as of June 2026)

| Internal id | Label | Modality | Tier | provider id (fal slug / google id) | Price | Refs | Native audio | Max clip |
| ----------- | ----- | -------- | ---- | ---------------------------------- | ----- | ---- | ------------ | -------- |
| `flux2-dev` | FLUX.2 [dev] | text→image | draft | `fal-ai/flux-2` | $0.025/img | 10 | – | – |
| `seedream-5` | Seedream 5.0 Lite | text→image | premium | `fal-ai/bytedance/seedream/v5/lite/text-to-image` | $0.035/img | 8 | – | – |
| `kling-3-pro` | Kling 3.0 Pro | image→video | premium | `fal-ai/kling-video/v3/pro/image-to-video` | $0.11/s | 1 | ✓ | 10s |
| `kling-25-turbo` | Kling 2.5 Turbo Pro | image→video | draft | `fal-ai/kling-video/v2.5-turbo/pro/image-to-video` | $0.07/s | 1 | ✓ | 10s |
| `seedance-2` | Seedance 2.0 | text→video | premium | `bytedance/seedance-2.0/text-to-video` | $0.3034/s | 9 | ✓ | 15s |
| `veo-31` | Veo 3.1 | text→video | premium | `veo-3.1-generate-preview` (google) | $0.15/s | 3 | ✓ | 8s |
| `veo-31-lite` | Veo 3.1 Lite | text→video | draft | `veo-3.1-lite-generate-preview` (google) | $0.05/s | – | ✓ | 8s |

ElevenLabs narration TTS: **$0.30 / 1k characters** (`TTS_PRICE_PER_1K_CHARS`).

Kling routes also carry `max_prompt_chars=2500` and `allowed_durations=(5, 10)` — the
fal provider truncates the prompt and snaps the clip length to satisfy the API.

> **Photo-to-video by default, text-to-video opt-in:** by default the pipeline animates
> the winning keyframe via the **image-to-video** (Kling) models. A scene can instead
> select **Veo** (text-to-video) as its `model_override`; that generates the clip from
> the prompt and **overrides the keyframe** (no image is sent). `seedance-2` remains
> parked (in the table, selectable as a t2v override but not a default). Lip-synced
> dialogue has been removed.
>
> **Sora is intentionally excluded** — deprecated April 2026, API shuts down Sept 2026.

### Roles

- **Keyframes:** `flux2-dev` (up to 10 reference images — how character/style
  consistency is enforced). One keyframe per scene by default (`KEYFRAME_VARIANTS=1`);
  premium fallback `seedream-5`.
- **Image→video (the default video path):** `kling-3-pro` (premium) / `kling-25-turbo`
  (draft) — animates the keyframe with native audio.
- **Text→video (opt-in per-scene override):** `veo-31` (premium) / `veo-31-lite`
  (draft) via Google direct; `seedance-2` (fal). Overrides the keyframe.

## How a scene's video model is resolved

Single source of truth: `resolve_video_model()`.

```
1. An explicit per-scene model_override → wins, in ANY modality (this is how a scene
   opts into Veo text-to-video, which overrides the keyframe).
2. else, Premium render → the storyboard's suggested_model...
3. else → the tier's image-to-video default.
4. For the auto/suggested paths (2, 3) ONLY: if the chosen model is not
   IMAGE_TO_VIDEO, fall back to the tier's Kling default — so anything not explicitly
   chosen animates the keyframe (photo-to-video).
```

Step 4 keeps text-to-video opt-in: a storyboard *suggestion* of Veo/Seedance still
animates the keyframe via Kling, but an explicit per-scene override into Veo is honored
and overrides the keyframe. Storyboards are narrated-only — no dialogue/lip-sync routing.

| Default i2v | Draft | Premium |
| ----------- | ----- | ------- |
| (all scenes) | `kling-25-turbo` | `kling-3-pro` |

## Cost estimator (`app/cost.py`)

| Function | Computes |
| -------- | -------- |
| `estimate_keyframes` | `KEYFRAME_VARIANTS` (=1) × keyframe price per scene |
| `estimate_video(tier)` | per scene: `resolve_video_model(tier)` price/s × duration |
| `estimate_audio` | per narrated scene: `chars/1000 × $0.30` |
| `estimate_full_project(tier)` | keyframes + video + narration |

Exposed at `GET /api/projects/{id}/cost?tier=premium|draft`. In mock mode actual
spend is $0, but the estimate still reflects a real run.

## Per-model prompt translator (`pipeline/prompts.py`)

Kling, Veo, and Seedance respond to very different prompt phrasing, so
`translate_video_prompt(model_id, scene, style_bible)` rewrites a neutral scene
description into each model's dialect — always embedding the locked **style block**
verbatim for consistency.

| `prompt_style` | Shape | Notes |
| -------------- | ----- | ----- |
| `kling` | Concise, motion-forward, camera inline | "… Camera: slow push in." + style. The active video dialect. |
| `veo` | Rich cinematic description | Text-to-video; active when a scene overrides to Veo (overrides the keyframe). |
| `seedance` | Reference-driven | Text-to-video; selectable as an override. |
| `flux` | Keyframe prompt + style block | Used for still keyframes |

## Adding or swapping a model

1. Add/edit a `ModelRoute` in `MODEL_ROUTES` (id, label, fal slug, modality, tier,
   `prompt_style`, pricing, capabilities).
2. If it's a new prompt dialect, add a branch in `translate_video_prompt`.
3. If it changes a default route, update `DEFAULT_*_BY_TIER`.

No pipeline, cost, or API code changes are required — they all read the table.
