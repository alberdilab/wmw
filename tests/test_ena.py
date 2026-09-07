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


def test_search_runs_has_no_tax_tree():
    # tax_tree() is invalid in result=read_run; must not appear regardless of args
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")
        assert "tax_tree" not in _query(mock_get)


# ---------------------------------------------------------------------------
# fetch_studies_batch
# ---------------------------------------------------------------------------

def test_fetch_studies_batch_single_chunk():
    fake = [{"study_accession": "PRJEB001"}, {"study_accession": "PRJEB002"}]
    with patch("wmw.ena.requests.get", return_value=_mock_response(fake)) as mock_get:
        result = ena.fetch_studies_batch(["PRJEB001", "PRJEB002"])
    assert result == fake
    params = mock_get.call_args[1]["params"]
    assert params["result"] == "study"
    assert 'study_accession="PRJEB001"' in params["query"]
    assert 'study_accession="PRJEB002"' in params["query"]


def test_fetch_studies_batch_empty():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        result = ena.fetch_studies_batch([])
    assert result == []
    mock_get.assert_not_called()


def test_fetch_studies_batch_chunking():
    fake = [{"study_accession": f"PRJEB{i:03d}"} for i in range(5)]
    with patch("wmw.ena.requests.get", return_value=_mock_response(fake)) as mock_get:
        ena.fetch_studies_batch(["PRJEB001", "PRJEB002", "PRJEB003"], chunk_size=2)
    assert mock_get.call_count == 2


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
        q = _query(mock_get)
        assert 'study_title="*fox*"' in q
        assert 'study_description="*fox*"' in q


def test_search_studies_keyword_pipe_separated():
    # Root-style keywords: *ECOLOG* matches ecology/ecological/ecologist etc.
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_studies(date_from="2024-01-01", date_to="2024-12-31",
                           keyword="ECOLOG|EVOLUT|WILD")
        q = _query(mock_get)
        assert 'study_title="*ECOLOG*"' in q
        assert 'study_description="*ECOLOG*"' in q
        assert 'study_title="*EVOLUT*"' in q
        assert 'study_description="*EVOLUT*"' in q
        assert 'study_title="*WILD*"' in q
        assert 'study_description="*WILD*"' in q


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


# ---------------------------------------------------------------------------
# _get retry behaviour
# ---------------------------------------------------------------------------

def _http_error(status_code: int, body=None):
    """Return a requests.HTTPError with the given status code and JSON body."""
    import requests as _req
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {}
    err = _req.exceptions.HTTPError(response=resp)
    return err


def test_get_retries_on_500_then_succeeds():
    """A transient 500 should be retried; success on second attempt is returned."""
    import requests as _req
    fake = [{"run_accession": "ERR001"}]
    ok_resp = _mock_response(fake)
    err_resp = MagicMock()
    err_resp.raise_for_status.side_effect = _http_error(500)

    with patch("wmw.ena.requests.get", side_effect=[err_resp, ok_resp]) as mock_get:
        with patch("wmw.ena.time.sleep"):
            result = ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")
    assert result == fake
    assert mock_get.call_count == 2


def test_get_raises_on_404():
    """A 4xx is the query's fault: not retried, and reported as an ENAError."""
    err_resp = MagicMock()
    err_resp.raise_for_status.side_effect = _http_error(404)

    with patch("wmw.ena.requests.get", return_value=err_resp) as mock_get:
        with patch("wmw.ena.time.sleep"):
            with pytest.raises(ena.ENAError):
                ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")
    assert mock_get.call_count == 1


def test_get_surfaces_the_ena_error_message_on_a_400():
    """ENA explains the rejection in the body; that beats a raw URL dump."""
    err_resp = MagicMock()
    err_resp.raise_for_status.side_effect = _http_error(
        400, {"message": "Invalid study_accession 'ST00359"}
    )

    with patch("wmw.ena.requests.get", return_value=err_resp):
        with pytest.raises(ena.ENAError, match="Invalid study_accession"):
            ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")


def test_get_raises_after_exhausting_retries():
    """A persistent 500 must not masquerade as an empty result set."""
    err_resp = MagicMock()
    err_resp.raise_for_status.side_effect = _http_error(503)

    with patch("wmw.ena.requests.get", return_value=err_resp):
        with patch("wmw.ena.time.sleep"):
            with pytest.raises(ena.ENAError, match="unavailable"):
                ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")


