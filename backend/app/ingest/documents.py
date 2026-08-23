"""PDF sources to authority-tagged chunks.

Every chunk carries the authority of its document: tier, status, effective
dates and account scope, all read from `knowledge/documents.yaml`. Nothing is
inferred from the filename. A PDF with no manifest entry aborts the ingest.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from pypdf import PdfReader

from app import config

# A numbered section heading on a line of its own: "2. Severity definitions".
_HEADING = re.compile(r"^(\d+)\.\s+(\S.*)$")

# An identifier-prefixed sub-heading: "KI-208 - Bulk Upload failures on large CSVs".
# Length-guarded so a body sentence that merely starts with an identifier is not
# mistaken for a heading.
_SUBHEADING = re.compile(r"^([A-Z]{2,}-\d+)\s*[-–]\s*(\S.*)$")
_SUBHEADING_MAX_LEN = 80

_BULLET = "●"


class IngestError(RuntimeError):
    """Raised when the sources and the manifest disagree. Never recovered from."""


@dataclass(frozen=True)
class DocumentMeta:
    doc_id: str
    title: str
    authority_tier: int
    status: str
    effective_from: str
    scope: str
    effective_to: str | None = None
    version: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    #: How many top-level numbered sections this document is known to contain.
    #: Structure is declared here for the same reason authority is: so that a
    #: document losing its shape is a failure, not a silent downgrade. Zero
    #: means the document genuinely has none -- Policy v2 is a single notice.
    min_sections: int = 0


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    section: str
    text: str
    authority_tier: int
    status: str
    effective_from: str
    effective_to: str | None
    scope: str
    version: str | None


def load_manifest(path: Path | None = None) -> dict[str, DocumentMeta]:
    raw = yaml.safe_load((path or config.DOCUMENTS_MANIFEST).read_text())
    metas: dict[str, DocumentMeta] = {}
    for entry in raw["documents"]:
        entry = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in entry.items()}
        meta = DocumentMeta(**entry)
        if meta.doc_id in metas:
            raise IngestError(f"duplicate doc_id in manifest: {meta.doc_id}")
        if meta.authority_tier not in (1, 2, 3, 4):
            raise IngestError(f"{meta.doc_id}: authority_tier must be 1-4")
        if meta.authority_tier == 1 and not meta.scope.startswith("account:"):
            raise IngestError(
                f"{meta.doc_id}: a tier-1 document is a signed customer agreement "
                f"and must be scoped to one account, not {meta.scope!r}"
            )
        metas[meta.doc_id] = meta
    return metas


def _normalise(fragment: str) -> str:
    """Collapse the extractor's spacing, then restore bullets as line breaks.

    The source PDFs are typeset with wide inter-word spacing and hard-wrap in
    the middle of sentences, so a naive extraction is unreadable. Collapsing all
    whitespace and re-splitting on the bullet glyph recovers the structure.
    """
    text = re.sub(r"\s+", " ", fragment.replace("\xa0", " ")).strip()
    if _BULLET in text:
        parts = [p.strip() for p in text.split(_BULLET)]
        lead, items = parts[0], [p for p in parts[1:] if p]
        text = "\n".join(([lead] if lead else []) + [f"- {p}" for p in items])
    return text.strip()


def split_sections(raw_text: str) -> list[tuple[str, str]]:
    """Split extracted text into (section label, body) pairs.

    Text before the first numbered heading becomes a "Header" section: it holds
    the status and effective-date line, which is worth citing on its own.
    """
    sections: list[tuple[str, list[str]]] = [("Header", [])]

    for line in raw_text.splitlines():
        stripped = _normalise(line)
        if not stripped:
            continue
        if heading := _HEADING.match(stripped):
            sections.append((f"{heading.group(1)}. {heading.group(2)}".rstrip(" ."), []))
            continue
        if (sub := _SUBHEADING.match(stripped)) and len(stripped) <= _SUBHEADING_MAX_LEN:
            parent = sections[-1][0].split(" / ")[0]
            sections.append((f"{parent} / {sub.group(1)}", [stripped]))
            continue
        sections[-1][1].append(line)

    out = []
    for label, lines in sections:
        body = _normalise(" ".join(lines))
        if body:
            out.append((label, body))
    return out


def chunk_document(pdf_path: Path, meta: DocumentMeta) -> list[Chunk]:
    reader = PdfReader(pdf_path)
    raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not raw.strip():
        raise IngestError(f"{pdf_path.name}: no extractable text")

    sections = split_sections(raw)
    # Distinct top-level numbers, not labels: a section split into subheadings
    # ("2. Current known issues / KI-208") is still one numbered section.
    numbered = len({label.split(".", 1)[0] for label, _ in sections if label[0].isdigit()})
    if numbered < meta.min_sections:
        raise IngestError(
            f"{pdf_path.name}: the manifest declares at least {meta.min_sections} numbered "
            f"sections and extraction found {numbered}. The document has not changed, so the "
            f"PDF extractor has -- check the pypdf version. Indexing it anyway would serve a "
            f"document with no citable clauses, which reads as a retrieval failure rather than "
            f"a parsing one."
        )

    chunks = []
    for n, (section, body) in enumerate(sections):
        chunks.append(
            Chunk(
                chunk_id=f"{meta.doc_id}#{n}",
                doc_id=meta.doc_id,
                doc_title=meta.title,
                section=section,
                text=body,
                authority_tier=meta.authority_tier,
                status=meta.status,
                effective_from=meta.effective_from,
                effective_to=meta.effective_to,
                scope=meta.scope,
                version=meta.version,
            )
        )
    return chunks


def ingest_documents(source_dir: Path | None = None, out_path: Path | None = None) -> list[Chunk]:
    source_dir = source_dir or config.SOURCE_DIR
    out_path = out_path or config.CHUNKS_PATH
    manifest = load_manifest()

    pdfs = {p.stem: p for p in sorted(source_dir.glob("*.pdf"))}

    unmanifested = sorted(set(pdfs) - set(manifest))
    if unmanifested:
        raise IngestError(
            "these PDFs have no entry in knowledge/documents.yaml: "
            + ", ".join(f"{name}.pdf" for name in unmanifested)
            + ". An untagged document has unknown authority; refusing to guess it."
        )

    missing = sorted(set(manifest) - set(pdfs))
    if missing:
        raise IngestError(
            "the manifest declares documents that are not in data/source/: " + ", ".join(missing)
        )

    chunks: list[Chunk] = []
    for doc_id, meta in manifest.items():
        chunks.extend(chunk_document(pdfs[doc_id], meta))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([asdict(c) for c in chunks], indent=2, ensure_ascii=False))
    return chunks
