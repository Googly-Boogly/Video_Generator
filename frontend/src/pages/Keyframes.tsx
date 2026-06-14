import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, assetUrl, pollJob } from "../lib/api";
import type { Asset, Project, Scene } from "../types";

type KFMap = Record<string, Asset[]>; // scene_id -> keyframe variants

export default function Keyframes() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [animating, setAnimating] = useState(false);
  const [project, setProject] = useState<Project | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [refs, setRefs] = useState<Asset[]>([]);
  const [kf, setKf] = useState<KFMap>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const ordered = [...scenes].sort((a, b) => a.scene_number - b.scene_number);

  const loadAll = useCallback(async () => {
    if (!id) return null;
    const [p, sc, rf] = await Promise.all([
      api.getProject(id),
      api.listScenes(id),
      api.listReferences(id),
    ]);
    setProject(p);
    setScenes(sc);
    setRefs(rf);
    const entries = await Promise.all(
      sc.map(async (s) => [s.id, await api.listSceneKeyframes(id, s.id)] as const)
    );
    setKf(Object.fromEntries(entries));
    return sc;
  }, [id]);

  const startPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      await loadAll();
      // The keyframes task commits atomically at the very end (minutes later), so
      // scene statuses don't reflect "generating" mid-run. Track the JOB instead:
      // keep polling while a keyframes job is queued/running, stop once it settles.
      let active = false;
      if (id) {
        const jobs = await api.jobsForProject(id);
        active = jobs.some(
          (j) => j.type === "keyframes" && (j.status === "queued" || j.status === "running")
        );
      }
      if (!active && pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
        setBusy(false);
        await loadAll(); // final refresh so the finished keyframes render
      }
    }, 2000);
  }, [id, loadAll]);

  useEffect(() => {
    (async () => {
      const sc = await loadAll();
      // Resume polling if a keyframes job is mid-flight, or scenes are working.
      const working = sc?.some((s) => s.status === "queued" || s.status === "generating");
      if (working) {
        setBusy(true);
        startPolling();
        return;
      }
      if (id) {
        const jobs = await api.jobsForProject(id);
        const running = jobs.find(
          (j) => j.type === "keyframes" && (j.status === "queued" || j.status === "running")
        );
        if (running) {
          setBusy(true);
          startPolling();
        }
      }
    })();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [id, loadAll, startPolling]);

  async function generateAll() {
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      await api.generateKeyframes(id);
      startPolling();
    } catch (e: any) {
      setError(e.message ?? "Failed to start");
      setBusy(false);
    }
  }

  async function regenerateScene(sid: string) {
    if (!id) return;
    setBusy(true);
    try {
      const job = await api.regenerateSceneKeyframes(id, sid);
      await pollJob(job.id);
      await loadAll();
    } finally {
      setBusy(false);
    }
  }

  async function pickWinner(sid: string, assetId: string) {
    if (!id) return;
    setKf((prev) => ({
      ...prev,
      [sid]: (prev[sid] ?? []).map((a) => ({
        ...a,
        meta: { ...a.meta, is_winner: a.id === assetId },
      })),
    }));
    await api.selectKeyframe(id, sid, assetId);
    setScenes((prev) => prev.map((s) => (s.id === sid ? { ...s, keyframe_asset_id: assetId } : s)));
  }

  if (!project) return <p className="text-slate-500">Loading…</p>;

  const hasAny = Object.values(kf).some((v) => v.length > 0);
  const allDone = ordered.length > 0 && ordered.every((s) => s.status === "done");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link to={`/projects/${project.id}`} className="text-xs text-slate-500 hover:text-accent2">
            ← Back to storyboard
          </Link>
          <h1 className="text-2xl font-semibold mt-1">Keyframes · best of {3}</h1>
          <p className="text-slate-400 text-sm mt-1">
            Three FLUX.2 variants per scene with the style references attached. The auto-ranked
            winner is highlighted — click any variant to choose a different one.
          </p>
        </div>
        <button className="btn-ghost" onClick={generateAll} disabled={busy}>
          {busy ? "Generating…" : hasAny ? "↻ Regenerate all" : "Generate keyframes"}
        </button>
      </div>

      {/* Reference images */}
      {refs.length > 0 && (
        <div className="card p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400 mb-3">
            Master reference images
          </h2>
          <div className="flex flex-wrap gap-4">
            {refs.map((r) => (
              <figure key={r.id} className="w-40">
                <img
                  src={assetUrl(r.url)}
                  alt={r.meta?.role}
                  className="w-40 aspect-video object-cover rounded-lg border border-edge bg-panel2"
                />
                <figcaption className="text-[11px] text-slate-400 mt-1 capitalize">
                  {r.meta?.role}
                </figcaption>
              </figure>
            ))}
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {!hasAny && !busy ? (
        <div className="card p-10 text-center">
          <p className="text-slate-300">No keyframes yet.</p>
          <p className="text-slate-500 text-sm mt-1 mb-5">
            Generate 3 variants per scene and pick the best of each.
          </p>
          <button className="btn-primary" onClick={generateAll} disabled={busy}>
            Generate keyframes
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {ordered.map((scene) => (
            <SceneRow
              key={scene.id}
              scene={scene}
              variants={kf[scene.id] ?? []}
              busy={busy}
              onPick={(aid) => pickWinner(scene.id, aid)}
              onRegenerate={() => regenerateScene(scene.id)}
            />
          ))}
        </div>
      )}

      <div className="card p-4 flex items-center justify-between sticky bottom-4">
        <p className="text-sm text-slate-400">
          {allDone
            ? "All scenes have a chosen keyframe. Next: animate the winners."
            : "Pick a winning keyframe for every scene to continue."}
        </p>
        <button
          className="btn-primary"
          disabled={!allDone || animating}
          title={allDone ? "Generate clips from the chosen keyframes" : "Pick a keyframe for every scene first"}
          onClick={async () => {
            if (!id) return;
            setAnimating(true);
            try {
              await api.generateVideo(id, "draft");
              nav(`/projects/${id}/clips`);
            } catch (e: any) {
              setError(e.message ?? "Failed to start clips");
              setAnimating(false);
            }
          }}
        >
          {animating ? "Starting…" : "Animate winners →"}
        </button>
      </div>
    </div>
  );
}

