/* The API client.
 *
 * The principal travels as a header the server resolves for itself. It is never
 * a body field and never a query parameter, mirroring the backend rule that the
 * caller names a persona and the server decides what that persona can see.
 */

import { readEventStream, type ServerEvent } from "./sse";
import type { Persona, ProposedAction, Signal } from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function headers(personaId: string): HeadersInit {
  return { "Content-Type": "application/json", "X-Principal-Id": personaId };
}

async function unwrap(response: Response): Promise<unknown> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (body as { detail?: string }).detail ?? response.statusText;
    throw new ApiError(response.status, detail);
  }
  return body;
}

export async function fetchPersonas(): Promise<{ default: string; personas: Persona[] }> {
  const response = await fetch(`${BASE}/session/personas`);
  return (await unwrap(response)) as { default: string; personas: Persona[] };
}

export async function fetchSignals(personaId: string): Promise<Signal[]> {
  const response = await fetch(`${BASE}/signals`, { headers: headers(personaId) });
  const body = (await unwrap(response)) as { signals: Signal[] };
  return body.signals ?? [];
}

export async function fetchActions(
  personaId: string,
  status?: string,
): Promise<ProposedAction[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await fetch(`${BASE}/actions${query}`, { headers: headers(personaId) });
  const body = (await unwrap(response)) as { actions: ProposedAction[] };
  return body.actions ?? [];
}

export async function resolveAction(
  personaId: string,
  actionId: string,
  decision: "confirm" | "reject",
  note?: string,
): Promise<ProposedAction> {
  const response = await fetch(`${BASE}/actions/${actionId}/${decision}`, {
    method: "POST",
    headers: headers(personaId),
    body: decision === "reject" ? JSON.stringify({ reason: note ?? "" }) : undefined,
  });
  return (await unwrap(response)) as ProposedAction;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

/** Opens the chat stream and yields events as they arrive. */
export async function* streamChat(
  personaId: string,
  message: string,
  history: ChatTurn[],
  signal: AbortSignal,
): AsyncGenerator<ServerEvent> {
  const response = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: headers(personaId),
    body: JSON.stringify({ message, history }),
    signal,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body.slice(0, 400) || response.statusText);
  }

  yield* readEventStream(response, signal);
}
