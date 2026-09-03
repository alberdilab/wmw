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


# ---------------------------------------------------------------------------
# --refresh-metadata
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock


def _fetch_args(**overrides) -> argparse.Namespace:
    args = _args(
        source="ena",
        status="approved",
        study="PRJEB12345",
        dry_run=False,
        debug=False,
        refresh_metadata=False,
        fill_missing=False,
        studies_table="Studies",
        samples_table="Samples",
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


_FETCH_PARAMS = {
    "library_strategies": [],
    "library_sources": [],
    "instrument_platform": "",
    "min_bases": None,
    "exclude_ids": [],
}

_RAW_RUN = {
    "run_accession": "ERR1234567",
    "study_accession": "PRJEB12345",
    "collection_date": "2023-06-15",
    "country": "Denmark",
    "lat": "55.6761",
    "lon": "12.5683",
    "host_sex": "female",
    "broad_scale_environmental_context": "temperate broadleaf forest biome",
    "environmental_medium": "feces",
}


def _run_fetch(args):
    client = MagicMock()
    client.fetch_study_record_id.return_value = "recSTUDY"
    client.upsert_samples.return_value = (0, 1)
    client.refresh_sample_metadata.return_value = 1
    client.fill_missing_sample_fields.return_value = (1, 5)
    with patch("wmw.cli._require_airtable", return_value=client), \
         patch("wmw.cli._resolve_fetch_params", return_value=dict(_FETCH_PARAMS)), \
         patch("wmw.ena.search_study", return_value=[dict(_RAW_RUN)]):
        assert cli.cmd_fetch(args) == 0
    return client


def test_fetch_without_refresh_leaves_existing_rows_alone():
    client = _run_fetch(_fetch_args())
    client.upsert_samples.assert_called_once()
    client.refresh_sample_metadata.assert_not_called()


def test_fetch_refresh_metadata_rewrites_biosample_columns():
    from wmw import metadata

    client = _run_fetch(_fetch_args(refresh_metadata=True))
    client.refresh_sample_metadata.assert_called_once()
    table, samples, fields = client.refresh_sample_metadata.call_args[0]
    assert table == "Samples"
    assert list(fields) == list(metadata.BIOSAMPLE_FIELDS)
    assert samples[0]["run_accession"] == "ERR1234567"
    assert samples[0]["lat"] == 55.6761
    assert samples[0]["host_sex"] == "female"
    assert samples[0]["environmental_medium"] == "feces"


def test_fetch_refresh_metadata_still_inserts_new_runs():
    client = _run_fetch(_fetch_args(refresh_metadata=True))
    client.upsert_samples.assert_called_once()


# ---------------------------------------------------------------------------
# --fill-missing
# ---------------------------------------------------------------------------

def test_fetch_without_fill_missing_leaves_empty_cells_alone():
    client = _run_fetch(_fetch_args())
    client.fill_missing_sample_fields.assert_not_called()


def test_fetch_fill_missing_backfills_every_refreshable_column():
    from wmw import metadata

    client = _run_fetch(_fetch_args(fill_missing=True))
    client.fill_missing_sample_fields.assert_called_once()
    table, samples, fields = client.fill_missing_sample_fields.call_args[0]
    assert table == "Samples"
    assert list(fields) == list(metadata.REFRESHABLE_SAMPLE_FIELDS)
    assert samples[0]["run_accession"] == "ERR1234567"
    assert samples[0]["lat"] == 55.6761


def test_fetch_fill_missing_still_inserts_new_runs():
    client = _run_fetch(_fetch_args(fill_missing=True))
    client.upsert_samples.assert_called_once()


def test_fetch_fill_missing_dry_run_previews_without_writing(capsys):
    client = _run_fetch(_fetch_args(fill_missing=True, dry_run=True))
    client.upsert_samples.assert_not_called()
    client.fill_missing_sample_fields.assert_called_once()
    assert client.fill_missing_sample_fields.call_args[1]["dry_run"] is True
    assert "would fill 5 empty cells on 1 existing sample" in capsys.readouterr().out


def test_refreshable_sample_fields_never_touch_status():
    from wmw import metadata

    assert "status" not in metadata.REFRESHABLE_SAMPLE_FIELDS
    assert "parent_study" in metadata.REFRESHABLE_SAMPLE_FIELDS
    assert "collection_date" in metadata.REFRESHABLE_SAMPLE_FIELDS
    assert "fastq_url_1" in metadata.REFRESHABLE_SAMPLE_FIELDS
