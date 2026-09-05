"""Tests for the AMR workflow — qc parsing, launch script, ERDA transfer, finalisation."""

from __future__ import annotations

import argparse
import gzip
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from wmw import cli, drakkar, transfer
from wmw import config as cfg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

AMR_FIELD_IDS = {
    "SAMPLES_COL_AMR_AMRFINDER_HITS":   "fldAmrFinderHits",
    "SAMPLES_COL_AMR_RGI_HITS":         "fldAmrRgiHits",
    "SAMPLES_COL_AMR_MOBILITY_REGIONS": "fldAmrMobRegions",
    "SAMPLES_COL_AMR_LOCI":             "fldAmrLoci",
    "SAMPLES_COL_AMR_MULTI_TOOL_LOCI":  "fldAmrMultiTool",
    "SAMPLES_COL_AMR_MOBILITY_LINKS":   "fldAmrMobLinks",
    "SAMPLES_COL_AMR_MOBILE_LOCI":      "fldAmrMobileLoci",
    "STUDIES_COL_FILE_AMR_HITS":        "fldFileAmrHits",
    "STUDIES_COL_FILE_AMR_LOCI":        "fldFileAmrLoci",
    "STUDIES_COL_FILE_AMR_DRUG_CLASSES": "fldFileAmrDrugs",
    "STUDIES_COL_FILE_AMR_MOBILITY":    "fldFileAmrMobility",
    "STUDIES_COL_FILE_AMR_MOBILITY_REGIONS": "fldFileAmrMobRegions",
    "STUDIES_COL_FILE_AMR_MANIFEST":    "fldFileAmrManifest",
}


@pytest.fixture()
def amr_config():
    """Fill in the AMR field IDs, which ship blank so uploads stay opt-in."""
    merged = {**cfg.load_config(), **AMR_FIELD_IDS}
    with patch("wmw.config.load_config", return_value=merged):
        yield merged


QC_HEADER = (
    "assembly_id\tamrfinder_hits\tamrfinder_hits_without_coordinates\t"
    "rgi_hits\trgi_hits_without_coordinates\tmobility_regions\tamr_loci\t"
    "multi_tool_loci\tmobility_links\tmobile_loci\n"
)
QC_ROW = "SA000022\t12\t1\t9\t2\t4\t15\t6\t7\t3\n"


def _make_amr_output(root: Path, code: str = "ST001", *, qc: str = QC_ROW) -> Path:
    """Build a minimal drakkar amr output tree and return the work dir."""
    work_dir = root / code
    amr_dir = drakkar.amr_results_dir(work_dir)
    amr_dir.mkdir(parents=True)
    for name in drakkar.AMR_TABLE_FILES:
        (amr_dir / name).write_bytes(b"\xfd7zXZ" + name.encode())
    (amr_dir / "amr_qc.tsv").write_text(QC_HEADER + qc, encoding="utf-8")
    (amr_dir / "assembly_summary.tsv").write_text(
        "assembly_id\tcontig_count\nSA000022\t42\n", encoding="utf-8"
    )
    (amr_dir / "manifest.yaml").write_text("drakkar: 2.5.2\n", encoding="utf-8")
    return work_dir


def _make_assemblies(root: Path, code: str = "ST001") -> Path:
    work_dir = root / code
    megahit = work_dir / "cataloging" / "megahit" / "SA000022"
    megahit.mkdir(parents=True, exist_ok=True)
    (megahit / "SA000022.fna").write_text(">contig1\nACGT\n", encoding="utf-8")
    return work_dir


