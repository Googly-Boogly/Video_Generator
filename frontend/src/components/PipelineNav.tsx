import { Link } from "react-router-dom";

const STEPS = [
  { key: "storyboard", label: "Storyboard", path: "" },
  { key: "keyframes", label: "Keyframes", path: "/keyframes" },
  { key: "clips", label: "Clips", path: "/clips" },
  { key: "audio", label: "Audio", path: "/audio" },
  { key: "editor", label: "Editor", path: "/editor" },
  { key: "done", label: "Export", path: "/editor" },
];

// Which step a project status corresponds to (its highest reached step).
const STATUS_STEP: Record<string, number> = {
  draft: 0, styled: 0, storyboarded: 0,
  keyframes: 1, clips: 2, audio: 3,
  edited: 4, draft_rendered: 4, rendered: 5,
};

export default function PipelineNav({ projectId, status }: { projectId: string; status: string }) {
  const current = STATUS_STEP[status] ?? 0;
  return (
    <nav className="flex items-center gap-1 text-xs overflow-x-auto pb-1">
      {STEPS.map((s, i) => {
        const reached = i <= current;
        const isCurrent = i === current;
        const inner = (
          <span
            className={`px-2.5 py-1 rounded-md border whitespace-nowrap ${
              isCurrent
                ? "bg-accent text-ink border-accent font-semibold"
                : reached
                ? "bg-panel2 text-slate-200 border-edge hover:border-accent2"
                : "bg-panel/40 text-slate-600 border-edge/50"
            }`}
          >
            {i + 1}. {s.label}
          </span>
        );
        return (
          <div key={s.key} className="flex items-center gap-1">
            {reached ? <Link to={`/projects/${projectId}${s.path}`}>{inner}</Link> : inner}
            {i < STEPS.length - 1 && <span className="text-slate-600">›</span>}
          </div>
        );
      })}
      <Link
        to={`/projects/${projectId}/costs`}
        className="ml-2 px-2.5 py-1 rounded-md border border-edge bg-panel2 text-slate-300 hover:border-accent2 whitespace-nowrap"
      >
        ＄ Costs
      </Link>
    </nav>
  );
}
