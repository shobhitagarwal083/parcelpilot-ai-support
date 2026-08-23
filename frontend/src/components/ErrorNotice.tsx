import type { TurnError } from "../lib/types";

/* Errors here are not incidental -- the hosted demo runs on a free-tier model,
 * so a rate limit is the single most likely thing a reviewer meets. Saying so
 * plainly beats a blank box that reads as a broken app.
 *
 * ⚠️ Keep this copy true. It previously told users the quota was "50 requests a
 * day, resetting at 05:30 IST", which was accurate for the original provider
 * and became false the moment we moved to one with per-minute limits. A
 * confident, wrong explanation is worse than a vague one: it would have sent a
 * reviewer away for nineteen hours over something that clears in a minute.
 */

function explain(error: TurnError): { title: string; body: string } {
  const message = error.message ?? "";
  const rateLimited = /429|rate limit|quota/i.test(message);

  if (error.kind === "missing_credentials") {
    return {
      title: "No model credentials configured",
      body:
        "The rule engine, retrieval and access control all run without a key — " +
        "only the narration needs one. Add OPENROUTER_API_KEY to .env and restart.",
    };
  }
  if (rateLimited || error.kind === "rate_limited") {
    return {
      title: "Too many requests just now — try again in a moment",
      body:
        "This demo shares one free-tier model allowance across everyone using the link, " +
        "so it limits how fast any one visitor can spend it. The limits are per-minute, " +
        "so waiting briefly is enough. Nothing on screen is wrong: the decisions are " +
        "computed by the rule engine rather than the model, so only the narration is " +
        "unavailable.",
    };
  }
  if (error.kind === "turn_cap" || error.kind === "loop_cap") {
    return {
      title: "This turn ran too long and was stopped",
      body:
        "The agent hit its per-turn tool budget without reaching an answer. That cap " +
        "exists so a confused loop cannot spend the day's quota by itself.",
    };
  }
  return { title: "Something went wrong", body: "The turn did not complete." };
}

export function ErrorNotice({ error }: { error: TurnError }) {
  const { title, body } = explain(error);
  return (
    <div className="notice">
      <div className="notice-title">{title}</div>
      <div className="notice-body">{body}</div>
      {error.message && <div className="notice-detail">{error.message}</div>}
    </div>
  );
}