def _amr_args(output_dir: Path, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        study="ST001",
        what="amr",
        output_dir=str(output_dir),
        sftp_host="io.erda.dk",
        sftp_user="user@example.com",
        sftp_port="",
        sftp_identity="",
        sftp_remote_base="/WMW",
        sftp_assembly_dir="",
        sftp_bin_dir="",
        sftp_amr_dir="",
        replace_files=False,
        verbose=False,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


class _FakeTransfer:
    """Stand-in for SFTPTransfer that records what would go to ERDA."""

    def __init__(self, existing: set[str] | None = None, **_: object) -> None:
        self.existing = set(existing or ())
        self.streamed: dict[str, bytes] = {}
        self.removed_dirs: list[str] = []

    def __enter__(self) -> "_FakeTransfer":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def remote_exists(self, remote_path: str) -> bool:
        return remote_path in self.existing

    def remove_remote_dir(self, remote_dir: str) -> None:
        self.removed_dirs.append(remote_dir)
        self.existing = {p for p in self.existing if not p.startswith(remote_dir + "/")}

    def upload_stream(self, remote_path, writer, verbose=False) -> None:
        buf = io.BytesIO()
        writer(buf)
        self.streamed[remote_path] = buf.getvalue()
        self.existing.add(remote_path)

    def upload_file(self, source, remote_path, verbose=False, skip_existing=True) -> bool:
        if skip_existing and self.remote_exists(remote_path):
            return False
        self.upload_stream(remote_path, lambda h: h.write(Path(source).read_bytes()))
        return True

    def upload_gzipped(self, source, remote_path, verbose=False, skip_existing=True) -> bool:
        if skip_existing and self.remote_exists(remote_path):
            return False
        self.upload_stream(remote_path, lambda h: transfer.gzip_into(source, h))
        return True


class _FakeClient:
    """Records the Airtable writes the AMR finaliser makes."""

    def __init__(self, *, updated: int = 1) -> None:
        self.updated = updated
        self.amr_stats: list[dict] = []
        self.statuses: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str]] = []
        self.cleared: list[str] = []

    def update_sample_amr_stats(self, samples_table, stats):
        self.amr_stats.append(stats)
        return self.updated

    def set_study_status(self, studies_table, record_ids, status):
        self.statuses.append(status)

    def upload_study_file(self, studies_table, record_id, field_name, path, content_type=None):
        self.uploads.append((field_name, Path(path).name))
        return {}

    def clear_study_file(self, studies_table, record_id, field_name):
        self.cleared.append(field_name)


def _study(code: str = "ST001", **fields) -> dict:
    return {"id": "recST001", "fields": {"code": code, **fields}}


# ---------------------------------------------------------------------------
# amr_qc.tsv parsing
# ---------------------------------------------------------------------------

def test_parse_amr_qc_keys_rows_by_assembly_id(tmp_path, amr_config):
    work_dir = _make_amr_output(tmp_path)
    parsed = drakkar.parse_amr_qc_tsv(drakkar.amr_qc_path(work_dir))

    assert list(parsed) == ["SA000022"]
    assert parsed["SA000022"] == {
        "fldAmrFinderHits": 12,
        "fldAmrRgiHits": 9,
        "fldAmrMobRegions": 4,
        "fldAmrLoci": 15,
        "fldAmrMultiTool": 6,
        "fldAmrMobLinks": 7,
        "fldAmrMobileLoci": 3,
    }


def test_parse_amr_qc_never_writes_the_caller_diagnostics(tmp_path, amr_config):
    """The two '*_without_coordinates' columns are diagnostics, not results."""
    work_dir = _make_amr_output(tmp_path)
    parsed = drakkar.parse_amr_qc_tsv(drakkar.amr_qc_path(work_dir))
    # 1 and 2 are the diagnostic values in QC_ROW; neither reaches Airtable.
    assert 1 not in parsed["SA000022"].values()
    assert 2 not in parsed["SA000022"].values()


def test_parse_amr_qc_skips_rows_without_an_assembly_id(tmp_path, amr_config):
    work_dir = _make_amr_output(
        tmp_path, qc=QC_ROW + "\t0\t0\t0\t0\t0\t0\t0\t0\t0\n"
    )
    parsed = drakkar.parse_amr_qc_tsv(drakkar.amr_qc_path(work_dir))
    assert list(parsed) == ["SA000022"]


def test_parse_amr_qc_tolerates_missing_values(tmp_path, amr_config):
    work_dir = _make_amr_output(tmp_path, qc="SA000022\t12\t1\tNA\t2\t\t15\t6\t7\t3\n")
    parsed = drakkar.parse_amr_qc_tsv(drakkar.amr_qc_path(work_dir))
    assert "fldAmrRgiHits" not in parsed["SA000022"]
    assert "fldAmrMobRegions" not in parsed["SA000022"]
    assert parsed["SA000022"]["fldAmrFinderHits"] == 12


