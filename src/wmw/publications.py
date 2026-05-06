"""Publication metadata resolution for wmw.

Given a BioProject accession, PubMed ID, or DOI, fetches:
  pubmed_id, pub_doi, pub_url, pub_title, pub_year, pub_journal, pub_authors, pub_pdf.

Resolution order:
  1. BioProject accession → PubMed IDs via NCBI E-utils elink (BioProject→PubMed
     and BioProject→SRA→PubMed)
  2. PubMed efetch for full metadata
  3. CrossRef REST API — fallback when no PubMed record is found

NCBI authentication: set NCBI_TOKEN env var for 10 req/s (vs 3/s unauthenticated).
Unpaywall PDF lookup: requires NCBI_EMAIL in config (used as contact email per their TOS).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

import requests

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
EUROPE_PMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov"
UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}?email={email}"
NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# ---------------------------------------------------------------------------
# NCBI E-utilities helpers
# ---------------------------------------------------------------------------

def _ncbi_get(endpoint: str, params: dict, api_key: str | None = None) -> bytes:
    """Make a GET request to NCBI E-utilities and return raw bytes."""
    p = dict(params)
    p["tool"] = "wmw"
    if api_key:
        p["api_key"] = api_key
    url = f"{NCBI_BASE}/{endpoint}.fcgi?" + urllib.parse.urlencode(p)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _esearch(db: str, term: str, api_key: str | None = None) -> list[str]:
    data = _ncbi_get("esearch", {"db": db, "term": term, "retmode": "json"}, api_key)
    return json.loads(data)["esearchresult"]["idlist"]


def _elink(dbfrom: str, db: str, ids: list[str], api_key: str | None = None) -> list[str]:
    if not ids:
        return []
    data = _ncbi_get("elink", {
        "dbfrom": dbfrom, "db": db, "id": ",".join(ids), "retmode": "json"
    }, api_key)
    result = json.loads(data)
    linked: list[str] = []
    for linkset in result.get("linksets", []):
        for linkdb in linkset.get("linksetdbs", []):
            linked.extend(str(x) for x in linkdb.get("links", []))
    return sorted(set(linked))


def _europe_pmc_search(accession: str) -> list[dict[str, Any]]:
    """Search Europe PMC for publications mentioning *accession*.

    Returns a list of dicts with 'pmid' and 'doi' keys (strings, may be empty).
    """
    try:
        resp = requests.get(
            EUROPE_PMC_URL,
            params={"query": accession, "format": "json", "pageSize": 25},
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("resultList", {}).get("result", [])
        return [
            {
                "pmid": str(item.get("pmid") or "").strip(),
                "doi": str(item.get("doi") or "").strip(),
            }
            for item in items
        ]
    except Exception:
        return []


def _ncbi_esearch_accession(accession: str, api_key: str | None = None) -> list[str]:
    """Search NCBI PubMed and PMC text for publications mentioning *accession*.

    Returns deduplicated PubMed IDs.
    """
    pmids: list[str] = []
    try:
        ids = _esearch("pubmed", f'"{accession}"', api_key)
        pmids.extend(ids)
        time.sleep(0.34)
        pmc_ids = _esearch("pmc", f'"{accession}"', api_key)
        time.sleep(0.34)
        if pmc_ids:
            linked = _elink("pmc", "pubmed", pmc_ids[:50], api_key)
            pmids.extend(linked)
    except Exception:
        pass
    return sorted(set(pmids))


def find_pubmed_ids(bioproject_accession: str, api_key: str | None = None) -> list[str]:
    """Resolve a BioProject accession to PubMed IDs.

    Tries three strategies in order, stopping as soon as results are found:
    1. NCBI elink: direct BioProject→PubMed only
    2. NCBI esearch: text search in PubMed and PMC databases
    3. Europe PMC: text search (extracts PMIDs from results)
    Returns empty list on any failure.

    Note: the BioProject→SRA→PubMed indirect elink is intentionally omitted.
    SRA records are often reused across studies, so that path returns PMIDs for
    papers that cited the same runs in earlier publications, producing false
    positives that may pre-date the study by years.
    """
    try:
        # Strategy 1: NCBI elink (direct BioProject→PubMed only)
        bp_ids = _esearch("bioproject", bioproject_accession, api_key)
        if bp_ids:
            time.sleep(0.34)
            pmids = _elink("bioproject", "pubmed", bp_ids, api_key)
            if pmids:
                return pmids

        # Strategy 2: NCBI esearch text search
        time.sleep(0.34)
        pmids = _ncbi_esearch_accession(bioproject_accession, api_key)
        if pmids:
            return pmids

        # Strategy 3: Europe PMC text search
        time.sleep(0.2)
        hits = _europe_pmc_search(bioproject_accession)
        return sorted(set(h["pmid"] for h in hits if h["pmid"]))

    except Exception:
        return []


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

def fetch_from_pubmed(pmid: str, api_key: str | None = None) -> dict[str, Any]:
    """Fetch publication metadata from NCBI PubMed given a PubMed ID.

    Returns a dict with pub_* keys; empty dict on failure.
    """
    try:
        xml_bytes = _ncbi_get("efetch", {"db": "pubmed", "id": pmid, "retmode": "xml"}, api_key)
        root = ET.fromstring(xml_bytes)
    except Exception:
        return {}

    article = root.find(".//PubmedArticle/MedlineCitation/Article")
    if article is None:
        return {}

    title = (article.findtext("ArticleTitle") or "").strip()

    year = ""
    pub_date = article.find(".//Journal/JournalIssue/PubDate")
    if pub_date is not None:
        year = pub_date.findtext("Year") or pub_date.findtext("MedlineDate") or ""
    year_str = str(year)[:4]
    year_int: int | None = int(year_str) if year_str.isdigit() else None

    journal = (article.findtext(".//Journal/Title") or "").strip()

    doi = ""
    for aid in root.findall(".//ArticleId"):
        if aid.get("IdType") == "doi":
            doi = (aid.text or "").strip()
            break

    authors: list[str] = []
    for author in article.findall(".//AuthorList/Author"):
        last = author.findtext("LastName") or ""
        initials = author.findtext("Initials") or ""
        if last:
            authors.append(f"{last} {initials}".strip())
    author_str = ", ".join(authors[:5])
    if len(authors) > 5:
        author_str += " et al."

    pub_url = f"https://doi.org/{doi}" if doi else f"{PUBMED_BASE}/{pmid}"

    return {
        "pubmed_id": pmid,
        "pub_doi": doi,
        "pub_url": pub_url,
        "pub_title": title,
        "pub_year": year_int,
        "pub_journal": journal,
        "pub_authors": author_str,
    }


# ---------------------------------------------------------------------------
# CrossRef
# ---------------------------------------------------------------------------

def fetch_from_crossref(doi: str) -> dict[str, Any]:
    """Fetch publication metadata from CrossRef given a DOI.

    Returns a dict with pub_* keys; empty dict on failure.
    """
    if not doi:
        return {}
    url = CROSSREF_URL.format(doi=doi.strip())
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "wmw/0.1 (mailto:wmw@wildmicrobiome.org)"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("message", {})
    except Exception:
        return {}

    title_list = data.get("title") or []
    title = title_list[0] if title_list else ""

    year_int = None
    for date_key in ("published-print", "published-online", "issued"):
        parts = (data.get(date_key) or {}).get("date-parts", [[]])[0]
        if parts:
            try:
                year_int = int(parts[0])
            except (TypeError, ValueError):
                pass
            break

    journal_list = data.get("container-title") or []
    journal = journal_list[0] if journal_list else ""

    authors: list[str] = []
    for a in data.get("author") or []:
        last = a.get("family", "")
        given = a.get("given", "")
        initials = "".join(w[0] for w in given.split() if w) if given else ""
        if last:
            authors.append(f"{last} {initials}".strip())
    author_str = ", ".join(authors[:5])
    if len(authors) > 5:
        author_str += " et al."

    pub_url = data.get("URL") or (f"https://doi.org/{doi}" if doi else "")

    return {
        "pubmed_id": "",
        "pub_doi": doi,
        "pub_url": pub_url,
        "pub_title": title,
        "pub_year": year_int,
        "pub_journal": journal,
        "pub_authors": author_str,
    }


# ---------------------------------------------------------------------------
# Unpaywall — OA PDF URL
# ---------------------------------------------------------------------------

def fetch_pdf_url(doi: str, email: str) -> str:
    """Return the best OA PDF URL from Unpaywall for *doi*, or '' if unavailable."""
    if not doi or not email:
        return ""
    url = UNPAYWALL_URL.format(doi=doi.strip(), email=email)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "wmw/0.2 (mailto:wmw@wildmicrobiome.org)"},
            timeout=15,
        )
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return ""
    best = data.get("best_oa_location") or {}
    return str(best.get("url_for_pdf") or "").strip()


# ---------------------------------------------------------------------------
# Unified resolver
# ---------------------------------------------------------------------------

def resolve(
    pubmed_id: str = "",
    doi: str = "",
    api_key: str | None = None,
    email: str | None = None,
    bioproject_accession: str = "",
    release_year: int | None = None,
) -> dict[str, Any]:
    """Resolve publication metadata from PubMed (preferred) or CrossRef.

    If pubmed_id is not provided, attempts to find one via the BioProject
    accession using NCBI E-utils. Falls back to CrossRef when no PubMed record
    is found. Sets pub_pdf to an Airtable attachment list [{"url": ...}] when
    an OA PDF is found via Unpaywall (requires email).

    release_year: when provided, any auto-discovered paper whose pub_year is
    more than 2 years before release_year is discarded and Europe PMC is
    queried immediately as a recovery source.  This guard is not applied when
    pubmed_id is supplied directly (caller is assumed to have curated it).
    """
    result: dict[str, Any] = {}

    effective_pmid = pubmed_id.strip()
    _auto_discovered = False
    if not effective_pmid and bioproject_accession:
        pmids = find_pubmed_ids(bioproject_accession, api_key)
        if pmids:
            effective_pmid = pmids[0]
            _auto_discovered = True
        elif not doi:
            # All PMID strategies exhausted — try Europe PMC for a DOI
            hits = _europe_pmc_search(bioproject_accession)
            for h in hits:
                if h.get("doi"):
                    doi = h["doi"]
                    break

    if effective_pmid:
        result = fetch_from_pubmed(effective_pmid, api_key)

    # Year-plausibility guard (auto-discovery only): if the resolved paper
    # pre-dates the study release by more than 2 years, retry via Europe PMC.
    if _auto_discovered and release_year and result.get("pub_year"):
        try:
            if int(result["pub_year"]) < release_year - 2:
                result = {}
                effective_pmid = ""
                for h in _europe_pmc_search(bioproject_accession):
                    if h.get("pmid"):
                        candidate = fetch_from_pubmed(h["pmid"], api_key)
                        if candidate.get("pub_year"):
                            try:
                                if int(candidate["pub_year"]) >= release_year - 2:
                                    result = candidate
                                    effective_pmid = h["pmid"]
                                    break
                            except (TypeError, ValueError):
                                pass
                    if not result and h.get("doi"):
                        doi = h["doi"]
                        break
        except (TypeError, ValueError):
            pass

    if not result.get("pub_title") and (doi or result.get("pub_doi")):
        effective_doi = doi or result.get("pub_doi", "")
        result = fetch_from_crossref(effective_doi)
        if effective_pmid:
            result["pubmed_id"] = effective_pmid

    effective_doi = result.get("pub_doi") or doi
    if effective_doi and email:
        pdf_url = fetch_pdf_url(effective_doi, email)
        if pdf_url:
            result["pub_pdf"] = [{"url": pdf_url}]

    return result


def _extract_year(date_str: str | None) -> int | None:
    """Extract year from an ISO date string like '2026-01-15'. Returns None on failure."""
    if not date_str:
        return None
    try:
        return int(str(date_str).split("-")[0])
    except (ValueError, IndexError):
        return None


def resolve_batch(
    studies: list[dict[str, Any]],
    api_key: str | None = None,
    email: str | None = None,
    delay: float = 0.34,
    on_progress: Any = None,
) -> list[dict[str, Any]]:
    """Resolve publication metadata for a list of study dicts in-place.

    Uses BioProject accession → PubMed lookup when no pubmed_id or DOI is
    already known. Adds/overwrites pub_* keys on each study dict.
    Sleeps `delay` seconds between studies to respect rate limits (note:
    find_pubmed_ids also sleeps internally between its own API calls).
    If on_progress is callable it is called with each study dict after resolution.
    Returns the mutated list.

    Passes first_public from each study to resolve() so its year-plausibility
    guard can discard auto-discovered papers that pre-date the study release by
    more than 2 years and retry via Europe PMC.
    """
    for study in studies:
        pmid = str(study.get("pubmed_id") or "").strip()
        doi = str(study.get("pub_doi") or "").strip()
        bioproject_accession = str(study.get("study_accession") or "").strip()
        if not pmid and not doi and not bioproject_accession:
            if on_progress is not None:
                on_progress(study)
            continue
        pub = resolve(
            pubmed_id=pmid,
            doi=doi,
            api_key=api_key,
            email=email,
            bioproject_accession=bioproject_accession,
            release_year=_extract_year(study.get("first_public")),
        )
        study.update(pub)
        if on_progress is not None:
            on_progress(study)
        time.sleep(delay)
    return studies
