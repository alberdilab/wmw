# Changelog

All notable changes to wmw are documented here.

## [Unreleased]

### Added

- No unreleased changes yet.

## [0.1.0] - 2026-05-05

### Added

- `wmw scan` command to query ENA Portal API and NCBI SRA for shotgun metagenomic
  datasets from wild animals within a date window or by single study accession.
- `wmw process` command to pull pending samples from Airtable, build a Drakkar-compatible
  manifest, invoke the chosen workflow stage, and update sample statuses on completion.
- `wmw status` command to display a breakdown of Airtable sample statuses per batch.
- `wmw config --view / --edit` for managing the YAML configuration file.
- `wmw update` for upgrading the package via pip.
- ENA Portal API integration (`ena.py`) with support for filtering by library strategy,
  library source, instrument platform, date field, minimum base count, free-text keyword,
  host taxon inclusion, and query-time host taxon exclusion.
- NCBI SRA integration (`sra.py`) via Biopython Entrez with equivalent filter support;
  minimum base count filter applied post-fetch (Entrez has no base-count index).
- Airtable client (`airtable.py`) with deduplication-aware upsert for Studies and Samples
  tables, status management, and batch filtering.
- Metadata normalisation (`metadata.py`) mapping ENA and SRA records to a shared schema
  covering study and sample fields; post-fetch `filter_runs()` for taxon exclusion and
  minimum base count as a secondary safety net for SRA records.
- Publication metadata resolution (`publications.py`) via PubMed (Biopython Entrez) and
  CrossRef REST API, resolving DOI, URL, title, year, journal, and authors for each study.
- Default host taxon exclusion list in `config.yaml` covering human, livestock, poultry,
  aquaculture, and laboratory animals (28 taxa); fully configurable per run via
  `--exclude-taxa` and `--no-exclude`.
- Rich-styled terminal output (`output.py`) with wmw colour theme.
- `scripts/release.py` for version bumping, changelog cutting, build validation, and
  git tag and push.
