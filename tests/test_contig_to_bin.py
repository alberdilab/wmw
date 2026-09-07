"""Tests for the binette contig-to-bin attachment upload."""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path
from unittest.mock import patch

import pytest
from wmw import cli, drakkar
from wmw import config as cfg


CONTIG_TO_BIN_FIELD_ID = "fldContigToBin"


@pytest.fixture()
def contig_to_bin_config():
    """Point SAMPLES_COL_CONTIG_TO_BIN at a known field ID."""
    merged = {**cfg.load_config(), "SAMPLES_COL_CONTIG_TO_BIN": CONTIG_TO_BIN_FIELD_ID}
    with patch("wmw.config.load_config", return_value=merged):
        yield merged


@pytest.fixture()
def no_contig_to_bin_config():
    """Blank the config key, as a base without the column has it."""
    merged = {**cfg.load_config(), "SAMPLES_COL_CONTIG_TO_BIN": ""}
    with patch("wmw.config.load_config", return_value=merged):
        yield merged


class _FakeClient:
    """Records the Samples-table attachment writes the uploader makes."""

    def __init__(self, records: dict[str, dict] | None = None) -> None:
        self.records = records if records is not None else {}
        self.uploads: list[tuple[str, str, str]] = []
        self.cleared: list[tuple[str, str]] = []
        self.upload_error: Exception | None = None

    def fetch_samples_by_code(self, samples_table, sample_codes):
        codes = list(sample_codes)
        return {code: rec for code, rec in self.records.items() if code in codes}

    def upload_sample_file(
        self, samples_table, record_id, field_name, path, content_type=None
    ):
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads.append((record_id, field_name, Path(path).name))
        return {}

    def clear_sample_file(self, samples_table, record_id, field_name):
        self.cleared.append((record_id, field_name))


def _make_binette_output(
    root: Path,
    code: str = "ST001",
    samples: tuple[str, ...] = ("SA000022",),
    *,
    rows: str = "k141_1\tbin_1\n",
) -> Path:
    """Build a minimal binette output tree and return the study work dir."""
    work_dir = root / code
    for sample in samples:
        sample_dir = work_dir / "cataloging" / "binette" / sample
        sample_dir.mkdir(parents=True)
        (sample_dir / drakkar.CONTIG_TO_BIN_FILE).write_text(
            "contig\tbin\n" + rows, encoding="utf-8"
        )
    return work_dir


def _sample_record(code: str, **fields) -> dict:
    return {"id": f"rec{code}", "fields": {"code": code, **fields}}


