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


def test_fetch_study_record_id_found(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [_make_record("rec42", {"study_accession": "PRJEB042"})]
    mock_client._api.table.return_value = mock_table

    result = mock_client.fetch_study_record_id("Studies", "PRJEB042")
    assert result == "rec42"


def test_fetch_study_record_id_not_found(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = []
    mock_client._api.table.return_value = mock_table

    result = mock_client.fetch_study_record_id("Studies", "PRJEB999")
    assert result is None


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


def test_upload_study_file_uses_configured_field_id(mock_client, tmp_path):
    mock_table = MagicMock()
    mock_client._studies_fm = {"file_preprocessing": "fldPRE"}
    mock_client._api_fid = MagicMock()
    mock_client._api_fid.table.return_value = mock_table
    tsv_path = tmp_path / "preprocessing.tsv"
    tsv_path.write_text("sample\treads_pre_fastp\nSA000001\t10\n", encoding="utf-8")

    mock_client.upload_study_file(
        "Studies",
        "recStudy",
        "file_preprocessing",
        tsv_path,
    )

    mock_table.upload_attachment.assert_called_once_with(
        "recStudy",
        "fldPRE",
        tsv_path,
        content_type="text/tab-separated-values",
    )


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


def test_fetch_study_by_code_found(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [_make_record("rec10", {"code": "PRJ001", "study_accession": "PRJEB001"})]
    mock_client._api.table.return_value = mock_table

    result = mock_client.fetch_study_by_code("Studies", "PRJ001")
    assert result is not None
    assert result["id"] == "rec10"
    assert result["fields"]["code"] == "PRJ001"


def test_fetch_study_by_code_not_found(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = []
    mock_client._api.table.return_value = mock_table

    result = mock_client.fetch_study_by_code("Studies", "MISSING")
    assert result is None


def test_fetch_samples_for_study_with_status(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("recS1", {"run_accession": "ERR001", "study_accession": "PRJEB001", "status": "ready"}),
    ]
    mock_client._api.table.return_value = mock_table

    result = mock_client.fetch_samples_for_study("Samples", "PRJEB001", status="ready")
    assert len(result) == 1
    call_kwargs = mock_table.all.call_args[1]
    assert "PRJEB001" in call_kwargs["formula"]
    assert "ready" in call_kwargs["formula"]


def test_fetch_samples_for_study_no_status_filter(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("recS1", {"run_accession": "ERR001", "study_accession": "PRJEB001"}),
        _make_record("recS2", {"run_accession": "ERR002", "study_accession": "PRJEB001"}),
    ]
    mock_client._api.table.return_value = mock_table

    result = mock_client.fetch_samples_for_study("Samples", "PRJEB001")
    assert len(result) == 2
    call_kwargs = mock_table.all.call_args[1]
    assert "ready" not in call_kwargs["formula"]
    assert "PRJEB001" in call_kwargs["formula"]


def test_update_sample_cataloging_stats_updates_matching_codes(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("recS1", {"code": "SA000004"}),
    ]
    mock_client._api.table.return_value = mock_table

    n = mock_client.update_sample_cataloging_stats(
        "Samples",
        {
            "SA000004": {"fldGC": 41.69},
            "SA000005": {"fldGC": 38.15},
        },
    )

    assert n == 1
    mock_table.batch_update.assert_called_once()
    updates = mock_table.batch_update.call_args[0][0]
    assert updates == [{"id": "recS1", "fields": {"fldGC": 41.69}}]


def test_fetch_sample_record_ids_by_code(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("recS1", {"code": "SA000022", "record_id": "recFormula1"}),
        _make_record("recS2", {"code": "SA000023"}),
    ]
    mock_client._api.table.return_value = mock_table

    result = mock_client.fetch_sample_record_ids_by_code(
        "Samples",
        ["SA000022", "SA000023"],
    )

    assert result == {"SA000022": "recFormula1", "SA000023": "recS2"}
    call_kwargs = mock_table.all.call_args[1]
    assert "SA000022" in call_kwargs["formula"]
    assert "SA000023" in call_kwargs["formula"]


def test_create_genome_records_uses_field_id_table(mock_client):
    mock_table = MagicMock()
    mock_table.batch_create.return_value = [
        {"id": "recGenome1", "fields": {"fldNAME": "SA000022_bin_339957"}}
    ]
    mock_client._api_fid = MagicMock()
    mock_client._api_fid.table.return_value = mock_table

    n = mock_client.create_genome_records(
        "Genomes",
        [{"fldNAME": "SA000022_bin_339957", "fldLINK": ["recS1"]}],
    )

    assert n == 1
    mock_client._api_fid.table.assert_called_once_with("appFAKEBASE", "Genomes")
    mock_table.batch_create.assert_called_once_with(
        [{"fldNAME": "SA000022_bin_339957", "fldLINK": ["recS1"]}]
    )


def test_create_genome_records_with_response_returns_created_rows(mock_client):
    mock_table = MagicMock()
    created = [{"id": "recGenome1", "fields": {"fldNAME": "SA000022_bin_339957"}}]
    mock_table.batch_create.return_value = created
    mock_client._api_fid = MagicMock()
    mock_client._api_fid.table.return_value = mock_table

    result = mock_client.create_genome_records_with_response(
        "Genomes",
        [{"fldNAME": "SA000022_bin_339957"}],
    )

    assert result == created


def test_fetch_genome_records_by_name(mock_client):
    mock_table = MagicMock()
    mock_table.all.return_value = [
        _make_record("recGenome1", {"fldNAME": "SA000022_bin_339957"}),
    ]
    mock_client._api_fid = MagicMock()
    mock_client._api_fid.table.return_value = mock_table

    result = mock_client.fetch_genome_records_by_name(
        "Genomes",
        ["SA000022_bin_339957", "SA000023_bin_123"],
        "fldNAME",
    )

    assert result["SA000022_bin_339957"]["id"] == "recGenome1"
    call_kwargs = mock_table.all.call_args[1]
    assert "SA000022_bin_339957" in call_kwargs["formula"]
    assert "SA000023_bin_123" in call_kwargs["formula"]


def test_update_genome_records_uses_field_id_table(mock_client):
    mock_table = MagicMock()
    mock_client._api_fid = MagicMock()
    mock_client._api_fid.table.return_value = mock_table
    updates = [{"id": "recGenome1", "fields": {"fldNAME": "SA000022_bin_339957"}}]

    n = mock_client.update_genome_records("Genomes", updates)

    assert n == 1
    mock_client._api_fid.table.assert_called_once_with("appFAKEBASE", "Genomes")
    mock_table.batch_update.assert_called_once_with(updates)


def test_upload_genome_file_uses_field_id_table(mock_client, tmp_path):
    mock_table = MagicMock()
    mock_client._api_fid = MagicMock()
    mock_client._api_fid.table.return_value = mock_table
    gz_path = tmp_path / "SA000022_bin_339957.fa.gz"
    gz_path.write_bytes(b"gzip")

    mock_client.upload_genome_file(
        "Genomes",
        "recGenome",
        "fldGenomeFile",
        gz_path,
    )

    mock_table.upload_attachment.assert_called_once_with(
        "recGenome",
        "fldGenomeFile",
        gz_path,
        content_type="application/gzip",
    )


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
