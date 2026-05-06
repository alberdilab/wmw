"""Tests for wmw.publications — PubMed and CrossRef metadata resolution."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

from wmw import publications


# ---------------------------------------------------------------------------
# CrossRef
# ---------------------------------------------------------------------------

def _crossref_response(doi="10.1000/test", title="Test Paper", year=2023,
                       journal="Nature", authors=None):
    authors = authors or [{"family": "Smith", "given": "John"}, {"family": "Jones", "given": "A"}]
    return {
        "message": {
            "title": [title],
            "published-print": {"date-parts": [[year, 1, 1]]},
            "container-title": [journal],
            "author": authors,
            "URL": f"https://doi.org/{doi}",
            "DOI": doi,
        }
    }


def test_fetch_from_crossref_success():
    with patch("wmw.publications.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: _crossref_response(),
        )
        mock_get.return_value.raise_for_status.return_value = None
        result = publications.fetch_from_crossref("10.1000/test")

    assert result["pub_title"] == "Test Paper"
    assert result["pub_year"] == 2023
    assert result["pub_journal"] == "Nature"
    assert result["pub_doi"] == "10.1000/test"
    assert result["pub_url"] == "https://doi.org/10.1000/test"
    assert "Smith" in result["pub_authors"]


def test_fetch_from_crossref_failure_returns_empty():
    with patch("wmw.publications.requests.get", side_effect=Exception("timeout")):
        result = publications.fetch_from_crossref("10.1000/bad")
    assert result == {}


def test_fetch_from_crossref_empty_doi():
    result = publications.fetch_from_crossref("")
    assert result == {}


def test_fetch_from_crossref_authors_truncated():
    authors = [{"family": f"Author{i}", "given": "A"} for i in range(8)]
    with patch("wmw.publications.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: _crossref_response(authors=authors),
        )
        mock_get.return_value.raise_for_status.return_value = None
        result = publications.fetch_from_crossref("10.1000/many")
    assert result["pub_authors"].endswith("et al.")


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <Article>
        <Journal>
          <Title>Science</Title>
          <JournalIssue>
            <PubDate><Year>2022</Year></PubDate>
          </JournalIssue>
        </Journal>
        <ArticleTitle>Wild gut metagenomes</ArticleTitle>
        <AuthorList>
          <Author><LastName>Alberdi</LastName><Initials>A</Initials></Author>
          <Author><LastName>Hansen</LastName><Initials>LH</Initials></Author>
        </AuthorList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1126/science.abc123</ArticleId>
        <ArticleId IdType="pubmed">12345678</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""


def test_fetch_from_pubmed_success():
    with patch("wmw.publications._ncbi_get", return_value=PUBMED_XML.encode()):
        result = publications.fetch_from_pubmed("12345678")

    assert result["pub_title"] == "Wild gut metagenomes"
    assert result["pub_year"] == 2022
    assert result["pub_journal"] == "Science"
    assert result["pub_doi"] == "10.1126/science.abc123"
    assert result["pub_url"] == "https://doi.org/10.1126/science.abc123"
    assert "Alberdi" in result["pub_authors"]
    assert result["pubmed_id"] == "12345678"


# ---------------------------------------------------------------------------
# fetch_pdf_url (Unpaywall)
# ---------------------------------------------------------------------------

def test_fetch_pdf_url_success():
    payload = {
        "doi": "10.1000/test",
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://europepmc.org/articles/pmc123/pdf/",
        },
    }
    with patch("wmw.publications.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: payload)
        mock_get.return_value.raise_for_status.return_value = None
        result = publications.fetch_pdf_url("10.1000/test", "test@example.com")
    assert result == "https://europepmc.org/articles/pmc123/pdf/"


def test_fetch_pdf_url_no_pdf_location():
    payload = {"doi": "10.1000/test", "is_oa": True, "best_oa_location": {"url_for_pdf": None}}
    with patch("wmw.publications.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: payload)
        mock_get.return_value.raise_for_status.return_value = None
        result = publications.fetch_pdf_url("10.1000/test", "test@example.com")
    assert result == ""


def test_fetch_pdf_url_404():
    with patch("wmw.publications.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=404)
        result = publications.fetch_pdf_url("10.1000/missing", "test@example.com")
    assert result == ""


def test_fetch_pdf_url_failure():
    with patch("wmw.publications.requests.get", side_effect=Exception("timeout")):
        result = publications.fetch_pdf_url("10.1000/bad", "test@example.com")
    assert result == ""


def test_fetch_pdf_url_empty_inputs():
    assert publications.fetch_pdf_url("", "test@example.com") == ""
    assert publications.fetch_pdf_url("10.1000/test", "") == ""


def test_resolve_includes_pub_pdf():
    fake_pub = {
        "pub_title": "A paper", "pub_year": "2024", "pub_journal": "Nature",
        "pub_doi": "10.1000/x", "pub_url": "https://doi.org/10.1000/x",
        "pub_authors": "Smith J", "pubmed_id": "",
    }
    with patch("wmw.publications.fetch_from_crossref", return_value=dict(fake_pub)), \
         patch("wmw.publications.fetch_pdf_url", return_value="https://example.com/paper.pdf"):
        result = publications.resolve(doi="10.1000/x", email="test@example.com")
    assert result["pub_pdf"] == [{"url": "https://example.com/paper.pdf"}]


def test_resolve_no_pub_pdf_when_unpaywall_empty():
    fake_pub = {
        "pub_title": "A paper", "pub_year": "2024", "pub_journal": "Nature",
        "pub_doi": "10.1000/x", "pub_url": "https://doi.org/10.1000/x",
        "pub_authors": "Smith J", "pubmed_id": "",
    }
    with patch("wmw.publications.fetch_from_crossref", return_value=dict(fake_pub)), \
         patch("wmw.publications.fetch_pdf_url", return_value=""):
        result = publications.resolve(doi="10.1000/x", email="test@example.com")
    assert "pub_pdf" not in result


def test_resolve_finds_pubmed_id_via_bioproject():
    fake_pub = {
        "pub_title": "A paper", "pub_year": "2023", "pub_journal": "Science",
        "pub_doi": "10.1000/y", "pub_url": "https://doi.org/10.1000/y",
        "pub_authors": "Smith J", "pubmed_id": "99999999",
    }
    with patch("wmw.publications.find_pubmed_ids", return_value=["99999999"]), \
         patch("wmw.publications.fetch_from_pubmed", return_value=fake_pub):
        result = publications.resolve(bioproject_accession="PRJNA123456")
    assert result["pubmed_id"] == "99999999"
    assert result["pub_title"] == "A paper"


# ---------------------------------------------------------------------------
# _europe_pmc_search
# ---------------------------------------------------------------------------

def _epmc_response(pmid="12345678", doi="10.1000/test"):
    return {
        "resultList": {
            "result": [{"pmid": pmid, "doi": doi, "title": "A study", "pubYear": "2024"}]
        }
    }


def test_europe_pmc_search_success():
    with patch("wmw.publications.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: _epmc_response()
        )
        mock_get.return_value.raise_for_status.return_value = None
        hits = publications._europe_pmc_search("PRJNA123456")
    assert len(hits) == 1
    assert hits[0]["pmid"] == "12345678"
    assert hits[0]["doi"] == "10.1000/test"
    # Query must be bare (no surrounding quotes) so EuropePMC matches accession-ID fields
    call_params = mock_get.call_args[1]["params"]
    assert call_params["query"] == "PRJNA123456"


def test_europe_pmc_search_no_pmid_returns_doi():
    resp = {"resultList": {"result": [{"pmid": None, "doi": "10.1000/x"}]}}
    with patch("wmw.publications.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: resp)
        mock_get.return_value.raise_for_status.return_value = None
        hits = publications._europe_pmc_search("PRJEB001")
    assert hits[0]["pmid"] == ""
    assert hits[0]["doi"] == "10.1000/x"


def test_europe_pmc_search_failure_returns_empty():
    with patch("wmw.publications.requests.get", side_effect=Exception("timeout")):
        hits = publications._europe_pmc_search("PRJNA000")
    assert hits == []


# ---------------------------------------------------------------------------
# _ncbi_esearch_accession
# ---------------------------------------------------------------------------

def test_ncbi_esearch_accession_pubmed_hit():
    esearch_json = json.dumps({"esearchresult": {"idlist": ["99887766"]}}).encode()
    pmc_json = json.dumps({"esearchresult": {"idlist": []}}).encode()
    elink_json = json.dumps({"linksets": []}).encode()

    with patch("wmw.publications._ncbi_get", side_effect=[esearch_json, pmc_json, elink_json]), \
         patch("wmw.publications.time.sleep"):
        pmids = publications._ncbi_esearch_accession("PRJNA123456")
    assert "99887766" in pmids


def test_ncbi_esearch_accession_pmc_converted():
    pubmed_json = json.dumps({"esearchresult": {"idlist": []}}).encode()
    pmc_json = json.dumps({"esearchresult": {"idlist": ["7654321"]}}).encode()
    elink_json = json.dumps({
        "linksets": [{"linksetdbs": [{"links": ["11223344"]}]}]
    }).encode()

    with patch("wmw.publications._ncbi_get", side_effect=[pubmed_json, pmc_json, elink_json]), \
         patch("wmw.publications.time.sleep"):
        pmids = publications._ncbi_esearch_accession("PRJNA999")
    assert "11223344" in pmids


def test_ncbi_esearch_accession_failure_returns_empty():
    with patch("wmw.publications._ncbi_get", side_effect=Exception("network")):
        pmids = publications._ncbi_esearch_accession("PRJNA000")
    assert pmids == []


# ---------------------------------------------------------------------------
# find_pubmed_ids — multi-strategy fallback
# ---------------------------------------------------------------------------

def test_find_pubmed_ids_direct_bioproject_link():
    # Strategy 1 direct BioProject→PubMed elink succeeds
    with patch("wmw.publications._esearch", return_value=["bp1"]), \
         patch("wmw.publications._elink", return_value=["12345678"]), \
         patch("wmw.publications.time.sleep"):
        pmids = publications.find_pubmed_ids("PRJNA111")
    assert pmids == ["12345678"]


def test_find_pubmed_ids_falls_back_to_ncbi_esearch():
    # Direct elink finds nothing; esearch finds a PMID.
    # Only one _elink call is made (direct BioProject→PubMed); SRA path is absent.
    with patch("wmw.publications._esearch", return_value=["bp1"]), \
         patch("wmw.publications._elink", return_value=[]), \
         patch("wmw.publications._ncbi_esearch_accession", return_value=["55443322"]), \
         patch("wmw.publications.time.sleep"):
        pmids = publications.find_pubmed_ids("PRJNA111")
    assert pmids == ["55443322"]


def test_find_pubmed_ids_falls_back_to_europe_pmc():
    # Direct elink and esearch both find nothing; Europe PMC has a PMID.
    with patch("wmw.publications._esearch", return_value=["bp1"]), \
         patch("wmw.publications._elink", return_value=[]), \
         patch("wmw.publications._ncbi_esearch_accession", return_value=[]), \
         patch("wmw.publications._europe_pmc_search",
               return_value=[{"pmid": "77665544", "doi": "10.1000/x"}]), \
         patch("wmw.publications.time.sleep"):
        pmids = publications.find_pubmed_ids("PRJNA222")
    assert pmids == ["77665544"]


def test_find_pubmed_ids_europe_pmc_no_pmid_returns_empty():
    # Europe PMC finds only a DOI, no PMID — find_pubmed_ids returns [].
    with patch("wmw.publications._esearch", return_value=["bp1"]), \
         patch("wmw.publications._elink", return_value=[]), \
         patch("wmw.publications._ncbi_esearch_accession", return_value=[]), \
         patch("wmw.publications._europe_pmc_search",
               return_value=[{"pmid": "", "doi": "10.1000/doionly"}]), \
         patch("wmw.publications.time.sleep"):
        pmids = publications.find_pubmed_ids("PRJNA333")
    assert pmids == []


# ---------------------------------------------------------------------------
# resolve — Europe PMC DOI fallback
# ---------------------------------------------------------------------------

def test_resolve_uses_europe_pmc_doi_fallback():
    fake_pub = {
        "pub_title": "A paper", "pub_year": 2024, "pub_journal": "Nature",
        "pub_doi": "10.1000/doionly", "pub_url": "https://doi.org/10.1000/doionly",
        "pub_authors": "Jones A", "pubmed_id": "",
    }
    with patch("wmw.publications.find_pubmed_ids", return_value=[]), \
         patch("wmw.publications._europe_pmc_search",
               return_value=[{"pmid": "", "doi": "10.1000/doionly"}]), \
         patch("wmw.publications.fetch_from_crossref", return_value=fake_pub):
        result = publications.resolve(bioproject_accession="PRJNA444")
    assert result["pub_doi"] == "10.1000/doionly"
    assert result["pub_title"] == "A paper"


# ---------------------------------------------------------------------------
# resolve_batch
# ---------------------------------------------------------------------------

def test_resolve_batch_skips_entries_without_ids():
    studies = [
        {"pubmed_id": "", "pub_doi": ""},
    ]
    result = publications.resolve_batch(studies, delay=0)
    assert result[0].get("pub_title", "") == ""


def test_resolve_batch_mutates_in_place():
    studies = [{"study_accession": "PRJEB002", "pubmed_id": "", "pub_doi": "10.1000/x"}]
    fake_pub = {"pub_title": "A paper", "pub_year": "2024", "pub_journal": "Nature",
                "pub_doi": "10.1000/x", "pub_url": "https://doi.org/10.1000/x",
                "pub_authors": "Smith J", "pubmed_id": ""}
    with patch("wmw.publications.fetch_from_crossref", return_value=fake_pub):
        publications.resolve_batch(studies, delay=0)
    assert studies[0]["pub_title"] == "A paper"


def test_resolve_batch_uses_bioproject_lookup():
    studies = [{"study_accession": "PRJNA999", "pubmed_id": "", "pub_doi": ""}]
    fake_pub = {"pub_title": "Wild study", "pub_year": "2024", "pub_journal": "Nature",
                "pub_doi": "10.1000/z", "pub_url": "https://doi.org/10.1000/z",
                "pub_authors": "Jones A", "pubmed_id": "11111111"}
    with patch("wmw.publications.find_pubmed_ids", return_value=["11111111"]), \
         patch("wmw.publications.fetch_from_pubmed", return_value=fake_pub):
        publications.resolve_batch(studies, delay=0)
    assert studies[0]["pub_title"] == "Wild study"


def test_resolve_batch_discards_implausibly_old_publication():
    # Auto-discovered old paper (pub_year 2003 vs first_public 2026) is discarded
    # via the year guard inside resolve(); EuropePMC recovery returns nothing here.
    studies = [{"study_accession": "PRJNA999", "pubmed_id": "",
                "pub_doi": "", "first_public": "2026-03-01"}]
    old_pub = {"pub_title": "Old paper", "pub_year": 2003, "pub_journal": "Old Journal",
               "pub_doi": "10.1000/old", "pub_url": "https://doi.org/10.1000/old",
               "pub_authors": "Old A", "pubmed_id": "00001111"}
    with patch("wmw.publications.find_pubmed_ids", return_value=["00001111"]), \
         patch("wmw.publications.fetch_from_pubmed", return_value=old_pub), \
         patch("wmw.publications._europe_pmc_search", return_value=[]):
        publications.resolve_batch(studies, delay=0)
    assert studies[0].get("pub_title", "") == ""


def test_resolve_batch_keeps_recent_publication():
    # pub_year 2025 is within 2 years of first_public 2026 → kept
    studies = [{"study_accession": "PRJNA999", "pubmed_id": "",
                "pub_doi": "", "first_public": "2026-03-01"}]
    recent_pub = {"pub_title": "Recent paper", "pub_year": 2025, "pub_journal": "Nature",
                  "pub_doi": "10.1000/recent", "pub_url": "https://doi.org/10.1000/recent",
                  "pub_authors": "New A", "pubmed_id": "22223333"}
    with patch("wmw.publications.find_pubmed_ids", return_value=["22223333"]), \
         patch("wmw.publications.fetch_from_pubmed", return_value=recent_pub):
        publications.resolve_batch(studies, delay=0)
    assert studies[0]["pub_title"] == "Recent paper"


# ---------------------------------------------------------------------------
# resolve — year-plausibility guard with EuropePMC recovery
# ---------------------------------------------------------------------------

def test_resolve_year_guard_discards_old_auto_discovered_paper():
    old_pub = {"pub_title": "Old paper", "pub_year": 2003, "pubmed_id": "00001111",
               "pub_doi": "10.1000/old", "pub_url": "", "pub_journal": "Old J", "pub_authors": "X"}
    with patch("wmw.publications.find_pubmed_ids", return_value=["00001111"]), \
         patch("wmw.publications.fetch_from_pubmed", return_value=old_pub), \
         patch("wmw.publications._europe_pmc_search", return_value=[]):
        result = publications.resolve(bioproject_accession="PRJNA999", release_year=2026)
    assert result.get("pub_title", "") == ""


def test_resolve_year_guard_recovery_via_europe_pmc_pmid():
    old_pub = {"pub_title": "Old paper", "pub_year": 2003, "pubmed_id": "00001111",
               "pub_doi": "", "pub_url": "", "pub_journal": "Old J", "pub_authors": "X"}
    good_pub = {"pub_title": "New paper", "pub_year": 2026, "pubmed_id": "41987827",
                "pub_doi": "10.1093/ismeco/ycag036", "pub_url": "https://doi.org/10.1093/ismeco/ycag036",
                "pub_journal": "ISME Commun", "pub_authors": "Bornbusch SL et al."}
    with patch("wmw.publications.find_pubmed_ids", return_value=["00001111"]), \
         patch("wmw.publications.fetch_from_pubmed", side_effect=[old_pub, good_pub]), \
         patch("wmw.publications._europe_pmc_search",
               return_value=[{"pmid": "41987827", "doi": "10.1093/ismeco/ycag036"}]):
        result = publications.resolve(bioproject_accession="PRJNA1337465", release_year=2026)
    assert result["pub_title"] == "New paper"
    assert result["pub_year"] == 2026


def test_resolve_year_guard_not_applied_to_direct_pmid():
    # Guard only fires for auto-discovered papers; explicit pubmed_id is trusted as-is.
    old_pub = {"pub_title": "Old paper", "pub_year": 2003, "pubmed_id": "00001111",
               "pub_doi": "10.1000/old", "pub_url": "", "pub_journal": "Old J", "pub_authors": "X"}
    with patch("wmw.publications.fetch_from_pubmed", return_value=old_pub):
        result = publications.resolve(pubmed_id="00001111", release_year=2026)
    assert result["pub_title"] == "Old paper"
