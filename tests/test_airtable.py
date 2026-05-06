"""Tests for wmw.airtable — AirtableClient logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_client():
    with patch("wmw.airtable._AVAILABLE", True), patch("wmw.airtable.Api") as MockApi:
        from wmw.airtable import AirtableClient
        client = AirtableClient("fake_token", "appFAKEBASE")
        client._api = MockApi.return_value
        yield client


def _make_record(record_id: str, fields: dict) -> dict:
    return {"id": record_id, "fields": fields, "createdTime": "2024-01-01T00:00:00.000Z"}


def test_existing_study_accessions(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("rec1", {"study_accession": "PRJEB001"}),
        _make_record("rec2", {"study_accession": "PRJEB002"}),
    ]
    mock_client._api.table.return_value = mock_table

    result = mock_client.existing_study_accessions("Studies")
    assert result == {"PRJEB001", "PRJEB002"}


def test_upsert_studies_updates_existing(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("rec1", {"study_accession": "PRJEB001"}),
    ]
    mock_client._api.table.return_value = mock_table

    studies = [
        {"study_accession": "PRJEB001", "study_title": "Updated"},
        {"study_accession": "PRJEB002", "study_title": "New"},
    ]
    inserted, updated = mock_client.upsert_studies("Studies", studies)
    assert inserted == 1
    assert updated == 1
    mock_table.batch_create.assert_called_once()
    created = mock_table.batch_create.call_args[0][0]
    assert len(created) == 1
    assert created[0]["study_accession"] == "PRJEB002"
    mock_table.batch_update.assert_called_once()
    updates = mock_table.batch_update.call_args[0][0]
    assert len(updates) == 1
    assert updates[0]["id"] == "rec1"
    assert updates[0]["fields"]["study_title"] == "Updated"


def test_upsert_studies_does_not_overwrite_status(mock_client):
    """Re-scanning an existing study must not reset its status to 'new'."""
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("rec1", {"study_accession": "PRJEB001"}),
    ]
    mock_client._api.table.return_value = mock_table

    studies = [{"study_accession": "PRJEB001", "study_title": "Updated", "status": "new"}]
    mock_client.upsert_studies("Studies", studies)

    updates = mock_table.batch_update.call_args[0][0]
    assert "status" not in updates[0]["fields"]


def test_upsert_studies_all_new(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = []
    mock_client._api.table.return_value = mock_table

    studies = [{"study_accession": "PRJEB003"}, {"study_accession": "PRJEB004"}]
    inserted, updated = mock_client.upsert_studies("Studies", studies)
    assert inserted == 2
    assert updated == 0


def test_upsert_samples_skips_existing(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("rec1", {"run_accession": "ERR001"}),
    ]
    mock_client._api.table.return_value = mock_table

    samples = [
        {"run_accession": "ERR001"},
        {"run_accession": "ERR002"},
    ]
    inserted, skipped = mock_client.upsert_samples("Samples", samples)
    assert inserted == 1
    assert skipped == 1


def test_set_sample_status(mock_client):
    mock_table = MagicMock()
    mock_client._api.table.return_value = mock_table

    mock_client.set_sample_status("Samples", ["rec1", "rec2"], "running")
    mock_table.batch_update.assert_called_once()
    updates = mock_table.batch_update.call_args[0][0]
    assert all(u["fields"]["status"] == "running" for u in updates)
    assert {u["id"] for u in updates} == {"rec1", "rec2"}


def test_fetch_studies_by_status(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("rec1", {"study_accession": "PRJEB001", "status": "approved"}),
        _make_record("rec2", {"study_accession": "PRJEB002", "status": "approved"}),
    ]
    mock_client._api.table.return_value = mock_table

    result = mock_client.fetch_studies_by_status("Studies", status="approved")
    assert len(result) == 2
    mock_table.all.assert_called_once_with(formula='{status} = "approved"')


def test_set_study_status(mock_client):
    mock_table = MagicMock()
    mock_client._api.table.return_value = mock_table

    mock_client.set_study_status("Studies", ["rec1", "rec2"], "indexed")
    mock_table.batch_update.assert_called_once()
    updates = mock_table.batch_update.call_args[0][0]
    assert all(u["fields"]["status"] == "indexed" for u in updates)
    assert {u["id"] for u in updates} == {"rec1", "rec2"}


def test_link_studies_to_species(mock_client):
    studies_table_mock = MagicMock()
    studies_table_mock.all.return_value = [
        _make_record("recStudy1", {"study_accession": "PRJEB001"}),
        _make_record("recStudy2", {"study_accession": "PRJEB002"}),
    ]
    species_table_mock = MagicMock()
    species_table_mock.all.return_value = [
        _make_record("recSp1", {"fldTAXID": "9606", "fldLINK": ["recStudy0"]}),
        _make_record("recSp2", {"fldTAXID": "9615", "fldLINK": []}),
    ]

    def table_side_effect(base_id, table_name):
        if table_name == "Studies":
            return studies_table_mock
        return species_table_mock

    mock_client._api.table.side_effect = table_side_effect
    mock_client._api_fid = MagicMock()
    mock_client._api_fid.table.return_value = species_table_mock

    host_taxids_by_study = {
        "PRJEB001": {"9606"},
        "PRJEB002": {"9615", "9606"},
    }
    n = mock_client.link_studies_to_species(
        "Studies", "tblSPECIES", "fldTAXID", "fldLINK", host_taxids_by_study
    )

    assert n == 2
    species_table_mock.batch_update.assert_called_once()
    updates = {u["id"]: set(u["fields"]["fldLINK"]) for u in species_table_mock.batch_update.call_args[0][0]}
    assert updates["recSp1"] == {"recStudy0", "recStudy1", "recStudy2"}
    assert updates["recSp2"] == {"recStudy2"}


def test_link_studies_to_species_no_new_links(mock_client):
    studies_table_mock = MagicMock()
    studies_table_mock.all.return_value = [
        _make_record("recStudy1", {"study_accession": "PRJEB001"}),
    ]
    species_table_mock = MagicMock()
    species_table_mock.all.return_value = [
        _make_record("recSp1", {"fldTAXID": "9606", "fldLINK": ["recStudy1"]}),
    ]
    mock_client._api.table.return_value = studies_table_mock
    mock_client._api_fid = MagicMock()
    mock_client._api_fid.table.return_value = species_table_mock

    n = mock_client.link_studies_to_species(
        "Studies", "tblSPECIES", "fldTAXID", "fldLINK", {"PRJEB001": {"9606"}}
    )
    assert n == 0
    species_table_mock.batch_update.assert_not_called()