function SceneRow({
  scene,
  variants,
  busy,
  onPick,
  onRegenerate,
}: {
  scene: Scene;
  variants: Asset[];
  busy: boolean;
  onPick: (assetId: string) => void;
  onRegenerate: () => void;
}) {
  const working = scene.status === "queued" || scene.status === "generating";
  const failed = scene.status === "failed";
  const sorted = [...variants].sort(
    (a, b) => (a.meta?.variant_index ?? 0) - (b.meta?.variant_index ?? 0)
  );

  return (
    <div className="card p-4">
      <div className="flex items-center gap-3 mb-3">
        <span className="flex items-center justify-center w-6 h-6 rounded-full bg-accent text-ink text-xs font-bold">
          {scene.scene_number}
        </span>
        <span className="text-sm font-medium flex-1 truncate">{scene.shot_description}</span>
        {working && <span className="text-xs text-amber-300">generating…</span>}
        {failed && <span className="text-xs text-red-300">failed — {scene.error}</span>}
        <button className="btn-ghost px-3 py-1 text-xs" onClick={onRegenerate} disabled={busy}>
          ↻ Regenerate
        </button>
      </div>

      {sorted.length === 0 ? (
        <div className="grid grid-cols-3 gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="aspect-video rounded-lg bg-panel2 border border-edge animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          {sorted.map((v) => {
            const winner = v.meta?.is_winner;
            const auto = v.meta?.auto_winner;
            const score = typeof v.meta?.score === "number" ? v.meta.score : null;
            return (
              <button
                key={v.id}
                onClick={() => onPick(v.id)}
                className={`group relative rounded-lg overflow-hidden border-2 transition text-left ${
                  winner ? "border-accent ring-2 ring-accent/40" : "border-edge hover:border-accent2"
                }`}
              >
                <img src={assetUrl(v.url)} alt="" className="w-full aspect-video object-cover bg-panel2" />
                <div className="absolute top-1 left-1 flex gap-1">
                  {winner && (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-accent text-ink">
                      ✓ CHOSEN
                    </span>
                  )}
                  {auto && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-panel/80 text-slate-300 border border-edge">
                      auto-rank
                    </span>
                  )}
                </div>
                {score != null && (
                  <span className="absolute top-1 right-1 text-[10px] px-1.5 py-0.5 rounded bg-panel/80 text-slate-200 border border-edge">
                    {score.toFixed(2)}
                  </span>
                )}
                {v.meta?.reason && (
                  <span className="block text-[11px] text-slate-400 px-2 py-1 truncate" title={String(v.meta.reason)}>
                    {String(v.meta.reason)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
