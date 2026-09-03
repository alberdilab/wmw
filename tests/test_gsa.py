"""Tests for wmw.gsa — GSA (NGDC/CNCB) queries and metadata parsing."""

from __future__ import annotations

import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from wmw import gsa


def _mock_response(text: str = "", content: bytes = b"") -> MagicMock:
    mock = MagicMock()
    mock.text = text
    mock.content = content
    mock.raise_for_status.return_value = None
    return mock


# ---------------------------------------------------------------------------
# Query grammar
# ---------------------------------------------------------------------------

def test_to_gsa_date_converts_iso():
    assert gsa.to_gsa_date("2025-09-07") == "09/07/2025"


def test_to_gsa_date_rejects_non_iso():
    with pytest.raises(ValueError):
        gsa.to_gsa_date("09/07/2025")


def test_date_range_term():
    assert gsa.date_range_term("2025-09-01", "2025-09-30") == (
        '"09/01/2025 - 09/30/2025"[ReleaseDate]'
    )


def test_build_query_pins_to_native_center():
    assert gsa.NATIVE_CENTER_TERM in gsa.build_query(organism="organismal metagenomes")


def test_build_query_can_drop_native_center():
    query = gsa.build_query(organism="gut metagenome", native_only=False)
    assert gsa.NATIVE_CENTER_TERM not in query


def test_build_query_joins_clauses_with_and():
    query = gsa.build_query(
        date_from="2025-09-01",
        date_to="2025-09-30",
        organism="organismal metagenomes",
    )
    assert query == (
        '"NGDC"[center] AND "organismal metagenomes"[organism] '
        'AND "09/01/2025 - 09/30/2025"[ReleaseDate]'
    )


def test_build_query_builds_or_group_for_comma_list():
    query = gsa.build_query(library_strategy="WGS,METAGENOMIC")
    assert '("WGS"[strategy] OR "METAGENOMIC"[strategy])' in query


def test_build_query_accepts_pipe_separated_values():
    query = gsa.build_query(keyword="ECOLOG|WILD")
    assert '("ECOLOG"[title] OR "WILD"[title])' in query


def test_build_query_single_value_is_not_parenthesised():
    assert '"WGS"[strategy]' in gsa.build_query(library_strategy="WGS")
    assert "(" not in gsa.build_query(library_strategy="WGS", native_only=False)


def test_build_query_omits_date_when_only_one_bound_given():
    assert "ReleaseDate" not in gsa.build_query(date_from="2025-09-01")


def test_build_query_strips_quotes_from_terms():
    query = gsa.build_query(keyword='wild "animal"', native_only=False)
    assert query == '"wild animal"[title]'


def test_term_rejects_unknown_field():
    with pytest.raises(ValueError):
        gsa._term("x", "nonsense")


# ---------------------------------------------------------------------------
# Keyword matching (study level)
# ---------------------------------------------------------------------------

def test_keyword_matches_title_and_description():
    record = {"study_title": "Bat gut survey", "study_description": "Wild populations."}
    assert gsa.keyword_matches(record, "WILD")
    assert gsa.keyword_matches(record, "bat")
    assert not gsa.keyword_matches(record, "soil")


def test_keyword_matches_any_of_a_pipe_list():
    record = {"study_title": "Evolutionary genomics", "study_description": ""}
    assert gsa.keyword_matches(record, "ECOLOG|EVOLUT|WILD")


def test_blank_keyword_matches_everything():
    assert gsa.keyword_matches({}, "")


# ---------------------------------------------------------------------------
# Search result parsing
# ---------------------------------------------------------------------------

_SEARCH_HTML = """
<span>Total Items:&nbsp;591</span>
<a href="https://ngdc.cncb.ac.cn/gsa/browse/CRA028180/CRX1868736">CRX1868736</a>
<a href="https://ngdc.cncb.ac.cn/gsa/browse/CRA028180/CRX1868737">CRX1868737</a>
<a href="https://ngdc.cncb.ac.cn/gsa/browse/CRA030652/CRX1900001">CRX1900001</a>
<a href="https://ngdc.cncb.ac.cn/gsa/browse/insdc/SRA1770170/SRX22919560">SRX22919560</a>
"""


def test_search_parses_pairs_and_total():
    with patch("wmw.gsa._request", return_value=_mock_response(_SEARCH_HTML)):
        pairs, total = gsa.search("q")
    assert total == 591
    assert pairs == [
        ("CRA028180", "CRX1868736"),
        ("CRA028180", "CRX1868737"),
        ("CRA030652", "CRX1900001"),
    ]


