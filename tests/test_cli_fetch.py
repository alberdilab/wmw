"""Tests for wmw fetch command helpers."""

from __future__ import annotations

from wmw import cli


def test_show_run_filter_exclusions_reports_criterion(capsys):
    cli._show_run_filter_exclusions(
        [
            {
                "run_accession": "ERR010",
                "criterion": "library_source",
                "value": "GENOMIC",
                "expected": "one of METAGENOMIC",
            },
            {
                "run_accession": "ERR011",
                "criterion": "library_source",
                "value": "GENOMIC",
                "expected": "one of METAGENOMIC",
            },
        ],
        max_examples=1,
    )

    output = capsys.readouterr().out

    assert "Exclusion criteria used" in output
    assert (
        "library_source=GENOMIC; expected one of METAGENOMIC: 2 runs (ERR010, +1 more)"
        in output
    )
