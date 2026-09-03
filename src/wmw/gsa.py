"""GSA (Genome Sequence Archive, NGDC/CNCB) queries for wmw.

GSA publishes no JSON API. This module drives the three session-free endpoints
that sit behind the public web interface:

* ``POST /gsa/search/``               — PubMed-style query grammar, HTML results
* ``GET  /gsa/browse/<CRA>``          — study summary and paginated run table
* ``POST /gsa/file/exportExcelFile``  — the full submitter metadata workbook

The workbook is the primary source for run records: it carries file names,
sizes, MD5 checksums and download URLs alongside host, collection date and
geographic location, none of which appear on the HTML pages.

GSA also mirrors INSDC (SRA) submissions. Those records link to
``/gsa/browse/insdc/SRA…/SRX…`` and carry ``Center=INSDC``; every query issued
here is pinned to ``"NGDC"[center]`` so only GSA-native studies are returned
and ENA-sourced studies are not duplicated.
"""

from __future__ import annotations

import re
import time
import zipfile
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

import requests

GSA_SEARCH_URL = "https://ngdc.cncb.ac.cn/gsa/search/"
GSA_BROWSE_URL = "https://ngdc.cncb.ac.cn/gsa/browse"
GSA_EXCEL_URL = "https://ngdc.cncb.ac.cn/gsa/file/exportExcelFile"
BIOPROJECT_BROWSE_URL = "https://ngdc.cncb.ac.cn/bioproject/browse"

# GSA serves FTP paths in the metadata workbook. The HTTPS mirror of the same
# tree supports range requests, so downloads are resumable.
GSA_FTP_PREFIX = "ftp://download.big.ac.cn/"
GSA_HTTPS_PREFIX = "https://download.cncb.ac.cn/"

# The site is Chinese by default and switches to English labels on this header.
# Every parser below keys off the English labels.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Restricts results to GSA-native submissions, excluding the INSDC mirror.
NATIVE_CENTER_TERM = '"NGDC"[center]'

# Search fields accepted by the advanced-search grammar.
VALID_SEARCH_FIELDS = {
    "center",
    "experiment",
    "fileType",
    "title",
    "libLayout",
    "platform",
    "source",
    "strategy",
    "selection",
    "insertSize",
    "ReleaseDate",
    "dataset",
    "projectAcc",
    "sampleACC",
    "sampleName",
    "sampleType",
    "organism",
}

# GSA labels metagenome submissions with this organism name.
DEFAULT_ORGANISM = "organismal metagenomes"

# Result rows link to /gsa/browse/<CRA>/<CRX> for native records and to
# /gsa/browse/insdc/<SRA>/<SRX> for mirrored ones.
_RESULT_LINK_RE = re.compile(r"/gsa/browse/(CRA\d+)/(CRX\d+)")
_TOTAL_ITEMS_RE = re.compile(r"Total Items:(?:&nbsp;|\s)*(\d+)")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_IN_TEXT_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PRJCA_RE = re.compile(r"PRJCA\d+")
_DOWNLOAD_ROOT_RE = re.compile(r"https://download\.cncb\.ac\.cn/(gsa\d*)/CRA\d+")
# "CRR2009389_r1.fq.gz (3402548213 bytes)"
_FILENAME_SIZE_RE = re.compile(r"^(?P<name>\S+)(?:\s*\((?P<size>\d+)\s*bytes?\))?\s*$")
_CELL_REF_RE = re.compile(r"^([A-Z]+)")

_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RELS_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

DEBUG: bool = False

# Max rows a single search page will return; larger values are ignored by GSA.
MAX_PAGE_SIZE = 1000

_taxonomy_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    retries: int = 3,
    timeout: int = 180,
) -> requests.Response:
    """Issue a GSA request, retrying on rate limits and server errors."""
    if DEBUG:
        print(f"DEBUG GSA {method} {url} params={params} data={data}")
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.request(
                method,
                url,
                params=params,
                data=data,
                headers=_HEADERS,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 429 or status >= 500:
                last_exc = exc
                time.sleep(2 ** attempt)
                continue
            raise
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"GSA request failed: {url}")


