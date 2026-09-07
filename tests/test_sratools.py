"""Tests for wmw.sratools — recovering R1/R2 from an unsplit archive run."""

from __future__ import annotations

import gzip
import subprocess
from pathlib import Path

import pytest
from wmw import sratools


def _write_fastq(path: Path, n_reads: int, tag: str = "r") -> Path:
    path.write_text(
        "".join(
            f"@{tag}{i}\nACGT\n+\nJJJJ\n" for i in range(n_reads)
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def test_require_fasterq_dump_raises_when_absent(monkeypatch):
    monkeypatch.setattr(sratools.shutil, "which", lambda _: None)
    with pytest.raises(sratools.SraToolsError, match="not found on PATH"):
        sratools.require_fasterq_dump()


def test_require_fasterq_dump_returns_resolved_path(monkeypatch):
    monkeypatch.setattr(sratools.shutil, "which", lambda _: "/opt/bin/fasterq-dump")
    assert sratools.require_fasterq_dump() == "/opt/bin/fasterq-dump"


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def test_pair_paths_plain_and_gzipped(tmp_path):
    r1, r2 = sratools.pair_paths("SRR1", tmp_path)
    assert r1.name == "SRR1_1.fastq"
    assert r2.name == "SRR1_2.fastq"
    g1, g2 = sratools.pair_paths("SRR1", tmp_path, gzipped=True)
    assert g1.name == "SRR1_1.fastq.gz"
    assert g2.name == "SRR1_2.fastq.gz"


def test_existing_pair_finds_gzipped_and_plain(tmp_path):
    assert sratools.existing_pair("SRR1", tmp_path) is None

    r1, r2 = sratools.pair_paths("SRR1", tmp_path)
    _write_fastq(r1, 2)
    assert sratools.existing_pair("SRR1", tmp_path) is None  # only one mate

    _write_fastq(r2, 2)
    found = sratools.existing_pair("SRR1", tmp_path)
    assert found == (r1, r2)


def test_existing_pair_prefers_gzipped(tmp_path):
    g1, g2 = sratools.pair_paths("SRR1", tmp_path, gzipped=True)
    g1.write_bytes(b"")
    g2.write_bytes(b"")
    assert sratools.existing_pair("SRR1", tmp_path) == (g1, g2)


def test_build_command_includes_split_files_and_temp(tmp_path):
    cmd = sratools.build_command(
        "SRR1", tmp_path, threads=4, tmp_dir=tmp_path / "scratch"
    )
    assert "--split-files" in cmd
    assert cmd[cmd.index("--threads") + 1] == "4"
    assert cmd[cmd.index("--temp") + 1] == str(tmp_path / "scratch")
    assert cmd[-1] == "SRR1"


def test_build_command_omits_temp_when_unset(tmp_path):
    assert "--temp" not in sratools.build_command("SRR1", tmp_path)


# ---------------------------------------------------------------------------
# read counting and verification
# ---------------------------------------------------------------------------

def test_count_reads_plain(tmp_path):
    assert sratools.count_reads(_write_fastq(tmp_path / "a.fastq", 5)) == 5


def test_count_reads_gzipped(tmp_path):
    path = tmp_path / "a.fastq.gz"
    with gzip.open(path, "wt") as handle:
        handle.write("@r1\nACGT\n+\nJJJJ\n")
    assert sratools.count_reads(path) == 1


def test_count_reads_rejects_truncated_file(tmp_path):
    path = tmp_path / "a.fastq"
    path.write_text("@r1\nACGT\n+\n", encoding="utf-8")
    with pytest.raises(sratools.SraToolsError, match="truncated"):
        sratools.count_reads(path)


def test_verify_pair_accepts_equal_counts(tmp_path):
    r1 = _write_fastq(tmp_path / "a_1.fastq", 3)
    r2 = _write_fastq(tmp_path / "a_2.fastq", 3)
    assert sratools.verify_pair(r1, r2) == 3


def test_verify_pair_rejects_mismatched_counts(tmp_path):
    r1 = _write_fastq(tmp_path / "a_1.fastq", 3)
    r2 = _write_fastq(tmp_path / "a_2.fastq", 2)
    with pytest.raises(sratools.SraToolsError, match="not mates"):
        sratools.verify_pair(r1, r2)


# ---------------------------------------------------------------------------
# gzip
# ---------------------------------------------------------------------------

def test_gzip_in_place_without_pigz(tmp_path, monkeypatch):
    monkeypatch.setattr(sratools.shutil, "which", lambda _: None)
    src = _write_fastq(tmp_path / "a.fastq", 2)
    target = sratools.gzip_in_place(src)
    assert target.name == "a.fastq.gz"
    assert not src.exists()
    assert sratools.count_reads(target) == 2


# ---------------------------------------------------------------------------
# split_run
# ---------------------------------------------------------------------------

def _fake_run(tmp_path, *, returncode=0, write=("_1", "_2"), stderr=""):
    def runner(cmd, capture_output=True, text=True):
        out_dir = Path(cmd[cmd.index("--outdir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        acc = cmd[-1]
        for suffix in write:
            _write_fastq(out_dir / f"{acc}{suffix}.fastq", 2)
        return subprocess.CompletedProcess(cmd, returncode, "", stderr)
    return runner


def test_split_run_returns_gzipped_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(sratools.shutil, "which", lambda name: f"/bin/{name}"
                        if name == "fasterq-dump" else None)
    monkeypatch.setattr(sratools.subprocess, "run", _fake_run(tmp_path))
    r1, r2 = sratools.split_run("SRR1", tmp_path / "reads")
    assert r1.name == "SRR1_1.fastq.gz"
    assert r2.name == "SRR1_2.fastq.gz"
    assert sratools.count_reads(r1) == 2


def test_split_run_can_skip_compression(tmp_path, monkeypatch):
    monkeypatch.setattr(sratools.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(sratools.subprocess, "run", _fake_run(tmp_path))
    r1, r2 = sratools.split_run("SRR1", tmp_path / "reads", compress=False)
    assert r1.name == "SRR1_1.fastq"
    assert r2.name == "SRR1_2.fastq"


def test_split_run_raises_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(sratools.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        sratools.subprocess, "run",
        _fake_run(tmp_path, returncode=3, write=(), stderr="boom"),
    )
    with pytest.raises(sratools.SraToolsError, match="exit 3.*boom"):
        sratools.split_run("SRR1", tmp_path / "reads")


def test_split_run_reports_orphan_single_read_dump(tmp_path, monkeypatch):
    """A run whose reads all carry one read index cannot be split by mate."""
    monkeypatch.setattr(sratools.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(sratools.subprocess, "run", _fake_run(tmp_path, write=("",)))
    with pytest.raises(sratools.SraToolsError, match="cannot be split by mate"):
        sratools.split_run("SRR1", tmp_path / "reads")


def test_split_run_raises_when_one_mate_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sratools.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(sratools.subprocess, "run", _fake_run(tmp_path, write=("_1",)))
    with pytest.raises(sratools.SraToolsError, match="SRR1_2.fastq"):
        sratools.split_run("SRR1", tmp_path / "reads")
