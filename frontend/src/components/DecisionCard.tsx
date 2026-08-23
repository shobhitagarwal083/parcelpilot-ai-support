import type { Audience, Decision } from "../lib/types";
import { CitationCard } from "./CitationCard";

const GOOD = new Set(["allowed", "eligible", "within_target"]);
const BAD = new Set(["denied", "ineligible", "breached"]);

function outcomeClass(outcome: string): string {
  if (GOOD.has(outcome)) return "outcome-good";
  if (BAD.has(outcome)) return "outcome-bad";
  return "outcome-unknown";
}

function formatAmount(amount: Decision["amount_inr"]): string | null {
  if (amount === null || amount === undefined) return null;
  const value = typeof amount === "string" ? Number(amount) : amount;
  if (Number.isNaN(value)) return String(amount);
  return `₹${value.toLocaleString("en-IN")}`;
}

/** Facts worth showing. The rest are engine internals and would only add noise. */
const FACT_ORDER = [
  "order_id",
  "ticket_id",
  "account_id",
  "plan",
  "status",
  "severity",
  "target_minutes",
  "coverage",
  "clock_starts_at",
  "due_at",
  "elapsed_minutes",
  "breach_minutes",
  "minutes_since_booking",
  "hours_past_window_end",
  "carrier_fault",
  "customer_fault",
];

function orderedFacts(facts: Record<string, unknown>): [string, string][] {
  const known = FACT_ORDER.filter((key) => key in facts);
  const rest = Object.keys(facts).filter((key) => !FACT_ORDER.includes(key));
  return [...known, ...rest]
    .filter((key) => facts[key] !== null && facts[key] !== undefined)
    .map((key) => [key, String(facts[key])]);
}

export function DecisionCard({
  decision,
  audience = "internal",
}: {
  decision: Decision;
  audience?: Audience;
}) {
  const amount = formatAmount(decision.amount_inr);
  const facts = orderedFacts(decision.facts_used ?? {});

  return (
    <div className="card">
      <div className="card-label">Decision · computed by the rule engine</div>

      <div className="decision-head">
        <span className={`outcome ${outcomeClass(decision.outcome)}`}>
          {decision.outcome.replace(/_/g, " ")}
        </span>
        {amount && <span className="amount">{amount}</span>}
      </div>

      {decision.summary && <p className="decision-summary">{decision.summary}</p>}

      {facts.length > 0 && (
        <div className="facts">
          {facts.map(([key, value]) => (
            <div key={key}>
              <span className="fact-key">{key}</span> <span className="fact-val">{value}</span>
            </div>
          ))}
        </div>
      )}

      {/* The overrides are the reason this system exists. A rule that lost is
          shown, with *why* it lost -- a silently dropped SOP is indistinguishable
          from one that was never retrieved at all. */}
      {decision.overrides?.map((override) => (
        <div
          key={`${override.winning_rule_id}-${override.overridden_rule_id}`}
          className={`override override-${override.kind}`}
        >
          <span className="override-kind">
            {override.kind === "replaced"
              ? "replaced by the agreement"
              : "outranked on authority"}
          </span>
          {override.explanation}
        </div>
      ))}

      {/* A poisoned past answer is surfaced, never quietly ignored -- a human
          reading the same ticket history would otherwise repeat the mistake. */}
      {decision.contradicts?.map((contradiction) => (
        <div className="contradiction" key={contradiction.ticket_id}>
          <strong>{contradiction.ticket_id} said otherwise, and was wrong.</strong>{" "}
          {audience === "internal" && (
            <em>&ldquo;{contradiction.recorded_resolution}&rdquo; — </em>
          )}
          {contradiction.why_wrong}
        </div>
      ))}

      {/* Q7: the same caveat in two registers. A customer gets the behaviour;
          only internal users get the issue tracker id. */}
      {decision.caveats?.map((caveat) => (
        <div key={caveat.issue_id} className="caveat">
          {audience === "customer" ? caveat.customer_safe_text : `${caveat.issue_id}: ${caveat.text}`}
        </div>
      ))}

      {decision.notes?.map((note) => (
        <div key={note} className="caveat">
          {note}
        </div>
      ))}

      {decision.unknowns?.length > 0 && (
        <div className="caveat">
          Not established: {decision.unknowns.join(", ")}. No promise is made on these.
        </div>
      )}

      {decision.citations?.length > 0 && (
        <div className="disclosed">
          {decision.citations.map((citation, index) => (
            <CitationCard key={`${citation.doc_id}-${citation.section}-${index}`} citation={citation} />
          ))}
        </div>
      )}
    </div>
  );
}
