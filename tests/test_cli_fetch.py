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


# ---------------------------------------------------------------------------
# Archive selection (--source)
# ---------------------------------------------------------------------------

import argparse

import pytest
from unittest.mock import patch


def _args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_resolve_source_defaults_to_ena():
    with patch("wmw.config.get", return_value=""):
        assert cli._resolve_source(_args(source="")) == "ENA"


def test_resolve_source_accepts_gsa_flag():
    with patch("wmw.config.get", return_value=""):
        assert cli._resolve_source(_args(source="gsa")) == "GSA"


def test_resolve_source_falls_back_to_config():
    with patch("wmw.config.get", return_value="GSA"):
        assert cli._resolve_source(_args(source="")) == "GSA"


def test_resolve_source_cli_flag_beats_config():
    with patch("wmw.config.get", return_value="GSA"):
        assert cli._resolve_source(_args(source="ena")) == "ENA"


def test_resolve_source_rejects_unknown_archive():
    with patch("wmw.config.get", return_value=""), pytest.raises(SystemExit):
        cli._resolve_source(_args(source="ddbj"))


def test_gsa_organism_defaults_to_metagenome_label():
    from wmw.gsa import DEFAULT_ORGANISM

    with patch("wmw.config.get", return_value=""):
        assert cli._gsa_organism(_args(gsa_organism="")) == DEFAULT_ORGANISM


def test_gsa_organism_honours_flag():
    with patch("wmw.config.get", return_value=""):
        assert cli._gsa_organism(_args(gsa_organism="bat metagenome")) == "bat metagenome"
