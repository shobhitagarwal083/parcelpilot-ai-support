export type Tier = 1 | 2 | 3 | 4;

export interface Persona {
  id: string;
  display_name: string;
  kind: "customer" | "internal";
  role: string;
  account_ids: string[];
  capabilities: string[];
}

export interface Citation {
  doc_id: string;
  doc_title: string;
  section: string;
  quote: string;
  authority_tier: Tier;
}

export interface Override {
  winning_rule_id: string;
  overridden_rule_id: string;
  kind: "outranked" | "replaced";
  explanation: string;
}

/** A past ticket whose recorded resolution conflicts with the winning rule.
 *  Never suppressed — the right behaviour is to say the earlier answer was
 *  wrong, so a human reading the same history does not repeat it. */
export interface Contradiction {
  ticket_id: string;
  recorded_resolution: string;
  why_wrong: string;
}

/** A known-issue warning. Two registers of the same caveat: `text` names the
 *  internal issue id, `customer_safe_text` describes only the behaviour. */
export interface Caveat {
  issue_id: string;
  text: string;
  customer_safe_text: string;
}

export interface Decision {
  domain: string;
  outcome: string;
  amount_inr: number | string | null;
  summary?: string;
  facts_used: Record<string, unknown>;
  unknowns: string[];
  citations: Citation[];
  overrides: Override[];
  contradicts: Contradiction[];
  caveats: Caveat[];
  notes: string[];
  requires_human: boolean;
  human_reason: string | null;
  winning_rule_id: string | null;
}

export type Audience = "customer" | "internal";

export interface ToolStep {
  id: string;
  name: string;
  label: string;
  input: Record<string, unknown>;
  state: "running" | "ok" | "error";
  summary?: string;
  decision?: Decision | null;
}

export interface ProposedAction {
  action_id: string;
  action_type: string;
  target_id: string | null;
  account_id: string | null;
  payload: Record<string, unknown>;
  status: string;
  needs_approval: boolean;
  decision?: Decision | null;
  /** Set once the user confirms or rejects it -- never by the model. */
  resolution?: { status: string; note?: string };
}

export interface TurnError {
  message: string;
  kind?: string;
}

/** One exchange. The agent side is assembled from the event stream. */
export interface Turn {
  id: string;
  question: string;
  answer: string;
  steps: ToolStep[];
  citations: Citation[];
  actions: ProposedAction[];
  escalations: string[];
  error?: TurnError;
  streaming: boolean;
}

export interface Signal {
  id: string;
  detector: string;
  rank: "critical" | "high" | "medium" | "low";
  title: string;
  detail: string;
  evidence: string[];
  account_id: string | null;
  suggested_action: string;
  /** Citations live on the decision, not on the signal -- there is no separate list. */
  decision: Decision | null;
}

export const RANKS = ["critical", "high", "medium", "low"] as const;
