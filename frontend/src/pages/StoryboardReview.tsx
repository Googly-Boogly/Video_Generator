import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { api, pollJob } from "../lib/api";
import type { AppConfig, CostEstimate, ModelInfo, Project, Scene } from "../types";
import SceneCard from "../components/SceneCard";

export default function StoryboardReview() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [approving, setApproving] = useState(false);
  const [project, setProject] = useState<Project | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [cost, setCost] = useState<CostEstimate | null>(null);
  const [generating, setGenerating] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [revising, setRevising] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const videoModels: ModelInfo[] = useMemo(
    () =>
      (config?.models ?? []).filter(
        (m) => m.modality === "image_to_video" || m.modality === "text_to_video"
      ),
    [config]
  );

  const refreshCost = useCallback(async (pid: string) => {
    try {
      setCost(await api.projectCost(pid, "premium"));
    } catch {
      /* ignore */
    }
  }, []);

  const load = useCallback(async () => {
    if (!id) return;
    const [p, sc] = await Promise.all([api.getProject(id), api.listScenes(id)]);
    setProject(p);
    setScenes(sc);
    return { p, sc };
  }, [id]);

  // Initial load + poll while the storyboard is still being generated.
  useEffect(() => {
    if (!id) return;
    api.config().then(setConfig);
    let active = true;
    (async () => {
      const res = await load();
      if (!active || !res) return;
      const empty = res.sc.length === 0 && res.p.status !== "storyboarded";
      if (empty) {
        setGenerating(true);
        pollRef.current = window.setInterval(async () => {
          const r = await load();
          if (r && (r.sc.length > 0 || r.p.status === "storyboarded")) {
            setGenerating(false);
            if (pollRef.current) clearInterval(pollRef.current);
            refreshCost(id);
          }
        }, 1000);
      } else {
        refreshCost(id);
      }
    })();
    return () => {
      active = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [id, load, refreshCost]);

  async function patchScene(scene: Scene, patch: Partial<Scene>) {
    setScenes((prev) => prev.map((s) => (s.id === scene.id ? { ...s, ...patch } : s)));
    const updated = await api.updateScene(scene.project_id, scene.id, patch);
    setScenes((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    if (id) refreshCost(id);
  }

  async function deleteScene(scene: Scene) {
    await api.deleteScene(scene.project_id, scene.id);
    setScenes(await api.listScenes(scene.project_id));
    if (id) refreshCost(id);
  }

  async function moveScene(scene: Scene, dir: -1 | 1) {
    const ordered = [...scenes].sort((a, b) => a.scene_number - b.scene_number);
    const i = ordered.findIndex((s) => s.id === scene.id);
    const j = i + dir;
    if (j < 0 || j >= ordered.length) return;
    [ordered[i], ordered[j]] = [ordered[j], ordered[i]];
    const ids = ordered.map((s) => s.id);
    setScenes(ordered.map((s, idx) => ({ ...s, scene_number: idx + 1 })));
    setScenes(await api.reorderScenes(scene.project_id, ids));
  }

  async function addScene() {
    if (!id) return;
    const last = scenes.length ? Math.max(...scenes.map((s) => s.scene_number)) : 0;
    await api.addScene(id, last);
    setScenes(await api.listScenes(id));
    refreshCost(id);
  }

  async function regenerateScene(scene: Scene) {
    await revise(`Rewrite the prompts for scene ${scene.scene_number} for stronger visual impact.`);
  }

  async function revise(text: string) {
    if (!id || !text.trim()) return;
    setRevising(true);
    setError(null);
    try {
      const job = await api.reviseStoryboard(id, text);
      const final = await pollJob(job.id);
      if (final.status === "failed") throw new Error(final.error ?? "revision failed");
      setScenes(await api.listScenes(id));
      setInstruction("");
      refreshCost(id);
    } catch (e: any) {
      setError(e.message ?? "Revision failed");
    } finally {
      setRevising(false);
    }
  }

  async function regenerateAll() {
    if (!id) return;
    setGenerating(true);
    const job = await api.generateStoryboard(id);
    await pollJob(job.id);
    await load();
    setGenerating(false);
    refreshCost(id);
  }

  const totalDuration = scenes.reduce((a, s) => a + s.duration_seconds, 0);
  const sb = project?.style_bible;
  const ordered = [...scenes].sort((a, b) => a.scene_number - b.scene_number);

  if (!project) return <p className="text-slate-500">Loading…</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link to="/" className="text-xs text-slate-500 hover:text-accent2">← All projects</Link>
          <h1 className="text-2xl font-semibold mt-1">{project.title}</h1>
          <p className="text-slate-400 text-sm mt-1 max-w-2xl">{project.idea}</p>
          <div className="flex gap-2 mt-2 text-xs text-slate-500">
            <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">{project.target_length}s target</span>
            <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">{project.aspect_ratio}</span>
            <span className="px-2 py-0.5 rounded bg-panel2 border border-edge capitalize">{project.style_preset}</span>
            <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">{ordered.length} scenes · {totalDuration.toFixed(0)}s</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {["keyframes", "clips", "audio", "edited", "draft_rendered", "rendered"].includes(
            project.status
          ) && (
            <Link to={`/projects/${project.id}/keyframes`} className="btn-ghost">
              Keyframes
            </Link>
          )}
          {["clips", "audio", "edited", "draft_rendered", "rendered"].includes(project.status) && (
            <Link to={`/projects/${project.id}/clips`} className="btn-ghost">
              Clips
            </Link>
          )}
          {["audio", "edited", "draft_rendered", "rendered"].includes(project.status) && (
            <Link to={`/projects/${project.id}/audio`} className="btn-ghost">
              Audio
            </Link>
          )}
          <button className="btn-ghost" onClick={regenerateAll} disabled={generating}>
            ↻ Regenerate storyboard
          </button>
        </div>
      </div>

      {/* Style bible */}
      {sb && (
        <div className="card p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400 mb-3">Style bible</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-slate-300">{sb.style_summary}</p>
              <p className="text-slate-500 mt-2"><span className="text-slate-400">Lighting:</span> {sb.lighting}</p>
              <p className="text-slate-500"><span className="text-slate-400">Lens:</span> {sb.lens}</p>
              {sb.mood && <p className="text-slate-500"><span className="text-slate-400">Mood:</span> {sb.mood}</p>}
            </div>
            <div>
              {sb.palette && (
                <div className="flex gap-1 mb-3">
                  {sb.palette.map((c) => (
                    <span key={c} className="w-7 h-7 rounded border border-edge" style={{ background: c }} title={c} />
                  ))}
                </div>
              )}
              {sb.character_sheet?.map((c) => (
                <p key={c.name} className="text-slate-500"><span className="text-accent">{c.name}:</span> {c.physical_descriptors}</p>
              ))}
            </div>
          </div>
        </div>
      )}

      {generating ? (
        <div className="card p-10 text-center">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin mb-3" />
          <p className="text-slate-300">Writing your storyboard…</p>
          <p className="text-slate-500 text-sm mt-1">Style bible → shot list. This is instant in mock mode.</p>
        </div>
      ) : (
        <>
          {/* Revision box */}
          <div className="card p-4">
            <label className="label">Revise conversationally</label>
            <div className="flex gap-2">
              <input
                className="input"
                placeholder='e.g. "make scene 3 moodier", "add a transition before the ending", "cut scene 2"'
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && revise(instruction)}
                disabled={revising}
              />
              <button className="btn-primary whitespace-nowrap" onClick={() => revise(instruction)} disabled={revising || !instruction.trim()}>
                {revising ? "Revising…" : "Apply"}
              </button>
            </div>
            {error && <p className="text-sm text-red-400 mt-2">{error}</p>}
          </div>

          {/* Scene cards */}
          <div className="space-y-4">
            {ordered.map((scene, i) => (
              <SceneCard
                key={scene.id}
                scene={scene}
                index={i}
                total={ordered.length}
                videoModels={videoModels}
                busy={revising}
                onChange={(patch) => patchScene(scene, patch)}
                onDelete={() => deleteScene(scene)}
                onMove={(dir) => moveScene(scene, dir)}
                onRegenerate={() => regenerateScene(scene)}
              />
            ))}
          </div>

          <button onClick={addScene} className="btn-ghost w-full border-dashed">
            + Add scene
          </button>

          {/* Cost + approval */}
          <div className="card p-4 flex items-center justify-between flex-wrap gap-4 sticky bottom-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Estimated cost to produce (premium)</p>
              <p className="text-2xl font-semibold text-accent">
                ${cost ? cost.total.toFixed(2) : "—"}
                <span className="text-sm text-slate-500 font-normal ml-2">
                  {config?.mock_generation ? "· $0 actual in mock mode" : ""}
                </span>
              </p>
              {cost && (
                <p className="text-xs text-slate-500 mt-1">
                  {cost.line_items.map((li) => `${li.label} $${li.amount.toFixed(2)}`).join("  ·  ")}
                </p>
              )}
            </div>
            <button
              className="btn-primary"
              disabled={approving || ordered.length === 0}
              onClick={async () => {
                if (!id) return;
                setApproving(true);
                try {
                  await api.generateKeyframes(id);
                  nav(`/projects/${id}/keyframes`);
                } catch (e: any) {
                  setError(e.message ?? "Failed to start keyframes");
                  setApproving(false);
                }
              }}
            >
              {approving ? "Starting…" : "Approve & generate keyframes →"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