def test_parse_amr_qc_returns_empty_for_a_missing_file(tmp_path):
    assert drakkar.parse_amr_qc_tsv(tmp_path / "nope.tsv") == {}


def test_parse_amr_qc_returns_empty_when_no_field_is_configured(tmp_path):
    """The AMR columns ship blank, so an unconfigured base writes nothing."""
    work_dir = _make_amr_output(tmp_path)
    assert drakkar.parse_amr_qc_tsv(drakkar.amr_qc_path(work_dir)) == {}


def test_amr_outputs_present_tracks_the_qc_summary(tmp_path):
    work_dir = _make_amr_output(tmp_path)
    assert drakkar.amr_outputs_present(work_dir) is True
    drakkar.amr_qc_path(work_dir).unlink()
    assert drakkar.amr_outputs_present(work_dir) is False


# ---------------------------------------------------------------------------
# launch script
# ---------------------------------------------------------------------------

def test_amr_script_runs_drakkar_amr_over_the_work_dir(tmp_path):
    work_dir = tmp_path / "ST001"
    script = drakkar.generate_amr_script("ST001", work_dir, conda_env="")

    assert f"drakkar amr -i {work_dir} -o {work_dir}" in script
    assert "--env_path" in script


def test_amr_script_reports_the_stage_transitions(tmp_path):
    work_dir = tmp_path / "ST001"
    script = drakkar.generate_amr_script("ST001", work_dir, conda_env="")

    assert f"exec >> {work_dir}/ST001.out 2>> {work_dir}/ST001.err" in script
    assert f'_WMW_STOP_FILE={work_dir}/.wmw-stop' in script
    assert "--workflow amr --status amr " in script
    assert "--workflow amr --status amr_done" in script
    assert "--workflow amr --status error" in script
    assert "--workflow amr --status stopped" in script


def test_amr_script_fails_when_the_qc_summary_is_absent(tmp_path):
    work_dir = tmp_path / "ST001"
    script = drakkar.generate_amr_script("ST001", work_dir, conda_env="")
    qc_path = drakkar.amr_qc_path(work_dir)
    assert f"if [ ! -f {qc_path} ]; then" in script
    assert "Missing required AMR output" in script


def test_amr_script_passes_slurm_and_boost_options(tmp_path):
    script = drakkar.generate_amr_script(
        "ST001",
        tmp_path / "ST001",
        conda_env="",
        slurm=True,
        memory_multiplier="2",
        time_multiplier="1.5",
        slurm_partition="lazyqueue",
        slurm_qos="lazy",
    )
    assert "-p slurm" in script
    assert "--memory-multiplier 2" in script
    assert "--time-multiplier 1.5" in script
    assert "--slurm-partition lazyqueue" in script
    assert "--slurm-qos lazy" in script


def test_amr_script_runs_drakkar_inside_the_conda_env(tmp_path):
    script = drakkar.generate_amr_script(
        "ST001", tmp_path / "ST001", conda_env="/envs/drakkar", wmw_conda_env="/envs/wmw"
    )
    assert "conda run -p /envs/drakkar drakkar amr" in script
    assert "conda run -p /envs/wmw wmw set-status" in script


# ---------------------------------------------------------------------------
# ERDA file selection and transfer
# ---------------------------------------------------------------------------

def test_amr_erda_files_are_study_prefixed_and_compressed_once(tmp_path):
    work_dir = _make_amr_output(tmp_path)
    selected = {name: compress for _, name, compress in cli._amr_erda_files(work_dir, "ST001")}

    assert selected == {
        # already xz-compressed: sent as they are
        "ST001_amr_hits.tsv.xz": False,
        "ST001_amr_loci.tsv.xz": False,
        "ST001_amr_drug_classes.tsv.xz": False,
        "ST001_amr_mobility.tsv.xz": False,
        "ST001_mobility_regions.tsv.xz": False,
        # plain text: gzipped into the connection
        "ST001_amr_qc.tsv.gz": True,
        "ST001_assembly_summary.tsv.gz": True,
        "ST001_amr_manifest.yaml": False,
    }


