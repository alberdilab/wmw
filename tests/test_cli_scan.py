"""Tests for wmw scan run-count helpers and single-study scan."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from wmw import cli


def _run(acc: str, run_accession: str, host_tax_id: str = "9627") -> dict:
    return {
        "study_accession": acc,
        "run_accession": run_accession,
        "host_tax_id": host_tax_id,
    }


def _scan_args(**kwargs) -> argparse.Namespace:
    defaults = {
        "no_publications": True,
        "include": "All",
        "library_strategy": "",
        "library_source": "",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Run-stat helpers
# ---------------------------------------------------------------------------

def test_run_stats_by_study_counts_runs_and_distinct_host_taxa():
    runs = [
        _run("PRJEB1", "ERR1", "9627"),
        _run("PRJEB1", "ERR2", "9627"),
        _run("PRJEB1", "ERR3", "9615"),
        _run("PRJEB2", "ERR4", "9913"),
    ]

    stats = cli._run_stats_by_study(runs)

    assert stats["PRJEB1"]["runs"] == 3
    assert stats["PRJEB1"]["host_taxids"] == {"9627", "9615"}
    assert stats["PRJEB2"]["runs"] == 1


def test_run_stats_by_study_skips_blank_accessions_and_host_taxa():
    runs = [
        _run("", "ERR1"),
        _run("PRJEB1", "ERR2", ""),
    ]

    stats = cli._run_stats_by_study(runs)

    assert "" not in stats
    assert stats["PRJEB1"] == {"runs": 1, "host_taxids": set()}


def test_summarize_run_stats_collapses_host_taxid_sets():
    stats = {"PRJEB1": {"runs": 3, "host_taxids": {"9627", "9615"}}}

    assert cli._summarize_run_stats(stats) == {"PRJEB1": {"runs": 3, "host_taxa": 2}}


def test_apply_run_stats_writes_detected_fields():
    studies = [{"study_accession": "PRJEB1"}, {"study_accession": "PRJEB2"}]

    cli._apply_run_stats(
        studies,
        {"PRJEB1": {"runs": 3, "host_taxa": 2}, "PRJEB2": {"runs": 0, "host_taxa": 0}},
    )

    assert studies[0]["detected_runs"] == 3
    assert studies[0]["detected_host_taxa"] == 2
    # Zero counts stay out of the payload rather than overwriting a curated cell.
    assert "detected_runs" not in studies[1]
    assert "detected_host_taxa" not in studies[1]


# ---------------------------------------------------------------------------
# _scan_single_study
# ---------------------------------------------------------------------------

def test_scan_single_study_populates_run_counts():
    client = MagicMock()
    client.upsert_studies.return_value = (1, 0)

    with patch("wmw.ena.fetch_study_metadata", return_value={"study_accession": "PRJEB61088"}), \
         patch("wmw.ena.search_runs", return_value=[
             _run("PRJEB61088", "ERR1", "100830"),
             _run("PRJEB61088", "ERR2", "100830"),
         ]):
        rc = cli._scan_single_study(
            _scan_args(), "PRJEB61088", "Studies", dry_run=False, client=client,
        )

    assert rc == 0
    sent = client.upsert_studies.call_args[0][1][0]
    assert sent["detected_runs"] == 2
    assert sent["detected_host_taxa"] == 1


def test_scan_single_study_queries_the_resolved_primary_accession():
    """A secondary (ERP/SRP) accession resolves before the run query, which
    only matches on study_accession."""
    client = MagicMock()
    client.upsert_studies.return_value = (1, 0)

    with patch("wmw.ena.fetch_study_metadata", return_value={
        "study_accession": "PRJEB61088",
        "secondary_study_accession": "ERP146183",
    }), patch("wmw.ena.search_runs", return_value=[]) as search_runs:
        cli._scan_single_study(
            _scan_args(), "ERP146183", "Studies", dry_run=False, client=client,
        )

    assert search_runs.call_args.kwargs["study_accessions"] == ["PRJEB61088"]


def test_scan_single_study_reports_zero_for_a_study_without_qualifying_runs():
    """A successful query that found nothing shows 0, not the '—' placeholder."""
    client = MagicMock()
    client.upsert_studies.return_value = (1, 0)

    with patch("wmw.ena.fetch_study_metadata", return_value={"study_accession": "PRJNA717299"}), \
         patch("wmw.ena.search_runs", return_value=[]), \
         patch("wmw.cli._print_scan_summary") as summary:
        cli._scan_single_study(
            _scan_args(), "PRJNA717299", "Studies", dry_run=False, client=client,
        )

    assert summary.call_args.kwargs["run_stats"] == {
        "PRJNA717299": {"runs": 0, "host_taxa": 0}
    }
    sent = client.upsert_studies.call_args[0][1][0]
    assert "detected_runs" not in sent


def test_scan_single_study_applies_host_taxon_exclusions():
    client = MagicMock()
    client.upsert_studies.return_value = (1, 0)

    with patch("wmw.cli._build_exclude_ids", return_value=["9606"]), \
         patch("wmw.ena.fetch_study_metadata", return_value={"study_accession": "PRJEB1"}), \
         patch("wmw.ena.search_runs", return_value=[
             _run("PRJEB1", "ERR1", "9627"),
             _run("PRJEB1", "ERR2", "9606"),
         ]):
        cli._scan_single_study(
            _scan_args(include=""), "PRJEB1", "Studies", dry_run=False, client=client,
        )

    sent = client.upsert_studies.call_args[0][1][0]
    assert sent["detected_runs"] == 1
    assert sent["detected_host_taxa"] == 1


def test_scan_single_study_still_inserts_when_the_run_query_fails(capsys):
    client = MagicMock()
    client.upsert_studies.return_value = (1, 0)

    with patch("wmw.ena.fetch_study_metadata", return_value={"study_accession": "PRJEB1"}), \
         patch("wmw.ena.search_runs", side_effect=RuntimeError("ENA down")), \
         patch("wmw.cli._print_scan_summary") as summary:
        rc = cli._scan_single_study(
            _scan_args(), "PRJEB1", "Studies", dry_run=False, client=client,
        )

    assert rc == 0
    output = capsys.readouterr().out
    assert "run counts unavailable" in output
    # No counts at all, so the summary keeps the '—' placeholder.
    assert summary.call_args.kwargs["run_stats"] is None
    client.upsert_studies.assert_called_once()
    sent = client.upsert_studies.call_args[0][1][0]
    assert "detected_runs" not in sent


def test_scan_single_study_dry_run_makes_no_airtable_calls():
    client = MagicMock()

    with patch("wmw.ena.fetch_study_metadata", return_value={"study_accession": "PRJEB1"}), \
         patch("wmw.ena.search_runs", return_value=[_run("PRJEB1", "ERR1")]):
        cli._scan_single_study(
            _scan_args(), "PRJEB1", "Studies", dry_run=True, client=client,
        )

    client.upsert_studies.assert_not_called()
    client.link_studies_to_species.assert_not_called()


def test_scan_single_study_links_species_by_host_taxon():
    client = MagicMock()
    client.upsert_studies.return_value = (1, 0)
    client.link_studies_to_species.return_value = 1

    with patch("wmw.ena.fetch_study_metadata", return_value={"study_accession": "PRJEB61088"}), \
         patch("wmw.ena.search_runs", return_value=[_run("PRJEB61088", "ERR1", "100830")]), \
         patch("wmw.config.get", side_effect=lambda key, default="": {
             "SPECIES_TABLE": "tblSpecies",
             "SPECIES_TAXID_FIELD": "fldTaxid",
             "SPECIES_STUDIES_LINK_FIELD": "fldLink",
         }.get(key, default)):
        cli._scan_single_study(
            _scan_args(), "PRJEB61088", "Studies", dry_run=False, client=client,
        )

    args, _ = client.link_studies_to_species.call_args
    assert args[1:4] == ("tblSpecies", "fldTaxid", "fldLink")
    assert args[4] == {"PRJEB61088": {"100830"}}
