"""Phase 05a -- decide which free model drives the agent, by measurement.

The question is not which model is smarter. It is which one reliably completes a
five-to-six call tool chain without dropping a step or inventing a tool name,
because the engine already owns correctness and the model only has to route.

Run:  python -m scripts.bakeoff            (both models, both questions, 5 runs)
      python -m scripts.bakeoff --runs 3

It also performs the hard gate first: a model that does not support native tool
calling cannot drive this agent at all, however well it scores on anything else.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass, field

import httpx

from app import config
from app.agent import loop
from app.auth import principals

CANDIDATES = [config.PRIMARY_MODEL, *config.FALLBACK_MODELS]


@dataclass(frozen=True)
class Question:
    label: str
    persona: str
    text: str
    #: The decision tool the chain must reach. Anything short of this is an
    #: answer the model composed itself, which is the failure mode that matters.
    must_reach: str
    #: Phrases that indicate the right answer survived narration.
    expects_any: tuple[str, ...]


QUESTIONS = (
    Question(
        label="Q1 cancellation",
        persona="cust-northstar",
        text="Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.",
        must_reach="evaluate_cancellation",
        expects_any=("no fee", "no cancellation fee", "without a fee", "0"),
    ),
    Question(
        label="Q2 service credit",
        persona="cust-lumenworks",
        text=(
            "A pickup is three hours late because of carrier fault. "
            "Should I get a service credit?"
        ),
        must_reach="evaluate_service_credit",
        expects_any=("not eligible", "no credit", "4 hours", "four hours"),
    ),
)


@dataclass
class Run:
    tools_called: list[str] = field(default_factory=list)
    unknown_tools: list[str] = field(default_factory=list)
    tool_errors: int = 0
    answer: str = ""
    seconds: float = 0.0
    failed: str | None = None

    def reached(self, tool_name: str) -> bool:
        return tool_name in self.tools_called

    def answered_correctly(self, expects_any: tuple[str, ...]) -> bool:
        lowered = self.answer.lower()
        return any(phrase in lowered for phrase in expects_any)


async def supports_tool_calling(model: str) -> bool | None:
    """The hard gate. Returns None if the check itself could not be made."""
    url = f"{config.OPENROUTER_BASE_URL}/models"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            for entry in response.json().get("data", []):
                if entry.get("id") == model:
                    return "tools" in (entry.get("supported_parameters") or [])
    except Exception:
        return None
    return False


async def one_run(question: Question, model: str) -> Run:
    principal = principals.get(question.persona)
    record = Run()
    started = time.perf_counter()
    answer_parts: list[str] = []

    try:
        async for event in loop.run(
            principal,
            [{"role": "user", "content": question.text}],
            as_of=config.SNAPSHOT_AT,
            model=model,
        ):
            if event.kind == "token":
                answer_parts.append(event.data["text"])
            elif event.kind == "tool_call":
                name = event.data["name"]
                record.tools_called.append(name)
                from app.agent.tools import REGISTRY

                if REGISTRY.get(name) is None:
                    record.unknown_tools.append(name)
            elif event.kind == "tool_result" and event.data.get("is_error"):
                record.tool_errors += 1
            elif event.kind == "error":
                record.failed = event.data.get("message", "unknown error")
    except Exception as exc:  # noqa: BLE001 - a bake-off should survive anything
        record.failed = f"{type(exc).__name__}: {exc}"

    record.seconds = time.perf_counter() - started
    record.answer = "".join(answer_parts)
    return record


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--models", nargs="*", default=CANDIDATES)
    args = parser.parse_args()

    if not config.openrouter_api_key():
        print("OPENROUTER_API_KEY is not set. Add it to .env and re-run.")
        return 1

    print("Hard gate -- native tool calling support\n")
    usable = []
    for model in args.models:
        supported = await supports_tool_calling(model)
        mark = {True: "yes", False: "NO", None: "could not check"}[supported]
        print(f"  {model:<44} tools: {mark}")
        if supported is False:
            print("      -> cannot drive this agent at all; excluded.")
        else:
            usable.append(model)

    if not usable:
        print("\nNo candidate supports tool calling. Stopping.")
        return 1

    results: dict[tuple[str, str], list[Run]] = {}
    print(f"\nRunning {args.runs} x {len(QUESTIONS)} questions per model\n")

    for model in usable:
        for question in QUESTIONS:
            runs = [await one_run(question, model) for _ in range(args.runs)]
            results[(model, question.label)] = runs
            reached = sum(r.reached(question.must_reach) for r in runs)
            correct = sum(r.answered_correctly(question.expects_any) for r in runs)
            invented = sum(bool(r.unknown_tools) for r in runs)
            failed = sum(bool(r.failed) for r in runs)
            median = sorted(r.seconds for r in runs)[len(runs) // 2]
            print(
                f"  {model:<44} {question.label:<18} "
                f"chain {reached}/{args.runs}  answer {correct}/{args.runs}  "
                f"invented {invented}  failed {failed}  {median:5.1f}s"
            )

    print("\nSummary -- tool-chain completion rate is the deciding metric\n")
    ranked = []
    for model in usable:
        runs = [r for q in QUESTIONS for r in results[(model, q.label)]]
        reached = sum(
            r.reached(q.must_reach) for q in QUESTIONS for r in results[(model, q.label)]
        )
        correct = sum(
            r.answered_correctly(q.expects_any)
            for q in QUESTIONS
            for r in results[(model, q.label)]
        )
        total = len(runs)
        ranked.append((reached / total, correct / total, model))
        print(
            f"  {model:<44} chain {reached}/{total} ({reached / total:.0%})  "
            f"answer {correct}/{total} ({correct / total:.0%})"
        )

    ranked.sort(reverse=True)
    print(f"\n  -> primary should be: {ranked[0][2]}")
    if len(ranked) > 1:
        print(f"     fallback:          {ranked[1][2]}")
    print("\n  Set these in app/config.py (PRIMARY_MODEL / FALLBACK_MODELS).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
