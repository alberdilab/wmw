"""Tests for wmw.drakkar — manifest generation and version detection."""

from __future__ import annotations

from pathlib import Path

import pytest
from wmw import drakkar


def _sample_record(run_acc: str, r1: str = "", r2: str = "", ftp: str = "") -> dict:
    return {
        "id": f"rec_{run_acc}",
        "fields": {
            "run_accession": run_acc,
            "fastq_url_1": r1,
            "fastq_url_2": r2,
            "fastq_ftp": ftp,
        },
    }


def test_build_manifest_paired(tmp_path):
    samples = [
        _sample_record("ERR001", r1="ftp://host/ERR001_1.fastq.gz", r2="ftp://host/ERR001_2.fastq.gz"),
        _sample_record("ERR002", r1="ftp://host/ERR002_1.fastq.gz", r2="ftp://host/ERR002_2.fastq.gz"),
    ]
    out = tmp_path / "manifest.tsv"
    drakkar.build_manifest(samples, out)

    lines = out.read_text().splitlines()
    assert lines[0] == "sample\tR1\tR2"
    assert lines[1].startswith("ERR001\t")
    assert "ERR001_1.fastq.gz" in lines[1]
    assert lines[2].startswith("ERR002\t")


def test_build_manifest_skips_missing_r1(tmp_path):
    samples = [
        _sample_record("ERR003"),  # no URLs at all
        _sample_record("ERR004", r1="ftp://host/ERR004_1.fastq.gz"),
    ]
    out = tmp_path / "manifest.tsv"
    drakkar.build_manifest(samples, out)
    lines = out.read_text().splitlines()
    # Only ERR004 should appear
    assert len(lines) == 2  # header + 1 row
    assert "ERR004" in lines[1]


def test_build_manifest_falls_back_to_fastq_ftp(tmp_path):
    samples = [
        _sample_record(
            "ERR005",
            ftp="ftp.sra.ebi.ac.uk/vol1/ERR005_1.fastq.gz;ftp.sra.ebi.ac.uk/vol1/ERR005_2.fastq.gz",
        )
    ]
    out = tmp_path / "manifest.tsv"
    drakkar.build_manifest(samples, out)
    lines = out.read_text().splitlines()
    assert "ERR005" in lines[1]
    assert "ERR005_1.fastq.gz" in lines[1]


def test_build_manifest_creates_parent_dirs(tmp_path):
    samples = [_sample_record("ERR006", r1="ftp://host/ERR006_1.fastq.gz")]
    out = tmp_path / "nested" / "deep" / "manifest.tsv"
    drakkar.build_manifest(samples, out)
    assert out.exists()


# ---------------------------------------------------------------------------
# build_input_tsv
# ---------------------------------------------------------------------------

def _input_sample(
    code: str = "S001",
    r1: str = "ftp://host/S001_1.fastq.gz",
    r2: str = "ftp://host/S001_2.fastq.gz",
    ref_name: str = "ref_hg38",
    ref_path: str = "/refs/hg38.fa",
    assembly: str = "",
    coverage: str = "",
) -> dict:
    return {
        "id": f"rec_{code}",
        "fields": {
            "code": code,
            "fastq_url_1": r1,
            "fastq_url_2": r2,
            "reference_name": ref_name,
            "reference_path": ref_path,
            "assembly": assembly,
            "coverage": coverage,
        },
    }


def test_build_input_tsv_required_columns(tmp_path):
    samples = [_input_sample("S001"), _input_sample("S002")]
    out = tmp_path / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    lines = out.read_text().splitlines()
    assert lines[0] == "sample\trawreads1\trawreads2\treference_name\treference_path"
    assert lines[1].startswith("S001\t")
    assert "S001_1.fastq.gz" in lines[1]


def test_build_input_tsv_optional_columns_excluded_when_empty(tmp_path):
    samples = [_input_sample("S001", assembly="", coverage="")]
    out = tmp_path / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    header = out.read_text().splitlines()[0]
    assert "assembly" not in header
    assert "coverage" not in header


def test_build_input_tsv_optional_columns_included_when_nonempty(tmp_path):
    samples = [
        _input_sample("S001", assembly="/path/assembly.fa", coverage=""),
        _input_sample("S002", assembly="", coverage=""),
    ]
    out = tmp_path / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    header = out.read_text().splitlines()[0]
    assert "assembly" in header
    assert "coverage" not in header


def test_build_input_tsv_both_optional_columns(tmp_path):
    samples = [_input_sample("S001", assembly="/asm.fa", coverage="10")]
    out = tmp_path / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    lines = out.read_text().splitlines()
    assert "assembly" in lines[0]
    assert "coverage" in lines[0]
    assert "/asm.fa" in lines[1]
    assert "10" in lines[1]


def test_build_input_tsv_creates_parent_dirs(tmp_path):
    samples = [_input_sample()]
    out = tmp_path / "deep" / "nested" / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    assert out.exists()


# ---------------------------------------------------------------------------
# generate_preprocessing_script
# ---------------------------------------------------------------------------

def test_generate_preprocessing_script_contains_key_elements(tmp_path):
    script = drakkar.generate_preprocessing_script(
        code="PRJ001",
        tsv_path=tmp_path / "PRJ001.tsv",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
    )
    assert "#!/usr/bin/env bash" in script
    assert "PRJ001" in script
    assert "drakkar preprocessing" in script
    assert "wmw set-status --study PRJ001 --workflow preprocessing --status preprocessing" in script
    assert "wmw set-status --study PRJ001 --workflow preprocessing --status completed" in script
    assert "wmw set-status --study PRJ001 --workflow preprocessing --status error" in script
    assert "trap _on_exit EXIT" in script


def test_generate_preprocessing_script_slurm_flag(tmp_path):
    script = drakkar.generate_preprocessing_script(
        code="PRJ002",
        tsv_path=tmp_path / "PRJ002.tsv",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
        slurm=True,
    )
    assert "-p slurm" in script


def test_generate_preprocessing_script_no_conda(tmp_path):
    script = drakkar.generate_preprocessing_script(
        code="PRJ003",
        tsv_path=tmp_path / "PRJ003.tsv",
        work_dir=tmp_path,
        conda_env="",
    )
    assert "conda run" not in script
    assert "drakkar preprocessing" in script
