# wmw Architecture

## Purpose

wmw automates the discovery, curation, and processing of shotgun metagenomic datasets
from wild animals. It connects three external systems:

- **ENA** — source of raw dataset metadata and FASTQ file URLs
- **Airtable** — persistent store and curation layer (Studies + Samples tables)
- **Drakkar** — genome-resolved metagenomics pipeline (invoked as a subprocess)

## Data flow

```
ENA Portal API ──► wmw scan  ──► normalize ──► upsert ──► Airtable Studies
                                             PubMed/CrossRef ─┘
                                                  │
                                   (user reviews; sets status = "approved")
                                                  │
ENA Portal API ──► wmw fetch ──► normalize ──► filter ──► upsert ──► Airtable Samples
                                                               │
                                                  study status → "indexed"

Airtable ──► wmw process ──► build manifest ──► drakkar <workflow> ──► update status
```

## Module responsibilities

### `cli.py`
Single argparse module; all command logic lives here as `cmd_*()` functions.

- `cmd_scan` / `_scan_single_study` — ENA study-level discovery; writes to Studies only.
- `cmd_fetch` / `_resolve_fetch_params` — run-level fetch for approved studies; writes to Samples.
- `cmd_process`, `cmd_stop`, `cmd_status`, `cmd_config`, `cmd_update` — downstream pipeline commands.

### `config.py`
Loads `src/wmw/data/config.yaml` on demand (no caching). Provides `get()`, `require()`,
`view_config()`, `edit_config()`. The file path is the installed package's own copy —
editing it with `wmw config --edit` modifies the installed file, not a user home dir.

### `output.py`
Wraps Rich `Console` with a wmw green/teal theme. Exports `info()`, `warn()`, `error()`,
`success()`, `section()`, `make_table()`, `render_table()`. Degrades gracefully when Rich
is not installed. Colour disabled by `WMW_NO_COLOR=1`.

### `airtable.py`
`AirtableClient` wraps pyairtable `Api`. Uses field names (not field IDs). Deduplication
is accession-based: `upsert_studies()` fetches all existing `study_accession` values before
inserting; `upsert_samples()` does the same for `run_accession`. Re-running over overlapping
date ranges is safe. `fetch_studies_by_status()` / `set_study_status()` drive the approval
workflow between `wmw scan` and `wmw fetch`.

### `ena.py`
Queries the ENA Portal REST API.

- `search_studies()` — `result=study` endpoint; used by `wmw scan`. Returns study-level
  metadata (title, description, pubmed_id, tax_id). Supports date range, keyword, and
  approximate organism filter (`tax_id`). Date field must be `first_public` or `last_updated`.
- `search_study(accession)` — `result=read_run` for a single accession; used by `wmw fetch`.
- `fetch_study_metadata(accession)` — single-study metadata lookup; used by `wmw scan --study`.
- `search_runs()` — bulk run search (retained for direct use / future reference).

### `sra.py`
Queries NCBI SRA via Biopython Entrez (`esearch` → `efetch XML`). Retained for direct use
but no longer invoked by `wmw scan` or `wmw fetch` — all automated discovery goes through ENA.

### `metadata.py`
Provides two normalization paths (ENA / SRA) for both runs and studies, converging on a
shared schema. `filter_runs()` is the post-fetch safety net and the primary filter layer
for `wmw fetch`: it checks `host_tax_id`, `base_count`, `library_strategy`, `library_source`,
and `instrument_platform` on already-normalized records. Fields with empty values are never
excluded by a filter (unknown ≠ excluded).

### `drakkar.py`
`build_manifest()` writes a three-column TSV (`sample`, `R1`, `R2`) from Airtable sample
records. `fastq_url_1`/`fastq_url_2` are used first; falls back to parsing `fastq_ftp`.
`run_workflow()` invokes `drakkar <workflow>` as a subprocess, optionally wrapped in
`conda run -n <env>` when `DRAKKAR_CONDA_ENV` is set. Exit code is returned and used to
update Airtable status.

### `publications.py`
`resolve_batch()` iterates study records and calls `resolve()` per study. `resolve()`
tries PubMed first (when `pubmed_id` is present — ENA usually provides it), then CrossRef
(using the DOI obtained from PubMed or any pre-existing `pub_doi`). Adds a 0.35 s delay
between requests. Returns empty dict on any failure (publication metadata is optional).

## Design decisions

| Decision | Rationale |
|---|---|
| Two-phase scan/fetch | Users review study metadata in Airtable before committing to run-level data fetches, which can be expensive for large studies. |
| ENA-only for scan | ENA provides a `result=study` endpoint with study-level metadata (including `study_description`). SRA has no equivalent date-filtered study search. |
| Study-level vs run-level filters | Broad filters (date, keyword, organism) apply at study level in `wmw scan`; precise filters (library_strategy, min_bases, platform) apply at run level in `wmw fetch`. |
| No Snakemake host | `wmw process` delegates to `drakkar` as a subprocess, same as ehio. |
| src layout | Cleaner packaging isolation; matches ehio. |
| No Click/Typer | Matches ehio and drakkar; no extra dependency. |
| Dedup by accession | Re-running scan or fetch over overlapping accessions is safe. |
| Config in package dir | Consistent with ehio; single location, editable in-place. |
