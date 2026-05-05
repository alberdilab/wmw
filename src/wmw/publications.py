"""Publication metadata resolution for wmw.

Given a PubMed ID (from ENA study records) or a DOI, fetches:
  pub_title, pub_year, pub_journal, pub_doi, pub_url, pub_authors.

Resolution order:
  1. PubMed (via Biopython Entrez) — preferred when pubmed_id is available
  2. CrossRef REST API — fallback when only a DOI is known
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

try:
    from Bio import Entrez
    _BIO_AVAILABLE = True
except ImportError:
    Entrez = None  # type: ignore[assignment]
    _BIO_AVAILABLE = False

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov"


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

def fetch_from_pubmed(pmid: str, email: str, api_key: str | None = None) -> dict[str, Any]:
    """Fetch publication metadata from NCBI PubMed given a PubMed ID.

    Returns a dict with pub_* keys; empty dict on failure.
    """
    if not _BIO_AVAILABLE or Entrez is None:
        return {}

    Entrez.email = email
    if api_key:
        Entrez.api_key = api_key

    try:
        handle = Entrez.efetch(db="pubmed", id=pmid, rettype="xml", retmode="xml")
        root = ET.fromstring(handle.read())
        handle.close()
    except Exception:
        return {}

    article = root.find(".//PubmedArticle/MedlineCitation/Article")
    if article is None:
        return {}

    title = (article.findtext("ArticleTitle") or "").strip()

    # Year — prefer PubDate/Year, fall back to MedlineDate
    year = ""
    pub_date = article.find(".//Journal/JournalIssue/PubDate")
    if pub_date is not None:
        year = pub_date.findtext("Year") or pub_date.findtext("MedlineDate") or ""
    year = str(year)[:4]  # keep only the 4-digit year

    journal = (article.findtext(".//Journal/Title") or "").strip()

    # DOI from ArticleIdList
    doi = ""
    for aid in root.findall(".//ArticleId"):
        if aid.get("IdType") == "doi":
            doi = (aid.text or "").strip()
            break

    # Authors — "Last FM, Last FM, ..."  (truncated to first 5)
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
        "pub_year": year,
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

    year = ""
    for date_key in ("published-print", "published-online", "issued"):
        parts = (data.get(date_key) or {}).get("date-parts", [[]])[0]
        if parts:
            year = str(parts[0])
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
        "pub_year": year,
        "pub_journal": journal,
        "pub_authors": author_str,
    }


# ---------------------------------------------------------------------------
# Unified resolver
# ---------------------------------------------------------------------------

def resolve(
    pubmed_id: str = "",
    doi: str = "",
    email: str = "",
    api_key: str | None = None,
) -> dict[str, Any]:
    """Resolve publication metadata from PubMed (preferred) or CrossRef.

    Tries PubMed first when a pubmed_id is available; falls back to CrossRef
    using the DOI (which may itself come from the PubMed response).
    """
    result: dict[str, Any] = {}

    if pubmed_id and email:
        result = fetch_from_pubmed(pubmed_id.strip(), email, api_key)

    if not result.get("pub_title") and (doi or result.get("pub_doi")):
        effective_doi = doi or result.get("pub_doi", "")
        result = fetch_from_crossref(effective_doi)
        if pubmed_id:
            result["pubmed_id"] = pubmed_id

    return result


def resolve_batch(
    studies: list[dict[str, Any]],
    email: str,
    api_key: str | None = None,
    delay: float = 0.35,
) -> list[dict[str, Any]]:
    """Resolve publication metadata for a list of study dicts in-place.

    Adds/overwrites pub_* keys on each study dict.
    Sleeps `delay` seconds between requests to respect rate limits.
    Returns the mutated list.
    """
    for study in studies:
        pmid = str(study.get("pubmed_id") or "").strip()
        doi = str(study.get("pub_doi") or "").strip()
        if not pmid and not doi:
            continue
        pub = resolve(pubmed_id=pmid, doi=doi, email=email, api_key=api_key)
        study.update(pub)
        time.sleep(delay)
    return studies
