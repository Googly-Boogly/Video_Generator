import { Link, Outlet, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "./lib/api";

export default function App() {
  const loc = useLocation();
  const [mock, setMock] = useState<boolean | null>(null);

  useEffect(() => {
    api.config().then((c) => setMock(c.mock_generation)).catch(() => setMock(null));
  }, []);

  return (
    <div className="min-h-screen">
      <header className="border-b border-edge bg-panel/60 backdrop-blur sticky top-0 z-20">
        <div className="mx-auto max-w-6xl px-6 h-14 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2 font-semibold text-lg">
            <span className="text-accent">◆</span> StoryForge
          </Link>
          <div className="flex items-center gap-3 text-sm">
            {mock !== null && (
              <span
                className={`px-2 py-1 rounded-md text-xs font-semibold border ${
                  mock
                    ? "bg-amber-500/10 text-amber-300 border-amber-500/30"
                    : "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                }`}
                title={mock ? "No API spend — placeholder assets" : "Live generation — real spend"}
              >
                {mock ? "MOCK MODE" : "LIVE"}
              </span>
            )}
            {loc.pathname !== "/new" && (
              <Link to="/new" className="btn-primary">
                + New project
              </Link>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
