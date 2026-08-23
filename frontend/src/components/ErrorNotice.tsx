import type { TurnError } from "../lib/types";

/* Errors here are not incidental -- the hosted demo runs on a free-tier model
 * with a hard daily request cap, so a rate limit is the single most likely
 * thing a reviewer meets. Saying so plainly is better than a blank box that
 * reads as a broken app.
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
      title: "The demo's model quota for today is used up",
      body:
        "This runs on a free-tier model capped at 50 requests a day, shared across " +
        "everyone using the link. It resets at 05:30 IST. The decisions themselves are " +
        "computed by the rule engine rather than the model, so nothing here is wrong — " +
        "there is just no narrator available right now. The demo video shows the full flow.",
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
