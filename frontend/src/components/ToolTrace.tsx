import { useState } from "react";
import type { ToolStep } from "../lib/types";

/* Requirement 6: the interface should show which tool is being used.
 *
 * Interleaved into the transcript in arrival order rather than parked in a
 * sidebar, because a five-call chain only reads as a chain if you watch it
 * unfold. It stays expanded while the turn is live and collapses once the
 * answer lands, so the trace is evidence rather than clutter.
 */

function summariseArgs(input: Record<string, unknown>): string {
  const entries = Object.entries(input ?? {});
  if (!entries.length) return "";
  return entries
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(" ");
}

function Mark({ state }: { state: ToolStep["state"] }) {
  if (state === "running") return <span className="trace-mark run spin">◐</span>;
  if (state === "error") return <span className="trace-mark err">✗</span>;
  return <span className="trace-mark ok">✓</span>;
}

export function ToolTrace({ steps, live }: { steps: ToolStep[]; live: boolean }) {
  const [open, setOpen] = useState(true);
  const expanded = live || open;

  if (!steps.length) return null;

  return (
    <div className="trace">
      <button className="trace-head" onClick={() => setOpen((value) => !value)}>
        <span>{expanded ? "▾" : "▸"}</span>
        <span>
          {steps.length} tool {steps.length === 1 ? "call" : "calls"}
        </span>
        {!expanded && <span>· {steps.map((step) => step.name).join(" → ")}</span>}
      </button>

      {expanded && (
        <div className="trace-body">
          {steps.map((step) => (
            <div className="trace-row" key={step.id}>
              <Mark state={step.state} />
              <span className="trace-name">{step.name}</span>
              <span className="trace-args">{summariseArgs(step.input)}</span>
              {step.summary && <span className="trace-summary">{step.summary}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
