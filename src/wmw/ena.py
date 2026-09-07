"""ENA Portal API queries for wmw."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlencode

import requests
from xml.etree import ElementTree as ET

ENA_SEARCH_URL = "https://www.ebi.ac.uk/ena/portal/api/search"
ENA_TAXONOMY_URL = "https://www.ebi.ac.uk/ena/taxonomy/rest/any-name"
ENA_TAXONOMY_TAXID_URL = "https://www.ebi.ac.uk/ena/taxonomy/rest/tax-id"
NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

DEBUG: bool = False

_lineage_cache: dict[str, str] = {}


class ENAError(RuntimeError):
    """A user-facing failure talking to (or preparing a request for) ENA.

    Carries a message meant to be printed as-is: no traceback, no request URL.
    """


# INSDC study accessions ENA will accept in a `study_accession` /
# `secondary_study_accession` query.  Anything else makes the Portal API answer
# 400, so it is caught before the request goes out.
STUDY_ACCESSION_RE = re.compile(r"^(?:PRJ[EDN][A-Z]\d+|[EDS]RP\d{6,})$")

# wmw's own Airtable study codes (ST00359) and GSA's accessions (CRA012991,
# PRJCA020434).  Recognised only to explain the mix-up when one is passed
# where an ENA accession is expected.
_WMW_CODE_RE = re.compile(r"^ST\d+$")
_GSA_ACCESSION_RE = re.compile(r"^(?:CRA|PRJCA)\d+$")


def normalize_accession(accession: str) -> str:
    """Strip surrounding whitespace and upper-case an accession.

    ENA matches accessions case-sensitively, so ``prjna1300861`` is rejected
    with a 400 while ``PRJNA1300861`` resolves.
    """
    return (accession or "").strip().upper()


def is_study_accession(accession: str) -> bool:
    """True if the (normalized) accession is shaped like an INSDC study accession."""
    return bool(STUDY_ACCESSION_RE.match(normalize_accession(accession)))


def validate_study_accession(accession: str) -> str:
    """Return the normalized accession, or raise ENAError explaining why it can't be one."""
    acc = normalize_accession(accession)
    if not acc:
        raise ENAError("No study accession given.")
    if STUDY_ACCESSION_RE.match(acc):
        return acc
    if _GSA_ACCESSION_RE.match(acc):
        raise ENAError(
            f"{accession!r} is a GSA (NGDC) accession, which ENA does not index. "
            "wmw routes a GSA accession given to --study to GSA on its own; "
            "elsewhere, select the archive with --source gsa."
        )
    if _WMW_CODE_RE.match(acc):
        raise ENAError(
            f"{accession!r} is a wmw study code, not an ENA accession. "
            "Pass the study's INSDC accession (e.g. PRJNA1300861 or ERP146183) — "
            "the Studies table records it in study_accession."
        )
    raise ENAError(
        f"{accession!r} is not a valid ENA study accession. Expected a BioProject "
        "accession (PRJEB…, PRJNA…, PRJDB…) or a secondary study accession "
        "(ERP…, SRP…, DRP…)."
    )


def _error_message(resp: requests.Response) -> str:
    """Pull ENA's own explanation out of an error response body."""
    try:
        payload = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:300]
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            if payload.get(key):
                return str(payload[key]).strip()
    return str(payload).strip()[:300]


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
    "host",
    "host_tax_id",
    "host_scientific_name",
    "country",
    "center_name",
    "study_title",
    # --- BioSample / MIxS attributes ---
    # ENA joins the registered sample's attributes onto every run record, so
    # these need no separate BioSample call. The MIxS v5 names are used;
    # `environment_biome` and `environment_material` are the v4 aliases ENA
    # resolves to the same underlying value.
    "host_sex",
    "lat",
    "lon",
    "broad_scale_environmental_context",
    "environmental_medium",
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
])


