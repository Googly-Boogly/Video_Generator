import type {
  AppConfig,
  Asset,
  CostEstimate,
  Job,
  Project,
  Scene,
} from "../types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

/** Turn an asset's API path into an absolute URL usable in <img src>. */
export const assetUrl = (path: string) => `${BASE}${path}`;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  config: () => req<AppConfig>("/api/config"),

  listProjects: () => req<Project[]>("/api/projects"),
  getProject: (id: string) => req<Project>(`/api/projects/${id}`),
  createProject: (body: {
    idea: string;
    title?: string;
    target_length: number;
    aspect_ratio: string;
    style_preset: string;
    llm_model?: string;
  }) => req<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  deleteProject: (id: string) =>
    req<void>(`/api/projects/${id}`, { method: "DELETE" }),

  generateStoryboard: (id: string) =>
    req<Job>(`/api/projects/${id}/storyboard`, { method: "POST" }),
  projectCost: (id: string, tier = "premium") =>
    req<CostEstimate>(`/api/projects/${id}/cost?tier=${tier}`),

  listScenes: (id: string) => req<Scene[]>(`/api/projects/${id}/scenes`),
  updateScene: (pid: string, sid: string, body: Partial<Scene>) =>
    req<Scene>(`/api/projects/${pid}/scenes/${sid}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteScene: (pid: string, sid: string) =>
    req<void>(`/api/projects/${pid}/scenes/${sid}`, { method: "DELETE" }),
  reorderScenes: (pid: string, scene_ids: string[]) =>
    req<Scene[]>(`/api/projects/${pid}/scenes/reorder`, {
      method: "POST",
      body: JSON.stringify({ scene_ids }),
    }),
  addScene: (pid: string, after_scene_number: number | null) =>
    req<Scene>(`/api/projects/${pid}/scenes`, {
      method: "POST",
      body: JSON.stringify({ after_scene_number }),
    }),
  reviseStoryboard: (pid: string, instruction: string) =>
    req<Job>(`/api/projects/${pid}/scenes/revise`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),

  getJob: (id: string) => req<Job>(`/api/jobs/${id}`),
  jobsForProject: (pid: string) => req<Job[]>(`/api/jobs/project/${pid}`),

  // --- Phase 2: keyframes ---
  generateKeyframes: (pid: string) =>
    req<Job>(`/api/projects/${pid}/keyframes`, { method: "POST" }),
  regenerateSceneKeyframes: (pid: string, sid: string) =>
    req<Job>(`/api/projects/${pid}/scenes/${sid}/keyframes`, { method: "POST" }),
  listReferences: (pid: string) => req<Asset[]>(`/api/projects/${pid}/references`),
  listSceneKeyframes: (pid: string, sid: string) =>
    req<Asset[]>(`/api/projects/${pid}/scenes/${sid}/keyframes`),
  selectKeyframe: (pid: string, sid: string, asset_id: string) =>
    req<Scene>(`/api/projects/${pid}/scenes/${sid}/keyframe/select`, {
      method: "POST",
      body: JSON.stringify({ asset_id }),
    }),
  getScene: (pid: string, sid: string) =>
    req<Scene>(`/api/projects/${pid}/scenes/${sid}`),

  // --- Phase 3: video + quality gate ---
  generateVideo: (pid: string, tier: "draft" | "premium" = "draft") =>
    req<Job>(`/api/projects/${pid}/video?tier=${tier}`, { method: "POST" }),
  regenerateSceneVideo: (pid: string, sid: string, tier: "draft" | "premium" = "draft") =>
    req<Job>(`/api/projects/${pid}/scenes/${sid}/video?tier=${tier}`, { method: "POST" }),
  listSceneFrames: (pid: string, sid: string) =>
    req<Asset[]>(`/api/projects/${pid}/scenes/${sid}/frames`),

  // --- Phase 4: audio ---
  listVoices: () =>
    req<{ voices: { voice_id: string; name: string; labels: Record<string, string> }[]; default: string }>(
      "/api/voices"
    ),
  musicLibrary: () =>
    req<{ tracks: { id: string; name: string; bpm: number; style: string; seconds: number }[] }>(
      "/api/music/library"
    ),
  setVoice: (pid: string, voice_id: string) =>
    req<{ voice_id: string }>(`/api/projects/${pid}/voice`, {
      method: "POST",
      body: JSON.stringify({ voice_id }),
    }),
  getMusic: (pid: string) => req<Asset | null>(`/api/projects/${pid}/music`),
  pickLibraryMusic: (pid: string, track_id: string) =>
    req<Asset>(`/api/projects/${pid}/music/library`, {
      method: "POST",
      body: JSON.stringify({ track_id }),
    }),
  removeMusic: (pid: string) => req<void>(`/api/projects/${pid}/music`, { method: "DELETE" }),
  uploadMusic: async (pid: string, file: File): Promise<Asset> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/api/projects/${pid}/music`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.json();
  },
  buildAudio: (pid: string) => req<Job>(`/api/projects/${pid}/audio`, { method: "POST" }),
  regenerateNarration: (pid: string, sid: string) =>
    req<Job>(`/api/projects/${pid}/scenes/${sid}/narration`, { method: "POST" }),
  listNarration: (pid: string) => req<Asset[]>(`/api/projects/${pid}/narration`),
  mixPlan: (pid: string) =>
    req<{ levels: Record<string, number>; scenes: { scene_number: number; audio_mode: string; mix: Record<string, unknown> }[] }>(
      `/api/projects/${pid}/mix-plan`
    ),

  // --- Phase 5: editor + render ---
  buildEdl: (pid: string) => req<Job>(`/api/projects/${pid}/edl`, { method: "POST" }),
  getEdl: (pid: string) => req<Edl>(`/api/projects/${pid}/edl`),
  render: (pid: string, final: boolean) =>
    req<Job>(`/api/projects/${pid}/render?final=${final}`, { method: "POST" }),
  listRenders: (pid: string) => req<Asset[]>(`/api/projects/${pid}/renders`),

  // --- Phase 6: cost dashboard ---
  costDashboard: (pid: string) => req<CostDashboard>(`/api/projects/${pid}/costs`),
};

export interface CostDashboard {
  currency: string;
  mock: boolean;
  estimated: { total: number; line_items: { label: string; detail: string; amount: number }[] };
  actual: {
    total: number;
    by_step: Record<string, number>;
    entries: { step: string; label: string; detail: string; amount: number; mock: boolean }[];
  };
}

export interface EdlCut {
  scene_number: number;
  in: number;
  out: number;
  trim_head: number;
  trim_tail: number;
  transition: string;
  caption: string;
  on_beat: number | null;
  mix: {
    narration_db: number | null;
    music_db: number;
    native_db: number | null;
    duck_music_under_narration: boolean;
    pause_narration_for_dialogue: boolean;
  };
}
export interface Edl {
  total_duration: number;
  cuts: EdlCut[];
  beat_grid: { bpm: number | null; beats: number } | null;
  levels: Record<string, number>;
  engine: string;
}

/** Poll a job until it reaches a terminal state. */
export async function pollJob(
  jobId: string,
  onTick?: (job: Job) => void,
  intervalMs = 800
): Promise<Job> {
  for (;;) {
    const job = await api.getJob(jobId);
    onTick?.(job);
    if (job.status === "success" || job.status === "failed") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
