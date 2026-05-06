# wmw — Wild Microbiome Watch

Discovers wild-animal shotgun metagenome studies in ENA, populates Airtable, and drives Drakkar genome-resolved metagenomic workflows.

## Two-phase workflow
1. **`wmw scan`** — queries ENA study endpoint → populates Airtable Studies table (status `new`)
2. User reviews in Airtable and sets status to `approved` for studies to keep
3. **`wmw fetch`** — reads approved studies → fetches runs from ENA → populates Samples table (study status → `indexed`)
4. **`wmw process`** — runs Drakkar on pending samples

## Module map
| File | Responsibility |
|---|---|
| `cli.py` | argparse; `cmd_scan`, `_scan_single_study`, `cmd_fetch`, `_resolve_fetch_params`, `cmd_process`, `cmd_status`, `cmd_config`, `cmd_update` |
| `config.py` | YAML at `src/wmw/data/config.yaml`; `get()`, `require()`, `view_config()`, `edit_config()` |
| `output.py` | Rich console; `info()` `warn()` `error()` `success()` `section()` `make_table()` `render_table()` |
| `airtable.py` | `AirtableClient` — `upsert_studies()`, `upsert_samples()`, `set_sample_status()`, `fetch_studies_by_status()`, `set_study_status()`, dedup by accession |
| `ena.py` | ENA Portal REST; `search_studies()` (study endpoint, used by scan), `search_study()` (run endpoint, used by fetch), `fetch_study_metadata()`, `search_runs()` |
| `sra.py` | NCBI SRA via Biopython Entrez; `search_runs()`, `search_study()` — retained but not used in automated scan/fetch |
| `metadata.py` | `normalize_ena/sra_run/study()`, `filter_runs()` (host_tax_id, min_bases, library_strategy, library_source, instrument_platform), `deduplicate_runs()`, `studies_from_runs()` |
| `drakkar.py` | `build_manifest()` → TSV; `run_workflow()` → subprocess; `parse_preprocessing_stats()` |
| `publications.py` | `fetch_from_pubmed()`, `fetch_from_crossref()`, `fetch_pdf_url()` (Unpaywall), `resolve_batch()` |

## Airtable schema
**Studies** — `study_accession`, `secondary_study_accession`, `study_title`, `study_description`, `source` (ENA), `scientific_name`, `tax_id`, `first_public`, `center_name`, `status` (`new`→`approved`→`indexed`), `pubmed_id`, `pub_doi`, `pub_url`, `pub_title`, `pub_year`, `pub_journal`, `pub_authors`, `pub_pdf` (Attachment; OA PDF via Unpaywall)
**Samples** — `run_accession`, `study_accession`, `sample_accession`, `experiment_accession`, `scientific_name`, `tax_id`, `instrument_platform`, `instrument_model`, `library_strategy`, `library_source`, `library_layout`, `base_count`, `read_count`, `fastq_ftp`, `fastq_md5`, `fastq_url_1`, `fastq_url_2`, `collection_date`, `first_public`, `geo_loc_name`, `host`, `host_tax_id`, `host_scientific_name`, `country`, `center_name`, `source`, `status`

## Config keys (`src/wmw/data/config.yaml`)
`WMW_BASE` `STUDIES_TABLE` `SAMPLES_TABLE` `DRAKKAR_CONDA_ENV` `DRAKKAR_OUTPUT_DIR` `NCBI_EMAIL` `NCBI_API_KEY` `LIBRARY_SOURCE` `INSTRUMENT_PLATFORM` `MIN_BASES` `DATE_FIELD` `EXCLUDED_HOST_TAX_IDS`

## CLI commands
```
wmw scan   [--from DATE] [--to DATE] [--study ACC]
           [--host-tax-id ID] [--date-field first_public|last_updated]
           [--keyword TEXT] [--include GROUPS] [--run-batch N]
           [--dry-run] [--no-publications]
wmw fetch  [--status VALUE] [--study ACC]
           [--library-strategy STR] [--library-source STR]
           [--instrument-platform STR] [--min-bases N]
           [--include GROUPS] [--exclude-taxa IDs] [--dry-run]
wmw process --batch BATCH [--workflow preprocessing|cataloging|...] [--slurm] [--output-dir DIR]
wmw status  [--batch BATCH]
wmw config  --view | --edit
wmw update
```

## Key patterns
- `_conf(args, "cli_attr", "CONFIG_KEY")` — CLI flag → config → `''`
- `_die(msg)` — error to stderr + `sys.exit(1)`
- Scan filters: study-level (date, organism tax_id, keyword, host taxon exclusions) — applied at query/normalize time
- `--run-batch N` (default 20): when a taxonomy/keyword filter is active, studies are split into batches of N and runs are queried per-batch to avoid the ENA 10 000-record API limit
- Fetch filters: run-level (library_strategy, library_source, instrument_platform, min_bases, host_tax_id exclusions) — applied post-fetch by `metadata.filter_runs()`
- `_build_exclude_ids(args)` — reads `EXCLUDED_HOST_TAX_IDS` dict groups minus any named in `--include`; `--include All` disables all exclusions
- Fields with blank values are never excluded by any filter

## Tests & release
`pytest tests/` (74 tests) · `python scripts/release.py X.Y.Z` (add `--dry-run` first)

## Changelog policy
- Every code change must be logged under the `[Unreleased]` section of `CHANGELOG.md` before the work is considered done.
- Do NOT run `scripts/release.py` or bump the version unless the user explicitly requests a release.

## Docs
`docs/architecture.md` · `docs/schema.md` · `docs/cli.md` · `docs/filters.md` · `docs/release.md`
