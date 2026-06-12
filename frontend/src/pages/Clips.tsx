import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, assetUrl, pollJob } from "../lib/api";
import type { Asset, Project, Scene } from "../types";

type FrameMap = Record<string, Asset[]>;

export default function Clips() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [advancing, setAdvancing] = useState(false);
  const [project, setProject] = useState<Project | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [frames, setFrames] = useState<FrameMap>({});
  const [tier, setTier] = useState<"draft" | "premium">("draft");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const ordered = [...scenes].sort((a, b) => a.scene_number - b.scene_number);

  const loadAll = useCallback(async () => {
    if (!id) return null;
    const [p, sc] = await Promise.all([api.getProject(id), api.listScenes(id)]);
    setProject(p);
    setScenes(sc);
    const entries = await Promise.all(
      sc
        .filter((s) => s.clip_asset_id)
        .map(async (s) => [s.id, await api.listSceneFrames(id, s.id)] as const)
    );
    setFrames(Object.fromEntries(entries));
    return sc;
  }, [id]);

  const startPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      const sc = await loadAll();
      const active = sc?.some((s) => s.status === "queued" || s.status === "generating");
      if (!active && pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
        setBusy(false);
      }
    }, 1200);
  }, [loadAll]);

  useEffect(() => {
    (async () => {
      const sc = await loadAll();
      const working = sc?.some((s) => s.status === "queued" || s.status === "generating");
      if (working) {
        setBusy(true);
        startPolling();
        return;
      }
      if (id) {
        const jobs = await api.jobsForProject(id);
        if (jobs.find((j) => j.type === "video" && (j.status === "queued" || j.status === "running"))) {
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
      await api.generateVideo(id, tier);
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
      const job = await api.regenerateSceneVideo(id, sid, tier);
      await pollJob(job.id);
      await loadAll();
    } finally {
      setBusy(false);
    }
  }

  if (!project) return <p className="text-slate-500">Loading…</p>;

  const anyClips = ordered.some((s) => s.clip_asset_id);
  const flaggedCount = ordered.filter((s) => s.status === "flagged").length;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link to={`/projects/${project.id}/keyframes`} className="text-xs text-slate-500 hover:text-accent2">
            ← Back to keyframes
          </Link>
          <h1 className="text-2xl font-semibold mt-1">Clips · animate the winners</h1>
          <p className="text-slate-400 text-sm mt-1">
            Each winning keyframe is animated by its routed model. Native audio is demuxed per clip,
            and a vision quality gate flags artifacts for one-click regeneration.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-edge overflow-hidden text-sm">
            {(["draft", "premium"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTier(t)}
                className={`px-3 py-2 capitalize ${tier === t ? "bg-accent2 text-ink" : "bg-panel2"}`}
                title={t === "draft" ? "Budget-tier models" : "Premium-tier models"}
              >
                {t}
              </button>
            ))}
          </div>
          <button className="btn-ghost" onClick={generateAll} disabled={busy}>
            {busy ? "Generating…" : anyClips ? "↻ Regenerate all" : "Generate clips"}
          </button>
        </div>
      </div>

      {flaggedCount > 0 && (
        <div className="text-sm rounded-lg bg-orange-600/10 border border-orange-600/30 text-orange-300 px-3 py-2">
          Quality gate flagged {flaggedCount} clip{flaggedCount > 1 ? "s" : ""}. Review and regenerate below.
        </div>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {!anyClips && !busy ? (
        <div className="card p-10 text-center">
          <p className="text-slate-300">No clips yet.</p>
          <p className="text-slate-500 text-sm mt-1 mb-5">Animate the chosen keyframes into video.</p>
          <button className="btn-primary" onClick={generateAll} disabled={busy}>
            Generate clips
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {ordered.map((scene) => (
            <ClipCard
              key={scene.id}
              scene={scene}
              frames={frames[scene.id] ?? []}
              busy={busy}
              onRegenerate={() => regenerateScene(scene.id)}
            />
          ))}
        </div>
      )}

      <div className="card p-4 flex items-center justify-between sticky bottom-4">
        <p className="text-sm text-slate-400">
          {anyClips
            ? "Clips ready. Next: narration, music bed, and the native-audio mix."
            : "Generate clips for every scene to continue."}
        </p>
        <button
          className="btn-primary"
          disabled={!anyClips || advancing}
          onClick={async () => {
            if (!id) return;
            setAdvancing(true);
            try {
              await api.buildAudio(id);
              nav(`/projects/${id}/audio`);
            } catch (e: any) {
              setError(e.message ?? "Failed to start audio");
              setAdvancing(false);
            }
          }}
        >
          {advancing ? "Starting…" : "Build audio →"}
        </button>
      </div>
    </div>
  );
}

function ClipCard({
  scene,
  frames,
  busy,
  onRegenerate,
}: {
  scene: Scene;
  frames: Asset[];
  busy: boolean;
  onRegenerate: () => void;
}) {
  const working = scene.status === "queued" || scene.status === "generating";
  const failed = scene.status === "failed";
  const flagged = scene.status === "flagged";
  const reasons = (scene.quality?.reasons as string[] | undefined) ?? [];
  const muted = Boolean(scene.quality?.native_audio_muted);

  return (
    <div className={`card overflow-hidden ${flagged ? "border-orange-600/50" : ""}`}>
      <div className="flex items-center gap-3 px-4 py-3 bg-panel2/60 border-b border-edge">
        <span className="flex items-center justify-center w-6 h-6 rounded-full bg-accent text-ink text-xs font-bold">
          {scene.scene_number}
        </span>
        <span className="text-sm font-medium flex-1 truncate">{scene.shot_description}</span>
        {working && <span className="text-xs text-amber-300">generating…</span>}
        {failed && <span className="text-xs text-red-300">failed</span>}
        {flagged && <span className="text-xs text-orange-300">⚑ flagged</span>}
        {scene.status === "done" && <span className="text-xs text-emerald-300">✓ done</span>}
        <button className="btn-ghost px-3 py-1 text-xs" onClick={onRegenerate} disabled={busy}>
          ↻ Regenerate
        </button>
      </div>

      <div className="p-4 space-y-3">
        {scene.clip_asset_id ? (
          <video
            key={scene.clip_asset_id}
            src={assetUrl(`/api/assets/${scene.clip_asset_id}/content`)}
            controls
            loop
            className="w-full rounded-lg border border-edge bg-black aspect-video"
          />
        ) : (
          <div className="w-full aspect-video rounded-lg bg-panel2 border border-edge animate-pulse" />
        )}

        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          {scene.audio_mode === "dialogue" ? (
            <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">lip-synced dialogue</span>
          ) : (
            <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">narrated</span>
          )}
          <span className={`px-2 py-0.5 rounded border ${muted ? "border-red-700/50 text-red-300" : "border-edge"}`}>
            native audio {muted ? "muted (garbled)" : "✓"}
          </span>
          {failed && scene.error && <span className="text-red-300 truncate">{scene.error}</span>}
        </div>

        {flagged && reasons.length > 0 && (
          <div className="text-xs rounded-lg bg-orange-600/10 border border-orange-600/30 text-orange-300 px-3 py-2">
            {reasons.join("; ")}
          </div>
        )}

        {frames.length > 0 && (
          <div>
            <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">Quality-gate frames</p>
            <div className="grid grid-cols-4 gap-2">
              {frames.map((f) => (
                <img
                  key={f.id}
                  src={assetUrl(f.url)}
                  alt=""
                  className="w-full aspect-video object-cover rounded border border-edge bg-panel2"
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
