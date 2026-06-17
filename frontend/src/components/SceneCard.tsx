import { useState } from "react";
import type { ModelInfo, Scene } from "../types";

interface Props {
  scene: Scene;
  index: number;
  total: number;
  videoModels: ModelInfo[];
  onChange: (patch: Partial<Scene>) => void;
  onDelete: () => void;
  onMove: (dir: -1 | 1) => void;
  onRegenerate: () => void;
  busy?: boolean;
}

function Field({
  label,
  value,
  onCommit,
  textarea,
  rows = 2,
  placeholder,
}: {
  label: string;
  value: string;
  onCommit: (v: string) => void;
  textarea?: boolean;
  rows?: number;
  placeholder?: string;
}) {
  const [local, setLocal] = useState(value);
  // Keep local in sync if the prop changes externally (e.g. after revise).
  if (value !== local && document.activeElement?.getAttribute("data-f") !== label) {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    setTimeout(() => setLocal(value), 0);
  }
  const common = {
    "data-f": label,
    className: "input",
    value: local,
    placeholder,
    onChange: (e: any) => setLocal(e.target.value),
    onBlur: () => local !== value && onCommit(local),
  };
  return (
    <div>
      <label className="label">{label}</label>
      {textarea ? <textarea {...common} rows={rows} className="input resize-y" /> : <input {...common} />}
    </div>
  );
}

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-slate-600/30 text-slate-300 border-slate-600/40",
  queued: "bg-sky-600/20 text-sky-300 border-sky-600/40",
  generating: "bg-amber-600/20 text-amber-300 border-amber-600/40",
  done: "bg-emerald-600/20 text-emerald-300 border-emerald-600/40",
  failed: "bg-red-700/30 text-red-300 border-red-700/50",
  flagged: "bg-orange-600/20 text-orange-300 border-orange-600/40",
};

export default function SceneCard(props: Props) {
  const { scene, index, total, videoModels, onChange, onDelete, onMove, onRegenerate, busy } = props;
  const [open, setOpen] = useState(true);
  const isDialogue = scene.audio_mode === "dialogue";
  const effectiveModel = scene.model_override ?? scene.suggested_model ?? "";

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 bg-panel2/60 border-b border-edge">
        <span className="flex items-center justify-center w-7 h-7 rounded-full bg-accent text-ink text-sm font-bold">
          {scene.scene_number}
        </span>
        <button onClick={() => setOpen((o) => !o)} className="flex-1 text-left">
          <span className="font-medium">{scene.shot_description || "Untitled shot"}</span>
        </button>
        <span className={`text-[11px] px-2 py-0.5 rounded border ${STATUS_COLORS[scene.status] ?? ""}`}>
          {scene.status}
        </span>
        <div className="flex items-center gap-1">
          <button className="btn-ghost px-2 py-1" disabled={index === 0} onClick={() => onMove(-1)} title="Move up">
            ↑
          </button>
          <button className="btn-ghost px-2 py-1" disabled={index === total - 1} onClick={() => onMove(1)} title="Move down">
            ↓
          </button>
          <button className="btn-danger px-2 py-1" onClick={onDelete} title="Delete scene">
            ✕
          </button>
        </div>
      </div>

      {open && (
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Shot description" value={scene.shot_description} onCommit={(v) => onChange({ shot_description: v })} textarea />
            <Field label="Camera movement" value={scene.camera_movement} onCommit={(v) => onChange({ camera_movement: v })} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Image prompt (keyframe)" value={scene.image_prompt} onCommit={(v) => onChange({ image_prompt: v })} textarea rows={3} />
            <Field label="Video prompt (motion)" value={scene.video_prompt} onCommit={(v) => onChange({ video_prompt: v })} textarea rows={3} />
          </div>

          {/* Audio + model controls */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-start">
            <div>
              <label className="label">Audio mode</label>
              <div className="flex rounded-lg border border-edge overflow-hidden">
                <button
                  className={`flex-1 py-2 text-sm ${!isDialogue ? "bg-accent2 text-ink" : "bg-panel2"}`}
                  onClick={() => onChange({ audio_mode: "narrated" })}
                >
                  Narrated
                </button>
                <button
                  className={`flex-1 py-2 text-sm ${isDialogue ? "bg-accent2 text-ink" : "bg-panel2"}`}
                  onClick={() => onChange({ audio_mode: "dialogue" })}
                >
                  Dialogue
                </button>
              </div>
              <p className="text-[11px] text-slate-500 mt-1">
                {isDialogue ? "On-screen speech, lip-synced. Narration pauses." : "Narration carries the words (ElevenLabs)."}
              </p>
            </div>

            <div>
              <label className="label">Model</label>
              <select
                className="input"
                value={effectiveModel}
                onChange={(e) => onChange({ model_override: e.target.value })}
              >
                {videoModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label} {m.tier === "premium" ? "★" : ""}{" "}
                    {m.modality === "text_to_video" ? "(text→video, overrides keyframe)" : "(photo→video)"}
                  </option>
                ))}
              </select>
              {videoModels.find((m) => m.id === effectiveModel)?.modality === "text_to_video" && (
                <p className="text-[11px] text-slate-500 mt-1">
                  Text-to-video: generated from the prompt, the keyframe is overridden.
                </p>
              )}
              {scene.model_override && (
                <button
                  className="text-[11px] text-slate-500 hover:text-accent2 mt-1"
                  onClick={() => onChange({ model_override: null as any })}
                >
                  reset to suggested ({scene.suggested_model})
                </button>
              )}
            </div>

            <div>
              <label className="label">Duration (s)</label>
              <input
                type="number"
                min={2}
                max={15}
                step={0.5}
                className="input"
                defaultValue={scene.duration_seconds}
                onBlur={(e) => {
                  const v = parseFloat(e.target.value);
                  if (!Number.isNaN(v) && v !== scene.duration_seconds) onChange({ duration_seconds: v });
                }}
              />
            </div>
          </div>

          {/* Narration / dialogue text */}
          {isDialogue ? (
            <Field
              label="Dialogue text (spoken on screen)"
              value={scene.dialogue_text ?? ""}
              onCommit={(v) => onChange({ dialogue_text: v })}
              textarea
            />
          ) : (
            <Field label="Narration text" value={scene.narration_text} onCommit={(v) => onChange({ narration_text: v })} textarea />
          )}

          {scene.quality?.flagged ? (
            <div className="text-xs rounded-lg bg-orange-600/10 border border-orange-600/30 text-orange-300 px-3 py-2">
              Quality gate flagged this clip: {(scene.quality.reasons as string[] | undefined)?.join("; ")}
            </div>
          ) : null}

          <div className="flex justify-end">
            <button className="btn-ghost" onClick={onRegenerate} disabled={busy}>
              ↻ Regenerate this scene's prompts
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
