# Changelog

All notable changes to wmw are documented here.

## [Unreleased]

### Added

- No unreleased changes yet.

## [0.3.0] - 2026-05-06

### Added

- `wmw scan` now fetches the open-access PDF of each resolved publication via the
  [Unpaywall API](https://unpaywall.org/products/api) and stores it in the `pub_pdf`
  Attachment field of the Studies table. Only populated when an OA PDF URL is available;
  skipped along with other publication metadata when `--no-publications` is passed.
- `wmw scan --run-batch N` (default 20): when a keyword filter is active, the matched study
  accessions are split into batches of N and runs are queried per-batch via the ENA run
  endpoint. This avoids ENA's 10 000-record API limit that previously caused qualifying runs
  to be silently truncated when many studies matched the date window.
- `wmw scan --debug`: prints the full ENA API URL (with encoded query string) before every
  request, making it straightforward to paste queries into a browser and inspect raw results.
- `ena.get_lineage(tax_id)` — fetches the semicolon-separated ancestor-name lineage for a
  taxon via the ENA Taxonomy REST API (`/tax-id/{id}`), with in-process caching so each
  unique taxon is only looked up once per run.
- Per-batch progress line in scan output: after each batch completes, reports the number of
  runs found in that batch and the cumulative run and study totals.

### Changed

- `wmw scan --taxonomy` no longer filters studies at the ENA study endpoint using
  `tax_tree()`. Many multi-organism metagenomic studies (e.g. EHI) have no `tax_id` set in
  ENA's study index and were silently excluded. Taxonomy is now applied post-fetch at the
  run level: each unique `host_tax_id` in the fetched runs is checked against the ENA
  taxonomy lineage, and runs whose host falls outside the target taxon's subtree are
  removed. Runs with no `host_tax_id` are kept.
- `ena.search_runs()` gains an optional `study_accessions` parameter; when provided, a
  `study_accession` IN-clause is added to the query so results are restricted to those
  specific studies. Also makes `date_from`/`date_to` optional (date clause is omitted when
  both are empty), which is used by batch mode to avoid double-filtering runs that were
  already date-scoped at the study level.
- `ena.search_studies()` no longer accepts or applies the `taxonomy_tax_id` parameter in
  its query; taxonomy filtering has moved entirely to the post-fetch run-level step.
## [0.2.0] - 2026-05-05

### Added

- `wmw fetch` command (phase 2 of the study-to-sample pipeline): reads studies whose
  `status = "approved"` (or `--status VALUE`) from the Airtable Studies table, fetches all
  run records from ENA for each via `ena.search_study()`, applies run-level filters, upserts
  into the Samples table, and updates study status to `"indexed"`. Supports
  `--library-strategy`, `--library-source`, `--instrument-platform`, `--min-bases`,
  `--exclude-taxa`, `--no-exclude`, `--dry-run`, and `--study ACCESSION` for single-study
  direct fetch.
- `ena.search_studies()` — queries the ENA Portal `result=study` endpoint for a date range,
  returning study-level metadata including `study_description` and `pubmed_id`. Supports
  `first_public` and `last_updated` date fields, optional `--host-tax-id` (matched against
  study `tax_id`), and free-text `--keyword`.
- `AirtableClient.fetch_studies_by_status()` and `AirtableClient.set_study_status()` to
  support the approval workflow between `wmw scan` and `wmw fetch`.
- `metadata.filter_runs()` now accepts `library_strategies`, `library_sources`, and
  `instrument_platform` parameters for post-fetch run-level filtering in `wmw fetch`.

### Changed

- `wmw scan` is now study-level only (phase 1): it queries the ENA `result=study` endpoint
  instead of the run endpoint, writes only to the Studies table, and no longer populates
  Samples. Run-level sample data is deferred to `wmw fetch` after manual review.
- `wmw scan` is now ENA-only; SRA support has been removed from the scan phase. SRA modules
  are retained for direct use but are not invoked by automated discovery.
- `wmw scan --date-field` now accepts only `first_public` and `last_updated`; `collection_date`
  is a run-level field not available in the ENA study index.
- `wmw scan` no longer accepts `--db`, `--library-strategy`, `--library-source`,
  `--instrument-platform`, `--min-bases`, `--exclude-taxa`, `--no-exclude`, or
  `--samples-table`; these flags have moved to `wmw fetch`.
- `wmw scan --study ACCESSION` now calls `ena.fetch_study_metadata()` (study endpoint) rather
  than fetching runs and deriving study metadata from them.
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