def _upload_args(output_dir: Path, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        study="ST001",
        output_dir=str(output_dir),
        samples_table="Samples",
        replace_files=False,
        airtable_token="",
        base_id="",
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


# ---------------------------------------------------------------------------
# the uploader
# ---------------------------------------------------------------------------

def test_upload_attaches_one_gzipped_table_per_assembly(tmp_path, contig_to_bin_config):
    work_dir = _make_binette_output(tmp_path, samples=("SA000022", "SA000023"))
    client = _FakeClient(
        {"SA000022": _sample_record("SA000022"), "SA000023": _sample_record("SA000023")}
    )

    uploaded = cli._upload_contig_to_bin_attachments(
        client, "Samples", drakkar.contig_to_bin_files(work_dir)
    )

    assert uploaded == 2
    assert sorted(client.uploads) == [
        ("recSA000022", "contig_to_bin", "SA000022_contig_to_bin.tsv.gz"),
        ("recSA000023", "contig_to_bin", "SA000023_contig_to_bin.tsv.gz"),
    ]
    assert client.cleared == []
    gz_path = (
        work_dir / "cataloging" / "binette" / "SA000022" / "SA000022_contig_to_bin.tsv.gz"
    )
    with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
        assert fh.read() == "contig\tbin\nk141_1\tbin_1\n"


def test_upload_skips_a_sample_that_already_has_the_table(tmp_path, contig_to_bin_config):
    work_dir = _make_binette_output(tmp_path)
    client = _FakeClient(
        {"SA000022": _sample_record("SA000022", **{CONTIG_TO_BIN_FIELD_ID: [{"id": "att1"}]})}
    )

    uploaded = cli._upload_contig_to_bin_attachments(
        client, "Samples", drakkar.contig_to_bin_files(work_dir)
    )

    assert uploaded == 0
    assert client.uploads == []


def test_upload_replaces_an_existing_attachment_instead_of_stacking_it(
    tmp_path, contig_to_bin_config
):
    work_dir = _make_binette_output(tmp_path)
    client = _FakeClient(
        {"SA000022": _sample_record("SA000022", **{CONTIG_TO_BIN_FIELD_ID: [{"id": "att1"}]})}
    )

    uploaded = cli._upload_contig_to_bin_attachments(
        client, "Samples", drakkar.contig_to_bin_files(work_dir), replace_existing=True
    )

    assert uploaded == 1
    assert client.cleared == [("recSA000022", "contig_to_bin")]
    assert client.uploads == [
        ("recSA000022", "contig_to_bin", "SA000022_contig_to_bin.tsv.gz")
    ]


def test_upload_reports_assemblies_with_no_sample_row(tmp_path, contig_to_bin_config):
    work_dir = _make_binette_output(tmp_path, samples=("SA000022", "SA000023"))
    client = _FakeClient({"SA000022": _sample_record("SA000022")})

    uploaded = cli._upload_contig_to_bin_attachments(
        client, "Samples", drakkar.contig_to_bin_files(work_dir)
    )

    assert uploaded == 1
    assert client.uploads == [
        ("recSA000022", "contig_to_bin", "SA000022_contig_to_bin.tsv.gz")
    ]


def test_upload_skips_a_table_over_the_attachment_limit(tmp_path, contig_to_bin_config):
    # A contig-to-bin table gzips well, so the cap is lowered rather than a
    # multi-megabyte fixture written: what is under test is the size check.
    work_dir = _make_binette_output(tmp_path)
    client = _FakeClient({"SA000022": _sample_record("SA000022")})

    with patch("wmw.airtable.ATTACHMENT_MAX_BYTES", 8):
        uploaded = cli._upload_contig_to_bin_attachments(
            client, "Samples", drakkar.contig_to_bin_files(work_dir)
        )

    assert uploaded == 0
    assert client.uploads == []


def test_upload_does_nothing_when_the_column_is_not_configured(
    tmp_path, no_contig_to_bin_config
):
    work_dir = _make_binette_output(tmp_path)
    client = _FakeClient({"SA000022": _sample_record("SA000022")})

    uploaded = cli._upload_contig_to_bin_attachments(
        client, "Samples", drakkar.contig_to_bin_files(work_dir)
    )

    assert uploaded == 0
    assert client.uploads == []


def test_upload_survives_a_failing_attachment(tmp_path, contig_to_bin_config):
    work_dir = _make_binette_output(tmp_path, samples=("SA000022",))
    client = _FakeClient({"SA000022": _sample_record("SA000022")})
    client.upload_error = RuntimeError("Airtable said no")

    uploaded = cli._upload_contig_to_bin_attachments(
        client, "Samples", drakkar.contig_to_bin_files(work_dir)
    )

    assert uploaded == 0


# ---------------------------------------------------------------------------
# the standalone command
# ---------------------------------------------------------------------------

def test_cmd_upload_contig_to_bin_attaches_the_tables(tmp_path, contig_to_bin_config):
    _make_binette_output(tmp_path)
    client = _FakeClient({"SA000022": _sample_record("SA000022")})

    with patch("wmw.cli._require_airtable", return_value=client) as require_airtable:
        assert cli.cmd_upload_contig_to_bin(_upload_args(tmp_path)) == 0

    require_airtable.assert_called_once()
    assert client.uploads == [
        ("recSA000022", "contig_to_bin", "SA000022_contig_to_bin.tsv.gz")
    ]


def test_cmd_upload_contig_to_bin_reports_when_the_run_left_no_tables(
    tmp_path, contig_to_bin_config
):
    (tmp_path / "ST001").mkdir()

    with patch("wmw.cli._require_airtable") as require_airtable:
        assert cli.cmd_upload_contig_to_bin(_upload_args(tmp_path)) == 1

    require_airtable.assert_not_called()


def test_cmd_upload_contig_to_bin_needs_the_column_configured(
    tmp_path, no_contig_to_bin_config
):
    _make_binette_output(tmp_path)

    with pytest.raises(SystemExit):
        cli.cmd_upload_contig_to_bin(_upload_args(tmp_path))


# ---------------------------------------------------------------------------
# cataloging finalisation
# ---------------------------------------------------------------------------

def test_finalize_cataloging_attaches_the_contig_to_bin_tables(
    tmp_path, contig_to_bin_config
):
    work_dir = _make_binette_output(tmp_path)
    (work_dir / "ST001_cataloging.tsv").write_text(
        "assembly\tassembly_N50\nSA000022\t12345\n", encoding="utf-8"
    )
    client = _FakeClient({"SA000022": _sample_record("SA000022")})
    client.update_sample_cataloging_stats = lambda *a, **k: 1
    client.upload_study_file = lambda *a, **k: {}
    client.set_study_status = lambda *a, **k: None

    with (
        patch("wmw.cli._populate_genome_records_from_outputs", return_value=False),
        patch("wmw.cli._transfer_cataloging_outputs_to_erda"),
    ):
        cli._finalize_cataloging_outputs(
            client,
            "Studies",
            "Samples",
            "Genomes",
            {"id": "recST001", "fields": {"code": "ST001"}},
            tmp_path,
        )

    assert client.uploads == [
        ("recSA000022", "contig_to_bin", "SA000022_contig_to_bin.tsv.gz")
    ]
