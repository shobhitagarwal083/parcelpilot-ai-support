import { useState } from "react";
import { ApiError, resolveAction } from "../lib/api";
import type { Persona, ProposedAction } from "../lib/types";
import { DecisionCard } from "./DecisionCard";

/* Two-phase actions, made visible.
 *
 * The model wrote a PENDING row and stopped. Confirming posts to
 * /api/actions/{id}/confirm -- an endpoint with no tool bound to it, so there
 * is no path by which the model reaches execution even if it tries. The button
 * being the only way through is the design, not a UI convenience.
 */

const TITLES: Record<string, string> = {
  create_escalation: "Escalate to a human",
  update_ticket: "Update ticket",
  create_followup_task: "Create follow-up task",
  issue_service_credit: "Issue service credit",
};

export function ActionCard({
  action,
  persona,
  onResolved,
}: {
  action: ProposedAction;
  persona: Persona;
  onResolved: (updated: ProposedAction) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const resolved = action.resolution?.status ?? null;
  const canApprove = !action.needs_approval || persona.capabilities.includes("approve:credit");

  async function send(decision: "confirm" | "reject") {
    setBusy(true);
    setFailure(null);
    try {
      const updated = await resolveAction(persona.id, action.action_id, decision);
      onResolved({ ...action, ...updated, resolution: { status: updated.status } });
    } catch (error) {
      setFailure(
        error instanceof ApiError
          ? `${error.status} — ${error.message}`
          : "could not reach the server",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="action">
      <div className="card-label">Proposed · nothing has changed yet</div>
      <h3 className="action-title">{TITLES[action.action_type] ?? action.action_type}</h3>
      <div className="action-target">
        {action.target_id ?? "—"}
        {action.account_id ? ` · ${action.account_id}` : ""}
      </div>

      {Object.keys(action.payload ?? {}).length > 0 && (
        <pre className="payload">{JSON.stringify(action.payload, null, 2)}</pre>
      )}

      {action.decision && (
        <div style={{ marginTop: 12 }}>
          <DecisionCard decision={action.decision} audience={persona.kind} />
        </div>
      )}

      {!resolved && (
        <>
          <div className="action-buttons">
            <button
              className="btn btn-primary"
              disabled={busy || !canApprove}
              onClick={() => send("confirm")}
            >
              {busy ? "Working…" : "Confirm"}
            </button>
            <button className="btn" disabled={busy} onClick={() => send("reject")}>
              Reject
            </button>
            {!canApprove && (
              <span className="action-note">
                Above the ₹1,000 approval threshold in SOP v4 §3 — needs a support manager.
                Switch to Priya to approve.
              </span>
            )}
          </div>
          {failure && <div className="action-status status-rejected">{failure}</div>}
        </>
      )}

      {resolved && (
        <div
          className={`action-status ${
            resolved === "EXECUTED"
              ? "status-executed"
              : resolved === "NEEDS_APPROVAL"
                ? "status-approval"
                : "status-rejected"
          }`}
        >
          {resolved === "EXECUTED"
            ? "✓ executed and written to the audit log"
            : resolved === "NEEDS_APPROVAL"
              ? "awaiting manager approval"
              : `${resolved.toLowerCase()}`}
        </div>
      )}
    </div>
  );
}
