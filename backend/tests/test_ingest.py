"""Ingest is where authority is assigned. These tests pin the ways it can fail."""

from __future__ import annotations

import json
import shutil

import pytest

from app import config
from app.ingest.documents import (
    IngestError,
    ingest_documents,
    load_manifest,
    split_sections,
)
from app.ingest.structured import ingest_structured


@pytest.fixture
def source_copy(tmp_path):
    dest = tmp_path / "source"
    shutil.copytree(config.SOURCE_DIR, dest)
    return dest


def test_unmanifested_pdf_aborts_the_ingest(source_copy, tmp_path):
    """An untagged document has unknown authority. Guessing it is the failure
    this whole design exists to prevent, so the ingest refuses to proceed."""
    (source_copy / "07_Some_Untagged_Policy.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(IngestError) as exc:
        ingest_documents(source_dir=source_copy, out_path=tmp_path / "chunks.json")

    assert "07_Some_Untagged_Policy.pdf" in str(exc.value)
    assert not (tmp_path / "chunks.json").exists()


def test_manifest_entry_without_a_file_aborts_the_ingest(source_copy, tmp_path):
    (source_copy / "06_LumenWorks_Service_Agreement.pdf").unlink()

    with pytest.raises(IngestError, match="06_LumenWorks_Service_Agreement"):
        ingest_documents(source_dir=source_copy, out_path=tmp_path / "chunks.json")


def test_every_source_pdf_is_manifested():
    manifest = load_manifest()
    on_disk = {p.stem for p in config.SOURCE_DIR.glob("*.pdf")}
    assert on_disk == set(manifest)


def test_tier_one_documents_are_scoped_to_one_account():
    """A tier-1 document is a signed customer agreement. A global one would let
    one customer's negotiated terms outrank policy for everybody."""
    for meta in load_manifest().values():
        if meta.authority_tier == 1:
            assert meta.scope.startswith("account:"), meta.doc_id


def test_deprecated_status_comes_from_the_manifest_not_the_filename():
    """Policy v2 is excluded because the manifest says DEPRECATED, not because
    its filename happens to contain the word."""
    manifest = load_manifest()
    assert manifest["02_Support_Policy_v2_DEPRECATED"].status == "DEPRECATED"
    assert manifest["01_Support_Policy_v3_CURRENT"].status == "CURRENT"
    assert all("DEPRECATED" not in m.doc_id for m in manifest.values() if m.status != "DEPRECATED")


def test_chunks_carry_authority_from_their_document(source_copy, tmp_path):
    chunks = ingest_documents(source_dir=source_copy, out_path=tmp_path / "chunks.json")

    for chunk in chunks:
        assert chunk.authority_tier in (1, 2, 3)
        assert chunk.status in ("CURRENT", "ACTIVE", "DEPRECATED")
        assert chunk.effective_from
        assert chunk.scope
        assert chunk.text.strip()

    northstar = [c for c in chunks if c.doc_id.startswith("05_")]
    assert northstar and all(c.scope == "account:ACCT-001" for c in northstar)
    assert all(c.authority_tier == 1 for c in northstar)


def test_the_clauses_the_engine_depends_on_survive_extraction(source_copy, tmp_path):
    """Section splitting has to keep each operative clause intact and citable."""
    chunks = {
        (c.doc_id, c.section): c.text
        for c in ingest_documents(source_dir=source_copy, out_path=tmp_path / "chunks.json")
    }

    waiver = chunks[("05_Northstar_Logistics_Enterprise_Agreement", "2. Shipment cancellation")]
    assert "no cancellation fee, regardless of how long ago" in waiver

    lumen = chunks[("06_LumenWorks_Service_Agreement", "3. Failed-pickup credits")]
    assert "more than 4 hours" in lumen
    assert "fixed INR 300" in lumen
    assert "replaces the default failed-pickup credit amount and timing threshold" in lumen

    sop = chunks[("03_Cancellation_and_Service_Credit_SOP_v4", "1. Order cancellation")]
    assert "No fee within 30 minutes of booking" in sop
    assert "charge INR 250 unless a customer agreement explicitly waives" in sop

    ladder = chunks[("01_Support_Policy_v3_CURRENT", "1. Scope and source precedence")]
    assert "signed customer agreement first, then the current support policy" in ladder


def test_section_splitter_keeps_bullets_and_numbered_headings():
    sections = dict(
        split_sections(
            "Doc Title\n"
            "Status:  CURRENT\n"
            "1.  First  section\n"
            "●  alpha  item.  ●  beta  item.\n"
            "2.  Second  section\n"
            "body  text  here\n"
        )
    )
    assert sections["Header"] == "Doc Title Status: CURRENT"
    assert sections["1. First section"] == "- alpha item.\n- beta item."
    assert sections["2. Second section"] == "body text here"


def test_snapshot_is_read_from_the_workbook_and_is_a_sunday():
    """Three of the five open tickets are under business-hours coverage, so a
    Sunday snapshot means their SLA clocks have not started at all."""
    assert config.SNAPSHOT_AT.strftime("%A") == "Sunday"
    assert config.SNAPSHOT_AT.isoformat() == "2026-08-16T11:00:00+05:30"
    written = json.loads(config.SNAPSHOT_PATH.read_text())["snapshot_at"]
    assert written == "2026-08-16T11:00:00+05:30"


def test_structured_ingest_is_reproducible(tmp_path):
    first = ingest_structured(db_path=tmp_path / "a.db")
    second = ingest_structured(db_path=tmp_path / "b.db")
    assert first == second
    assert first["accounts"] == 4
    assert first["orders"] == 6
    assert first["tickets"] == 7
