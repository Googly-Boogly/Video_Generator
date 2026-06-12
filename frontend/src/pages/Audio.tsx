import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, assetUrl, pollJob } from "../lib/api";
import type { Asset, Project, Scene } from "../types";

type Voice = { voice_id: string; name: string; labels: Record<string, string> };
type Track = { id: string; name: string; bpm: number; style: string; seconds: number };

export default function Audio() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [music, setMusic] = useState<Asset | null>(null);
  const [narration, setNarration] = useState<Record<string, Asset>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const ordered = [...scenes].sort((a, b) => a.scene_number - b.scene_number);

  const loadAll = useCallback(async () => {
    if (!id) return;
    const [p, sc, mu, narr] = await Promise.all([
      api.getProject(id),
      api.listScenes(id),
      api.getMusic(id),
      api.listNarration(id),
    ]);
    setProject(p);
    setScenes(sc);
    setMusic(mu);
    const byScene: Record<string, Asset> = {};
    narr.forEach((a) => a.scene_id && (byScene[a.scene_id] = a));
    setNarration(byScene);
  }, [id]);

  useEffect(() => {
    api.listVoices().then((v) => setVoices(v.voices));
    api.musicLibrary().then((m) => setTracks(m.tracks));
    loadAll();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadAll]);

  async function runJob(p: Promise<{ id: string }>) {
    setBusy(true);
    setError(null);
    try {
      const job = await p;
      await pollJob(job.id);
      await loadAll();
    } catch (e: any) {
      setError(e.message ?? "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function changeVoice(voice_id: string) {
    if (!id) return;
    await api.setVoice(id, voice_id);
    setProject((p) => (p ? { ...p, voice_id } : p));
  }

  async function pickTrack(track_id: string) {
    if (!id || !track_id) return;
    setBusy(true);
    try {
      setMusic(await api.pickLibraryMusic(id, track_id));
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    if (!id) return;
    setBusy(true);
    try {
      setMusic(await api.uploadMusic(id, file));
    } catch (e: any) {
      setError(e.message ?? "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function dropMusic() {
    if (!id) return;
    await api.removeMusic(id);
    setMusic(null);
  }

  if (!project) return <p className="text-slate-500">Loading…</p>;

  const grid = (music?.meta?.beat_grid as { bpm?: number; beats?: number[]; engine?: string }) ?? null;
  const voiceId = project.voice_id ?? voices.find(() => true)?.voice_id ?? "";
  const narratedScenes = ordered.filter((s) => s.audio_mode !== "dialogue");
  const haveAllNarration = narratedScenes.every((s) => narration[s.id]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link to={`/projects/${project.id}/clips`} className="text-xs text-slate-500 hover:text-accent2">
            ← Back to clips
          </Link>
          <h1 className="text-2xl font-semibold mt-1">Audio build</h1>
          <p className="text-slate-400 text-sm mt-1">
            One locked voice narrates; a single music bed runs underneath (beat-detected so the
            editor can cut on beat); each clip's native audio sits ~15–30% under narration.
          </p>
        </div>
        <button className="btn-primary" disabled={busy} onClick={() => runJob(api.buildAudio(project.id))}>
          {busy ? "Working…" : "Build audio"}
        </button>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Voice + music */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card p-4">
          <label className="label">Narration voice (locked per project)</label>
          <select className="input" value={voiceId} onChange={(e) => changeVoice(e.target.value)}>
            {voices.map((v) => (
              <option key={v.voice_id} value={v.voice_id}>
                {v.name}
                {v.labels?.tone ? ` · ${v.labels.tone}` : ""}
              </option>
            ))}
          </select>
          <p className="text-[11px] text-slate-500 mt-2">
            Narration carries the words. Native model audio is never used for the voice — identity
            can't persist across separate generation calls.
          </p>
        </div>

        <div className="card p-4">
          <label className="label">Music bed (one continuous track)</label>
          <div className="flex gap-2">
            <select
              className="input"
              value=""
              onChange={(e) => pickTrack(e.target.value)}
              disabled={busy}
            >
              <option value="">Pick from library…</option>
              {tracks.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} · {t.bpm} bpm
                </option>
              ))}
            </select>
            <button className="btn-ghost whitespace-nowrap" disabled={busy} onClick={() => fileRef.current?.click()}>
              Upload
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
            />
          </div>

          {music ? (
            <div className="mt-3 space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-300">{String(music.meta?.name ?? "music bed")}</span>
                <button className="text-[11px] text-slate-500 hover:text-red-400" onClick={dropMusic}>
                  remove
                </button>
              </div>
              <audio src={assetUrl(music.url)} controls className="w-full h-9" />
              {grid && (
                <div className="flex items-center gap-2 text-[11px] text-slate-400">
                  <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">
                    {grid.bpm?.toFixed(0)} bpm
                  </span>
                  <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">
                    {grid.beats?.length ?? 0} beats
                  </span>
                  <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">
                    beat grid · {grid.engine}
                  </span>
                </div>
              )}
            </div>
          ) : (
            <p className="text-[11px] text-slate-500 mt-2">No bed yet — pick one or upload a track.</p>
          )}
        </div>
      </div>

      {/* Per-scene narration */}
      <div className="card overflow-hidden">
        <div className="px-4 py-3 bg-panel2/60 border-b border-edge text-sm font-semibold uppercase tracking-wide text-slate-400">
          Narration · {Object.keys(narration).length}/{narratedScenes.length} scenes
        </div>
        <div className="divide-y divide-edge">
          {ordered.map((scene) => {
            const isDialogue = scene.audio_mode === "dialogue";
            const narr = narration[scene.id];
            return (
              <div key={scene.id} className="px-4 py-3 flex items-center gap-3">
                <span className="flex items-center justify-center w-6 h-6 rounded-full bg-accent text-ink text-xs font-bold shrink-0">
                  {scene.scene_number}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm truncate">{scene.shot_description}</p>
                  <p className="text-[11px] text-slate-500 truncate">
                    {isDialogue
                      ? `Dialogue (native audio, narration paused): "${scene.dialogue_text ?? ""}"`
                      : scene.narration_text}
                  </p>
                </div>
                {isDialogue ? (
                  <span className="text-[11px] px-2 py-0.5 rounded bg-panel2 border border-edge text-slate-400 shrink-0">
                    dialogue
                  </span>
                ) : narr ? (
                  <>
                    <audio src={assetUrl(narr.url)} controls className="h-8 w-48 shrink-0" />
                    <span className="text-[11px] text-slate-500 shrink-0">
                      {Number(narr.meta?.duration ?? 0).toFixed(1)}s
                    </span>
                    <button
                      className="btn-ghost px-2 py-1 text-xs shrink-0"
                      disabled={busy}
                      onClick={() => runJob(api.regenerateNarration(project.id, scene.id))}
                    >
                      ↻
                    </button>
                  </>
                ) : (
                  <span className="text-[11px] text-slate-500 shrink-0">not built</span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Mix summary */}
      <div className="card p-4 text-sm">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-2">Mix plan</h2>
        <div className="flex flex-wrap gap-2 text-[11px] text-slate-400">
          <span className="px-2 py-1 rounded bg-panel2 border border-edge">narration 0 dB</span>
          <span className="px-2 py-1 rounded bg-panel2 border border-edge">native −16 dB (ducked)</span>
          <span className="px-2 py-1 rounded bg-panel2 border border-edge">music −18 dB bed</span>
          <span className="px-2 py-1 rounded bg-panel2 border border-edge">dialogue → narration pauses</span>
          <span className="px-2 py-1 rounded bg-panel2 border border-edge">music ducks under narration</span>
        </div>
      </div>

      <div className="card p-4 flex items-center justify-between sticky bottom-4">
        <p className="text-sm text-slate-400">
          {haveAllNarration
            ? "Narration ready. Next: the AI editor assembles the Edit Decision List."
            : "Build narration for every narrated scene to continue."}
        </p>
        <button
          className="btn-primary"
          disabled={!haveAllNarration && narratedScenes.length > 0}
          onClick={() => id && nav(`/projects/${id}/editor`)}
        >
          Open AI editor →
        </button>
      </div>
    </div>
  );
}