def test_get_raises_on_a_non_json_response():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.side_effect = ValueError("no json")
    resp.text = "<html>gateway timeout</html>"

    with patch("wmw.ena.requests.get", return_value=resp):
        with pytest.raises(ena.ENAError, match="not JSON"):
            ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")


def test_get_raises_when_ena_is_unreachable():
    import requests as _req

    with patch("wmw.ena.requests.get", side_effect=_req.exceptions.ConnectionError("boom")):
        with patch("wmw.ena.time.sleep"):
            with pytest.raises(ena.ENAError, match="Could not reach"):
                ena.search_runs(date_from="2024-01-01", date_to="2024-12-31")


# ---------------------------------------------------------------------------
# Accession normalization and validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("prjna1300861", "PRJNA1300861"),
    ("  PRJEB61088 ", "PRJEB61088"),
    ("erp146183", "ERP146183"),
])
def test_validate_study_accession_normalizes_case_and_whitespace(raw, expected):
    """ENA matches accessions case-sensitively — lowercase input 400s."""
    assert ena.validate_study_accession(raw) == expected


@pytest.mark.parametrize("acc", [
    "PRJEB61088", "PRJNA1300861", "PRJDB12345", "ERP146183", "SRP123456", "DRP000001",
])
def test_is_study_accession_accepts_insdc_forms(acc):
    assert ena.is_study_accession(acc)


@pytest.mark.parametrize("acc", ["ST00359", "", "  ", "ERR1234567", "SAMEA123", "nonsense"])
def test_is_study_accession_rejects_everything_else(acc):
    assert not ena.is_study_accession(acc)


def test_validate_study_accession_names_the_wmw_code_mixup():
    with pytest.raises(ena.ENAError, match="wmw study code"):
        ena.validate_study_accession("ST00359")


@pytest.mark.parametrize("acc", ["CRA012991", "PRJCA020434"])
def test_validate_study_accession_names_the_gsa_mixup(acc):
    """ENA does not index GSA accessions; --study routes them to GSA instead."""
    with pytest.raises(ena.ENAError, match="GSA"):
        ena.validate_study_accession(acc)


def test_validate_study_accession_rejects_other_junk():
    with pytest.raises(ena.ENAError, match="not a valid ENA study accession"):
        ena.validate_study_accession("nonsense")


def test_fetch_study_metadata_uppercases_before_querying():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.fetch_study_metadata("prjna1300861")
    assert 'study_accession="PRJNA1300861"' in _query(mock_get)


def test_fetch_study_metadata_rejects_a_non_accession_without_calling_ena():
    with patch("wmw.ena.requests.get") as mock_get:
        with pytest.raises(ena.ENAError):
            ena.fetch_study_metadata("ST00359")
    mock_get.assert_not_called()


def test_search_study_rejects_a_non_accession_without_calling_ena():
    with patch("wmw.ena.requests.get") as mock_get:
        with pytest.raises(ena.ENAError):
            ena.search_study("ST00359")
    mock_get.assert_not_called()


def test_fetch_studies_batch_drops_malformed_accessions():
    """One bad accession would otherwise 400 the whole chunk."""
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.fetch_studies_batch(["prjeb61088", "ST00359", "PRJNA1300861"])
    q = _query(mock_get)
    assert 'study_accession="PRJEB61088"' in q
    assert 'study_accession="PRJNA1300861"' in q
    assert "ST00359" not in q


def test_fetch_studies_batch_makes_no_call_when_nothing_is_valid():
    with patch("wmw.ena.requests.get") as mock_get:
        assert ena.fetch_studies_batch(["ST00359", "junk"]) == []
    mock_get.assert_not_called()


def test_search_runs_screens_the_study_accession_restriction():
    with patch("wmw.ena.requests.get", return_value=_mock_response([])) as mock_get:
        ena.search_runs(study_accessions=["prjeb61088", "ST00359"])
    q = _query(mock_get)
    assert 'study_accession="PRJEB61088"' in q
    assert "ST00359" not in q


def test_search_runs_returns_empty_rather_than_widening_the_query():
    """Dropping every accession must not turn a study query into a global one."""
    with patch("wmw.ena.requests.get") as mock_get:
        assert ena.search_runs(study_accessions=["ST00359"]) == []
    mock_get.assert_not_called()


def test_run_fields_include_biosample_attributes():
    """ENA joins the sample's MIxS attributes onto each run, so no extra call."""
    fields = set(ena.RUN_FIELDS.split(","))
    assert {
        "collection_date",
        "country",
        "lat",
        "lon",
        "host_sex",
        "broad_scale_environmental_context",
        "environmental_medium",
    } <= fields
