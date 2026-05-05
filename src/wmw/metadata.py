"""Normalize ENA / SRA run records to the wmw shared schema."""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Shared schema
# ---------------------------------------------------------------------------
# Studies table fields
STUDY_FIELDS = (
    "study_accession",
    "secondary_study_accession",
    "study_title",
    "study_description",
    "source",          # "ENA" | "SRA"
    "scientific_name",
    "tax_id",
    "first_public",
    "center_name",
    "status",          # default: "new"
    # --- publication ---
    "pubmed_id",
    "pub_doi",
    "pub_url",
    "pub_title",
    "pub_year",
    "pub_journal",
    "pub_authors",
)

# Samples table fields
SAMPLE_FIELDS = (
    "run_accession",
    "study_accession",
    "sample_accession",
    "experiment_accession",
    "scientific_name",
    "tax_id",
    "instrument_platform",
    "instrument_model",
    "library_strategy",
    "library_source",
    "library_layout",
    "base_count",
    "read_count",
    "fastq_ftp",
    "fastq_md5",
    "fastq_url_1",     # derived from fastq_ftp
    "fastq_url_2",     # derived from fastq_ftp
    "collection_date",
    "first_public",
    "geo_loc_name",
    "host",
    "host_tax_id",
    "host_scientific_name",
    "country",
    "center_name",
    "source",          # "ENA" | "SRA"
    "status",          # default: "pending"
)


def _str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _split_fastq_urls(fastq_ftp: str) -> tuple[str, str]:
    """Split a semicolon-delimited FTP string into (url1, url2).

    ENA returns up to two FASTQ paths separated by ';'.
    Prepend ftp:// if the path lacks a scheme.
    """
    parts = [p.strip() for p in fastq_ftp.split(";") if p.strip()]
    urls = []
    for p in parts:
        if p and "://" not in p:
            p = "ftp://" + p
        urls.append(p)
    url1 = urls[0] if len(urls) > 0 else ""
    url2 = urls[1] if len(urls) > 1 else ""
    return url1, url2


# ---------------------------------------------------------------------------
# ENA normalization
# ---------------------------------------------------------------------------

def normalize_ena_run(record: dict[str, Any]) -> dict[str, Any]:
    """Map a raw ENA Portal API run record to the wmw sample schema."""
    fastq_ftp = _str(record.get("fastq_ftp"))
    url1, url2 = _split_fastq_urls(fastq_ftp)
    return {
        "run_accession":        _str(record.get("run_accession")),
        "study_accession":      _str(record.get("study_accession")),
        "sample_accession":     _str(record.get("sample_accession")),
        "experiment_accession": _str(record.get("experiment_accession")),
        "scientific_name":      _str(record.get("scientific_name")),
        "tax_id":               _str(record.get("tax_id")),
        "instrument_platform":  _str(record.get("instrument_platform")),
        "instrument_model":     _str(record.get("instrument_model")),
        "library_strategy":     _str(record.get("library_strategy")),
        "library_source":       _str(record.get("library_source")),
        "library_layout":       _str(record.get("library_layout")),
        "base_count":           _str(record.get("base_count")),
        "read_count":           _str(record.get("read_count")),
        "fastq_ftp":            fastq_ftp,
        "fastq_md5":            _str(record.get("fastq_md5")),
        "fastq_url_1":          url1,
        "fastq_url_2":          url2,
        "collection_date":      _str(record.get("collection_date")),
        "first_public":         _str(record.get("first_public")),
        "geo_loc_name":         _str(record.get("geo_loc_name")),
        "host":                 _str(record.get("host")),
        "host_tax_id":          _str(record.get("host_tax_id")),
        "host_scientific_name": _str(record.get("host_scientific_name")),
        "country":              _str(record.get("country")),
        "center_name":          _str(record.get("center_name")),
        "source":               "ENA",
        "status":               "pending",
    }


def normalize_ena_study(record: dict[str, Any]) -> dict[str, Any]:
    """Map a raw ENA Portal API study record to the wmw study schema."""
    return {
        "study_accession":           _str(record.get("study_accession")),
        "secondary_study_accession": _str(record.get("secondary_study_accession")),
        "study_title":               _str(record.get("study_title")),
        "study_description":         _str(record.get("study_description")),
        "source":                    "ENA",
        "scientific_name":           _str(record.get("scientific_name")),
        "tax_id":                    _str(record.get("tax_id")),
        "first_public":              _str(record.get("first_public")),
        "center_name":               _str(record.get("center_name")),
        "status":                    "new",
        "pubmed_id":                 _str(record.get("pubmed_id")),
        "pub_doi":                   _str(record.get("pub_doi")),
        "pub_url":                   _str(record.get("pub_url")),
        "pub_title":                 _str(record.get("pub_title")),
        "pub_year":                  _str(record.get("pub_year")),
        "pub_journal":               _str(record.get("pub_journal")),
        "pub_authors":               _str(record.get("pub_authors")),
    }


# ---------------------------------------------------------------------------
# SRA normalization
# ---------------------------------------------------------------------------

