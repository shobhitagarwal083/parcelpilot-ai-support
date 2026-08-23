import { useEffect, useRef, useState } from "react";
import { ApiError, streamChat, type ChatTurn } from "../lib/api";
import type { Citation, Decision, Persona, ProposedAction, ToolStep, Turn } from "../lib/types";
import { ActionCard } from "./ActionCard";
import { CitationCard } from "./CitationCard";
import { DecisionCard } from "./DecisionCard";
import { ErrorNotice } from "./ErrorNotice";
import { ToolTrace } from "./ToolTrace";

const CUSTOMER_PROMPTS = [
  "Can I cancel ORD-1001 without a cancellation fee? Explain why.",
  "A pickup is three hours late because of carrier fault. Should I get a service credit?",
  "When will someone respond to my open ticket?",
];

const INTERNAL_PROMPTS = [
  "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.",
  "Which tickets have breached their SLA right now?",
  "Why is LumenWorks' 4,200-row CSV upload failing?",
  "Is any account owed a service credit nobody has claimed?",
];

function emptyTurn(question: string): Turn {
  return {
    id: `turn-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    question,
    answer: "",
    steps: [],
    citations: [],
    actions: [],
    escalations: [],
    streaming: true,
  };
}

export function Chat({ persona }: { persona: Persona }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const textarea = useRef<HTMLTextAreaElement | null>(null);

  /* Switching persona clears the transcript.
   *
   * Leaving a LumenWorks answer on screen while authenticated as Northstar
   * would imply a leak the backend does not actually have. Cheap to get right,
   * badly misleading to leave wrong. */
  useEffect(() => {
    abort.current?.abort();
    setTurns([]);
    setDraft("");
    setBusy(false);
  }, [persona.id]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  function patchLast(mutate: (turn: Turn) => Turn) {
    setTurns((current) => {
      if (!current.length) return current;
      const copy = [...current];
      copy[copy.length - 1] = mutate(copy[copy.length - 1]);
      return copy;
    });
  }

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    const history: ChatTurn[] = turns.flatMap<ChatTurn>((turn) =>
      turn.answer
        ? [
            { role: "user", content: turn.question },
            { role: "assistant", content: turn.answer },
          ]
        : [{ role: "user", content: turn.question }],
    );

    setTurns((current) => [...current, emptyTurn(trimmed)]);
    setDraft("");
    setBusy(true);

    const controller = new AbortController();
    abort.current = controller;

    try {
      for await (const event of streamChat(persona.id, trimmed, history, controller.signal)) {
        const data = event.data as Record<string, never>;

        switch (event.kind) {
          case "token":
            patchLast((turn) => ({ ...turn, answer: turn.answer + (data.text ?? "") }));
            break;

          case "tool_call":
            patchLast((turn) => ({
              ...turn,
              steps: [
                ...turn.steps,
                {
                  id: String(data.id),
                  name: String(data.name),
                  label: String(data.label ?? data.name),
                  input: (data.input ?? {}) as Record<string, unknown>,
                  state: "running",
                } satisfies ToolStep,
              ],
            }));
            break;

          case "tool_result":
            patchLast((turn) => ({
              ...turn,
              steps: turn.steps.map((step) =>
                step.id === String(data.id)
                  ? {
                      ...step,
                      state: data.is_error ? "error" : "ok",
                      summary: data.summary ? String(data.summary) : undefined,
                      decision: (data.decision ?? null) as Decision | null,
                    }
                  : step,
              ),
            }));
            break;

          case "citation":
            patchLast((turn) => ({
              ...turn,
              citations: [...turn.citations, data as unknown as Citation],
            }));
            break;

          case "action_proposed":
            patchLast((turn) => ({
              ...turn,
              actions: [...turn.actions, data as unknown as ProposedAction],
            }));
            break;

          case "escalation":
            patchLast((turn) => ({
              ...turn,
              escalations: [...turn.escalations, String(data.reason ?? "")],
            }));
            break;

          case "error":
            patchLast((turn) => ({
              ...turn,
              error: { message: String(data.message ?? ""), kind: data.kind },
            }));
            break;

          case "done":
            patchLast((turn) => ({ ...turn, streaming: false }));
            break;
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        patchLast((turn) => ({
          ...turn,
          error: {
            message: error instanceof ApiError ? `${error.status} — ${error.message}` : String(error),
            kind: error instanceof ApiError && error.status === 429 ? "rate_limited" : undefined,
          },
        }));
      }
    } finally {
      patchLast((turn) => ({ ...turn, streaming: false }));
      setBusy(false);
      abort.current = null;
    }
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(draft);
    }
  }

  const prompts = persona.kind === "customer" ? CUSTOMER_PROMPTS : INTERNAL_PROMPTS;

  return (
    <>
      <div className="pane">
        <div className="pane-inner">
          {turns.length === 0 && (
            <div className="empty">
              <h2>Ask about an order, a ticket, or a policy</h2>
              <p>
                Answers are resolved against the account's own agreement first, then current
                policy, then product documentation. You are signed in as{" "}
                <strong>{persona.display_name}</strong>.
              </p>
              {prompts.map((prompt) => (
                <button key={prompt} className="suggestion" onClick={() => void ask(prompt)}>
                  {prompt}
                </button>
              ))}
            </div>
          )}

          {turns.map((turn) => (
            <div className="turn" key={turn.id}>
              <div className="turn-user">
                <div className="bubble-user">{turn.question}</div>
              </div>

              <div className="turn-agent" style={{ marginTop: 14 }}>
                <ToolTrace steps={turn.steps} live={turn.streaming} />

                {turn.steps
                  .filter((step) => step.decision)
                  .map((step) => (
                    <DecisionCard
                      key={`${step.id}-decision`}
                      decision={step.decision as Decision}
                      audience={persona.kind}
                    />
                  ))}

                {/* Citations the decisions did not already carry. */}
                {turn.citations
                  .filter(
                    (citation) =>
                      !turn.steps.some((step) =>
                        step.decision?.citations?.some(
                          (owned) =>
                            owned.doc_id === citation.doc_id && owned.section === citation.section,
                        ),
                      ),
                  )
                  .map((citation, index) => (
                    <CitationCard key={`${citation.doc_id}-${index}`} citation={citation} />
                  ))}

                {turn.escalations.map((reason, index) => (
                  <div className="escalation" key={index}>
                    <strong>Handing this to a person.</strong> {reason}
                  </div>
                ))}

                {turn.answer && <div className="answer">{turn.answer}</div>}

                {turn.actions.map((action) => (
                  <ActionCard
                    key={action.action_id}
                    action={action}
                    persona={persona}
                    onResolved={(updated) =>
                      setTurns((current) =>
                        current.map((item) => ({
                          ...item,
                          actions: item.actions.map((existing) =>
                            existing.action_id === updated.action_id ? updated : existing,
                          ),
                        })),
                      )
                    }
                  />
                ))}

                {turn.error && <ErrorNotice error={turn.error} />}

                {turn.streaming && !turn.answer && (
                  <div className="thinking">
                    <span className="dot" /> working…
                  </div>
                )}
              </div>
            </div>
          ))}

          <div ref={bottom} />
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          <textarea
            ref={textarea}
            rows={1}
            value={draft}
            placeholder={`Ask as ${persona.display_name}…`}
            onChange={(event) => {
              setDraft(event.target.value);
              const node = event.target;
              node.style.height = "auto";
              node.style.height = `${Math.min(node.scrollHeight, 168)}px`;
            }}
            onKeyDown={onKeyDown}
          />
          <button className="btn btn-primary" disabled={busy || !draft.trim()} onClick={() => void ask(draft)}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      </div>
    </>
  );
}
