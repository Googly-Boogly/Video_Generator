import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import type { AppConfig } from "../types";

export default function NewProject() {
  const nav = useNavigate();
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [idea, setIdea] = useState("");
  const [title, setTitle] = useState("");
  const [length, setLength] = useState(30);
  const [aspect, setAspect] = useState("16:9");
  const [preset, setPreset] = useState("cinematic");
  const [llm, setLlm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.config().then((c) => {
      setConfig(c);
      setLlm(c.default_llm);
    });
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const project = await api.createProject({
        idea,
        title: title || undefined,
        target_length: length,
        aspect_ratio: aspect,
        style_preset: preset,
        llm_model: llm || undefined,
      });
      // Kick off storyboard generation immediately; the review page polls it.
      await api.generateStoryboard(project.id);
      nav(`/projects/${project.id}`);
    } catch (e: any) {
      setErr(e.message ?? "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-2xl font-semibold mb-1">New project</h1>
      <p className="text-slate-400 text-sm mb-6">
        Describe your idea. We'll write a style bible and a shot-by-shot storyboard for you to review —
        nothing renders until you approve.
      </p>

      <form onSubmit={submit} className="card p-6 space-y-5">
        <div>
          <label className="label">Your idea</label>
          <textarea
            className="input min-h-28 resize-y"
            placeholder="A lonely lighthouse keeper befriends a glowing sea creature during a storm…"
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            required
          />
        </div>

        <div>
          <label className="label">Title (optional)</label>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Auto from idea" />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="label">Target length</label>
            <div className="flex gap-2">
              {(config?.target_lengths ?? [15, 30, 60]).map((l) => (
                <button
                  type="button"
                  key={l}
                  onClick={() => setLength(l)}
                  className={`flex-1 rounded-lg py-2 text-sm border ${
                    length === l ? "bg-accent text-ink border-accent" : "bg-panel2 border-edge"
                  }`}
                >
                  {l}s
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="label">Aspect ratio</label>
            <select className="input" value={aspect} onChange={(e) => setAspect(e.target.value)}>
              {(config?.aspect_ratios ?? ["16:9", "9:16", "1:1"]).map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="label">Style preset</label>
            <select className="input capitalize" value={preset} onChange={(e) => setPreset(e.target.value)}>
              {(config?.style_presets ?? ["cinematic"]).map((p) => (
                <option key={p} value={p} className="capitalize">{p}</option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className="label">Writer model (LLM)</label>
          <div className="flex gap-2">
            {(config?.llms ?? []).map((m) => (
              <button
                type="button"
                key={m.id}
                onClick={() => setLlm(m.id)}
                className={`flex-1 rounded-lg py-2 px-3 text-sm border text-left ${
                  llm === m.id ? "bg-accent text-ink border-accent" : "bg-panel2 border-edge"
                }`}
              >
                <div className="font-medium">{m.label}</div>
                <div className={`text-[11px] ${llm === m.id ? "text-ink/70" : "text-slate-500"}`}>
                  {m.provider}{m.vision ? " · vision" : ""}
                </div>
              </button>
            ))}
          </div>
          <p className="text-[11px] text-slate-500 mt-1">
            Handles the storyboard, conversational revisions, and the vision steps (keyframe ranking,
            quality gate, editor) for this project.
          </p>
        </div>

        {err && <p className="text-sm text-red-400">{err}</p>}

        <div className="flex items-center justify-between pt-2">
          <p className="text-xs text-slate-500">
            {config?.mock_generation
              ? "Mock mode: instant, $0 — placeholder assets."
              : "Live mode: generation will incur real cost."}
          </p>
          <button className="btn-primary" disabled={busy || idea.trim().length < 3}>
            {busy ? "Generating storyboard…" : "Generate storyboard →"}
          </button>
        </div>
      </form>
    </div>
  );
}
