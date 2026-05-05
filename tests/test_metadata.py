"""Tests for wmw.metadata normalization."""

from __future__ import annotations

import pytest
from wmw import metadata


def test_normalize_ena_run(ena_run_record):
    result = metadata.normalize_ena_run(ena_run_record)
    assert result["run_accession"] == "ERR1234567"
    assert result["study_accession"] == "PRJEB12345"
    assert result["source"] == "ENA"
    assert result["status"] == "pending"
    assert result["fastq_url_1"].startswith("ftp://")
    assert result["fastq_url_2"].startswith("ftp://")
    assert "_1.fastq.gz" in result["fastq_url_1"]
    assert "_2.fastq.gz" in result["fastq_url_2"]


def test_normalize_sra_run(sra_run_record):
    result = metadata.normalize_sra_run(sra_run_record)
    assert result["run_accession"] == "SRR12345678"
    assert result["source"] == "SRA"
    assert result["status"] == "pending"
    assert result["fastq_url_1"] == ""


def test_normalize_ena_study(ena_run_record):
    study = metadata.normalize_ena_study(ena_run_record)
    assert study["study_accession"] == "PRJEB12345"
    assert study["source"] == "ENA"
    assert study["status"] == "new"


def test_split_fastq_urls_with_scheme():
    url1, url2 = metadata._split_fastq_urls(
        "ftp://host/path/file_1.fastq.gz;ftp://host/path/file_2.fastq.gz"
    )
    assert url1 == "ftp://host/path/file_1.fastq.gz"
    assert url2 == "ftp://host/path/file_2.fastq.gz"


def test_split_fastq_urls_no_scheme():
    url1, url2 = metadata._split_fastq_urls("host/path/file_1.fastq.gz;host/path/file_2.fastq.gz")
    assert url1.startswith("ftp://")
    assert url2.startswith("ftp://")


def test_split_fastq_urls_single():
    url1, url2 = metadata._split_fastq_urls("ftp://host/path/file.fastq.gz")
    assert url1 == "ftp://host/path/file.fastq.gz"
    assert url2 == ""


def test_split_fastq_urls_empty():
    url1, url2 = metadata._split_fastq_urls("")
    assert url1 == ""
    assert url2 == ""


def test_deduplicate_runs(ena_run_record):
    runs = [
        metadata.normalize_ena_run(ena_run_record),
        metadata.normalize_ena_run(ena_run_record),
    ]
    deduped = metadata.deduplicate_runs(runs)
    assert len(deduped) == 1


def test_studies_from_runs(ena_run_record, sra_run_record):
    runs = [
        metadata.normalize_ena_run(ena_run_record),
        metadata.normalize_sra_run(sra_run_record),
    ]
    studies = metadata.studies_from_runs(runs, "ENA")
    accessions = {s["study_accession"] for s in studies}
    assert "PRJEB12345" in accessions
    assert "PRJNA12345" in accessions
    assert len(studies) == 2


# ---------------------------------------------------------------------------
# filter_runs
# ---------------------------------------------------------------------------

def _run(run_accession, host_tax_id="", base_count="10000000000"):
    return {
        "run_accession": run_accession,
        "host_tax_id": host_tax_id,
        "base_count": base_count,
        "status": "pending",
    }


def test_filter_runs_excludes_by_host_tax_id():
    runs = [
        _run("ERR001", host_tax_id="9606"),   # human — should be excluded
        _run("ERR002", host_tax_id="7742"),   # vertebrate — kept
        _run("ERR003", host_tax_id="9913"),   # cattle — should be excluded
    ]
    kept, excluded = metadata.filter_runs(runs, exclude_host_tax_ids=["9606", "9913"])
    assert excluded == 2
    assert len(kept) == 1
    assert kept[0]["run_accession"] == "ERR002"


def test_filter_runs_keeps_blank_host_tax_id():
    """Runs without a host_tax_id (e.g. most SRA records) should not be excluded."""
    runs = [_run("SRR001", host_tax_id=""), _run("SRR002", host_tax_id="")]
    kept, excluded = metadata.filter_runs(runs, exclude_host_tax_ids=["9606"])
    assert excluded == 0
    assert len(kept) == 2


def test_filter_runs_min_bases():
    runs = [
        _run("ERR010", base_count="500000000"),    # 0.5 Gbp — below 1 Gbp threshold
        _run("ERR011", base_count="5000000000"),   # 5 Gbp — kept
        _run("ERR012", base_count=""),             # missing — kept (no filter applied)
    ]
    kept, excluded = metadata.filter_runs(runs, min_bases=1_000_000_000)
    assert excluded == 1
    assert {r["run_accession"] for r in kept} == {"ERR011", "ERR012"}


def test_filter_runs_combined():
    runs = [
        _run("ERR020", host_tax_id="9606",  base_count="5000000000"),  # excluded by taxon
        _run("ERR021", host_tax_id="7742",  base_count="100000000"),   # excluded by base count
        _run("ERR022", host_tax_id="7742",  base_count="5000000000"),  # kept
    ]
    kept, excluded = metadata.filter_runs(
        runs, exclude_host_tax_ids=["9606"], min_bases=1_000_000_000
    )
    assert excluded == 2
    assert len(kept) == 1
    assert kept[0]["run_accession"] == "ERR022"


def test_filter_runs_no_filters():
    runs = [_run("ERR030"), _run("ERR031")]
    kept, excluded = metadata.filter_runs(runs)
    assert excluded == 0
    assert len(kept) == 2
