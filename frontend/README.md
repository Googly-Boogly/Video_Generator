# StoryForge — frontend

React + Vite + TypeScript + Tailwind. Talks only to the FastAPI backend
(`VITE_API_BASE`, default `http://localhost:8800`). Served at
**http://localhost:5273** in the Compose stack.

## Layout

```
src/
  main.tsx                 router setup
  App.tsx                  shell: header, MOCK/LIVE badge, nav
  index.css                Tailwind + component classes (.btn, .card, .input…)
  types.ts                 shared TS types (Project, Scene, Asset, Job, ModelInfo…)
  vite-env.d.ts            import.meta.env typing
  lib/api.ts               typed API client + pollJob() + assetUrl()
  pages/
    Home.tsx               project list
    NewProject.tsx         prompt intake; kicks off storyboard generation
    StoryboardReview.tsx   the review UI (style bible, scene cards, revision, cost)
    Keyframes.tsx          best-of-N keyframe selection (references + variants)
    Clips.tsx              video clips, quality flags, frames, regenerate
    Audio.tsx              voice, music bed + beat grid, narration, mix plan
  components/
    SceneCard.tsx          one editable scene (fields, model picker, audio toggle…)
```

## Develop

```bash
# Via compose (HMR, recommended): see ../README.md
# Standalone:
npm install
VITE_API_BASE=http://localhost:8800 npm run dev   # http://localhost:5173
npm run build                                      # type-check + production build
```

## Notes

- The MOCK/LIVE badge in the header reflects `GET /api/config`.
- Async actions poll the returned `Job` (`lib/api.ts#pollJob`); the review page
  also polls while a storyboard is still generating.
- Keep `types.ts` in sync with backend `schemas.py`.
- New API host port? Update `VITE_API_BASE` and the CORS allowlist in
  `backend/app/main.py`.
