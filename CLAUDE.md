# wmw — Wild Microbiome Watch

Scans ENA/SRA for wild-animal shotgun metagenomes, populates Airtable, and drives Drakkar genome-resolved metagenomic workflows.

## Module map
| File | Responsibility |
|---|---|
| `cli.py` | argparse; `_resolve_scan_params()`, `cmd_scan`, `cmd_process`, `cmd_status`, `cmd_config`, `cmd_update` |
| `config.py` | YAML at `src/wmw/data/config.yaml`; `get()`, `require()`, `view_config()`, `edit_config()` |
| `output.py` | Rich console; `info()` `warn()` `error()` `success()` `section()` `make_table()` `render_table()` |
| `airtable.py` | `AirtableClient` — `upsert_studies()`, `upsert_samples()`, `set_sample_status()`, dedup by accession |
| `ena.py` | ENA Portal REST; `search_runs()` (all filters at query time), `search_study()` |
| `sra.py` | NCBI SRA via Biopython Entrez; `search_runs()` (min_bases post-fetch), `search_study()` |
| `metadata.py` | `normalize_ena/sra_run/study()`, `filter_runs()`, `deduplicate_runs()`, `studies_from_runs()` |
| `drakkar.py` | `build_manifest()` → TSV; `run_workflow()` → subprocess; `parse_preprocessing_stats()` |
| `publications.py` | `fetch_from_pubmed()`, `fetch_from_crossref()`, `resolve_batch()` |

## Airtable schema
**Studies** — `study_accession`, `secondary_study_accession`, `study_title`, `study_description`, `source` (ENA|SRA), `scientific_name`, `tax_id`, `first_public`, `center_name`, `status`, `pubmed_id`, `pub_doi`, `pub_url`, `pub_title`, `pub_year`, `pub_journal`, `pub_authors`
**Samples** — `run_accession`, `study_accession`, `sample_accession`, `experiment_accession`, `scientific_name`, `tax_id`, `instrument_platform`, `instrument_model`, `library_strategy`, `library_source`, `library_layout`, `base_count`, `read_count`, `fastq_ftp`, `fastq_md5`, `fastq_url_1`, `fastq_url_2`, `collection_date`, `first_public`, `geo_loc_name`, `host`, `host_tax_id`, `host_scientific_name`, `country`, `center_name`, `source`, `status`

## Config keys (`src/wmw/data/config.yaml`)
`AIRTABLE_TOKEN` `WMW_BASE` `STUDIES_TABLE` `SAMPLES_TABLE` `DRAKKAR_CONDA_ENV` `DRAKKAR_OUTPUT_DIR` `NCBI_EMAIL` `NCBI_API_KEY` `LIBRARY_SOURCE` `INSTRUMENT_PLATFORM` `MIN_BASES` `DATE_FIELD` `EXCLUDED_HOST_TAX_IDS`

## CLI commands
```
wmw scan   [--from DATE] [--to DATE] [--study ACC] [--db ena|sra|both]
           [--host-tax-id ID] [--library-strategy STR] [--library-source STR]
           [--instrument-platform STR] [--date-field first_public|collection_date|last_updated]
           [--min-bases N] [--keyword TEXT] [--exclude-taxa IDs] [--no-exclude]
           [--dry-run] [--no-publications]
wmw process --batch BATCH [--workflow preprocessing|cataloging|...] [--slurm] [--output-dir DIR]
wmw status  [--batch BATCH]
wmw config  --view | --edit
wmw update
```

## Key patterns
- `_conf(args, "cli_attr", "CONFIG_KEY")` — CLI flag → config → `''`
- `_die(msg)` — error to stderr + `sys.exit(1)`
- Exclusions: ENA query-time `NOT host_tax_id=X`; SRA `NOT txidX[Organism:exp]`; both followed by `metadata.filter_runs()` as post-fetch safety net
- `host_tax_id` is always empty in SRA records (not in NCBI XML); SRA exclusion is organism-level only

## Tests & release
`pytest tests/` (55 tests) · `python scripts/release.py X.Y.Z` (add `--dry-run` first)

## Docs
`docs/architecture.md` · `docs/schema.md` · `docs/cli.md` · `docs/filters.md` · `docs/release.md`