def test_amr_erda_files_skips_what_the_run_did_not_write(tmp_path):
    work_dir = _make_amr_output(tmp_path)
    (drakkar.amr_results_dir(work_dir) / "manifest.yaml").unlink()
    (drakkar.amr_results_dir(work_dir) / "amr_hits.tsv.xz").unlink()
    names = [name for _, name, _ in cli._amr_erda_files(work_dir, "ST001")]
    assert "ST001_amr_manifest.yaml" not in names
    assert "ST001_amr_hits.tsv.xz" not in names
    assert "ST001_amr_loci.tsv.xz" in names


def test_upload_amr_to_erda_sends_every_table(tmp_path):
    _make_amr_output(tmp_path)
    fake = _FakeTransfer()

    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli._upload_amr_outputs_to_erda(
            _amr_args(tmp_path), "ST001", tmp_path
        ) is True

    assert sorted(fake.streamed) == [
        "/WMW/ST001/amr/ST001_amr_drug_classes.tsv.xz",
        "/WMW/ST001/amr/ST001_amr_hits.tsv.xz",
        "/WMW/ST001/amr/ST001_amr_loci.tsv.xz",
        "/WMW/ST001/amr/ST001_amr_manifest.yaml",
        "/WMW/ST001/amr/ST001_amr_mobility.tsv.xz",
        "/WMW/ST001/amr/ST001_amr_qc.tsv.gz",
        "/WMW/ST001/amr/ST001_assembly_summary.tsv.gz",
        "/WMW/ST001/amr/ST001_mobility_regions.tsv.xz",
    ]
    # The .xz tables go up byte-for-byte; only the plain summaries are gzipped.
    assert fake.streamed["/WMW/ST001/amr/ST001_amr_hits.tsv.xz"].startswith(b"\xfd7zXZ")
    assert gzip.decompress(
        fake.streamed["/WMW/ST001/amr/ST001_amr_qc.tsv.gz"]
    ) == (QC_HEADER + QC_ROW).encode()
    assert fake.removed_dirs == []


def test_upload_amr_to_erda_skips_tables_already_present(tmp_path):
    _make_amr_output(tmp_path)
    fake = _FakeTransfer(
        existing={
            "/WMW/ST001/amr/ST001_amr_hits.tsv.xz",
            "/WMW/ST001/amr/ST001_amr_qc.tsv.gz",
        }
    )
    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli._upload_amr_outputs_to_erda(
            _amr_args(tmp_path), "ST001", tmp_path
        ) is True

    assert "/WMW/ST001/amr/ST001_amr_hits.tsv.xz" not in fake.streamed
    assert "/WMW/ST001/amr/ST001_amr_loci.tsv.xz" in fake.streamed


def test_upload_amr_to_erda_replace_clears_the_remote_folder_first(tmp_path):
    _make_amr_output(tmp_path)
    fake = _FakeTransfer(existing={"/WMW/ST001/amr/ST001_amr_hits.tsv.xz"})
    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        cli._upload_amr_outputs_to_erda(
            _amr_args(tmp_path), "ST001", tmp_path, replace_existing=True
        )

    assert fake.removed_dirs == ["/WMW/ST001/amr"]
    assert "/WMW/ST001/amr/ST001_amr_hits.tsv.xz" in fake.streamed


def test_upload_amr_to_erda_honours_a_custom_remote_folder(tmp_path):
    _make_amr_output(tmp_path)
    fake = _FakeTransfer()
    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        cli._upload_amr_outputs_to_erda(
            _amr_args(tmp_path, sftp_amr_dir="resistome"), "ST001", tmp_path
        )
    assert all(p.startswith("/WMW/ST001/resistome/") for p in fake.streamed)


def test_upload_amr_to_erda_reports_nothing_to_send_for_an_empty_run(tmp_path):
    drakkar.amr_results_dir(tmp_path / "ST001").mkdir(parents=True)
    with patch("wmw.transfer.paramiko_available", return_value=True):
        assert cli._upload_amr_outputs_to_erda(
            _amr_args(tmp_path), "ST001", tmp_path
        ) is False


