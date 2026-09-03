"""Tests for the ERDA transfer wiring (wmw.transfer and the cli orchestration)."""

from __future__ import annotations

import argparse
import gzip
import io
from pathlib import Path
from unittest.mock import patch

from wmw import cli, transfer
from wmw import config as cfg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_cataloging_output(root: Path, code: str = "ST001") -> Path:
    """Build a minimal drakkar cataloging output tree and return the work dir."""
    work_dir = root / code
    megahit = work_dir / "cataloging" / "megahit" / "SA000022"
    megahit.mkdir(parents=True)
    (megahit / "SA000022.fna").write_text(">contig1\nACGTACGT\n", encoding="utf-8")
    # megahit's own raw contigs keep the .fa suffix and must not be transferred
    (megahit / "final.contigs.raw.fa").write_text(">raw\nAAAA\n", encoding="utf-8")

    final = work_dir / "cataloging" / "final" / "SA000022"
    final.mkdir(parents=True)
    (final / "SA000022_bin_1.fa").write_text(">bin1\nGGGG\n", encoding="utf-8")
    (final / "SA000022_bin_2.fa").write_text(">bin2\nTTTT\n", encoding="utf-8")
    (work_dir / "cataloging" / "final" / "all_bin_paths.txt").write_text(
        "cataloging/final/SA000022/SA000022_bin_1.fa\n"
        "cataloging/final/SA000022/SA000022_bin_2.fa\n",
        encoding="utf-8",
    )
    return work_dir


