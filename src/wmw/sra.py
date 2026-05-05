"""NCBI SRA queries for wmw via Biopython Entrez."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

try:
    from Bio import Entrez
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

# SRA Entrez platform name map (ENA names → SRA Entrez names)
_PLATFORM_MAP = {
    "ILLUMINA": "illumina",
    "OXFORD_NANOPORE": "oxford nanopore",
    "PACBIO_SMRT": "pacbio smrt",
    "ION_TORRENT": "ion torrent",
    "LS454": "ls454",
    "ABI_SOLID": "abi solid",
    "BGISEQ": "bgiseq",
    "CAPILLARY": "capillary",
}


def _require() -> None:
    if not _AVAILABLE:
        import sys
        print("Error: biopython is required. Run: pip install biopython", file=sys.stderr)
        sys.exit(1)


def configure(email: str, api_key: str | None = None) -> None:
    _require()
    Entrez.email = email
    if api_key:
        Entrez.api_key = api_key


def _esearch(db: str, term: str, retmax: int = 10000) -> list[str]:
    handle = Entrez.esearch(db=db, term=term, retmax=retmax, usehistory="y")
    record = Entrez.read(handle)
    handle.close()
    return record.get("IdList", [])


def _efetch_xml(db: str, ids: list[str], batch_size: int = 200) -> list[ET.Element]:
    elements: list[ET.Element] = []
    for i in range(0, len(ids), batch_size):
        chunk = ids[i : i + batch_size]
        handle = Entrez.efetch(db=db, id=",".join(chunk), rettype="xml", retmode="xml")
        root = ET.fromstring(handle.read())
        handle.close()
        elements.extend(root)
        if i + batch_size < len(ids):
            time.sleep(0.4)
    return elements


def _attr(elem: ET.Element, path: str, attr: str) -> str:
    node = elem.find(path)
    return node.get(attr, "") if node is not None else ""


def _parse_run_element(elem: ET.Element) -> dict[str, Any] | None:
    """Parse a single <EXPERIMENT_PACKAGE> XML element into a flat dict."""

    def text(path: str) -> str:
        node = elem.find(path)
        return node.text.strip() if node is not None and node.text else ""

    run_elem = elem.find(".//RUN")
    if run_elem is None:
        return None

    run_accession = run_elem.get("accession", "")
    study_elem = elem.find(".//STUDY")
    study_accession = study_elem.get("accession", "") if study_elem is not None else ""

    # FASTQ URLs — best-effort from ENA cross-links embedded in SRA XML
    fastq_ftp = ""
    for link in elem.findall(".//XREF_LINK"):
        db_node = link.find("DB")
        id_node = link.find("ID")
        if db_node is not None and db_node.text == "ENA-FASTQ-FILES":
            fastq_ftp = id_node.text.strip() if id_node is not None and id_node.text else ""
            break

    lib_elem = elem.find(".//LIBRARY_DESCRIPTOR")
    base_count = run_elem.get("total_bases", "")
    return {
        "source": "SRA",
        "run_accession": run_accession,
        "study_accession": study_accession,
        "sample_accession": _attr(elem, ".//SAMPLE", "accession"),
        "experiment_accession": _attr(elem, ".//EXPERIMENT", "accession"),
        "scientific_name": text(".//SCIENTIFIC_NAME"),
        "tax_id": text(".//TAXON_ID"),
        "instrument_platform": text(".//PLATFORM//INSTRUMENT_MODEL"),
        "instrument_model": text(".//PLATFORM//INSTRUMENT_MODEL"),
        "library_strategy": lib_elem.findtext("LIBRARY_STRATEGY", "") if lib_elem is not None else "",
        "library_source": lib_elem.findtext("LIBRARY_SOURCE", "") if lib_elem is not None else "",
        "library_layout": (
            "PAIRED"
            if lib_elem is not None and lib_elem.find("LIBRARY_LAYOUT/PAIRED") is not None
            else "SINGLE"
        ),
        "base_count": base_count,
        "read_count": run_elem.get("total_spots", ""),
        "fastq_ftp": fastq_ftp,
        "fastq_md5": "",
        "collection_date": "",
        "first_public": run_elem.get("published", ""),
        "study_title": text(".//STUDY_TITLE"),
        "center_name": _attr(elem, ".//STUDY", "center_name"),
        # host_tax_id is not in standard SRA XML; populated as empty string
        "host_tax_id": "",
    }


def search_runs(
    date_from: str,
    date_to: str,
    *,
    host_tax_id: str = "",
    library_strategy: str = "WGS,METAGENOMIC",
    library_source: str = "",
    instrument_platform: str = "",
    min_bases: int | None = None,
    keyword: str = "",
    exclude_tax_ids: list[str] | None = None,
    retmax: int = 10000,
) -> list[dict[str, Any]]:
    """Search NCBI SRA for metagenomic run records within a date window.

    Parameters
    ----------
    date_from / date_to:
        ISO date strings (YYYY-MM-DD), applied to publication date (PDAT).
    host_tax_id:
        NCBI taxon ID subtree to *include* (searches organism taxonomy, not host field).
    library_strategy:
        Comma-separated strategies (e.g. "WGS,METAGENOMIC").
    library_source:
        Comma-separated sources (e.g. "METAGENOMIC"). Empty = no filter.
    instrument_platform:
        Platform string (e.g. "ILLUMINA"). Empty = no filter.
    min_bases:
        Minimum base count — applied as a post-fetch filter (Entrez has no base-count index).
    keyword:
        Free-text search in study title/description.
    exclude_tax_ids:
        Organism-level taxon IDs to exclude from Entrez term. Note: SRA does not expose
        host_tax_id as a searchable field; this excludes by organism taxon subtree.
    retmax:
        Maximum records to retrieve from Entrez.
    """
    _require()

    term_parts: list[str] = []

    # Library strategy
    strategies = [s.strip() for s in library_strategy.split(",") if s.strip()]
    term_parts.append("(" + " OR ".join(f'"{s}"[Strategy]' for s in strategies) + ")")

    # Library source
    if library_source:
        sources = [s.strip() for s in library_source.split(",") if s.strip()]
        if sources:
            term_parts.append("(" + " OR ".join(f'"{s}"[Source]' for s in sources) + ")")

    # Date range
    term_parts.append(f'("{date_from}"[PDAT] : "{date_to}"[PDAT])')

    # Organism taxon inclusion (approximates host filtering for SRA)
    if host_tax_id:
        term_parts.append(f"txid{host_tax_id}[Organism:exp]")

    # Instrument platform
    if instrument_platform:
        platform_key = instrument_platform.strip().upper()
        entrez_platform = _PLATFORM_MAP.get(platform_key, instrument_platform.lower())
        term_parts.append(f'"{entrez_platform}"[Platform]')

    # Free-text keyword
    if keyword:
        safe = keyword.replace('"', "")
        term_parts.append(f'"{safe}"[Title]')

    # Taxon exclusions (organism-level; SRA has no host_tax_id index)
    for tid in (exclude_tax_ids or []):
        tid = str(tid).strip()
        if tid:
            term_parts.append(f"NOT txid{tid}[Organism:exp]")

    term = " AND ".join(term_parts)
    ids = _esearch("sra", term, retmax=retmax)
    if not ids:
        return []

    elements = _efetch_xml("sra", ids)
    results: list[dict[str, Any]] = []
    for elem in elements:
        parsed = _parse_run_element(elem)
        if parsed is None:
            continue
        # Post-fetch base_count filter (Entrez has no base-count search index)
        if min_bases is not None:
            try:
                if int(parsed.get("base_count") or 0) < min_bases:
                    continue
            except (ValueError, TypeError):
                pass
        results.append(parsed)
    return results


def search_study(study_accession: str) -> list[dict[str, Any]]:
    """Return all run records for a single SRA/BioProject accession."""
    _require()
    term = f'"{study_accession}"[BioProject] OR "{study_accession}"[All Fields]'
    ids = _esearch("sra", term, retmax=50000)
    if not ids:
        return []
    elements = _efetch_xml("sra", ids)
    results: list[dict[str, Any]] = []
    for elem in elements:
        parsed = _parse_run_element(elem)
        if parsed:
            results.append(parsed)
    return results
