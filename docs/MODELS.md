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

| Internal id | Label | Modality | Tier | fal slug | Price | Refs | Native audio | Lip-sync | Max clip |
| ----------- | ----- | -------- | ---- | -------- | ----- | ---- | ------------ | -------- | -------- |
| `flux2-dev` | FLUX.2 [dev] | text→image | draft | `fal-ai/flux-2/dev` | $0.025/img | 10 | – | – | – |
| `seedream-5` | Seedream 5.0 | text→image | premium | `fal-ai/seedream/v5` | $0.06/img | 8 | – | – | – |
| `kling-3-pro` | Kling 3.0 Pro | image→video | premium | `fal-ai/kling-video/v3/pro/image-to-video` | $0.11/s | 1 | ✓ | – | 10s |
| `kling-25-turbo` | Kling 2.5 Turbo Pro | image→video | draft | `fal-ai/kling-video/v2.5-turbo/pro/image-to-video` | $0.07/s | 1 | ✓ | – | 10s |
| `veo-31` | Veo 3.1 | text→video | premium | `fal-ai/veo/v3.1` | $0.15/s | 3 | ✓ | ✓ | 8s |
| `veo-31-lite` | Veo 3.1 Lite | text→video | draft | `fal-ai/veo/v3.1/lite` | $0.05/s | – | ✓ | ✓ | 8s |
| `seedance-2` | Seedance 2.0 | text→video | premium | `fal-ai/seedance/v2` | $0.12/s | 9 | ✓ | – | 15s |

ElevenLabs narration TTS: **$0.30 / 1k characters** (`TTS_PRICE_PER_1K_CHARS`).

> **Sora is intentionally excluded** — deprecated April 2026, API shuts down
> September 2026.

### Roles

- **Keyframes:** `flux2-dev` (up to 10 reference images — this is how character/style
  consistency is enforced). Premium fallback `seedream-5` for stronger composition.
- **Default image→video:** `kling-3-pro` (premium) / `kling-25-turbo` (draft).
- **Hero shots / dialogue:** `veo-31` (lip-synced speech, 4K) / `veo-31-lite` (draft).
- **Reference-driven consistency / multi-shot:** `seedance-2` (up to 9 refs, ≤15s).

## How a scene's video model is resolved

Single source of truth: `resolve_video_model()`.

```
1. An explicit per-scene model_override → always wins (draft and premium).
2. Premium render → the storyboard's suggested_model (a premium pick).
3. Draft render → the budget-tier default for the scene's audio_mode.
```

So draft passes are genuinely cheaper than finals, while a user's explicit
per-scene choice is always honored. Dialogue scenes route to a **lip-sync capable**
model (`veo-31` premium / `veo-31-lite` draft) — and toggling a scene to
`audio_mode="dialogue"` in the UI auto-updates its suggestion accordingly.

| Defaults | Draft | Premium |
| -------- | ----- | ------- |
| narrated | `kling-25-turbo` | `kling-3-pro` |
| dialogue | `veo-31-lite` | `veo-31` |

## Cost estimator (`app/cost.py`)

| Function | Computes |
| -------- | -------- |
| `estimate_keyframes` | 3 (best-of-N) × keyframe price per scene |
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
| `kling` | Concise, motion-forward, camera inline | "… Camera: slow push in." + style |
| `veo` | Rich cinematic description | Adds lip-sync line for dialogue; "ambient only" otherwise |
| `seedance` | Reference-driven | Emphasizes strict consistency to reference images |
| `flux` | Keyframe prompt + style block | Used for still keyframes |

## Adding or swapping a model

1. Add/edit a `ModelRoute` in `MODEL_ROUTES` (id, label, fal slug, modality, tier,
   `prompt_style`, pricing, capabilities).
2. If it's a new prompt dialect, add a branch in `translate_video_prompt`.
3. If it changes a default route, update `DEFAULT_*_BY_TIER`.

No pipeline, cost, or API code changes are required — they all read the table.