# ---------------------------------------------------------------------------
# Query grammar
# ---------------------------------------------------------------------------

def to_gsa_date(iso_date: str) -> str:
    """Convert an ISO date (YYYY-MM-DD) to the MM/DD/YYYY form GSA expects."""
    s = str(iso_date).strip()
    if not _ISO_DATE_RE.match(s):
        raise ValueError(f"Expected an ISO date (YYYY-MM-DD), got {iso_date!r}")
    year, month, day = s.split("-")
    return f"{month}/{day}/{year}"


def date_range_term(date_from: str, date_to: str) -> str:
    """Build the ``[ReleaseDate]`` clause for a date window."""
    return f'"{to_gsa_date(date_from)} - {to_gsa_date(date_to)}"[ReleaseDate]'


def _term(value: str, field: str) -> str:
    """Quote a single search term, tagged with its field when one is given."""
    # Double quotes delimit the term in the grammar and cannot be escaped.
    cleaned = str(value).replace('"', "").strip()
    if not cleaned:
        return ""
    if field and field not in VALID_SEARCH_FIELDS:
        raise ValueError(
            f"Unknown GSA search field {field!r}; expected one of "
            f"{sorted(VALID_SEARCH_FIELDS)}"
        )
    return f'"{cleaned}"[{field}]' if field else f'"{cleaned}"'


def _or_group(values: str, field: str) -> str:
    """Build a parenthesised OR clause from a comma- or pipe-separated value list."""
    terms = [_term(v, field) for v in re.split(r"[,|]", str(values))]
    terms = [t for t in terms if t]
    if not terms:
        return ""
    if len(terms) == 1:
        return terms[0]
    return "(" + " OR ".join(terms) + ")"


def build_query(
    *,
    date_from: str = "",
    date_to: str = "",
    organism: str = "",
    keyword: str = "",
    library_strategy: str = "",
    library_source: str = "",
    instrument_platform: str = "",
    project_accession: str = "",
    native_only: bool = True,
) -> str:
    """Compose a GSA advanced-search query string.

    Every argument is optional; comma- or pipe-separated values become OR
    groups, and the clauses are joined with AND.

    ``keyword`` searches GSA's ``title`` field, which holds the *experiment*
    title — usually a submitter's per-sample alias, not a study title. Callers
    wanting wmw's study-level keyword semantics should leave it blank and use
    ``keyword_matches()`` against the resolved study records instead.
    """
    parts: list[str] = []
    if native_only:
        parts.append(NATIVE_CENTER_TERM)
    if organism:
        parts.append(_or_group(organism, "organism"))
    if date_from and date_to:
        parts.append(date_range_term(date_from, date_to))
    if keyword:
        parts.append(_or_group(keyword, "title"))
    if library_strategy:
        parts.append(_or_group(library_strategy, "strategy"))
    if library_source:
        parts.append(_or_group(library_source, "source"))
    if instrument_platform:
        parts.append(_or_group(instrument_platform, "platform"))
    if project_accession:
        parts.append(_or_group(project_accession, "projectAcc"))
    return " AND ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _text_lines(html: str) -> list[str]:
    """Flatten an HTML page to the sequence of visible text fragments."""
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", "|", body)
    body = body.replace("&nbsp;", " ").replace("&amp;", "&")
    body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return [frag.strip() for frag in re.split(r"[|\n]+", body) if frag.strip()]


def _labelled(lines: list[str], *labels: str) -> str:
    """Return the fragment following the first matching label."""
    wanted = {label.rstrip(":").casefold() for label in labels}
    for i, line in enumerate(lines):
        if line.rstrip(":").casefold() in wanted and i + 1 < len(lines):
            return lines[i + 1]
    return ""


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(query: str, *, page_size: int = MAX_PAGE_SIZE, page: int = 1) -> tuple[list[tuple[str, str]], int]:
    """Run one page of a GSA search.

    Returns ``(pairs, total)`` where ``pairs`` is the list of
    ``(study_accession, experiment_accession)`` links on the page and ``total``
    is the reported number of matching records. Only GSA-native rows appear in
    ``pairs``; INSDC-mirrored rows link elsewhere and are skipped.
    """
    resp = _request(
        "POST",
        GSA_SEARCH_URL,
        data={
            "searchTerm": query,
            "searchField": "",
            "pageSize": min(int(page_size), MAX_PAGE_SIZE),
            "from": int(page),
        },
    )
    html = resp.text
    total_match = _TOTAL_ITEMS_RE.search(html)
    total = int(total_match.group(1)) if total_match else 0
    return _RESULT_LINK_RE.findall(html), total


