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
import re
import time
from dataclasses import dataclass, field

import httpx

from app import config
from app.agent import loop
from app.auth import principals

CANDIDATES = config.provider_models()

#: A free-tier pool being saturated says nothing about whether a model can hold a
#: tool chain. Scoring it as a chain failure would decide the primary on who
#: happened to be busy during the run, which is not the metric this exists to
#: measure. Runs that fail this way are retried, then reported on their own axis.
UNAVAILABLE = re.compile(
    r"\b(429|50[0234])\b|rate.?limit|temporarily|overloaded|capacity|timeout|timed out",
    re.I,
)


@dataclass(frozen=True)
class Question:
    label: str
    persona: str
    text: str
    #: The decision tool the chain must reach. Anything short of this is an
    #: answer the model composed itself, which is the failure mode that matters.
    must_reach: str
    #: The verdict must survive narration. Patterns, not substrings: "a 4-hour
    #: threshold" and "4 hours" are the same answer, and a matcher that accepts
    #: one but not the other understates a model it should not.
    right: re.Pattern[str]
    #: The confidently-wrong answer this question is engineered to produce. Worth
    #: counting separately -- silence and a wrong verdict are not the same failure.
    wrong: re.Pattern[str]


QUESTIONS = (
    Question(
        label="Q1 cancellation",
        persona="cust-northstar",
        text="Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.",
        must_reach="evaluate_cancellation",
        right=re.compile(
            r"no (cancellation )?fee|without (a|any)( cancellation)? fee"
            r"|fee (is |will be )?waive|no charge|free of charge|₹\s?0\b",
            re.I,
        ),
        # T1/T2: the SOP's ₹250, which TKT-450 also wrongly asserts.
        wrong=re.compile(r"(₹|inr|rs\.?)\s?250|250 (rupee|fee)", re.I),
    ),
    Question(
        label="Q2 service credit",
        persona="cust-lumenworks",
        text=(
            "A pickup is three hours late because of carrier fault. Should I get a service credit?"
        ),
        must_reach="evaluate_service_credit",
        right=re.compile(
            r"not eligible|ineligible|no (service )?credit|not entitled"
            r"|does not (qualify|meet|apply)|doesn't (qualify|meet)|^\s*#*\s*no\b",
            re.I,
        ),
        # T4: the SOP's 2-hour threshold applied to an account whose agreement
        # replaced it with 4 hours.
        wrong=re.compile(r"you (are|would be) eligible|credit (is|of) ₹?\s?\d|₹\s?240", re.I),
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
    attempts: int = 1

    @property
    def unavailable(self) -> bool:
        """Failed because the provider would not serve it, not because it fumbled."""
        return bool(self.failed) and bool(UNAVAILABLE.search(self.failed))

    @property
    def served(self) -> bool:
        return not self.unavailable

    def reached(self, tool_name: str) -> bool:
        """Reached the decision tool *and* survived to produce a turn.

        Counting the tool_call event alone was wrong, and wrong in the most
        misleading direction. A Gemini run that called evaluate_cancellation and
        then died on the next request scored a full chain while producing no
        answer at all -- the summary recommended a primary model on the strength
        of six runs that had every one of them failed. A chain that does not
        finish is not a completed chain.
        """
        return tool_name in self.tools_called and not self.failed

    def verdict(self, question: Question) -> str:
        """The right verdict wins even when the wrong number is also present.

        Deliberate, and the ordering is the whole point. D-15 requires an
        overridden rule to be *disclosed* rather than silently dropped, so the
        correct answer to Q1 says "the SOP would have charged ₹250, but your
        agreement waives it" -- it quotes the wrong number on purpose. Checking
        `wrong` first would score that as a failure and penalise precisely the
        disclosure the design exists to produce.
        """
        if question.right.search(self.answer):
            return "right"
        if question.wrong.search(self.answer):
            return "wrong"
        return "unclear"


async def supports_tool_calling(model: str) -> bool | None:
    """The hard gate. Returns None when the check itself could not be made.

    Only OpenRouter publishes `supported_parameters`, so this can be answered
    from the catalogue there and not on Google, whose model list describes
    generation methods instead. Returning None rather than False is the
    important part: "we could not check" and "this model cannot call tools" are
    different claims, and collapsing them would exclude a working model on the
    strength of a missing field. An unverifiable model proceeds to the run,
    where a failure to call any tool shows up as a chain score of zero anyway.
    """
    if config.PROVIDER != "openrouter":
        return None

    url = f"{config.provider_base_url()}/models"
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


async def run_measured(question: Question, model: str, *, retries: int) -> Run:
    """Retry past a saturated free-tier pool so capability is what gets measured.

    Only capacity failures are retried. A model that fumbles the chain is not
    given a second attempt -- that is the thing being measured.
    """
    record = await one_run(question, model)
    for attempt in range(1, retries + 1):
        if not record.unavailable:
            break
        await asyncio.sleep(2**attempt)
        record = await one_run(question, model)
        record.attempts = attempt + 1
    return record


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--models", nargs="*", default=CANDIDATES)
    parser.add_argument("--retries", type=int, default=3, help="retries per capacity failure")
    parser.add_argument(
        "--dump",
        help="write every answer and its verdict here, so the scoring can be audited "
        "rather than trusted",
    )
    args = parser.parse_args()

    settings = config.provider_settings()
    if not config.api_key():
        print(f"{settings['key_env']} is not set. Add it to .env and re-run.")
        return 1
    print(f"provider: {config.PROVIDER}\n")

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
            runs = [
                await run_measured(question, model, retries=args.retries) for _ in range(args.runs)
            ]
            results[(model, question.label)] = runs
            served = [r for r in runs if r.served]
            reached = sum(r.reached(question.must_reach) for r in served)
            right = sum(r.verdict(question) == "right" for r in served)
            wrong = sum(r.verdict(question) == "wrong" for r in served)
            invented = sum(bool(r.unknown_tools) for r in served)
            median = sorted(r.seconds for r in served)[len(served) // 2] if served else 0.0
            print(
                f"  {model:<44} {question.label:<18} "
                f"served {len(served)}/{args.runs}  chain {reached}/{len(served) or '-'}  "
                f"right {right}  wrong {wrong}  invented {invented}  {median:5.1f}s"
            )

    print("\nSummary\n")
    print("  chain = tool-chain completion, the deciding metric (over served runs only).")
    print(
        "  served = runs the provider actually answered -- a capacity axis, not a capability one.\n"
    )

    ranked = []
    for model in usable:
        runs = [r for q in QUESTIONS for r in results[(model, q.label)]]
        served = [r for r in runs if r.served]
        reached = sum(
            r.reached(q.must_reach)
            for q in QUESTIONS
            for r in results[(model, q.label)]
            if r.served
        )
        right = sum(
            r.verdict(q) == "right"
            for q in QUESTIONS
            for r in results[(model, q.label)]
            if r.served
        )
        chain_rate = reached / len(served) if served else 0.0
        ranked.append((chain_rate, right, len(served), model))
        availability = f"served {len(served)}/{len(runs)}"
        if not served:
            print(f"  {model:<44} {availability} -- NOT RANKABLE, provider never answered")
            continue
        print(
            f"  {model:<44} chain {reached}/{len(served)} ({chain_rate:.0%})  "
            f"right {right}/{len(served)}  {availability}"
        )

    if args.dump:
        _dump(args.dump, results)
        print(f"\n  Answers written to {args.dump} -- read them before trusting the counts.")

    rankable = [entry for entry in ranked if entry[2] > 0]
    if not rankable:
        print("\n  No model was served often enough to rank. Retry when the pool frees up.")
        return 1

    rankable.sort(reverse=True)
    print(f"\n  -> primary should be: {rankable[0][3]}")
    if len(rankable) > 1:
        print(f"     fallback:          {rankable[1][3]}")

    starved = [entry for entry in ranked if entry[2] < len(QUESTIONS) * args.runs]
    if starved:
        print(
            "\n  Note: capacity failures occurred. Availability is a real constraint on a\n"
            "  hosted demo, but it is not chain-completion -- weigh the two separately."
        )
    print("\n  Set these in app/config.py (PRIMARY_MODEL / FALLBACK_MODELS).")

    return 0


def _dump(path: str, results: dict[tuple[str, str], list[Run]]) -> None:
    """Written before the ranking, not after.

    A run where nothing was served produces no ranking at all, and that is
    precisely the run whose error messages you need to read.
    """
    lines = []
    for (model, label), runs in results.items():
        question = next(q for q in QUESTIONS if q.label == label)
        for index, run in enumerate(runs, start=1):
            lines.append(f"=== {model} | {label} | run {index} (attempts: {run.attempts}) ===")
            lines.append(f"verdict: {run.verdict(question)}   chain: {run.tools_called}")
            if run.failed:
                lines.append(f"failed: {run.failed}")
            lines.append(run.answer or "(no answer)")
            lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