def test_upload_amr_to_erda_is_skipped_when_not_configured(tmp_path):
    _make_amr_output(tmp_path)
    args = _amr_args(tmp_path, sftp_host="", sftp_remote_base="")
    with (
        patch("wmw.config.get", return_value=""),
        patch("wmw.transfer.SFTPTransfer") as sftp,
    ):
        assert cli._upload_amr_outputs_to_erda(args, "ST001", tmp_path) is False
    sftp.assert_not_called()


def test_upload_amr_to_erda_survives_a_connection_failure(tmp_path):
    _make_amr_output(tmp_path)
    with (
        patch("wmw.transfer.paramiko_available", return_value=True),
        patch("wmw.transfer.SFTPTransfer", side_effect=OSError("no route to host")),
    ):
        assert cli._upload_amr_outputs_to_erda(
            _amr_args(tmp_path), "ST001", tmp_path
        ) is False


def test_upload_amr_to_erda_continues_past_one_failed_table(tmp_path):
    _make_amr_output(tmp_path)
    fake = _FakeTransfer()
    real_upload = fake.upload_file

    def flaky(source, remote_path, verbose=False, skip_existing=True):
        if remote_path.endswith("ST001_amr_hits.tsv.xz"):
            raise OSError("connection reset")
        return real_upload(source, remote_path, verbose, skip_existing)

    fake.upload_file = flaky
    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli._upload_amr_outputs_to_erda(
            _amr_args(tmp_path), "ST001", tmp_path
        ) is False
    assert "/WMW/ST001/amr/ST001_amr_loci.tsv.xz" in fake.streamed


def test_upload_file_sends_the_bytes_unchanged(tmp_path):
    source = tmp_path / "table.tsv.xz"
    source.write_bytes(b"\xfd7zXZ raw bytes")
    buf = io.BytesIO()
    transfer._copy_into(source, buf)
    assert buf.getvalue() == b"\xfd7zXZ raw bytes"


# ---------------------------------------------------------------------------
# screen sessions, stop markers, and the standalone command
# ---------------------------------------------------------------------------

def test_cmd_upload_erda_what_amr_transfers_only_the_tables(tmp_path):
    _make_amr_output(tmp_path)
    fake = _FakeTransfer()
    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli.cmd_upload_erda(_amr_args(tmp_path, what="amr")) == 0
    assert all("/amr/" in p for p in fake.streamed)


def test_cmd_upload_erda_what_all_transfers_both_payloads(tmp_path):
    _make_amr_output(tmp_path)
    _make_assemblies(tmp_path)
    (tmp_path / "ST001" / "cataloging" / "final").mkdir(parents=True)
    (tmp_path / "ST001" / "cataloging" / "final" / "all_bin_paths.txt").write_text(
        "", encoding="utf-8"
    )
    fake = _FakeTransfer()
    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli.cmd_upload_erda(_amr_args(tmp_path, what="all")) == 0
    assert "/WMW/ST001/assemblies/SA000022_contigs.fasta.gz" in fake.streamed
    assert "/WMW/ST001/amr/ST001_amr_hits.tsv.xz" in fake.streamed


def test_cmd_upload_erda_defaults_to_cataloging(tmp_path):
    _make_amr_output(tmp_path)
    args = _amr_args(tmp_path)
    del args.what
    with (
        patch("wmw.cli._upload_cataloging_outputs_to_erda", return_value=True) as cataloging,
        patch("wmw.cli._upload_amr_outputs_to_erda") as amr,
    ):
        assert cli.cmd_upload_erda(args) == 0
    cataloging.assert_called_once()
    amr.assert_not_called()


# ---------------------------------------------------------------------------
# status reporting
# ---------------------------------------------------------------------------

def test_amr_status_map_covers_the_generated_script_transitions():
    # The generated scripts say amr/amr_done; the base's select offers
    # amring/amred, so the map is what bridges the two vocabularies.
    assert cli._PROCESS_STATUS_MAP[("amr", "amr")] == "amring"
    assert cli._PROCESS_STATUS_MAP[("amr", "amr_done")] == "amred"
    assert cli._PROCESS_STATUS_MAP[("amr", "completed")] == "amred"
    assert cli._PROCESS_STATUS_MAP[("amr", "stopped")] == "stopped"
    assert cli._PROCESS_STATUS_MAP[("amr", "error")] == "error"


