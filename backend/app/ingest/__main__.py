"""`python -m app.ingest` — rebuild every derived artefact from data/source/.

Deliberately destructive and fully reproducible: the chunk index and the
database are rebuilt from scratch each run, so nothing can accumulate state
that is not traceable to a supplied file.
"""

from __future__ import annotations

import sys

from app import config
from app.ingest.documents import IngestError, ingest_documents
from app.ingest.structured import ingest_structured


def main() -> int:
    try:
        chunks = ingest_documents()
        counts = ingest_structured()
    except IngestError as exc:
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1

    by_tier: dict[int, int] = {}
    for chunk in chunks:
        by_tier[chunk.authority_tier] = by_tier.get(chunk.authority_tier, 0) + 1

    print(f"snapshot   {counts['snapshot_at']}  (from the workbook's own README sheet)")
    print(f"documents  {len(chunks)} chunks  " + "  ".join(
        f"tier{t}={n}" for t, n in sorted(by_tier.items())
    ))
    deprecated = sum(1 for c in chunks if c.status == "DEPRECATED")
    print(f"           {deprecated} chunk(s) marked DEPRECATED and excluded from default search")
    print(f"structured accounts={counts['accounts']} orders={counts['orders']} "
          f"tickets={counts['tickets']}")
    print(f"wrote      {config.CHUNKS_PATH.relative_to(config.ROOT_DIR)}")
    print(f"           {config.DB_PATH.relative_to(config.ROOT_DIR)}")
    print(f"           {config.SNAPSHOT_PATH.relative_to(config.ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
