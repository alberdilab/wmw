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
    assert result["base_count"] == 15000000000
    assert result["read_count"] == 100000000
    assert isinstance(result["base_count"], int)
    assert isinstance(result["read_count"], int)
    assert result["collection_date"] == "2023-06-15"
    assert result["first_public"] == "2024-01-10"


def test_normalize_sra_run(sra_run_record):
    result = metadata.normalize_sra_run(sra_run_record)
    assert result["run_accession"] == "SRR12345678"
    assert result["source"] == "SRA"
    assert result["status"] == "pending"
    assert result["fastq_url_1"] == ""
    assert result["base_count"] == 12000000000
    assert result["read_count"] == 80000000
    assert isinstance(result["base_count"], int)
    assert isinstance(result["read_count"], int)
    assert result["collection_date"] is None  # empty string in fixture → None
    assert result["first_public"] == "2024-02-20"


# ---------------------------------------------------------------------------
# _int / _date helpers
# ---------------------------------------------------------------------------

def test_int_valid():
    assert metadata._int("15000000000") == 15000000000
    assert metadata._int(42) == 42

def test_int_blank():
    assert metadata._int("") is None
    assert metadata._int(None) is None

def test_int_invalid():
    assert metadata._int("n/a") is None

def test_date_valid():
    assert metadata._date("2023-06-15") == "2023-06-15"

def test_date_partial():
    assert metadata._date("2023-06") is None
    assert metadata._date("2023") is None

def test_date_blank():
    assert metadata._date("") is None
    assert metadata._date(None) is None


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


def test_split_fastq_urls_three_files_selects_paired():
    # ENA sometimes returns a merged singleton alongside the _1/_2 paired files.
    # The unsuffixed file should be ignored and _1/_2 selected as R1/R2.
    ftp = (
        "ftp://host/path/SRR13765885.fastq.gz;"
        "ftp://host/path/SRR13765885_1.fastq.gz;"
        "ftp://host/path/SRR13765885_2.fastq.gz"
    )
    url1, url2 = metadata._split_fastq_urls(ftp)
    assert url1 == "ftp://host/path/SRR13765885_1.fastq.gz"
    assert url2 == "ftp://host/path/SRR13765885_2.fastq.gz"


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


# ---------------------------------------------------------------------------
# filter_runs — library strategy, source, platform
# ---------------------------------------------------------------------------

def _run_full(run_accession, library_strategy="WGS", library_source="METAGENOMIC",
              instrument_platform="ILLUMINA"):
    return {
        "run_accession": run_accession,
        "host_tax_id": "",
        "base_count": "10000000000",
        "library_strategy": library_strategy,
        "library_source": library_source,
        "instrument_platform": instrument_platform,
        "status": "pending",
    }


def test_filter_runs_reports_exclusion_criterion():
    runs = [
        _run_full("ERR010", library_source="GENOMIC"),
        _run_full("ERR011", library_source="METAGENOMIC"),
    ]
    kept, excluded, exclusions = metadata.filter_runs(
        runs,
        library_sources=["METAGENOMIC"],
        include_exclusions=True,
    )

    assert excluded == 1
    assert kept[0]["run_accession"] == "ERR011"
    assert exclusions == [
        {
            "run_accession": "ERR010",
            "study_accession": "",
            "criterion": "library_source",
            "value": "GENOMIC",
            "expected": "one of METAGENOMIC",
        }
    ]


def test_filter_runs_library_strategy():
    runs = [
        _run_full("ERR040", library_strategy="WGS"),
        _run_full("ERR041", library_strategy="AMPLICON"),
        _run_full("ERR042", library_strategy="METAGENOMIC"),
    ]
    kept, excluded = metadata.filter_runs(runs, library_strategies=["WGS", "METAGENOMIC"])
    assert excluded == 1
    assert {r["run_accession"] for r in kept} == {"ERR040", "ERR042"}


def test_filter_runs_library_source():
    runs = [
        _run_full("ERR050", library_source="METAGENOMIC"),
        _run_full("ERR051", library_source="GENOMIC"),
    ]
    kept, excluded = metadata.filter_runs(runs, library_sources=["METAGENOMIC"])
    assert excluded == 1
    assert kept[0]["run_accession"] == "ERR050"


def test_filter_runs_instrument_platform():
    runs = [
        _run_full("ERR060", instrument_platform="ILLUMINA"),
        _run_full("ERR061", instrument_platform="OXFORD_NANOPORE"),
    ]
    kept, excluded = metadata.filter_runs(runs, instrument_platform="ILLUMINA")
    assert excluded == 1
    assert kept[0]["run_accession"] == "ERR060"


def test_filter_runs_unknown_field_not_excluded():
    """Runs with blank library_strategy/source/platform should not be excluded."""
    runs = [
        _run_full("ERR070", library_strategy="", library_source="", instrument_platform=""),
    ]
    kept, excluded = metadata.filter_runs(
        runs,
        library_strategies=["WGS"],
        library_sources=["METAGENOMIC"],
        instrument_platform="ILLUMINA",
    )
    assert excluded == 0
    assert len(kept) == 1
