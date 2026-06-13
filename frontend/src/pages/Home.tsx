import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, assetUrl } from "../lib/api";
import type { Project } from "../types";

const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  styled: "Styled",
  storyboarded: "Storyboarded",
  keyframes: "Keyframes",
  clips: "Clips",
  audio: "Audio",
  edited: "Edited",
  draft_rendered: "Draft render",
  rendered: "Rendered",
};

// How far through the 6-step pipeline a status is (0..1).
const STATUS_PROGRESS: Record<string, number> = {
  draft: 0.08, styled: 0.12, storyboarded: 0.17,
  keyframes: 0.33, clips: 0.5, audio: 0.67,
  edited: 0.83, draft_rendered: 0.92, rendered: 1,
};

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listProjects().then(setProjects).finally(() => setLoading(false));
  }, []);

  async function remove(id: string) {
    if (!confirm("Delete this project?")) return;
    await api.deleteProject(id);
    setProjects((p) => p.filter((x) => x.id !== id));
  }

  return (
    <div>
      <div className="flex items-end justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">Your projects</h1>
          <p className="text-slate-400 text-sm mt-1">
            One prompt → a finished short film. Start with a storyboard, approve it, then render.
          </p>
        </div>
      </div>

      {loading ? (
        <p className="text-slate-500">Loading…</p>
      ) : projects.length === 0 ? (
        <div className="card p-10 text-center">
          <p className="text-slate-300 text-lg">No projects yet.</p>
          <p className="text-slate-500 mt-1 mb-5">Turn an idea into a storyboard in seconds.</p>
          <Link to="/new" className="btn-primary">Create your first project</Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map((p) => {
            const done = p.status === "rendered";
            const progress = STATUS_PROGRESS[p.status] ?? 0;
            return (
              <div key={p.id} className="card overflow-hidden flex flex-col hover:border-accent2/50 transition group">
                {/* Thumbnail */}
                <Link to={`/projects/${p.id}`} className="block relative aspect-video bg-panel2">
                  {p.thumbnail_url ? (
                    <img src={assetUrl(p.thumbnail_url)} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-slate-600 text-sm">
                      no preview yet
                    </div>
                  )}
                  {done && (
                    <span className="absolute top-2 right-2 text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-500 text-ink">
                      ✓ RENDERED
                    </span>
                  )}
                </Link>

                <div className="p-4 flex flex-col gap-2 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <Link to={`/projects/${p.id}`} className="font-medium leading-snug hover:text-accent2">
                      {p.title}
                    </Link>
                    <button onClick={() => remove(p.id)} className="text-slate-500 hover:text-red-400 text-sm shrink-0">
                      ✕
                    </button>
                  </div>
                  <p className="text-sm text-slate-400 line-clamp-2">{p.idea}</p>

                  {/* Progress */}
                  <div className="mt-auto pt-2">
                    <div className="h-1 rounded bg-panel2 overflow-hidden">
                      <div className="h-full bg-accent transition-all" style={{ width: `${progress * 100}%` }} />
                    </div>
                    <div className="flex items-center justify-between mt-2 text-xs text-slate-500">
                      <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">
                        {STATUS_LABEL[p.status] ?? p.status}
                      </span>
                      <span className="flex items-center gap-2">
                        <span>{p.target_length}s · {p.aspect_ratio}</span>
                        {["draft_rendered", "rendered"].includes(p.status) && (
                          <Link to={`/projects/${p.id}/editor`} className="text-accent2 hover:underline">▶ watch</Link>
                        )}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
