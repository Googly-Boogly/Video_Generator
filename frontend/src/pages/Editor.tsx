import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, assetUrl, pollJob } from "../lib/api";
import type { Edl } from "../lib/api";
import type { Asset, Project } from "../types";
import PipelineNav from "../components/PipelineNav";

function db(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v} dB`;
}

export default function Editor() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [edl, setEdl] = useState<Edl | null>(null);
  const [renders, setRenders] = useState<Asset[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    const [p, rs] = await Promise.all([api.getProject(id), api.listRenders(id)]);
    setProject(p);
    setRenders(rs);
    try {
      setEdl(await api.getEdl(id));
    } catch {
      setEdl(null);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function run(label: string, p: Promise<{ id: string }>) {
    setBusy(label);
    setError(null);
    try {
      const job = await p;
      const final = await pollJob(job.id);
      if (final.status === "failed") throw new Error(final.error ?? "failed");
      await load();
    } catch (e: any) {
      setError(e.message ?? "Failed");
    } finally {
      setBusy(null);
    }
  }

  if (!project) return <p className="text-slate-500">Loading…</p>;

  const draft = renders.find((r) => r.kind === "draft");
  const final = renders.find((r) => r.kind === "final");

  return (
    <div className="space-y-6">
      <PipelineNav projectId={project.id} status={project.status} />
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link to={`/projects/${project.id}/audio`} className="text-xs text-slate-500 hover:text-accent2">
            ← Back to audio
          </Link>
          <h1 className="text-2xl font-semibold mt-1">AI editor &amp; export</h1>
          <p className="text-slate-400 text-sm mt-1">
            Claude builds an Edit Decision List (trims, transitions, captions, mix). FFmpeg renders
            a 480p watermarked draft; on approval it regenerates hero shots at premium and renders
            the final 1080p film.
          </p>
        </div>
        <button className="btn-ghost" disabled={!!busy} onClick={() => run("edl", api.buildEdl(project.id))}>
          {busy === "edl" ? "Editing…" : edl ? "↻ Rebuild EDL" : "Build EDL"}
        </button>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {!edl ? (
        <div className="card p-10 text-center">
          <p className="text-slate-300">No edit decision list yet.</p>
          <p className="text-slate-500 text-sm mt-1 mb-5">
            The AI editor assembles the cut from your clips, narration, and beat grid.
          </p>
          <button className="btn-primary" disabled={!!busy} onClick={() => run("edl", api.buildEdl(project.id))}>
            Build EDL
          </button>
        </div>
      ) : (
        <>
          {/* EDL timeline */}
          <div className="card overflow-hidden">
            <div className="px-4 py-3 bg-panel2/60 border-b border-edge flex items-center justify-between">
              <span className="text-sm font-semibold uppercase tracking-wide text-slate-400">
                Edit decision list
              </span>
              <span className="text-[11px] text-slate-500">
                {edl.total_duration}s · {edl.cuts.length} cuts
                {edl.beat_grid?.bpm ? ` · ${edl.beat_grid.bpm.toFixed(0)} bpm grid` : ""} · {edl.engine}
              </span>
            </div>
            <div className="divide-y divide-edge">
              {edl.cuts.map((c) => (
                <div key={c.scene_number} className="px-4 py-2.5 flex items-center gap-3 text-sm">
                  <span className="flex items-center justify-center w-6 h-6 rounded-full bg-accent text-ink text-xs font-bold shrink-0">
                    {c.scene_number}
                  </span>
                  <span className="font-mono text-[11px] text-slate-500 w-28 shrink-0">
                    {c.in.toFixed(1)}–{c.out.toFixed(1)}s
                  </span>
                  <span className="flex-1 truncate text-slate-300">{c.caption || <em className="text-slate-600">no caption</em>}</span>
                  <span className="text-[11px] px-2 py-0.5 rounded bg-panel2 border border-edge shrink-0">{c.transition}</span>
                  <span className="text-[11px] px-2 py-0.5 rounded bg-panel2 border border-edge shrink-0" title="trim head/tail">
                    ✂ {c.trim_head}/{c.trim_tail}
                  </span>
                  <span
                    className="text-[11px] px-2 py-0.5 rounded bg-panel2 border border-edge shrink-0"
                    title="narration / native / music"
                  >
                    🎙 {db(c.mix.narration_db)} · amb {db(c.mix.native_db)} · ♪ {db(c.mix.music_db)}
                  </span>
                  {c.mix.pause_narration_for_dialogue && (
                    <span className="text-[11px] px-2 py-0.5 rounded bg-accent2/20 text-accent2 border border-accent2/40 shrink-0">
                      dialogue
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Render + preview */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <RenderPanel
              title="Draft"
              badge="480p · watermarked"
              asset={draft}
              busyLabel={busy === "draft" ? "Rendering…" : draft ? "↻ Re-render draft" : "Render draft"}
              disabled={!!busy}
              onRender={() => run("draft", api.render(project.id, false))}
            />
            <RenderPanel
              title="Final"
              badge="1080p · hero shots at premium"
              asset={final}
              busyLabel={busy === "final" ? "Rendering…" : final ? "↻ Re-render final" : "Render final"}
              disabled={!!busy || !draft}
              hint={!draft ? "Render a draft first" : undefined}
              onRender={() => run("final", api.render(project.id, true))}
            />
          </div>
        </>
      )}
    </div>
  );
}

function RenderPanel({
  title,
  badge,
  asset,
  busyLabel,
  disabled,
  hint,
  onRender,
}: {
  title: string;
  badge: string;
  asset?: Asset;
  busyLabel: string;
  disabled: boolean;
  hint?: string;
  onRender: () => void;
}) {
  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-medium">{title}</h2>
          <p className="text-[11px] text-slate-500">{badge}</p>
        </div>
        <button className="btn-ghost" disabled={disabled} onClick={onRender} title={hint}>
          {busyLabel}
        </button>
      </div>
      {asset ? (
        <>
          <video
            key={asset.id}
            src={assetUrl(asset.url)}
            controls
            className="w-full rounded-lg border border-edge bg-black aspect-video"
          />
          <a
            href={assetUrl(asset.url) + "?download=1"}
            className="btn-primary w-full"
            download
          >
            ↓ Download {title.toLowerCase()} ({String(asset.meta?.resolution ?? "")})
          </a>
        </>
      ) : (
        <div className="w-full aspect-video rounded-lg bg-panel2 border border-edge flex items-center justify-center text-sm text-slate-500">
          {hint ?? "Not rendered yet"}
        </div>
      )}
    </div>
  );
}
