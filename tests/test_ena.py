"""Tests for wmw.ena — ENA Portal API queries."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from wmw import ena


def _mock_response(json_data):
    mock = MagicMock()
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


def _query(mock_get) -> str:
    return mock_get.call_args[1]["params"]["query"]


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def test_search_runs_builds_correct_params():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")
        params = mock_get.call_args[1]["params"]
        assert params["result"] == "read_run"
        assert "first_public>=2024-01-01" in params["query"]
        assert "first_public<=2024-12-31" in params["query"]
        assert params["format"] == "json"


def test_search_runs_returns_list():
    fake = [{"run_accession": "ERR001", "study_accession": "PRJEB001"}]
    with patch("wmw.ena.requests.get", return_value=_mock_response(fake)):
        result = ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")
    assert result == fake


# ---------------------------------------------------------------------------
# Inclusion filters
# ---------------------------------------------------------------------------

def test_host_tax_id_inclusion():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31", host_tax_id="7742")
        assert "host_tax_id=7742" in _query(mock_get)


def test_library_source_single():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        library_source="METAGENOMIC")
        assert 'library_source="METAGENOMIC"' in _query(mock_get)


def test_library_source_multiple():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        library_source="METAGENOMIC,METATRANSCRIPTOMIC")
        q = _query(mock_get)
        assert 'library_source="METAGENOMIC"' in q
        assert 'library_source="METATRANSCRIPTOMIC"' in q


def test_instrument_platform():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        instrument_platform="ILLUMINA")
        assert 'instrument_platform="ILLUMINA"' in _query(mock_get)


def test_min_bases():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        min_bases=1_000_000_000)
        assert "base_count>=1000000000" in _query(mock_get)


def test_keyword():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        keyword="fox")
        assert 'study_title="*fox*"' in _query(mock_get)


def test_date_field_collection_date():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2023-01-01", date_to="2023-12-31",
                        date_field="collection_date")
        q = _query(mock_get)
        assert "collection_date>=2023-01-01" in q
        assert "collection_date<=2023-12-31" in q
        assert "first_public" not in q


def test_invalid_date_field_raises():
    with pytest.raises(ValueError, match="date_field"):
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        date_field="bad_field")


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------

def test_single_exclusion():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        exclude_host_tax_ids=["9606"])
        assert "NOT host_tax_id=9606" in _query(mock_get)


def test_multiple_exclusions():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        exclude_host_tax_ids=["9606", "9913", "9823"])
        q = _query(mock_get)
        assert "NOT host_tax_id=9606" in q
        assert "NOT host_tax_id=9913" in q
        assert "NOT host_tax_id=9823" in q


def test_empty_exclusion_list_adds_no_not_clause():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31",
                        exclude_host_tax_ids=[])
        assert "NOT" not in _query(mock_get)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def test_unique_studies():
    runs = [
        {"study_accession": "PRJEB001"},
        {"study_accession": "PRJEB002"},
        {"study_accession": "PRJEB001"},
        {"study_accession": ""},
    ]
    assert ena.unique_studies(runs) == ["PRJEB001", "PRJEB002"]


# ---------------------------------------------------------------------------
# search_studies
# ---------------------------------------------------------------------------

def test_search_studies_builds_correct_params():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_studies(date_from="2024-01-01", date_to="2024-12-31")
        params = mock_get.call_args[1]["params"]
        assert params["result"] == "study"
        assert "first_public>=2024-01-01" in params["query"]
        assert "first_public<=2024-12-31" in params["query"]
        assert params["format"] == "json"


def test_search_studies_host_tax_id():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_studies(date_from="2024-01-01", date_to="2024-12-31", host_tax_id="7742")
        assert "tax_id=7742" in _query(mock_get)


def test_search_studies_keyword():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_studies(date_from="2024-01-01", date_to="2024-12-31", keyword="fox")
        assert 'study_title="*fox*"' in _query(mock_get)


def test_search_studies_last_updated_field():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_studies(date_from="2024-01-01", date_to="2024-12-31",
                           date_field="last_updated")
        q = _query(mock_get)
        assert "last_updated>=2024-01-01" in q
        assert "first_public" not in q


def test_search_studies_invalid_date_field_raises():
    with pytest.raises(ValueError, match="date_field"):
        ena.search_studies(date_from="2024-01-01", date_to="2024-12-31",
                           date_field="collection_date")


def test_search_studies_returns_list():
    fake = [{"study_accession": "PRJEB001", "study_title": "Test study"}]
    with patch("wmw.ena.requests.get", return_value=_mock_response(fake)):
        result = ena.search_studies(date_from="2024-01-01", date_to="2024-12-31")
    assert result == fake
