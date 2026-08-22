"""The chunk index, loaded once from what ingest produced."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import config


class IndexMissing(RuntimeError):
    pass


@lru_cache(maxsize=4)
def load_chunks(path: Path | None = None) -> tuple[dict[str, Any], ...]:
    path = path or config.CHUNKS_PATH
    if not path.exists():
        raise IndexMissing(
            f"{path} does not exist. Run `python -m app.ingest` to build it from data/source/."
        )
    return tuple(json.loads(path.read_text()))