def normalize_sra_run(record: dict[str, Any]) -> dict[str, Any]:
    """Map a raw SRA Entrez record (already flat dict from sra.py) to wmw sample schema."""
    fastq_ftp = _str(record.get("fastq_ftp"))
    url1, url2 = _split_fastq_urls(fastq_ftp)
    return {
        "run_accession":        _str(record.get("run_accession")),
        "study_accession":      _str(record.get("study_accession")),
        "sample_accession":     _str(record.get("sample_accession")),
        "experiment_accession": _str(record.get("experiment_accession")),
        "scientific_name":      _str(record.get("scientific_name")),
        "tax_id":               _str(record.get("tax_id")),
        "instrument_platform":  _str(record.get("instrument_platform")),
        "instrument_model":     _str(record.get("instrument_model")),
        "library_strategy":     _str(record.get("library_strategy")),
        "library_source":       _str(record.get("library_source")),
        "library_layout":       _str(record.get("library_layout")),
        "base_count":           _str(record.get("base_count")),
        "read_count":           _str(record.get("read_count")),
        "fastq_ftp":            fastq_ftp,
        "fastq_md5":            "",
        "fastq_url_1":          url1,
        "fastq_url_2":          url2,
        "collection_date":      _str(record.get("collection_date")),
        "first_public":         _str(record.get("first_public")),
        "geo_loc_name":         "",
        "host":                 "",
        "host_tax_id":          "",
        "host_scientific_name": "",
        "country":              "",
        "center_name":          _str(record.get("center_name")),
        "source":               "SRA",
        "status":               "pending",
    }


def normalize_sra_study(record: dict[str, Any]) -> dict[str, Any]:
    """Build a wmw study record from a representative SRA run record."""
    return {
        "study_accession":           _str(record.get("study_accession")),
        "secondary_study_accession": "",
        "study_title":               _str(record.get("study_title")),
        "study_description":         "",
        "source":                    "SRA",
        "scientific_name":           _str(record.get("scientific_name")),
        "tax_id":                    _str(record.get("tax_id")),
        "first_public":              _str(record.get("first_public")),
        "center_name":               _str(record.get("center_name")),
        "status":                    "new",
        "pubmed_id":                 _str(record.get("pubmed_id")),
        "pub_doi":                   _str(record.get("pub_doi")),
        "pub_url":                   _str(record.get("pub_url")),
        "pub_title":                 _str(record.get("pub_title")),
        "pub_year":                  _str(record.get("pub_year")),
        "pub_journal":               _str(record.get("pub_journal")),
        "pub_authors":               _str(record.get("pub_authors")),
    }


# ---------------------------------------------------------------------------
# Batch normalization helpers
# ---------------------------------------------------------------------------

def normalize_runs(
    records: list[dict[str, Any]],
    source: str,
) -> list[dict[str, Any]]:
    fn = normalize_ena_run if source == "ENA" else normalize_sra_run
    return [fn(r) for r in records if r.get("run_accession")]


def studies_from_runs(
    runs: list[dict[str, Any]],
    source: str,
) -> list[dict[str, Any]]:
    """Derive one study record per unique study_accession from a list of run records."""
    seen: set[str] = set()
    studies: list[dict[str, Any]] = []
    fn = normalize_sra_study if source == "SRA" else None
    for run in runs:
        acc = run.get("study_accession", "")
        if not acc or acc in seen:
            continue
        seen.add(acc)
        if fn:
            studies.append(fn(run))
        else:
            studies.append(normalize_ena_study(run))
    return studies


def deduplicate_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate run_accession entries, keeping the first occurrence."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in runs:
        acc = r.get("run_accession", "")
        if acc and acc not in seen:
            seen.add(acc)
            out.append(r)
    return out


def filter_runs(
    runs: list[dict[str, Any]],
    *,
    exclude_host_tax_ids: list[str] | None = None,
    min_bases: int | None = None,
    library_strategies: list[str] | None = None,
    library_sources: list[str] | None = None,
    instrument_platform: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Post-fetch filter applied after normalization.

    Used as a secondary safety net for host/base filters, and as the primary
    filter layer for wmw fetch (library strategy, source, platform).

    Returns (kept_runs, excluded_count).
    """
    exclude_set = {str(t).strip() for t in (exclude_host_tax_ids or []) if str(t).strip()}
    strategy_set = (
        {s.strip().upper() for s in library_strategies if s.strip()}
        if library_strategies else None
    )
    source_set = (
        {s.strip().upper() for s in library_sources if s.strip()}
        if library_sources else None
    )
    platform_upper = instrument_platform.strip().upper() if instrument_platform else None

    kept: list[dict[str, Any]] = []
    excluded = 0

    for run in runs:
        host_tid = str(run.get("host_tax_id") or "").strip()
        if exclude_set and host_tid and host_tid in exclude_set:
            excluded += 1
            continue

        if min_bases is not None:
            raw = str(run.get("base_count") or "").strip()
            if raw:
                try:
                    if int(raw) < min_bases:
                        excluded += 1
                        continue
                except (ValueError, TypeError):
                    pass

        if strategy_set:
            strat = str(run.get("library_strategy") or "").strip().upper()
            if strat and strat not in strategy_set:
                excluded += 1
                continue

        if source_set:
            src = str(run.get("library_source") or "").strip().upper()
            if src and src not in source_set:
                excluded += 1
                continue

        if platform_upper:
            plat = str(run.get("instrument_platform") or "").strip().upper()
            if plat and plat != platform_upper:
                excluded += 1
                continue

        kept.append(run)

    return kept, excluded