def keyword_matches(record: dict[str, Any], keyword: str) -> bool:
    """Test a study record against a comma- or pipe-separated keyword list.

    Matches case-insensitively against the study title and description, the
    same two fields ``wmw scan`` searches in ENA. A blank keyword matches
    everything.
    """
    terms = [k.strip().casefold() for k in re.split(r"[,|]", str(keyword or "")) if k.strip()]
    if not terms:
        return True
    haystack = " ".join(
        str(record.get(field, "") or "")
        for field in ("study_title", "study_description")
    ).casefold()
    return any(term in haystack for term in terms)


def search_study_accessions(
    date_from: str = "",
    date_to: str = "",
    *,
    organism: str = DEFAULT_ORGANISM,
    keyword: str = "",
    library_strategy: str = "",
    library_source: str = "",
    instrument_platform: str = "",
    limit: int = 10000,
) -> list[str]:
    """Return the GSA study accessions (CRA…) matching a search.

    GSA indexes experiments rather than studies, so this pages through the
    experiment hits and collects the distinct parent study of each. ``limit``
    caps the number of experiment records inspected, not the studies returned.
    """
    query = build_query(
        date_from=date_from,
        date_to=date_to,
        organism=organism,
        keyword=keyword,
        library_strategy=library_strategy,
        library_source=library_source,
        instrument_platform=instrument_platform,
    )
    accessions: list[str] = []
    seen: set[str] = set()
    # GSA's "from" is a page index, so the page size has to stay constant
    # across the loop or later offsets would overlap.
    page_size = max(1, min(MAX_PAGE_SIZE, limit))
    inspected = 0
    page = 1
    while inspected < limit:
        pairs, total = search(query, page_size=page_size, page=page)
        for study_acc, _experiment_acc in pairs:
            if study_acc not in seen:
                seen.add(study_acc)
                accessions.append(study_acc)
        inspected += page_size
        if not pairs or inspected >= total:
            break
        page += 1
    return accessions


# ---------------------------------------------------------------------------
# Study metadata
# ---------------------------------------------------------------------------

def fetch_browse_summary(study_accession: str) -> dict[str, str]:
    """Parse the study header of ``/gsa/browse/<CRA>``.

    Returns title, BioProject accession, release date and the download root
    (``gsa``, ``gsa2`` … ``gsa5``). The download root is not derivable from the
    accession and is only published here and in the metadata workbook.
    """
    resp = _request(
        "GET",
        f"{GSA_BROWSE_URL}/{study_accession}",
        params={"pageSize": 1, "pageNo": 1},
    )
    html = resp.text
    lines = _text_lines(html)

    release_date = ""
    raw_date = _labelled(lines, "Release date")
    date_match = _DATE_IN_TEXT_RE.search(raw_date)
    if date_match:
        release_date = date_match.group(0)

    bioproject = ""
    raw_bioproject = _labelled(lines, "BioProject")
    prj_match = _PRJCA_RE.search(raw_bioproject) or _PRJCA_RE.search(html)
    if prj_match:
        bioproject = prj_match.group(0)

    root_match = _DOWNLOAD_ROOT_RE.search(html)

    return {
        "study_accession": study_accession,
        "study_title": _labelled(lines, "Title"),
        "bioproject_accession": bioproject,
        "first_public": release_date,
        "download_root": root_match.group(1) if root_match else "",
    }