def test_amr_status_map_also_accepts_the_airtable_spellings():
    assert cli._PROCESS_STATUS_MAP[("amr", "amring")] == "amring"
    assert cli._PROCESS_STATUS_MAP[("amr", "amred")] == "amred"


# ---------------------------------------------------------------------------
# finalisation
# ---------------------------------------------------------------------------

def test_finalize_amr_writes_stats_attaches_tables_and_transfers(tmp_path, amr_config):
    _make_amr_output(tmp_path)
    client = _FakeClient()

    with patch("wmw.cli._upload_amr_outputs_to_erda") as xfer:
        assert cli._finalize_amr_outputs(
            client, "Studies", "Samples", _study(), tmp_path,
            set_status=True, transfer_args=_amr_args(tmp_path),
        ) is True

    assert client.amr_stats == [{"SA000022": {
        "fldAmrFinderHits": 12,
        "fldAmrRgiHits": 9,
        "fldAmrMobRegions": 4,
        "fldAmrLoci": 15,
        "fldAmrMultiTool": 6,
        "fldAmrMobLinks": 7,
        "fldAmrMobileLoci": 3,
    }}]
    assert client.statuses == ["amred"]
    assert sorted(client.uploads) == [
        ("file_amr_drug_classes", "ST001_amr_drug_classes.tsv.xz"),
        ("file_amr_hits", "ST001_amr_hits.tsv.xz"),
        ("file_amr_loci", "ST001_amr_loci.tsv.xz"),
        ("file_amr_manifest", "ST001_amr_manifest.yaml"),
        ("file_amr_mobility", "ST001_amr_mobility.tsv.xz"),
        ("file_amr_mobility_regions", "ST001_mobility_regions.tsv.xz"),
    ]
    xfer.assert_called_once()


def test_finalize_amr_leaves_no_prefixed_copies_behind(tmp_path, amr_config):
    work_dir = _make_amr_output(tmp_path)
    with patch("wmw.cli._upload_amr_outputs_to_erda"):
        cli._finalize_amr_outputs(
            _FakeClient(), "Studies", "Samples", _study(), tmp_path
        )
    leftovers = sorted(
        p.name for p in drakkar.amr_results_dir(work_dir).iterdir()
        if p.name.startswith("ST001_")
    )
    assert leftovers == []


def test_finalize_amr_stops_when_the_run_left_no_summary(tmp_path, amr_config):
    work_dir = _make_amr_output(tmp_path)
    drakkar.amr_qc_path(work_dir).unlink()
    client = _FakeClient()

    with patch("wmw.cli._upload_amr_outputs_to_erda") as xfer:
        assert cli._finalize_amr_outputs(
            client, "Studies", "Samples", _study(), tmp_path,
            set_status=True, transfer_args=_amr_args(tmp_path),
        ) is False

    assert client.statuses == []
    assert client.uploads == []
    xfer.assert_not_called()


def test_finalize_amr_attaches_nothing_when_no_file_field_is_configured(tmp_path):
    _make_amr_output(tmp_path)
    client = _FakeClient()
    with patch("wmw.cli._upload_amr_outputs_to_erda"):
        assert cli._finalize_amr_outputs(
            client, "Studies", "Samples", _study(), tmp_path
        ) is True
    assert client.uploads == []


def test_finalize_amr_leaves_an_oversize_table_on_erda_only(tmp_path, amr_config):
    work_dir = _make_amr_output(tmp_path)
    (drakkar.amr_results_dir(work_dir) / "amr_hits.tsv.xz").write_bytes(b"x" * (6 * 1024 * 1024))
    client = _FakeClient()

    with patch("wmw.cli._upload_amr_outputs_to_erda"):
        cli._finalize_amr_outputs(client, "Studies", "Samples", _study(), tmp_path)

    assert ("file_amr_hits", "ST001_amr_hits.tsv.xz") not in client.uploads
    assert ("file_amr_loci", "ST001_amr_loci.tsv.xz") in client.uploads