def _erda_args(output_dir: Path, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        study="ST001",
        output_dir=str(output_dir),
        sftp_host="io.erda.dk",
        sftp_user="user@example.com",
        sftp_port="",
        sftp_identity="",
        sftp_remote_base="/WMW",
        sftp_assembly_dir="",
        sftp_bin_dir="",
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

    def upload_gzipped(self, source, remote_path, verbose=False, skip_existing=True) -> bool:
        if skip_existing and self.remote_exists(remote_path):
            return False
        self.upload_stream(remote_path, lambda h: transfer.gzip_into(source, h))
        return True


# ---------------------------------------------------------------------------
# path helpers
# ---------------------------------------------------------------------------

def test_find_assembly_fastas_picks_renamed_contigs_only(tmp_path):
    work_dir = _make_cataloging_output(tmp_path)
    found = cli._find_assembly_fastas(work_dir)
    assert [p.name for p in found] == ["SA000022.fna"]


def test_find_assembly_fastas_returns_empty_without_megahit_dir(tmp_path):
    assert cli._find_assembly_fastas(tmp_path / "nothing") == []


def test_assembly_remote_name_uses_erda_contigs_convention():
    assert cli._assembly_remote_name(Path("/x/SA000022.fna")) == "SA000022_contigs.fasta.gz"


def test_gzip_into_writes_a_readable_gzip_stream(tmp_path):
    source = tmp_path / "a.fna"
    source.write_text(">c\nACGT\n", encoding="utf-8")
    buf = io.BytesIO()
    transfer.gzip_into(source, buf)
    assert gzip.decompress(buf.getvalue()) == b">c\nACGT\n"


# ---------------------------------------------------------------------------
# settings resolution
# ---------------------------------------------------------------------------

def test_erda_settings_returns_none_without_host(tmp_path):
    args = _erda_args(tmp_path, sftp_host="")
    with patch("wmw.config.get", return_value=""):
        assert cli._erda_settings(args) is None


def test_erda_settings_falls_back_to_port_22_on_a_bad_value(tmp_path):
    args = _erda_args(tmp_path, sftp_port="not-a-number")
    settings = cli._erda_settings(args)
    assert settings is not None
    assert settings["port"] == 22


def test_erda_settings_strips_trailing_slash_from_remote_base(tmp_path):
    settings = cli._erda_settings(_erda_args(tmp_path, sftp_remote_base="/WMW/"))
    assert settings is not None
    assert settings["remote_base"] == "/WMW"


# ---------------------------------------------------------------------------
# transfer orchestration
# ---------------------------------------------------------------------------

def test_upload_to_erda_sends_assemblies_and_bins_study_first(tmp_path):
    _make_cataloging_output(tmp_path)
    fake = _FakeTransfer()

    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli._upload_cataloging_outputs_to_erda(
            _erda_args(tmp_path), "ST001", tmp_path
        ) is True

    assert sorted(fake.streamed) == [
        "/WMW/ST001/assemblies/SA000022_contigs.fasta.gz",
        "/WMW/ST001/bins/SA000022_bin_1.fa.gz",
        "/WMW/ST001/bins/SA000022_bin_2.fa.gz",
    ]
    assert gzip.decompress(
        fake.streamed["/WMW/ST001/assemblies/SA000022_contigs.fasta.gz"]
    ) == b">contig1\nACGTACGT\n"
    assert gzip.decompress(
        fake.streamed["/WMW/ST001/bins/SA000022_bin_1.fa.gz"]
    ) == b">bin1\nGGGG\n"
    assert fake.removed_dirs == []


def test_upload_to_erda_skips_files_already_present(tmp_path):
    _make_cataloging_output(tmp_path)
    fake = _FakeTransfer(
        existing={
            "/WMW/ST001/assemblies/SA000022_contigs.fasta.gz",
            "/WMW/ST001/bins/SA000022_bin_1.fa.gz",
        }
    )

    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        cli._upload_cataloging_outputs_to_erda(_erda_args(tmp_path), "ST001", tmp_path)

    assert list(fake.streamed) == ["/WMW/ST001/bins/SA000022_bin_2.fa.gz"]


def test_upload_to_erda_replace_clears_both_remote_dirs_first(tmp_path):
    _make_cataloging_output(tmp_path)
    fake = _FakeTransfer(existing={"/WMW/ST001/bins/SA000022_bin_1.fa.gz"})

    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        cli._upload_cataloging_outputs_to_erda(
            _erda_args(tmp_path), "ST001", tmp_path, replace_existing=True
        )

    assert fake.removed_dirs == ["/WMW/ST001/assemblies", "/WMW/ST001/bins"]
    assert len(fake.streamed) == 3


def test_upload_to_erda_skips_bins_missing_on_disk(tmp_path):
    work_dir = _make_cataloging_output(tmp_path)
    (work_dir / "cataloging" / "final" / "SA000022" / "SA000022_bin_2.fa").unlink()
    fake = _FakeTransfer()

    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        cli._upload_cataloging_outputs_to_erda(_erda_args(tmp_path), "ST001", tmp_path)

    assert "/WMW/ST001/bins/SA000022_bin_2.fa.gz" not in fake.streamed
    assert "/WMW/ST001/bins/SA000022_bin_1.fa.gz" in fake.streamed


def test_upload_to_erda_is_skipped_when_not_configured(tmp_path):
    _make_cataloging_output(tmp_path)
    args = _erda_args(tmp_path, sftp_host="")
    with (
        patch("wmw.config.get", return_value=""),
        patch("wmw.transfer.SFTPTransfer") as sftp,
    ):
        assert cli._upload_cataloging_outputs_to_erda(args, "ST001", tmp_path) is False
    sftp.assert_not_called()


def test_upload_to_erda_is_skipped_without_a_user(tmp_path):
    _make_cataloging_output(tmp_path)
    # Host and remote base still resolve (from the args); only the user is blank.
    args = _erda_args(tmp_path, sftp_user="")
    real_get = cfg.get

    def only_user_blank(key, default=None):
        return "" if key == "SFTP_USER" else real_get(key, default)

    with (
        patch("wmw.config.get", side_effect=only_user_blank),
        patch("wmw.transfer.SFTPTransfer") as sftp,
    ):
        assert cli._erda_settings(args) is not None
        assert cli._upload_cataloging_outputs_to_erda(args, "ST001", tmp_path) is False
    sftp.assert_not_called()


def test_upload_to_erda_reports_nothing_to_send_for_an_empty_run(tmp_path):
    (tmp_path / "ST001").mkdir()
    with (
        patch("wmw.transfer.SFTPTransfer") as sftp,
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli._upload_cataloging_outputs_to_erda(
            _erda_args(tmp_path), "ST001", tmp_path
        ) is False
    sftp.assert_not_called()


def test_upload_to_erda_survives_a_connection_failure(tmp_path):
    _make_cataloging_output(tmp_path)
    with (
        patch("wmw.transfer.SFTPTransfer", side_effect=OSError("connection refused")),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli._upload_cataloging_outputs_to_erda(
            _erda_args(tmp_path), "ST001", tmp_path
        ) is False


def test_upload_to_erda_succeeds_when_the_study_is_already_fully_archived(tmp_path):
    _make_cataloging_output(tmp_path)
    fake = _FakeTransfer(
        existing={
            "/WMW/ST001/assemblies/SA000022_contigs.fasta.gz",
            "/WMW/ST001/bins/SA000022_bin_1.fa.gz",
            "/WMW/ST001/bins/SA000022_bin_2.fa.gz",
        }
    )
    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        # Nothing moves, but the archive is complete — that is success.
        assert cli.cmd_upload_erda(_erda_args(tmp_path)) == 0
    assert fake.streamed == {}


def test_upload_to_erda_continues_past_one_failed_file(tmp_path):
    _make_cataloging_output(tmp_path)
    fake = _FakeTransfer()
    real_stream = fake.upload_stream

    def flaky(remote_path, writer, verbose=False):
        if remote_path.endswith("SA000022_bin_1.fa.gz"):
            raise OSError("write failed")
        real_stream(remote_path, writer, verbose=verbose)

    fake.upload_stream = flaky  # type: ignore[method-assign]

    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        # A partial transfer is not success: a retry loop must come back.
        assert cli._upload_cataloging_outputs_to_erda(
            _erda_args(tmp_path), "ST001", tmp_path
        ) is False

    assert "/WMW/ST001/assemblies/SA000022_contigs.fasta.gz" in fake.streamed
    assert "/WMW/ST001/bins/SA000022_bin_2.fa.gz" in fake.streamed
    assert "/WMW/ST001/bins/SA000022_bin_1.fa.gz" not in fake.streamed


def test_upload_to_erda_is_skipped_without_paramiko(tmp_path):
    _make_cataloging_output(tmp_path)
    with (
        patch("wmw.transfer.paramiko_available", return_value=False),
        patch("wmw.transfer.SFTPTransfer") as sftp,
    ):
        assert cli._upload_cataloging_outputs_to_erda(
            _erda_args(tmp_path), "ST001", tmp_path
        ) is False
    sftp.assert_not_called()


# ---------------------------------------------------------------------------
# screen detachment and the standalone command
# ---------------------------------------------------------------------------

def test_transfer_runs_inline_when_already_inside_screen(tmp_path):
    _make_cataloging_output(tmp_path)
    with (
        patch.dict("os.environ", {"STY": "1234.session"}),
        patch("wmw.cli._upload_cataloging_outputs_to_erda") as inline,
    ):
        cli._transfer_cataloging_outputs_to_erda(
            _erda_args(tmp_path), "ST001", tmp_path, in_screen=True
        )
    inline.assert_called_once()


def test_transfer_is_a_no_op_without_screen_args(tmp_path):
    with patch("wmw.cli._upload_cataloging_outputs_to_erda") as inline:
        cli._transfer_cataloging_outputs_to_erda(None, "ST001", tmp_path, in_screen=True)
    inline.assert_not_called()


def test_erda_upload_screen_script_carries_no_token(tmp_path):
    _make_cataloging_output(tmp_path)
    args = _erda_args(tmp_path, airtable_token="secret-token")
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("shutil.which", return_value="/usr/bin/screen"),
        patch("subprocess.run") as run,
    ):
        assert cli._launch_erda_upload_screen(args, "ST001", tmp_path) is True

    script_path = tmp_path / "ST001" / "ST001_upload_erda.sh"
    text = script_path.read_text(encoding="utf-8")
    assert "upload-erda" in text
    assert "--study ST001" in text
    assert "secret-token" not in text
    assert run.call_args[0][0][:3] == ["screen", "-dmS", "ST001-erda-upload"]


def test_erda_upload_screen_falls_back_to_inline_without_screen(tmp_path):
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("shutil.which", return_value=None),
    ):
        assert cli._launch_erda_upload_screen(_erda_args(tmp_path), "ST001", tmp_path) is False


def test_stop_matches_the_erda_upload_session():
    sessions = cli._screen_sessions_for_code(
        "\t123.ST001\t(Detached)\n"
        "\t124.ST001-genome-upload\t(Detached)\n"
        "\t125.ST001-erda-upload\t(Detached)\n"
        "\t126.ST002\t(Detached)\n",
        "ST001",
    )
    assert sessions == ["123.ST001", "124.ST001-genome-upload", "125.ST001-erda-upload"]


def test_cmd_upload_erda_returns_nonzero_when_nothing_is_transferred(tmp_path):
    (tmp_path / "ST001").mkdir()
    with patch("wmw.transfer.paramiko_available", return_value=True):
        assert cli.cmd_upload_erda(_erda_args(tmp_path)) == 1


def test_cmd_upload_erda_returns_zero_after_a_transfer(tmp_path):
    _make_cataloging_output(tmp_path)
    fake = _FakeTransfer()
    with (
        patch("wmw.transfer.SFTPTransfer", return_value=fake),
        patch("wmw.transfer.paramiko_available", return_value=True),
    ):
        assert cli.cmd_upload_erda(_erda_args(tmp_path)) == 0
    assert len(fake.streamed) == 3