def test_search_skips_insdc_mirrored_rows():
    with patch("wmw.gsa._request", return_value=_mock_response(_SEARCH_HTML)):
        pairs, _total = gsa.search("q")
    assert not any(acc.startswith("SRA") for acc, _ in pairs)


def test_search_posts_query_as_search_term():
    with patch("wmw.gsa._request", return_value=_mock_response(_SEARCH_HTML)) as req:
        gsa.search("my query", page_size=50, page=2)
    data = req.call_args[1]["data"]
    assert data["searchTerm"] == "my query"
    assert data["searchField"] == ""
    assert data["pageSize"] == 50
    assert data["from"] == 2


def test_search_caps_page_size():
    with patch("wmw.gsa._request", return_value=_mock_response(_SEARCH_HTML)) as req:
        gsa.search("q", page_size=99999)
    assert req.call_args[1]["data"]["pageSize"] == gsa.MAX_PAGE_SIZE


def test_search_study_accessions_dedupes_and_preserves_order():
    with patch("wmw.gsa.search", return_value=([("CRA2", "CRX1"), ("CRA1", "CRX2"), ("CRA2", "CRX3")], 3)):
        assert gsa.search_study_accessions("2025-09-01", "2025-09-30") == ["CRA2", "CRA1"]


def test_search_study_accessions_pages_until_total_reached():
    pages = [
        ([("CRA1", "CRX1")], 1500),
        ([("CRA2", "CRX2")], 1500),
    ]
    with patch("wmw.gsa.search", side_effect=pages) as search:
        result = gsa.search_study_accessions("2025-09-01", "2025-09-30")
    assert result == ["CRA1", "CRA2"]
    assert search.call_count == 2
    assert search.call_args_list[1][1]["page"] == 2


def test_search_study_accessions_stops_on_empty_result():
    with patch("wmw.gsa.search", return_value=([], 0)) as search:
        assert gsa.search_study_accessions("2025-09-01", "2025-09-30") == []
    assert search.call_count == 1


# ---------------------------------------------------------------------------
# Browse and BioProject page parsing
# ---------------------------------------------------------------------------

_BROWSE_HTML = """
<h3>CRA028180 Information</h3>
<span>Title:</span><span>virome and microbiome of non-traditional mammals</span>
<span>BioProject:</span><a href="/bioproject/browse/PRJCA042537">PRJCA042537</a>
<span>Release date:</span><span>2025-09-07</span>
<a href="https://download.cncb.ac.cn/gsa5/CRA028180">HTTPS</a>
"""


def test_fetch_browse_summary_parses_header():
    with patch("wmw.gsa._request", return_value=_mock_response(_BROWSE_HTML)):
        summary = gsa.fetch_browse_summary("CRA028180")
    assert summary["study_title"] == "virome and microbiome of non-traditional mammals"
    assert summary["bioproject_accession"] == "PRJCA042537"
    assert summary["first_public"] == "2025-09-07"
    assert summary["download_root"] == "gsa5"


def test_fetch_browse_summary_requests_english_labels():
    assert gsa._HEADERS["Accept-Language"].startswith("en")


_BIOPROJECT_HTML = """
<span>Accession</span><span>PRJCA042537</span>
<span>Title</span><span>virome and microbiome of non-traditional mammals</span>
<span>Organisms</span><span>organismal metagenomes</span>
<span>Description</span><span>Zoonotic diseases pose a threat to wild species.</span>
<span>Release date</span><span>2025-09-06</span>
<span>Organization</span><span>Fudan University</span>
"""


def test_fetch_bioproject_parses_description_and_organization():
    with patch("wmw.gsa._request", return_value=_mock_response(_BIOPROJECT_HTML)):
        project = gsa.fetch_bioproject("PRJCA042537")
    assert project["study_description"] == "Zoonotic diseases pose a threat to wild species."
    assert project["scientific_name"] == "organismal metagenomes"
    assert project["center_name"] == "Fudan University"
    assert project["first_public"] == "2025-09-06"


def test_fetch_study_metadata_merges_browse_and_bioproject():
    with patch("wmw.gsa._request", side_effect=[
        _mock_response(_BROWSE_HTML),
        _mock_response(_BIOPROJECT_HTML),
    ]):
        record = gsa.fetch_study_metadata("CRA028180")
    assert record["study_accession"] == "CRA028180"
    assert record["secondary_study_accession"] == "PRJCA042537"
    assert record["center_name"] == "Fudan University"
    # The GSA release date wins over the BioProject one.
    assert record["first_public"] == "2025-09-07"


