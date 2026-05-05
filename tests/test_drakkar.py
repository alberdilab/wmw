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
