import { useEffect, useState } from "react";
import { fetchPersonas } from "./lib/api";
import type { Persona } from "./lib/types";
import { Chat } from "./components/Chat";
import { PersonaRail } from "./components/PersonaRail";
import { TriageBoard } from "./components/TriageBoard";

/** The pinned snapshot. Every timing answer in the system is relative to it,
 *  and it is a Sunday -- which is why business-hours clocks have not started. */
const SNAPSHOT = "Sun 16 Aug 2026, 11:00 IST";

type View = "chat" | "board";

export default function App() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [view, setView] = useState<View>("chat");
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    fetchPersonas()
      .then((body) => {
        setPersonas(body.personas);
        setActiveId(body.default);
      })
      .catch(() => setFailure("Could not reach the backend. Is it running on port 8000?"));
  }, []);

  const active = personas.find((persona) => persona.id === activeId) ?? null;

  // The board needs the `signals` capability, which no customer holds. Falling
  // back to chat on switch keeps the UI honest rather than showing a tab that
  // would only 403.
  const canSeeBoard = Boolean(active?.capabilities.includes("signals"));
  useEffect(() => {
    if (!canSeeBoard) setView("chat");
  }, [canSeeBoard]);

  if (failure) {
    return (
      <div className="pane">
        <div className="pane-inner">
          <div className="notice">
            <div className="notice-title">Backend unavailable</div>
            <div className="notice-body">{failure}</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <PersonaRail personas={personas} active={active} onPick={setActiveId} />

      <main className="main">
        <div className="tabs">
          <div className="tab-group">
            <button
              className="tab"
              aria-selected={view === "chat"}
              onClick={() => setView("chat")}
            >
              Chat
            </button>
            {canSeeBoard && (
              <button
                className="tab"
                aria-selected={view === "board"}
                onClick={() => setView("board")}
              >
                Triage board
              </button>
            )}
          </div>
          <div className="snapshot">now: {SNAPSHOT}</div>
        </div>

        {active ? (
          view === "chat" ? (
            <Chat persona={active} />
          ) : (
            <TriageBoard persona={active} />
          )
        ) : (
          <div className="pane">
            <div className="pane-inner">
              <div className="thinking">
                <span className="dot" /> loading personas…
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
