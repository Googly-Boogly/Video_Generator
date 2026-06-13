import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { CostDashboard } from "../lib/api";
import type { Project } from "../types";
import PipelineNav from "../components/PipelineNav";

const STEP_LABEL: Record<string, string> = {
  keyframes: "Keyframes (FLUX.2)",
  video: "Video clips",
  audio: "Narration (ElevenLabs)",
  render: "Final render (premium hero shots)",
};
const STEP_COLOR: Record<string, string> = {
  keyframes: "bg-sky-500",
  video: "bg-violet-500",
  audio: "bg-emerald-500",
  render: "bg-amber-500",
};

export default function Costs() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [cost, setCost] = useState<CostDashboard | null>(null);

  useEffect(() => {
    if (!id) return;
    api.getProject(id).then(setProject);
    api.costDashboard(id).then(setCost);
  }, [id]);

  if (!project || !cost) return <p className="text-slate-500">Loading…</p>;

  const estimated = cost.estimated.total;
  const actual = cost.actual.total;
  const maxStep = Math.max(0.0001, ...Object.values(cost.actual.by_step));
  const steps = Object.entries(cost.actual.by_step);
  const overBudget = actual > estimated && estimated > 0;
  const pct = estimated > 0 ? Math.round((actual / estimated) * 100) : 0;

  return (
    <div className="space-y-6">
      <div>
        <Link to={`/projects/${project.id}`} className="text-xs text-slate-500 hover:text-accent2">
          ← {project.title}
        </Link>
        <h1 className="text-2xl font-semibold mt-1">Cost dashboard</h1>
        <div className="mt-3">
          <PipelineNav projectId={project.id} status={project.status} />
        </div>
      </div>

      {cost.mock && (
        <div className="text-sm rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 px-3 py-2">
          Mock mode — <strong>$0 was actually charged</strong>. The figures below are the real
          provider cost these operations <em>would</em> incur (so you can budget before going live).
        </div>
      )}

      {overBudget && (
        <div className="text-sm rounded-lg bg-red-600/10 border border-red-600/40 text-red-300 px-3 py-2">
          ⚠ Spend is <strong>{pct}% of the estimate</strong> — regeneration has added
          ${(actual - estimated).toFixed(2)} over the ${estimated.toFixed(2)} pre-flight estimate.
          Each re-run of a scene's keyframes/clip is billed again.
        </div>
      )}

      {/* Estimate vs actual */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="card p-5">
          <p className="text-xs uppercase tracking-wide text-slate-400">Pre-flight estimate</p>
          <p className="text-3xl font-semibold mt-1">${estimated.toFixed(2)}</p>
          <p className="text-[11px] text-slate-500 mt-1">Full project at premium tiers</p>
          <div className="mt-3 space-y-1">
            {cost.estimated.line_items.map((li) => (
              <div key={li.label} className="flex justify-between text-xs text-slate-400">
                <span>{li.label}</span>
                <span>${li.amount.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="card p-5">
          <p className="text-xs uppercase tracking-wide text-slate-400">Actual runs (ledger)</p>
          <p className="text-3xl font-semibold mt-1 text-accent">${actual.toFixed(2)}</p>
          <p className="text-[11px] text-slate-500 mt-1">
            What was generated so far · includes regeneration
          </p>
          <div className="mt-3 space-y-2">
            {steps.map(([step, amt]) => (
              <div key={step}>
                <div className="flex justify-between text-xs text-slate-300">
                  <span>{STEP_LABEL[step] ?? step}</span>
                  <span>${amt.toFixed(2)}</span>
                </div>
                <div className="h-1.5 rounded bg-panel2 mt-1 overflow-hidden">
                  <div
                    className={`h-full ${STEP_COLOR[step] ?? "bg-slate-500"}`}
                    style={{ width: `${(amt / maxStep) * 100}%` }}
                  />
                </div>
              </div>
            ))}
            {steps.length === 0 && <p className="text-xs text-slate-500">Nothing generated yet.</p>}
          </div>
        </div>
      </div>

      {/* Ledger */}
      <div className="card overflow-hidden">
        <div className="px-4 py-3 bg-panel2/60 border-b border-edge text-sm font-semibold uppercase tracking-wide text-slate-400">
          Ledger · {cost.actual.entries.length} entries
        </div>
        <div className="divide-y divide-edge max-h-96 overflow-auto">
          {cost.actual.entries.map((e, i) => (
            <div key={i} className="px-4 py-2 flex items-center gap-3 text-sm">
              <span className="text-[10px] uppercase tracking-wide text-slate-500 w-20 shrink-0">{e.step}</span>
              <span className="flex-1 truncate">{e.label}</span>
              <span className="text-[11px] text-slate-500 truncate hidden sm:block">{e.detail}</span>
              <span className="font-mono text-slate-300 shrink-0">${e.amount.toFixed(3)}</span>
            </div>
          ))}
          {cost.actual.entries.length === 0 && (
            <p className="px-4 py-6 text-sm text-slate-500 text-center">No paid steps have run yet.</p>
          )}
        </div>
      </div>
    </div>
  );
}
