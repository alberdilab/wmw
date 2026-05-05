"""Tests for wmw.publications — PubMed and CrossRef metadata resolution."""

from __future__ import annotations

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
    assert result["pub_year"] == "2023"
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
    mock_handle = MagicMock()
    mock_handle.read.return_value = PUBMED_XML.encode()

    with patch("wmw.publications.Entrez") as mock_entrez:
        mock_entrez.efetch.return_value = mock_handle
        result = publications.fetch_from_pubmed("12345678", "test@example.com")

    assert result["pub_title"] == "Wild gut metagenomes"
    assert result["pub_year"] == "2022"
    assert result["pub_journal"] == "Science"
    assert result["pub_doi"] == "10.1126/science.abc123"
    assert result["pub_url"] == "https://doi.org/10.1126/science.abc123"
    assert "Alberdi" in result["pub_authors"]
    assert result["pubmed_id"] == "12345678"


# ---------------------------------------------------------------------------
# resolve_batch
# ---------------------------------------------------------------------------

def test_resolve_batch_skips_entries_without_ids():
    studies = [
        {"study_accession": "PRJEB001", "pubmed_id": "", "pub_doi": ""},
    ]
    result = publications.resolve_batch(studies, email="test@example.com", delay=0)
    assert result[0].get("pub_title", "") == ""


def test_resolve_batch_mutates_in_place():
    studies = [{"study_accession": "PRJEB002", "pubmed_id": "", "pub_doi": "10.1000/x"}]
    fake_pub = {"pub_title": "A paper", "pub_year": "2024", "pub_journal": "Nature",
                "pub_doi": "10.1000/x", "pub_url": "https://doi.org/10.1000/x",
                "pub_authors": "Smith J", "pubmed_id": ""}
    with patch("wmw.publications.fetch_from_crossref", return_value=fake_pub):
        publications.resolve_batch(studies, email="test@example.com", delay=0)
    assert studies[0]["pub_title"] == "A paper"