def _get(url: str, params: dict[str, Any], retries: int = 3) -> list[dict[str, Any]]:
    if DEBUG:
        print(f"DEBUG ENA request: {url}?{urlencode(params)}")
    last_error = ""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as exc:
                raise ENAError(
                    "ENA returned a response that is not JSON: "
                    f"{(resp.text or '').strip()[:200]!r}"
                ) from exc
        except requests.exceptions.HTTPError as exc:
            resp = exc.response
            status = resp.status_code if resp is not None else 0
            detail = _error_message(resp) if resp is not None else ""
            if status == 429 or status >= 500:
                last_error = f"ENA API is unavailable ({status})"
                if detail:
                    last_error += f": {detail}"
                time.sleep(2 ** attempt)
                continue
            # A 4xx is the query's fault — retrying sends the same bad request.
            raise ENAError(
                f"ENA rejected the query ({status})"
                + (f": {detail}" if detail else ".")
            ) from exc
        except requests.exceptions.RequestException as exc:
            last_error = f"Could not reach the ENA API: {exc}"
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise ENAError(last_error) from exc
    # Every attempt was a retryable failure — report it rather than pretending
    # the query returned no records.
    raise ENAError(last_error or f"ENA request failed after {retries} attempts.")


def _ncbi_efetch_taxon(tax_id: str) -> ET.Element | None:
    """Fetch a single <Taxon> XML element from NCBI Entrez for the given tax_id."""
    resp = requests.get(
        NCBI_EFETCH_URL,
        params={"db": "taxonomy", "id": tax_id, "retmode": "xml"},
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    return root.find("Taxon")


def resolve_taxonomy_name(name: str) -> tuple[str, str]:
    """Resolve a taxonomic name to (tax_id, scientific_name).

    Tries ENA Taxonomy REST first; falls back to NCBI Entrez on failure.
    Raises ValueError if neither source can resolve the name.
    """
    # ENA primary
    try:
        resp = requests.get(
            f"{ENA_TAXONOMY_URL}/{requests.utils.quote(name, safe='')}",
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            first = results[0]
            return str(first["taxId"]), first.get("scientificName", name)
    except Exception:
        pass

    # NCBI Entrez fallback
    search_resp = requests.get(
        NCBI_ESEARCH_URL,
        params={"db": "taxonomy", "term": name, "retmode": "json"},
        timeout=30,
    )
    search_resp.raise_for_status()
    ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        raise ValueError(f"Taxonomy name not found: {name!r}")
    taxon = _ncbi_efetch_taxon(ids[0])
    if taxon is None:
        raise ValueError(f"Could not fetch taxonomy record for {name!r} (id={ids[0]})")
    return str(taxon.findtext("TaxId") or ids[0]), taxon.findtext("ScientificName") or name


def get_lineage(tax_id: str) -> str:
    """Return the semicolon-separated lineage string for a taxon.

    Tries ENA Taxonomy REST first; falls back to NCBI Entrez on failure.
    Results are cached in-process. Returns empty string on error or blank input.
    """
    if not tax_id:
        return ""
    if tax_id in _lineage_cache:
        return _lineage_cache[tax_id]
    lineage = ""
    try:
        resp = requests.get(f"{ENA_TAXONOMY_TAXID_URL}/{tax_id}", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        lineage = data.get("lineage", "") if isinstance(data, dict) else ""
    except Exception:
        pass
    if not lineage:
        try:
            taxon = _ncbi_efetch_taxon(tax_id)
            lineage = taxon.findtext("Lineage") or "" if taxon is not None else ""
        except Exception:
            lineage = ""
    _lineage_cache[tax_id] = lineage
    return lineage


def search_runs(
    date_from: str = "",
    date_to: str = "",
    *,
    host_tax_id: str = "",
    library_strategy: str = "WGS,METAGENOMIC",
    library_source: str = "",
    instrument_platform: str = "",
    date_field: str = "first_public",
    min_bases: int | None = None,
    keyword: str = "",
    exclude_host_tax_ids: list[str] | None = None,
    study_accessions: list[str] | None = None,
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

    # Date range (omitted when querying by explicit study accessions)
    if date_from and date_to:
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

    # Restrict to specific studies (batched scan mode).  One malformed accession
    # would 400 the whole batch, so they are normalized and screened first; if
    # that leaves nothing, the restriction must not silently widen the query.
    if study_accessions:
        accs = [a for a in (normalize_accession(a) for a in study_accessions) if is_study_accession(a)]
        if not accs:
            return []
        parts = " OR ".join(f'study_accession="{acc}"' for acc in accs)
        query_parts.append(f"({parts})")

    params = {
        "result": "read_run",
        "query": " AND ".join(query_parts),
        "fields": RUN_FIELDS,
        "format": "json",
        "limit": limit,
    }
    return _get(ENA_SEARCH_URL, params)


def search_study(study_accession: str) -> list[dict[str, Any]]:
    """Return all run records for a single study accession.

    Raises ``ENAError`` if the accession cannot be an ENA study accession —
    the Portal API answers such a query with a bare 400.
    """
    study_accession = validate_study_accession(study_accession)
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
    taxonomy_tax_id: str = "",
    limit: int = 10000,
) -> list[dict[str, Any]]:
    """Search ENA for study records within a date window.

    Uses the ENA Portal ``result=study`` endpoint, which returns study-level
    metadata including ``study_description``.  Run-level
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
    taxonomy_tax_id:
        NCBI taxon ID used with ENA's ``tax_tree()`` operator to match all
        studies whose organism falls within the given taxonomic subtree
        (e.g. "9397" for Chiroptera).  Resolved from a name via
        ``resolve_taxonomy_name()`` before calling this function.
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
        kws = [k.strip().replace('"', '') for k in keyword.split('|') if k.strip()]
        if kws:
            conditions = []
            for kw in kws:
                conditions.append(f'study_title="*{kw}*"')
                conditions.append(f'study_description="*{kw}*"')
            query_parts.append("(" + " OR ".join(conditions) + ")")

    params = {
        "result": "study",
        "query": " AND ".join(query_parts),
        "fields": STUDY_FIELDS,
        "format": "json",
        "limit": limit,
    }
    return _get(ENA_SEARCH_URL, params)


def fetch_study_metadata(study_accession: str) -> dict[str, Any] | None:
    """Return metadata for a single study from the ENA study result set.

    Raises ``ENAError`` if the accession cannot be an ENA study accession —
    the Portal API answers such a query with a bare 400.
    """
    study_accession = validate_study_accession(study_accession)
    params = {
        "result": "study",
        "query": f'study_accession="{study_accession}" OR secondary_study_accession="{study_accession}"',
        "fields": STUDY_FIELDS,
        "format": "json",
        "limit": 1,
    }
    records = _get(ENA_SEARCH_URL, params)
    return records[0] if records else None


def fetch_studies_batch(
    accessions: list[str],
    *,
    chunk_size: int = 20,
) -> list[dict[str, Any]]:
    """Fetch study metadata for multiple accessions in chunked API calls.

    Splits accessions into chunks to avoid excessively long query strings,
    then concatenates the results.
    """
    # One malformed accession would 400 the whole chunk, so drop those first.
    accessions = [a for a in (normalize_accession(a) for a in accessions) if is_study_accession(a)]
    results: list[dict[str, Any]] = []
    for i in range(0, len(accessions), chunk_size):
        chunk = accessions[i : i + chunk_size]
        parts = [
            f'study_accession="{acc}" OR secondary_study_accession="{acc}"'
            for acc in chunk
        ]
        params = {
            "result": "study",
            "query": " OR ".join(parts),
            "fields": STUDY_FIELDS,
            "format": "json",
            "limit": len(chunk) * 2 + 10,
        }
        results.extend(_get(ENA_SEARCH_URL, params))
    return results


def unique_studies(run_records: list[dict[str, Any]]) -> list[str]:
    """Extract the sorted unique set of study_accession values from run records."""
    return sorted({r.get("study_accession", "") for r in run_records if r.get("study_accession")})
