"""ENA Portal API queries for wmw."""

from __future__ import annotations

import time
from typing import Any

import requests

ENA_SEARCH_URL = "https://www.ebi.ac.uk/ena/portal/api/search"

VALID_DATE_FIELDS = {"first_public", "collection_date", "last_updated"}
VALID_STUDY_DATE_FIELDS = {"first_public", "last_updated"}

# Fields fetched per run record from ENA
RUN_FIELDS = ",".join([
    "study_accession",
    "secondary_study_accession",
    "sample_accession",
    "secondary_sample_accession",
    "experiment_accession",
    "run_accession",
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
    "collection_date",
    "first_public",
    "geo_loc_name",
    "host",
    "host_tax_id",
    "host_scientific_name",
    "country",
    "center_name",
    "study_title",
])

# Fields fetched per study record from ENA
STUDY_FIELDS = ",".join([
    "study_accession",
    "secondary_study_accession",
    "study_title",
    "study_description",
    "scientific_name",
    "tax_id",
    "first_public",
    "last_updated",
    "center_name",
    "pubmed_id",
])


def _get(url: str, params: dict[str, Any], retries: int = 3) -> list[dict[str, Any]]:
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            raise
        except requests.exceptions.RequestException:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return []


def search_runs(
    date_from: str,
    date_to: str,
    *,
    host_tax_id: str = "",
    library_strategy: str = "WGS,METAGENOMIC",
    library_source: str = "",
    instrument_platform: str = "",
    date_field: str = "first_public",
    min_bases: int | None = None,
    keyword: str = "",
    exclude_host_tax_ids: list[str] | None = None,
    limit: int = 10000,
) -> list[dict[str, Any]]:
    """Search ENA for metagenomic run records within a date window.

    Parameters
    ----------
    date_from / date_to:
        ISO date strings (YYYY-MM-DD).
    host_tax_id:
        NCBI taxon ID to *include* (e.g. "7742" for Vertebrata).
    library_strategy:
        Comma-separated ENA library strategies (e.g. "WGS,METAGENOMIC").
    library_source:
        Comma-separated ENA library sources (e.g. "METAGENOMIC"). Empty = no filter.
    instrument_platform:
        Single platform string (e.g. "ILLUMINA"). Empty = no filter.
    date_field:
        ENA field to apply the date window to: first_public | collection_date | last_updated.
    min_bases:
        Minimum base_count. Applied at query time by ENA. None = no filter.
    keyword:
        Free-text substring matched against study_title (case-insensitive wildcard).
    exclude_host_tax_ids:
        List of host_tax_id values to exclude from results. Applied at query time.
    limit:
        Maximum number of records to return.
    """
    if date_field not in VALID_DATE_FIELDS:
        raise ValueError(f"date_field must be one of {VALID_DATE_FIELDS}, got {date_field!r}")

    query_parts: list[str] = []

    # Library strategy (required)
    strategies = [s.strip() for s in library_strategy.split(",") if s.strip()]
    query_parts.append("(" + " OR ".join(f'library_strategy="{s}"' for s in strategies) + ")")

    # Library source (optional)
    if library_source:
        sources = [s.strip() for s in library_source.split(",") if s.strip()]
        if sources:
            query_parts.append("(" + " OR ".join(f'library_source="{s}"' for s in sources) + ")")

    # Date range
    query_parts.append(f"{date_field}>={date_from} AND {date_field}<={date_to}")

    # Host taxon inclusion
    if host_tax_id:
        query_parts.append(f"host_tax_id={host_tax_id}")

    # Instrument platform
    if instrument_platform:
        query_parts.append(f'instrument_platform="{instrument_platform.strip().upper()}"')

    # Minimum base count
    if min_bases is not None:
        query_parts.append(f"base_count>={min_bases}")

    # Free-text keyword in study title
    if keyword:
        safe = keyword.replace('"', "")
        query_parts.append(f'study_title="*{safe}*"')

    # Host taxon exclusions
    for tid in (exclude_host_tax_ids or []):
        tid = str(tid).strip()
        if tid:
            query_parts.append(f"NOT host_tax_id={tid}")

    params = {
        "result": "read_run",
        "query": " AND ".join(query_parts),
        "fields": RUN_FIELDS,
        "format": "json",
        "limit": limit,
    }
    return _get(ENA_SEARCH_URL, params)


def search_study(study_accession: str) -> list[dict[str, Any]]:
    """Return all run records for a single study accession."""
    params = {
        "result": "read_run",
        "query": f'study_accession="{study_accession}" OR secondary_study_accession="{study_accession}"',
        "fields": RUN_FIELDS,
        "format": "json",
        "limit": 50000,
    }
    return _get(ENA_SEARCH_URL, params)


def search_studies(
    date_from: str,
    date_to: str,
    *,
    host_tax_id: str = "",
    date_field: str = "first_public",
    keyword: str = "",
    limit: int = 10000,
) -> list[dict[str, Any]]:
    """Search ENA for study records within a date window.

    Uses the ENA Portal ``result=study`` endpoint, which returns study-level
    metadata including ``study_description`` and ``pubmed_id``.  Run-level
    fields (library_strategy, instrument_platform, base_count) are not
    available at this level; they are applied post-fetch by ``wmw fetch``.

    Parameters
    ----------
    date_from / date_to:
        ISO date strings (YYYY-MM-DD).
    host_tax_id:
        NCBI taxon ID matched against the study ``tax_id`` field (approximate
        host filter at study level).
    date_field:
        ``first_public`` or ``last_updated`` — ``collection_date`` is a
        run-level field not available in the study index.
    keyword:
        Free-text substring matched against ``study_title``.
    limit:
        Maximum number of records to return.
    """
    if date_field not in VALID_STUDY_DATE_FIELDS:
        raise ValueError(
            f"date_field for study search must be one of {sorted(VALID_STUDY_DATE_FIELDS)}, "
            f"got {date_field!r}"
        )

    query_parts: list[str] = [f"{date_field}>={date_from} AND {date_field}<={date_to}"]

    if host_tax_id:
        query_parts.append(f"tax_id={host_tax_id}")

    if keyword:
        safe = keyword.replace('"', "")
        query_parts.append(f'study_title="*{safe}*"')

    params = {
        "result": "study",
        "query": " AND ".join(query_parts),
        "fields": STUDY_FIELDS,
        "format": "json",
        "limit": limit,
    }
    return _get(ENA_SEARCH_URL, params)


def fetch_study_metadata(study_accession: str) -> dict[str, Any] | None:
    """Return metadata for a single study from the ENA study result set."""
    params = {
        "result": "study",
        "query": f'study_accession="{study_accession}" OR secondary_study_accession="{study_accession}"',
        "fields": STUDY_FIELDS,
        "format": "json",
        "limit": 1,
    }
    records = _get(ENA_SEARCH_URL, params)
    return records[0] if records else None


def unique_studies(run_records: list[dict[str, Any]]) -> list[str]:
    """Extract the sorted unique set of study_accession values from run records."""
    return sorted({r.get("study_accession", "") for r in run_records if r.get("study_accession")})