def fetch_bioproject(project_accession: str) -> dict[str, str]:
    """Parse ``/bioproject/browse/<PRJCA>`` for study description and organism."""
    resp = _request("GET", f"{BIOPROJECT_BROWSE_URL}/{project_accession}")
    lines = _text_lines(resp.text)

    release_date = ""
    date_match = _DATE_IN_TEXT_RE.search(_labelled(lines, "Release date"))
    if date_match:
        release_date = date_match.group(0)

    return {
        "study_title": _labelled(lines, "Title"),
        "study_description": _labelled(lines, "Description"),
        "scientific_name": _labelled(lines, "Organisms", "Organism"),
        "center_name": _labelled(lines, "Organization"),
        "first_public": release_date,
    }


def fetch_study_metadata(study_accession: str) -> dict[str, Any] | None:
    """Return study-level metadata for one GSA accession.

    Merges the GSA browse header with the linked NGDC BioProject record, which
    is where the description, organism and submitting organization live. A
    BioProject lookup failure is not fatal — the browse fields are still
    returned.
    """
    try:
        summary = fetch_browse_summary(study_accession)
    except requests.exceptions.RequestException:
        return None
    if not summary.get("study_title") and not summary.get("bioproject_accession"):
        return None

    record: dict[str, Any] = {
        "study_accession": study_accession,
        "secondary_study_accession": summary.get("bioproject_accession", ""),
        "study_title": summary.get("study_title", ""),
        "study_description": "",
        "scientific_name": "",
        "tax_id": "",
        "first_public": summary.get("first_public", ""),
        "center_name": "",
        "download_root": summary.get("download_root", ""),
    }

    project_accession = summary.get("bioproject_accession", "")
    if project_accession:
        try:
            project = fetch_bioproject(project_accession)
        except requests.exceptions.RequestException:
            project = {}
        for key in ("study_description", "scientific_name", "center_name"):
            if project.get(key):
                record[key] = project[key]
        if not record["study_title"] and project.get("study_title"):
            record["study_title"] = project["study_title"]
        if not record["first_public"] and project.get("first_public"):
            record["first_public"] = project["first_public"]

    return record


def fetch_studies_batch(accessions: list[str]) -> list[dict[str, Any]]:
    """Fetch study metadata for several accessions.

    GSA has no batch endpoint, so this is a sequential loop; accessions that
    cannot be resolved are skipped.
    """
    records: list[dict[str, Any]] = []
    for accession in accessions:
        record = fetch_study_metadata(accession)
        if record:
            records.append(record)
    return records


# ---------------------------------------------------------------------------
# Metadata workbook (.xlsx)
# ---------------------------------------------------------------------------

