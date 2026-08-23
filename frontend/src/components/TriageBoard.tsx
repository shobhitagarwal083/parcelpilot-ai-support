import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchActions, fetchSignals } from "../lib/api";
import { RANKS, type Persona, type ProposedAction, type Signal } from "../lib/types";
import { ActionCard } from "./ActionCard";
import { DecisionCard } from "./DecisionCard";

/* Problem 1 -- proactive issue detection.
 *
 * The card that earns this whole view is the silent credit: ORD-2002 is 4h30m
 * past its pickup window with carrier fault accepted, LumenWorks is owed ₹300
 * under its own agreement, and no ticket exists. Nobody asked. A purely
 * reactive support system never finds it.
 */

function SignalCard({ signal }: { signal: Signal }) {
  const [open, setOpen] = useState(false);

  return (
    <div className={`signal signal-${signal.rank}`}>
      <h3 className="signal-title">{signal.title}</h3>
      <p className="signal-detail">{signal.detail}</p>

      <div className="signal-meta">
        <span className="detector">{signal.detector}</span>
        {signal.evidence.map((id) => (
          <span className="chip" key={id}>
            {id}
          </span>
        ))}
        {signal.account_id && <span className="chip">{signal.account_id}</span>}
      </div>

      <div className="suggested">
        <strong>Suggested:</strong> {signal.suggested_action}
      </div>

      {signal.decision && (
        <>
          <button className="disclose" onClick={() => setOpen((value) => !value)}>
            {open ? "Hide the reasoning" : "Show the reasoning and sources"}
          </button>
          {open && (
            <div className="disclosed">
              <DecisionCard decision={signal.decision} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function TriageBoard({ persona }: { persona: Persona }) {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [queue, setQueue] = useState<ProposedAction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  /* An action proposed in one session has to be resolvable from another.
   *
   * Switching persona clears the transcript, so a card telling an agent to
   * "switch to a manager to approve" would send them somewhere the card no
   * longer exists. The pending row outlives the conversation on the server, and
   * this is where it surfaces -- which is also how the real workflow runs: the
   * manager approving a credit is not the person who proposed it. */
  const refreshQueue = useCallback(() => {
    fetchActions(persona.id)
      .then((found) =>
        setQueue(found.filter((action) => ["PENDING", "NEEDS_APPROVAL"].includes(action.status))),
      )
      .catch(() => setQueue([]));
  }, [persona.id]);

  useEffect(refreshQueue, [refreshQueue]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchSignals(persona.id)
      .then((found) => {
        if (!cancelled) setSignals(found);
      })
      .catch((problem) => {
        if (cancelled) return;
        setError(
          problem instanceof ApiError
            ? `${problem.status} — ${problem.message}`
            : "could not reach the server",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [persona.id]);

  return (
    <div className="pane">
      <div className="pane-inner">
        <div className="board-head">
          <h2>Triage board</h2>
          <p>
            Issues found by scanning every order and ticket in scope, not by waiting to be
            asked. Ranked by urgency, each with the evidence behind it.
          </p>
        </div>

        {queue.length > 0 && (
          <div className="rank-group">
            <div className="rank-label rank-high">
              <span>awaiting a human · {queue.length}</span>
              <span className="rank-rule" />
            </div>
            {queue.map((action) => (
              <div key={action.action_id} style={{ marginBottom: 9 }}>
                <ActionCard
                  action={action}
                  persona={persona}
                  onResolved={(updated) => {
                    setQueue((current) =>
                      current.filter((item) => item.action_id !== updated.action_id),
                    );
                  }}
                />
              </div>
            ))}
          </div>
        )}

        {loading && (
          <div className="thinking">
            <span className="dot" /> scanning…
          </div>
        )}

        {error && (
          <div className="notice">
            <div className="notice-title">Could not load signals</div>
            <div className="notice-detail">{error}</div>
          </div>
        )}

        {!loading && !error && signals.length === 0 && (
          <div className="empty">
            <p>Nothing needs attention in this scope.</p>
          </div>
        )}

        {RANKS.map((rank) => {
          const group = signals.filter((signal) => signal.rank === rank);
          if (!group.length) return null;
          return (
            <div className="rank-group" key={rank}>
              <div className={`rank-label rank-${rank}`}>
                <span>
                  {rank} · {group.length}
                </span>
                <span className="rank-rule" />
              </div>
              {group.map((signal) => (
                <SignalCard key={signal.id} signal={signal} />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
