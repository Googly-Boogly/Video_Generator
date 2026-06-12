import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
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
          {projects.map((p) => (
            <div key={p.id} className="card p-4 flex flex-col gap-3 hover:border-accent2/50 transition">
              <div className="flex items-start justify-between gap-2">
                <Link to={`/projects/${p.id}`} className="font-medium leading-snug hover:text-accent2">
                  {p.title}
                </Link>
                <button onClick={() => remove(p.id)} className="text-slate-500 hover:text-red-400 text-sm">
                  ✕
                </button>
              </div>
              <p className="text-sm text-slate-400 line-clamp-2">{p.idea}</p>
              <div className="flex items-center gap-2 text-xs text-slate-500 mt-auto">
                <span className="px-2 py-0.5 rounded bg-panel2 border border-edge">
                  {STATUS_LABEL[p.status] ?? p.status}
                </span>
                <span>{p.target_length}s</span>
                <span>{p.aspect_ratio}</span>
                <span className="capitalize">{p.style_preset}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
