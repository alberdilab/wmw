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


# ---------------------------------------------------------------------------
# GSA normalization
# ---------------------------------------------------------------------------

_GSA_RUN = {
    "run_accession": "CRR2009389",
    "study_accession": "CRA028180",
    "sample_accession": "SAMC5697416",
    "experiment_accession": "CRX1868732",
    "scientific_name": "organismal metagenomes",
    "tax_id": "410657",
    "instrument_platform": "ILLUMINA",
    "instrument_model": "Illumina NovaSeq 6000",
    "library_strategy": "WGS",
    "library_source": "METAGENOMIC",
    "library_layout": "PAIRED",
    "fastq_ftp": "ftp://download.big.ac.cn/gsa5/CRA028180/CRR2009389/CRR2009389_r1.fq.gz",
    "fastq_md5": "md5one;md5two",
    "fastq_url_1": "https://download.cncb.ac.cn/gsa5/CRA028180/CRR2009389/CRR2009389_r1.fq.gz",
    "fastq_url_2": "https://download.cncb.ac.cn/gsa5/CRA028180/CRR2009389/CRR2009389_r2.fq.gz",
    "collection_date": "2024-05-02",
    "first_public": "2025-09-07",
    "geo_loc_name": "China: Fujian",
    "country": "China",
    "host": "Atelerix albiventris",
    "host_tax_id": "9368",
    "host_sex": "female",
    "lat": 26.075,
    "lon": 119.297,
    "broad_scale_environmental_context": "forest biome",
    "environmental_medium": "feces",
    # Non-schema extras that gsa.py carries for traceability.
    "file_name_1": "CRR2009389_r1.fq.gz",
    "file_size_1": 3402548213,
    "run_title": "Animal_7_2_1",
}


def test_normalize_gsa_run_maps_schema_fields():
    run = metadata.normalize_gsa_run(_GSA_RUN)
    assert run["run_accession"] == "CRR2009389"
    assert run["study_accession"] == "CRA028180"
    assert run["source"] == "GSA"
    assert run["status"] == "pending"
    assert run["collection_date"] == "2024-05-02"
    assert run["country"] == "China"


def test_normalize_gsa_run_leaves_counts_blank():
    """GSA publishes no base or read counts, so MIN_BASES cannot apply."""
    run = metadata.normalize_gsa_run(_GSA_RUN)
    assert run["base_count"] is None
    assert run["read_count"] is None


def test_normalize_gsa_run_drops_non_schema_keys():
    run = metadata.normalize_gsa_run(_GSA_RUN)
    assert set(run) == set(metadata.SAMPLE_FIELDS)


def test_normalize_gsa_run_defaults_host_scientific_name_to_host():
    run = metadata.normalize_gsa_run(_GSA_RUN)
    assert run["host_scientific_name"] == "Atelerix albiventris"


def test_normalize_gsa_study_pairs_cra_with_bioproject():
    study = metadata.normalize_gsa_study({
        "study_accession": "CRA028180",
        "secondary_study_accession": "PRJCA042537",
        "study_title": "virome and microbiome of non-traditional mammals",
        "study_description": "Zoonotic diseases.",
        "scientific_name": "organismal metagenomes",
        "first_public": "2025-09-07",
        "center_name": "Fudan University",
    })
    assert study["study_accession"] == "CRA028180"
    assert study["secondary_study_accession"] == "PRJCA042537"
    assert study["source"] == "GSA"
    assert study["status"] == "new"
    assert set(study) == set(metadata.STUDY_FIELDS)


def test_normalize_runs_dispatches_to_gsa():
    runs = metadata.normalize_runs([_GSA_RUN], "GSA")
    assert len(runs) == 1
    assert runs[0]["source"] == "GSA"


def test_studies_from_runs_dispatches_to_gsa():
    studies = metadata.studies_from_runs(
        [{"study_accession": "CRA028180", "study_title": "t"}], "GSA"
    )
    assert studies[0]["source"] == "GSA"


def test_normalize_runs_still_dispatches_to_ena_and_sra():
    assert metadata.normalize_runs([{"run_accession": "ERR1"}], "ENA")[0]["source"] == "ENA"
    assert metadata.normalize_runs([{"run_accession": "SRR1"}], "SRA")[0]["source"] == "SRA"


# ---------------------------------------------------------------------------
# BioSample metadata
# ---------------------------------------------------------------------------

def test_normalize_ena_run_carries_biosample_metadata(ena_run_record):
    result = metadata.normalize_ena_run(ena_run_record)
    assert result["collection_date"] == "2023-06-15"
    assert result["country"] == "Denmark"
    assert result["host_sex"] == "female"
    assert result["broad_scale_environmental_context"] == "temperate broadleaf forest biome"
    assert result["environmental_medium"] == "feces"
    # ENA returns coordinates as strings; Airtable number fields want numbers.
    assert result["lat"] == 55.6761
    assert result["lon"] == 12.5683
    assert isinstance(result["lat"], float)
    assert isinstance(result["lon"], float)


def test_normalize_ena_run_blank_biosample_metadata(ena_run_record):
    for key in ("lat", "lon", "host_sex", "broad_scale_environmental_context",
                "environmental_medium"):
        ena_run_record[key] = ""
    result = metadata.normalize_ena_run(ena_run_record)
    assert result["lat"] is None
    assert result["lon"] is None
    assert result["host_sex"] == ""
    assert result["broad_scale_environmental_context"] == ""
    assert result["environmental_medium"] == ""


def test_normalize_ena_run_tolerates_unparseable_coordinates(ena_run_record):
    ena_run_record["lat"] = "not applicable"
    ena_run_record["lon"] = "55N"
    result = metadata.normalize_ena_run(ena_run_record)
    assert result["lat"] is None
    assert result["lon"] is None


def test_normalize_sra_run_leaves_biosample_metadata_blank(sra_run_record):
    result = metadata.normalize_sra_run(sra_run_record)
    assert result["lat"] is None
    assert result["lon"] is None
    assert result["host_sex"] == ""
    assert result["broad_scale_environmental_context"] == ""
    assert result["environmental_medium"] == ""


def test_biosample_fields_are_all_sample_fields():
    assert set(metadata.BIOSAMPLE_FIELDS) <= set(metadata.SAMPLE_FIELDS)


def test_optional_sample_fields_are_all_sample_fields():
    assert metadata.OPTIONAL_SAMPLE_FIELDS <= set(metadata.SAMPLE_FIELDS)


def test_every_normalizer_emits_the_full_sample_schema(ena_run_record, sra_run_record):
    """A field missing from one normalizer would silently blank that column."""
    for fn, record in (
        (metadata.normalize_ena_run, ena_run_record),
        (metadata.normalize_sra_run, sra_run_record),
        (metadata.normalize_gsa_run, _GSA_RUN),
    ):
        keys = set(fn(record))
        missing = set(metadata.SAMPLE_FIELDS) - keys
        assert not missing, f"{fn.__name__} is missing {sorted(missing)}"


def test_normalize_gsa_run_carries_biosample_metadata():
    run = metadata.normalize_gsa_run(_GSA_RUN)
    assert run["lat"] == 26.075
    assert run["lon"] == 119.297
    assert run["host_sex"] == "female"
    assert run["broad_scale_environmental_context"] == "forest biome"
    assert run["environmental_medium"] == "feces"
