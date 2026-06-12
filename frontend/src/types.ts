export type AudioMode = "narrated" | "dialogue";

export interface Scene {
  id: string;
  project_id: string;
  scene_number: number;
  duration_seconds: number;
  shot_description: string;
  camera_movement: string;
  image_prompt: string;
  video_prompt: string;
  narration_text: string;
  audio_mode: AudioMode;
  dialogue_text: string | null;
  suggested_model: string | null;
  model_override: string | null;
  status: string;
  keyframe_asset_id: string | null;
  clip_asset_id: string | null;
  quality: Record<string, unknown> | null;
  error: string | null;
}

export interface Project {
  id: string;
  title: string;
  idea: string;
  target_length: number;
  aspect_ratio: string;
  style_preset: string;
  status: string;
  voice_id: string | null;
  style_bible: StyleBible | null;
  created_at: string;
  updated_at: string;
  scenes?: Scene[];
}

export interface StyleBible {
  style_summary?: string;
  palette?: string[];
  lighting?: string;
  lens?: string;
  mood?: string;
  character_sheet?: { name: string; physical_descriptors: string }[];
  reference_image_prompts?: string[];
}

export interface Asset {
  id: string;
  project_id: string;
  scene_id: string | null;
  kind: string;
  content_type: string;
  meta: {
    role?: string;
    prompt?: string;
    variant_index?: number;
    seed?: number;
    score?: number | null;
    reason?: string | null;
    is_winner?: boolean;
    auto_winner?: boolean;
    [k: string]: unknown;
  } | null;
  url: string; // path on the API host, e.g. /api/assets/{id}/content
}

export interface Job {
  id: string;
  project_id: string;
  scene_id: string | null;
  type: string;
  status: "queued" | "running" | "success" | "failed";
  progress: number;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface ModelInfo {
  id: string;
  label: string;
  modality: string;
  tier: string;
  price_per_image: number;
  price_per_second: number;
  native_audio: boolean;
  lip_sync: boolean;
  max_reference_images: number;
  notes: string;
}

export interface AppConfig {
  mock_generation: boolean;
  style_presets: string[];
  target_lengths: number[];
  aspect_ratios: string[];
  models: ModelInfo[];
  video_models: string[];
}

export interface CostEstimate {
  step: string;
  total: number;
  currency: string;
  line_items: { label: string; detail: string; amount: number }[];
}