def _column_index(cell_ref: str) -> int:
    """Convert a cell reference ("AB12") to a zero-based column index."""
    match = _CELL_REF_RE.match(cell_ref or "")
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in si.iter(_XLSX_NS + "t"))
        for si in root.iter(_XLSX_NS + "si")
    ]


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Return ``(sheet name, archive path)`` pairs in workbook order."""
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.get("Id"): rel.get("Target", "")
        for rel in rels_root.iter(_RELS_NS + "Relationship")
    }
    sheets: list[tuple[str, str]] = []
    for i, sheet in enumerate(workbook.iter(_XLSX_NS + "sheet")):
        rel_id = sheet.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        target = targets.get(rel_id or "", "")
        if not target:
            target = f"worksheets/sheet{i + 1}.xml"
        path = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
        sheets.append((sheet.get("name", ""), path))
    return sheets


def _sheet_rows(
    archive: zipfile.ZipFile,
    path: str,
    strings: list[str],
) -> list[dict[str, str]]:
    """Read one worksheet into a list of header-keyed dicts."""
    root = ET.fromstring(archive.read(path))
    header: list[str] = []
    rows: list[dict[str, str]] = []

    for row in root.iter(_XLSX_NS + "row"):
        values: dict[int, str] = {}
        for cell in row.iter(_XLSX_NS + "c"):
            cell_type = cell.get("t")
            if cell_type == "inlineStr":
                is_node = cell.find(_XLSX_NS + "is")
                text = (
                    "".join(node.text or "" for node in is_node.iter(_XLSX_NS + "t"))
                    if is_node is not None
                    else ""
                )
            else:
                value_node = cell.find(_XLSX_NS + "v")
                text = (value_node.text or "") if value_node is not None else ""
                if cell_type == "s" and text:
                    try:
                        text = strings[int(text)]
                    except (ValueError, IndexError):
                        text = ""
            values[_column_index(cell.get("r", ""))] = text.strip()

        if not header:
            header = [
                values.get(i, "") for i in range(max(values, default=-1) + 1)
            ]
            continue
        rows.append(
            {
                name: values.get(i, "")
                for i, name in enumerate(header)
                if name
            }
        )
    return rows


def download_metadata_workbook(study_accession: str) -> bytes:
    """Download the submitter metadata workbook for a GSA study."""
    resp = _request(
        "POST",
        GSA_EXCEL_URL,
        data={"type": "3", "dlAcession": study_accession},
    )
    return resp.content


def parse_metadata_workbook(data: bytes) -> dict[str, list[dict[str, str]]]:
    """Parse the GSA metadata workbook into its Sample/Experiment/Run sheets."""
    with zipfile.ZipFile(BytesIO(data)) as archive:
        strings = _shared_strings(archive)
        sheets: dict[str, list[dict[str, str]]] = {}
        for name, path in _sheet_targets(archive):
            try:
                sheets[name] = _sheet_rows(archive, path, strings)
            except KeyError:
                sheets[name] = []
    return sheets


# ---------------------------------------------------------------------------
# Run records
# ---------------------------------------------------------------------------

def to_https(url: str) -> str:
    """Rewrite a GSA FTP path to its HTTPS mirror, which supports resuming."""
    url = str(url or "").strip()
    if url.startswith(GSA_FTP_PREFIX):
        return GSA_HTTPS_PREFIX + url[len(GSA_FTP_PREFIX):]
    return url


def _split_filename_size(value: str) -> tuple[str, int | None]:
    """Split ``"CRR1_r1.fq.gz (123 bytes)"`` into its name and byte count."""
    match = _FILENAME_SIZE_RE.match(str(value or "").strip())
    if not match:
        return "", None
    size = match.group("size")
    return match.group("name"), int(size) if size else None


def _platform_family(model: str) -> str:
    """Map a sequencer model to the ENA-style platform name."""
    lowered = str(model or "").lower()
    families = {
        "illumina": "ILLUMINA",
        "bgiseq": "BGISEQ",
        "dnbseq": "DNBSEQ",
        "mgiseq": "DNBSEQ",
        "nanopore": "OXFORD_NANOPORE",
        "promethion": "OXFORD_NANOPORE",
        "gridion": "OXFORD_NANOPORE",
        "minion": "OXFORD_NANOPORE",
        "pacbio": "PACBIO_SMRT",
        "sequel": "PACBIO_SMRT",
        "revio": "PACBIO_SMRT",
        "ion torrent": "ION_TORRENT",
    }
    for needle, family in families.items():
        if needle in lowered:
            return family
    return str(model or "").strip().upper()


def _country_from_location(location: str) -> str:
    """Take the country part of a ``"China: Fujian"`` style location string."""
    return str(location or "").split(":")[0].strip()


def search_study(study_accession: str) -> list[dict[str, Any]]:
    """Return all run records for one GSA study accession.

    Builds each record from the metadata workbook, joining the Run sheet to the
    Experiment sheet on the experiment accession and to the Sample sheet on the
    BioSample accession. The study release date is taken from the browse page,
    which the workbook does not carry.
    """
    sheets = parse_metadata_workbook(download_metadata_workbook(study_accession))
    runs = sheets.get("Run", [])
    experiments = {
        row.get("Accession", ""): row
        for row in sheets.get("Experiment", [])
        if row.get("Accession")
    }
    samples = {
        row.get("Accession", ""): row
        for row in sheets.get("Sample", [])
        if row.get("Accession")
    }

    first_public = ""
    try:
        first_public = fetch_browse_summary(study_accession).get("first_public", "")
    except requests.exceptions.RequestException:
        pass

    records: list[dict[str, Any]] = []
    for run in runs:
        run_accession = run.get("Accession", "")
        if not run_accession:
            continue
        experiment_accession = run.get("Experiment accession", "")
        experiment = experiments.get(experiment_accession, {})
        sample_accession = experiment.get("BioSample accession", "")
        sample = samples.get(sample_accession, {})

        name1, size1 = _split_filename_size(run.get("Read filename 1", ""))
        name2, size2 = _split_filename_size(run.get("Read filename 2", ""))
        ftp1 = str(run.get("DownLoad Read file1", "") or "").strip()
        # The header carries a double space, which GSA has shipped both ways.
        ftp2 = str(
            run.get("DownLoad  Read file2") or run.get("DownLoad Read file2") or ""
        ).strip()
        md5_1 = str(run.get("Read file1 MD5", "") or "").strip()
        md5_2 = str(run.get("Read file2 MD5", "") or "").strip()

        ftp_urls = [u for u in (ftp1, ftp2) if u]
        md5s = [m for m in (md5_1, md5_2) if m]
        model = experiment.get("Platform", "")
        location = sample.get("Geographic location", "")
        host = sample.get("Host", "")
        # GSA writes "NA" where a submitter left an attribute blank.
        if host.strip().upper() == "NA":
            host = ""

        records.append(
            {
                "run_accession": run_accession,
                "study_accession": study_accession,
                "sample_accession": sample_accession,
                "experiment_accession": experiment_accession,
                "scientific_name": sample.get("Organism", ""),
                "instrument_platform": _platform_family(model),
                "instrument_model": model,
                "library_strategy": experiment.get("Strategy", ""),
                "library_source": experiment.get("Source", ""),
                "library_layout": experiment.get("Layout", ""),
                "fastq_ftp": ";".join(ftp_urls),
                "fastq_md5": ";".join(md5s),
                "fastq_url_1": to_https(ftp1),
                "fastq_url_2": to_https(ftp2),
                "file_name_1": name1,
                "file_name_2": name2,
                "file_size_1": size1,
                "file_size_2": size2,
                "collection_date": sample.get("Collection date", ""),
                "first_public": first_public,
                "geo_loc_name": location,
                "country": _country_from_location(location),
                "host": host,
                # The run title is the submitter's own sample alias, not a
                # study title; kept for traceability, unused by the schema.
                "run_title": run.get("Run title", ""),
                "secondary_study_accession": run.get("BioProject accession", ""),
            }
        )
    return records


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

def resolve_taxonomy(records: list[dict[str, Any]]) -> int:
    """Fill ``tax_id`` and ``host_tax_id`` from the organism and host names.

    GSA records carry taxon names but no taxon IDs, and wmw's host-exclusion
    filters key off ``host_tax_id``. Names are resolved through the shared ENA
    taxonomy helper and cached in-process; unresolvable names are left blank.
    Returns the number of records that gained at least one taxon ID.
    """
    from wmw import ena

    def lookup(name: str) -> str:
        key = name.strip().casefold()
        if not key:
            return ""
        if key not in _taxonomy_cache:
            try:
                tax_id, _sci_name = ena.resolve_taxonomy_name(name.strip())
            except Exception:
                tax_id = ""
            _taxonomy_cache[key] = tax_id
        return _taxonomy_cache[key]

    resolved = 0
    for record in records:
        gained = False
        if not record.get("tax_id"):
            tax_id = lookup(str(record.get("scientific_name", "")))
            if tax_id:
                record["tax_id"] = tax_id
                gained = True
        host = str(record.get("host", ""))
        if host and not record.get("host_tax_id"):
            host_tax_id = lookup(host)
            if host_tax_id:
                record["host_tax_id"] = host_tax_id
                record.setdefault("host_scientific_name", host)
                gained = True
        if gained:
            resolved += 1
    return resolved


def unique_studies(run_records: list[dict[str, Any]]) -> list[str]:
    """Extract the sorted unique set of study_accession values from run records."""
    return sorted({r.get("study_accession", "") for r in run_records if r.get("study_accession")})