def test_finalize_amr_replaces_rather_than_stacks_attachments(tmp_path, amr_config):
    """Airtable's upload endpoint appends, so a rerun clears the field first."""
    _make_amr_output(tmp_path)
    client = _FakeClient()
    study = _study(file_amr_hits=[{"filename": "ST001_amr_hits.tsv.xz"}])

    with patch("wmw.cli._upload_amr_outputs_to_erda"):
        cli._finalize_amr_outputs(
            client, "Studies", "Samples", study, tmp_path,
            replace_existing_attachments=True,
        )
    assert client.cleared == ["file_amr_hits"]


def test_finalize_amr_skips_an_attachment_already_present_on_resume(tmp_path, amr_config):
    _make_amr_output(tmp_path)
    client = _FakeClient()
    study = _study(file_amr_hits=[{"filename": "ST001_amr_hits.tsv.xz"}])

    with patch("wmw.cli._upload_amr_outputs_to_erda"):
        cli._finalize_amr_outputs(
            client, "Studies", "Samples", study, tmp_path,
            skip_existing_attachments=True,
        )
    assert ("file_amr_hits", "ST001_amr_hits.tsv.xz") not in client.uploads
    assert client.cleared == []


def test_finalize_amr_still_archives_when_no_sample_row_matches(tmp_path, amr_config):
    _make_amr_output(tmp_path)
    client = _FakeClient(updated=0)
    with patch("wmw.cli._upload_amr_outputs_to_erda") as xfer:
        assert cli._finalize_amr_outputs(
            client, "Studies", "Samples", _study(), tmp_path,
            transfer_args=_amr_args(tmp_path),
        ) is True
    xfer.assert_called_once()


def test_finalize_amr_skips_the_transfer_without_connection_args(tmp_path, amr_config):
    """Airtable work still happens; only the ERDA leg needs SFTP settings."""
    _make_amr_output(tmp_path)
    client = _FakeClient()
    with patch("wmw.cli._upload_amr_outputs_to_erda") as xfer:
        assert cli._finalize_amr_outputs(
            client, "Studies", "Samples", _study(), tmp_path
        ) is True
    xfer.assert_not_called()
    assert client.amr_stats


# ---------------------------------------------------------------------------
# the stage in the pipeline
# ---------------------------------------------------------------------------

def test_process_workflow_amr_writes_the_shared_batch_script(tmp_path):
    """--workflow amr is an ordinary stage: same script name, same screen session."""
    from unittest.mock import MagicMock

    work_dir = _make_assemblies(tmp_path)
    args = argparse.Namespace(
        batch="", workflow="amr", slurm=False, output_dir=str(tmp_path),
        studies_table="Studies", samples_table="Samples", genomes_table="Genomes",
        airtable_token="", base_id="",
    )
    client = MagicMock()
    client.fetch_studies_by_status.side_effect = lambda _t, status: (
        [{"id": "recStudy", "fields": {"code": "ST001", "study_accession": "PRJEB001",
                                       "status": "ready"}}]
        if status == "ready" else []
    )
    client.fetch_samples_for_study.return_value = [
        {"id": "recS1", "fields": {"code": "SA000022", "status": "use",
                                   "fastq_url_1": "ftp://h/a_1.fq.gz",
                                   "fastq_url_2": "ftp://h/a_2.fq.gz"}}
    ]

    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("shutil.which", return_value=None),
    ):
        assert cli.cmd_process(args) == 0

    script = (work_dir / "ST001.sh").read_text(encoding="utf-8")
    assert "drakkar amr" in script
    assert "--workflow amr --status amr_done" in script


def test_amr_runs_between_cataloging_and_profiling_in_the_pipeline(tmp_path):
    script = drakkar.generate_full_pipeline_script(
        code="ST001",
        tsv_path=tmp_path / "ST001.tsv",
        work_dir=tmp_path,
        conda_env="",
    )
    assert (
        script.index("drakkar cataloging")
        < script.index("drakkar amr")
        < script.index("drakkar profiling")
    )