def test_fetch_study_metadata_survives_bioproject_failure():
    import requests

    with patch("wmw.gsa._request", side_effect=[
        _mock_response(_BROWSE_HTML),
        requests.exceptions.ConnectionError("boom"),
    ]):
        record = gsa.fetch_study_metadata("CRA028180")
    assert record["study_title"]
    assert record["study_description"] == ""


def test_fetch_study_metadata_returns_none_for_unknown_accession():
    with patch("wmw.gsa._request", return_value=_mock_response("<html>not found</html>")):
        assert gsa.fetch_study_metadata("CRA999999") is None


def test_fetch_studies_batch_skips_unresolvable():
    with patch("wmw.gsa.fetch_study_metadata", side_effect=[{"study_accession": "CRA1"}, None]):
        assert gsa.fetch_studies_batch(["CRA1", "CRA2"]) == [{"study_accession": "CRA1"}]


# ---------------------------------------------------------------------------
# Metadata workbook parsing
# ---------------------------------------------------------------------------

def _build_workbook(sheets: dict[str, list[list[str]]]) -> bytes:
    """Build a minimal .xlsx with inline strings, matching GSA's layout."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        rels = ['<?xml version="1.0"?>',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
        book = ['<?xml version="1.0"?>',
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
                "<sheets>"]
        for i, (name, rows) in enumerate(sheets.items(), start=1):
            rel_id = f"rId{i}"
            book.append(f'<sheet name="{name}" sheetId="{i}" r:id="{rel_id}"/>')
            rels.append(
                f'<Relationship Id="{rel_id}" Target="worksheets/sheet{i}.xml" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            )
            xml = ['<?xml version="1.0"?>',
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
                   "<sheetData>"]
            for r, row in enumerate(rows, start=1):
                xml.append(f'<row r="{r}">')
                for c, value in enumerate(row):
                    ref = f"{chr(ord('A') + c)}{r}"
                    escaped = (
                        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    )
                    xml.append(f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>')
                xml.append("</row>")
            xml.append("</sheetData></worksheet>")
            archive.writestr(f"xl/worksheets/sheet{i}.xml", "".join(xml))
        book.append("</sheets></workbook>")
        rels.append("</Relationships>")
        archive.writestr("xl/workbook.xml", "".join(book))
        archive.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
    return buf.getvalue()


_SAMPLE_ROWS = [
    ["ID", "Sample name", "Accession", "Project accession", "Organism", "Host",
     "Collection date", "Geographic location"],
    ["1", "Animal_7_2_1", "SAMC5697416", "PRJCA042537", "organismal metagenomes",
     "Atelerix albiventris", "2024-05-02", "China: Fujian"],
    ["2", "Animal_7_2_2", "SAMC5697417", "PRJCA042537", "organismal metagenomes",
     "NA", "", ""],
]

_EXPERIMENT_ROWS = [
    ["ID", "Accession", "BioProject accession", "BioSample accession", "Platform",
     "Strategy", "Source", "Layout"],
    ["1", "CRX1868732", "PRJCA042537", "SAMC5697416", "Illumina NovaSeq 6000",
     "WGS", "METAGENOMIC", "PAIRED"],
    ["2", "CRX1868733", "PRJCA042537", "SAMC5697417", "DNBSEQ-T7",
     "RNA-Seq", "METATRANSCRIPTOMIC", "SINGLE"],
]

_RUN_ROWS = [
    ["ID", "Accession", "Run title", "BioProject accession", "Experiment accession",
     "Run data file type", "Read filename 1", "Read file1 MD5", "DownLoad Read file1",
     "Read filename 2", "Read file2 MD5", "DownLoad  Read file2"],
    ["1", "CRR2009389", "Animal_7_2_1", "PRJCA042537", "CRX1868732", "fastq",
     "CRR2009389_r1.fq.gz (3402548213 bytes)", "md5one",
     "ftp://download.big.ac.cn/gsa5/CRA028180/CRR2009389/CRR2009389_r1.fq.gz",
     "CRR2009389_r2.fq.gz (3573490885 bytes)", "md5two",
     "ftp://download.big.ac.cn/gsa5/CRA028180/CRR2009389/CRR2009389_r2.fq.gz"],
    ["2", "CRR2009390", "Animal_7_2_2", "PRJCA042537", "CRX1868733", "fastq",
     "CRR2009390.fq.gz (100 bytes)", "md5three",
     "ftp://download.big.ac.cn/gsa5/CRA028180/CRR2009390/CRR2009390.fq.gz",
     "", "", ""],
]


@pytest.fixture()
def workbook_bytes() -> bytes:
    return _build_workbook({
        "Sample": _SAMPLE_ROWS,
        "Experiment": _EXPERIMENT_ROWS,
        "Run": _RUN_ROWS,
    })


def test_parse_metadata_workbook_returns_named_sheets(workbook_bytes):
    sheets = gsa.parse_metadata_workbook(workbook_bytes)
    assert set(sheets) == {"Sample", "Experiment", "Run"}
    assert len(sheets["Run"]) == 2
    assert sheets["Sample"][0]["Accession"] == "SAMC5697416"


def test_parse_metadata_workbook_keys_rows_by_header(workbook_bytes):
    run = gsa.parse_metadata_workbook(workbook_bytes)["Run"][0]
    assert run["Accession"] == "CRR2009389"
    assert run["Read file1 MD5"] == "md5one"


def test_column_index_handles_multi_letter_refs():
    assert gsa._column_index("A1") == 0
    assert gsa._column_index("Z9") == 25
    assert gsa._column_index("AA1") == 26
    assert gsa._column_index("AB12") == 27


def test_download_metadata_workbook_posts_accession():
    with patch("wmw.gsa._request", return_value=_mock_response(content=b"xlsx")) as req:
        assert gsa.download_metadata_workbook("CRA028180") == b"xlsx"
    assert req.call_args[1]["data"] == {"type": "3", "dlAcession": "CRA028180"}


# ---------------------------------------------------------------------------
# Run record assembly
# ---------------------------------------------------------------------------

@pytest.fixture()
def study_runs(workbook_bytes) -> list[dict]:
    with patch("wmw.gsa.download_metadata_workbook", return_value=workbook_bytes), \
         patch("wmw.gsa.fetch_browse_summary", return_value={"first_public": "2025-09-07"}):
        return gsa.search_study("CRA028180")


def test_search_study_joins_run_experiment_and_sample(study_runs):
    run = study_runs[0]
    assert run["run_accession"] == "CRR2009389"
    assert run["experiment_accession"] == "CRX1868732"
    assert run["sample_accession"] == "SAMC5697416"
    assert run["library_strategy"] == "WGS"
    assert run["library_layout"] == "PAIRED"
    assert run["host"] == "Atelerix albiventris"
    assert run["collection_date"] == "2024-05-02"


def test_search_study_derives_platform_family(study_runs):
    assert study_runs[0]["instrument_platform"] == "ILLUMINA"
    assert study_runs[0]["instrument_model"] == "Illumina NovaSeq 6000"
    assert study_runs[1]["instrument_platform"] == "DNBSEQ"


def test_search_study_rewrites_download_urls_to_https(study_runs):
    run = study_runs[0]
    assert run["fastq_url_1"].startswith("https://download.cncb.ac.cn/gsa5/")
    assert run["fastq_url_2"].startswith("https://download.cncb.ac.cn/gsa5/")
    # The raw FTP paths are preserved for reference.
    assert run["fastq_ftp"].startswith("ftp://download.big.ac.cn/")
    assert run["fastq_ftp"].count(";") == 1


def test_search_study_handles_single_end_runs(study_runs):
    run = study_runs[1]
    assert run["fastq_url_1"].endswith("CRR2009390.fq.gz")
    assert run["fastq_url_2"] == ""
    assert run["fastq_md5"] == "md5three"


def test_search_study_splits_geographic_location(study_runs):
    assert study_runs[0]["geo_loc_name"] == "China: Fujian"
    assert study_runs[0]["country"] == "China"


def test_search_study_treats_na_host_as_blank(study_runs):
    assert study_runs[1]["host"] == ""


def test_search_study_applies_study_release_date(study_runs):
    assert all(r["first_public"] == "2025-09-07" for r in study_runs)


def test_search_study_records_file_sizes(study_runs):
    assert study_runs[0]["file_size_1"] == 3402548213
    assert study_runs[0]["file_size_2"] == 3573490885
    assert study_runs[1]["file_size_2"] is None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def test_to_https_rewrites_gsa_ftp_paths():
    assert gsa.to_https("ftp://download.big.ac.cn/gsa5/CRA1/CRR1/a.fq.gz") == (
        "https://download.cncb.ac.cn/gsa5/CRA1/CRR1/a.fq.gz"
    )


def test_to_https_leaves_other_urls_alone():
    assert gsa.to_https("https://example.org/a.fq.gz") == "https://example.org/a.fq.gz"
    assert gsa.to_https("") == ""


def test_split_filename_size_without_byte_count():
    assert gsa._split_filename_size("CRR1_r1.fq.gz") == ("CRR1_r1.fq.gz", None)


def test_split_filename_size_blank():
    assert gsa._split_filename_size("") == ("", None)


@pytest.mark.parametrize("model,expected", [
    ("Illumina NovaSeq 6000", "ILLUMINA"),
    ("DNBSEQ-T7", "DNBSEQ"),
    ("MGISEQ-2000", "DNBSEQ"),
    ("Oxford Nanopore PromethION", "OXFORD_NANOPORE"),
    ("PacBio Sequel II", "PACBIO_SMRT"),
    ("BGISEQ-500", "BGISEQ"),
    ("", ""),
])
def test_platform_family(model, expected):
    assert gsa._platform_family(model) == expected


def test_country_from_location_without_region():
    assert gsa._country_from_location("Denmark") == "Denmark"
    assert gsa._country_from_location("") == ""


def test_unique_studies():
    runs = [{"study_accession": "CRA2"}, {"study_accession": "CRA1"}, {"study_accession": "CRA2"}]
    assert gsa.unique_studies(runs) == ["CRA1", "CRA2"]


# ---------------------------------------------------------------------------
# Taxonomy resolution
# ---------------------------------------------------------------------------

def test_resolve_taxonomy_fills_ids_from_names():
    gsa._taxonomy_cache.clear()
    records = [{"scientific_name": "gut metagenome", "host": "Vulpes vulpes"}]
    with patch("wmw.ena.resolve_taxonomy_name", side_effect=[("749906", "gut metagenome"), ("9627", "Vulpes vulpes")]):
        assert gsa.resolve_taxonomy(records) == 1
    assert records[0]["tax_id"] == "749906"
    assert records[0]["host_tax_id"] == "9627"
    assert records[0]["host_scientific_name"] == "Vulpes vulpes"


def test_resolve_taxonomy_caches_repeated_names():
    gsa._taxonomy_cache.clear()
    records = [{"host": "Vulpes vulpes"}, {"host": "Vulpes vulpes"}]
    with patch("wmw.ena.resolve_taxonomy_name", return_value=("9627", "Vulpes vulpes")) as resolve:
        gsa.resolve_taxonomy(records)
    assert resolve.call_count == 1
    assert records[1]["host_tax_id"] == "9627"


def test_resolve_taxonomy_leaves_unresolvable_names_blank():
    gsa._taxonomy_cache.clear()
    records = [{"host": "not a taxon"}]
    with patch("wmw.ena.resolve_taxonomy_name", side_effect=ValueError("nope")):
        assert gsa.resolve_taxonomy(records) == 0
    assert records[0].get("host_tax_id", "") == ""


def test_resolve_taxonomy_skips_blank_host():
    gsa._taxonomy_cache.clear()
    records = [{"host": "", "scientific_name": ""}]
    with patch("wmw.ena.resolve_taxonomy_name") as resolve:
        assert gsa.resolve_taxonomy(records) == 0
    resolve.assert_not_called()


def test_search_study_accessions_keeps_page_size_constant():
    """GSA's `from` is a page index, so a shrinking page size would overlap offsets."""
    pages = [([("CRA1", "CRX1")], 2500), ([("CRA2", "CRX2")], 2500), ([("CRA3", "CRX3")], 2500)]
    with patch("wmw.gsa.search", side_effect=pages) as search:
        gsa.search_study_accessions("2025-09-01", "2025-09-30", limit=2500)
    sizes = [call[1]["page_size"] for call in search.call_args_list]
    assert len(set(sizes)) == 1
    assert [call[1]["page"] for call in search.call_args_list] == [1, 2, 3]


def test_search_study_accessions_stops_when_a_page_returns_nothing():
    pages = [([("CRA1", "CRX1")], 99999), ([], 99999)]
    with patch("wmw.gsa.search", side_effect=pages) as search:
        assert gsa.search_study_accessions("2025-09-01", "2025-09-30") == ["CRA1"]
    assert search.call_count == 2


def test_search_study_accessions_honours_limit():
    with patch("wmw.gsa.search", return_value=([("CRA1", "CRX1")], 10**6)) as search:
        gsa.search_study_accessions("2025-09-01", "2025-09-30", limit=500)
    assert search.call_count == 1
    assert search.call_args[1]["page_size"] == 500
