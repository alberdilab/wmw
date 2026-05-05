"""Tests for wmw.sra — NCBI SRA Entrez queries."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import patch, MagicMock

import pytest


def _make_experiment_package_xml(
    run_acc="SRR001", study_acc="PRJNA001", total_bases="10000000000"
) -> str:
    return f"""<EXPERIMENT_PACKAGE>
  <STUDY accession="{study_acc}">
    <DESCRIPTOR>
      <STUDY_TITLE>Test study</STUDY_TITLE>
    </DESCRIPTOR>
  </STUDY>
  <EXPERIMENT accession="SRX001">
    <LIBRARY_DESCRIPTOR>
      <LIBRARY_STRATEGY>WGS</LIBRARY_STRATEGY>
      <LIBRARY_SOURCE>METAGENOMIC</LIBRARY_SOURCE>
      <LIBRARY_LAYOUT><PAIRED/></LIBRARY_LAYOUT>
    </LIBRARY_DESCRIPTOR>
    <PLATFORM>
      <ILLUMINA>
        <INSTRUMENT_MODEL>Illumina NovaSeq 6000</INSTRUMENT_MODEL>
      </ILLUMINA>
    </PLATFORM>
  </EXPERIMENT>
  <SAMPLE accession="SAMN001">
    <SCIENTIFIC_NAME>gut metagenome</SCIENTIFIC_NAME>
    <TAXON_ID>749906</TAXON_ID>
  </SAMPLE>
  <RUN accession="{run_acc}" total_bases="{total_bases}" total_spots="66666666" published="2024-03-01"/>
