# wmw Architecture

## Purpose

wmw automates the discovery, curation, and processing of shotgun metagenomic datasets
from wild animals. It connects three external systems:

- **ENA / NCBI SRA** — source of raw dataset metadata and FASTQ file URLs
- **Airtable** — persistent store and curation layer (Studies + Samples tables)
- **Drakkar** — genome-resolved metagenomics pipeline (invoked as a subprocess)

## Data flow

```
ENA Portal API ──┐
                 ├─► wmw scan ─► normalize ─► filter ─► upsert ─► Airtable
NCBI SRA ────────┘                                   PubMed/CrossRef ─┘

Airtable ──► wmw process ──► build manifest ──► drakkar <workflow> ──► update status
```

## Module responsibilities

### `cli.py` (711 lines)
Single argparse module; all command logic lives here as `cmd_*()` functions.
`_resolve_scan_params()` consolidates CLI flags and config defaults into one dict passed
to both `ena.search_runs()` and `sra.search_runs()`.

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
inserting; `upsert_samples()` does the same for `run_accession`. This means re-running
`wmw scan` over an overlapping date range is safe.

### `ena.py`
Queries the ENA Portal REST API (`/search?result=read_run`). All filters (strategy, source,
platform, date field, min bases, keyword, exclusions) are encoded into a single `query`
string sent to ENA, so exclusions are applied server-side. `search_study()` fetches all
runs for a single accession and passes no additional filters.

### `sra.py`
Queries NCBI SRA via Biopython Entrez (`esearch` → `efetch XML`). Most filters map to
Entrez search fields (`[Strategy]`, `[Source]`, `[Platform]`, `[Title]`). Exception:
**base_count** — Entrez has no base-count index, so it is filtered post-fetch inside
`search_runs()`. Host taxon exclusion maps to `NOT txidX[Organism:exp]`, which filters
on the metagenome organism, not the host (NCBI limitation; host is in BioSample, not
the SRA index). Fetches in batches of 200 with 0.4 s delay between batches.

### `metadata.py`
Provides two normalization paths (ENA / SRA) for both runs and studies, converging on a
shared schema. `filter_runs()` is the post-fetch safety net: it checks `host_tax_id` on
already-normalized records (catches ENA records where the field was blank in the index,
and any SRA records that happen to carry it). Runs with no `base_count` are kept.

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
| No Snakemake host | `wmw process` delegates to `drakkar` as a subprocess, same as ehio |
| src layout | Cleaner packaging isolation; matches ehio |
| No Click/Typer | Matches ehio and drakkar; no extra dependency |
| Two-layer exclusion | ENA at query time (efficient); post-fetch fallback for SRA and blank ENA fields |
| Dedup by accession | Re-running scan over overlapping periods is safe |
| Config in package dir | Consistent with ehio; single location, editable in-place |
