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


def test_upsert_studies_skips_existing(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("rec1", {"study_accession": "PRJEB001"}),
    ]
    mock_client._api.table.return_value = mock_table

    studies = [
        {"study_accession": "PRJEB001", "study_title": "Existing"},
        {"study_accession": "PRJEB002", "study_title": "New"},
    ]
    inserted, skipped = mock_client.upsert_studies("Studies", studies)
    assert inserted == 1
    assert skipped == 1
    mock_table.batch_create.assert_called_once()
    created = mock_table.batch_create.call_args[0][0]
    assert len(created) == 1
    assert created[0]["study_accession"] == "PRJEB002"


def test_upsert_studies_all_new(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = []
    mock_client._api.table.return_value = mock_table

    studies = [{"study_accession": "PRJEB003"}, {"study_accession": "PRJEB004"}]
    inserted, skipped = mock_client.upsert_studies("Studies", studies)
    assert inserted == 2
    assert skipped == 0


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