</EXPERIMENT_PACKAGE>"""


# ---------------------------------------------------------------------------
# _parse_run_element
# ---------------------------------------------------------------------------

def test_parse_run_element():
    from wmw.sra import _parse_run_element
    elem = ET.fromstring(_make_experiment_package_xml("SRR999", "PRJNA999"))
    result = _parse_run_element(elem)
    assert result is not None
    assert result["run_accession"] == "SRR999"
    assert result["study_accession"] == "PRJNA999"
    assert result["library_strategy"] == "WGS"
    assert result["library_layout"] == "PAIRED"
    assert result["source"] == "SRA"
    assert result["base_count"] == "10000000000"
    assert result["host_tax_id"] == ""  # always empty for SRA


def test_parse_run_element_no_run():
    from wmw.sra import _parse_run_element
    elem = ET.fromstring("<EXPERIMENT_PACKAGE><STUDY accession='P'/></EXPERIMENT_PACKAGE>")
    assert _parse_run_element(elem) is None


# ---------------------------------------------------------------------------
# search_runs term construction (mocked Entrez)
# ---------------------------------------------------------------------------

def _mock_entrez(ids=None, xml_str="<ExperimentPackageSet/>"):
    """Return (mock_esearch_handle, mock_efetch_handle) for patching Entrez."""
    ids = ids or []
    esearch_handle = MagicMock()
    esearch_handle.__enter__ = MagicMock(return_value=esearch_handle)
    esearch_handle.__exit__ = MagicMock(return_value=False)

    efetch_handle = MagicMock()
    efetch_handle.read.return_value = xml_str.encode()

    return ids, esearch_handle, efetch_handle


def _patch_entrez(term_parts_check=None, ids=None, xml="<ExperimentPackageSet/>"):
    """Context manager factory that patches Entrez.esearch and Entrez.efetch."""
    from unittest.mock import patch as _patch, MagicMock
    ids = ids or []

    def _esearch_side_effect(db, term, **kwargs):
        if term_parts_check:
            for part in term_parts_check:
                assert part in term, f"Expected {part!r} in Entrez term:\n{term}"
        handle = MagicMock()
        handle.close = MagicMock()
        return handle

    def _read_side_effect(handle):
        return {"IdList": ids}

    efetch_handle = MagicMock()
    efetch_handle.read.return_value = xml.encode()
    efetch_handle.close = MagicMock()

    p1 = _patch("wmw.sra.Entrez.esearch", side_effect=_esearch_side_effect)
    p2 = _patch("wmw.sra.Entrez.read", side_effect=_read_side_effect)
    p3 = _patch("wmw.sra.Entrez.efetch", return_value=efetch_handle)
    p4 = _patch("wmw.sra._AVAILABLE", True)
    return p1, p2, p3, p4


def _run_search(term_parts=(), **kwargs):
    """Run sra.search_runs with Entrez fully mocked; assert term_parts appear in the term."""
    from wmw import sra

    captured_terms = []

    def fake_esearch(db, term, **kw):
        captured_terms.append(term)
        h = MagicMock()
        h.close = MagicMock()
        return h

    def fake_read(h):
        return {"IdList": []}

    with patch("wmw.sra.Entrez.esearch", side_effect=fake_esearch), \
         patch("wmw.sra.Entrez.read", side_effect=fake_read), \
         patch("wmw.sra._AVAILABLE", True):
        sra.search_runs(date_from="2024-01-01", date_to="2024-12-31", **kwargs)

    assert captured_terms, "esearch was never called"
    term = captured_terms[0]
    for part in term_parts:
        assert part in term, f"Expected {part!r} in SRA term:\n{term}"
    return term


def test_sra_library_source():
    _run_search(term_parts=['"METAGENOMIC"[Source]'], library_source="METAGENOMIC")


def test_sra_library_source_multiple():
    term = _run_search(library_source="METAGENOMIC,METATRANSCRIPTOMIC")
    assert '"METAGENOMIC"[Source]' in term
    assert '"METATRANSCRIPTOMIC"[Source]' in term


def test_sra_instrument_platform_illumina():
    _run_search(term_parts=['"illumina"[Platform]'], instrument_platform="ILLUMINA")


def test_sra_keyword():
    _run_search(term_parts=['"fox"[Title]'], keyword="fox")


def test_sra_host_tax_id():
    _run_search(term_parts=["txid7742[Organism:exp]"], host_tax_id="7742")


def test_sra_exclusion_single():
    _run_search(term_parts=["NOT txid9606[Organism:exp]"], exclude_tax_ids=["9606"])


def test_sra_exclusion_multiple():
    term = _run_search(exclude_tax_ids=["9606", "9913"])
    assert "NOT txid9606[Organism:exp]" in term
    assert "NOT txid9913[Organism:exp]" in term


def test_sra_date_range_in_term():
    _run_search(term_parts=['"2024-01-01"[PDAT]', '"2024-12-31"[PDAT]'])


# ---------------------------------------------------------------------------
# Post-fetch min_bases filter
# ---------------------------------------------------------------------------

def test_sra_min_bases_post_fetch_filter():
    """Runs with base_count below min_bases should be dropped after fetch."""
    from wmw import sra

    xml_low  = _make_experiment_package_xml("SRR001", "PRJNA001", total_bases="500000000")
    xml_high = _make_experiment_package_xml("SRR002", "PRJNA001", total_bases="5000000000")
    combined = (
        "<ExperimentPackageSet>"
        + xml_low.replace('<?xml version="1.0"?>', "")
        + xml_high.replace('<?xml version="1.0"?>', "")
        + "</ExperimentPackageSet>"
    )

    def fake_esearch(db, term, **kw):
        h = MagicMock(); h.close = MagicMock(); return h

    def fake_read(h):
        return {"IdList": ["1", "2"]}

    efetch_h = MagicMock()
    efetch_h.read.return_value = combined.encode()
    efetch_h.close = MagicMock()

    with patch("wmw.sra.Entrez.esearch", side_effect=fake_esearch), \
         patch("wmw.sra.Entrez.read", side_effect=fake_read), \
         patch("wmw.sra.Entrez.efetch", return_value=efetch_h), \
         patch("wmw.sra._AVAILABLE", True):
        results = sra.search_runs(
            date_from="2024-01-01", date_to="2024-12-31",
            min_bases=1_000_000_000,
        )

    accessions = [r["run_accession"] for r in results]
    assert "SRR001" not in accessions   # 500 Mbp — below threshold
    assert "SRR002" in accessions       # 5 Gbp — above threshold
