"""One module owns "now". This test is what keeps it that way.

Every timing answer in the system -- SLA breach, cancellation grace window,
pickup lateness -- derives from `config.SNAPSHOT_AT`. A stray call to the wall
clock anywhere else breaks all of them at once, silently, and only becomes
visible when somebody checks a number by hand.

Scanning is done over tokens rather than raw lines, so a docstring that
*discusses* the wall clock does not trip the check while a call that uses one
cannot hide inside a comment.
"""

from __future__ import annotations

import io
import re
import tokenize

from app import config

FORBIDDEN = re.compile(r"\b(?:datetime\.now|date\.today|time\.time|datetime\.utcnow)\s*\(")

#: The one file allowed to resolve the snapshot.
ALLOWED = {"config.py"}


def _code_lines(source: str) -> list[tuple[int, str]]:
    """Source lines with comments and string literals blanked out."""
    lines = source.splitlines()
    blanked = list(lines)
    readline = io.StringIO(source).readline
    for token in tokenize.generate_tokens(readline):
        if token.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        start_row, end_row = token.start[0] - 1, token.end[0] - 1
        for row in range(start_row, end_row + 1):
            blanked[row] = ""
    return [(n, text) for n, text in enumerate(blanked, start=1) if text.strip()]


def test_w1_the_wall_clock_is_read_nowhere_but_config():
    offenders = []
    for path in sorted((config.BACKEND_DIR / "app").rglob("*.py")):
        if path.name in ALLOWED:
            continue
        for number, line in _code_lines(path.read_text()):
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(config.BACKEND_DIR)}:{number}: {line.strip()}")

    assert not offenders, (
        "the wall clock is only allowed in config.py, because every timing answer "
        "in this system derives from the pinned snapshot:\n  " + "\n  ".join(offenders)
    )


def test_the_snapshot_is_a_constant_not_a_computation():
    """config.py does not call the clock either.

    The snapshot comes from the dataset's own README sheet, which is what makes
    every answer reproducible -- these tests return the same values in 2030.
    """
    source = (config.BACKEND_DIR / "app" / "config.py").read_text()
    calls = [line for _, line in _code_lines(source) if FORBIDDEN.search(line)]
    assert calls == []
