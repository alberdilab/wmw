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
  (or GSA, --source gsa)
                                             PubMed/CrossRef ─┘
                                                  │
                                   (user reviews; sets status = "approved")
                                                  │
ENA Portal API ──► wmw fetch ──► normalize ──► filter ──► upsert ──► Airtable Samples
  (or GSA, --source gsa)
                                                               │
                                                  study status → "indexed"

Airtable ──► wmw process ──► build manifest ──► drakkar <workflow> ──► update status
                                                          │
                            cataloging outputs ──► Airtable (stats, Genomes, attachments)
                                                          └──► ERDA (assemblies + final bins)

                            amr outputs ──► Airtable (per-assembly counts, table attachments)
                                                          └──► ERDA (AMR result tables)
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
but no longer invoked by `wmw scan` or `wmw fetch` — automated discovery goes through ENA
or GSA.

### `gsa.py`
Queries the Genome Sequence Archive (NGDC/CNCB), selected with `--source gsa`. GSA has no
JSON API, so this module drives three session-free endpoints behind the public web
interface. All requests send `Accept-Language: en-US`, which switches the pages to English
labels that the parsers key off.

- `build_query()` — composes GSA's PubMed-style grammar, `"term"[field] AND "term"[field]`.
  Fields include `organism`, `ReleaseDate` (as `MM/DD/YYYY - MM/DD/YYYY`), `strategy`,
  `source`, `platform`, `title`, `center` and `projectAcc`. Comma- or pipe-separated values
  become parenthesised OR groups. Every query is pinned to `"NGDC"[center]`, which excludes
  the INSDC mirror GSA also serves so ENA-sourced studies are not duplicated.
- `search()` / `search_study_accessions()` — `POST /gsa/search/`, returning HTML. GSA indexes
  experiments, not studies, so the parent study of each hit is read from its
  `/gsa/browse/<CRA>/<CRX>` link and de-duplicated; mirrored rows link to
  `/gsa/browse/insdc/…` and are skipped. Pages hold up to 1 000 rows.
- `fetch_study_metadata(accession)` — merges `GET /gsa/browse/<CRA>` (title, BioProject
  accession, release date, and the download root `gsa`…`gsa5`, which is *not* derivable
  from the accession) with `/bioproject/browse/<PRJCA>` (description, organism, submitting
  organization). Used by `wmw scan`.
- `bioproject_studies(accession)` / `resolve_study_accession(accession)` — a GSA study names
  its BioProject, but not the reverse, so a `PRJCA…` accession is resolved through the
  resource table on `/bioproject/browse/<PRJCA>`, which links each `CRA` study it holds.
  Everything GSA publishes — study metadata and the metadata workbook alike — is keyed by
  `CRA` accession, so this runs first whenever a BioProject accession is passed to
  `--study`. A BioProject can hold more than one study, and `wmw scan --study` takes them
  all.
- `is_gsa_accession(accession)` — recognises both GSA forms (`CRA…`, `PRJCA…`), which is how
  `cli._source_for_study()` routes a `--study` accession to the archive that issued it.
- `search_study(accession)` — run records for one study, from
  `POST /gsa/file/exportExcelFile`. The workbook's Run, Experiment and Sample sheets are
  joined on experiment and BioSample accession; it is the only source for file names,
  sizes, MD5 checksums, download URLs, host, collection date and geographic location.
  Parsed with `zipfile` + `ElementTree`, so no spreadsheet dependency. Used by `wmw fetch`.
- `resolve_taxonomy(records)` — GSA supplies taxon *names* but no taxon IDs, and the
  host-exclusion filter keys off `host_tax_id`; this fills both ID fields through
  `ena.resolve_taxonomy_name()`, cached in-process.
- `keyword_matches(record, keyword)` — study-level keyword test. GSA's searchable `title`
  field holds experiment titles (usually per-sample aliases), so `wmw scan` applies the
  keyword here, against the study title and description, rather than in the query.
- `to_https(url)` — rewrites GSA's FTP paths to `https://download.cncb.ac.cn/`, which
  supports range requests and therefore resumable downloads.

GSA publishes no base or read counts, so `base_count` / `read_count` stay blank and
`MIN_BASES` cannot exclude a GSA run; `wmw fetch` warns rather than filtering silently.
There is no `tax_tree()` equivalent, so `--taxonomy` is ignored under `--source gsa`.
Because this is a scraped interface rather than an API, form fields and page structure can
change without notice.

### `metadata.py`
Provides three normalization paths (ENA / SRA / GSA) for both runs and studies, converging
on a shared schema. `filter_runs()` is the post-fetch safety net and the primary filter layer
for `wmw fetch`: it checks `host_tax_id`, `base_count`, `library_strategy`, `library_source`,
and `instrument_platform` on already-normalized records. Fields with empty values are never
excluded by a filter (unknown ≠ excluded).

### `drakkar.py`
Targets Drakkar 2.x. `build_input_tsv()` writes the Drakkar sample detail file
(`sample`, `rawreads1`, `rawreads2`, `reference_name`, `reference_path`, plus `assembly`
and `coverage` when any row sets them) from Airtable sample records. `generate_pipeline_script()`
emits the bash script that `wmw process` launches: it runs the stages it is given —
`PIPELINE_STAGES` is `preprocessing → cataloging → amr → profiling → annotating`, and
`stages_from(stage)` returns that stage plus everything after it — with `wmw set-status`
calls and an `EXIT` trap around each stage so a failure or a `.wmw-stop` file is reflected
in Airtable. `_env_command()` decides how each binary is reached: an env given as a path
is called directly as `<env>/bin/<binary>`, an env given by name through
`conda run --no-capture-output -n <env>`. The direct call matters because `conda run`
chdirs into the current working directory before exec'ing the child, so a work dir that
has stopped being readable takes down the `set-status` call whose job is to report that
very failure; the traps also `cd /` before reporting, for the same reason.
Every run therefore continues to the end of the pipeline unless a stage fails; the
`set-status` calls are guarded so an Airtable outage costs the study a status update
rather than the stages it has not reached yet, and the script parks such a study in
`resume` so the next `wmw process` replays the missed finalization. The single-stage
`generate_*_script()` helpers are thin wrappers over it. AMR sits between cataloging and
profiling because it reads the assemblies cataloging produces and nothing the later
stages add; `drakkar amr -i <work dir>` discovers them under `cataloging/megahit`.

`platform_from_instrument()` maps a Samples-table `instrument_platform` (or instrument
model) onto the `illumina`/`bgi` value `drakkar preprocessing --platform` takes, and
`resolve_batch_platform()` settles a whole study on one of them — Drakkar takes the flag
once per run, so the majority platform among the `use` rows wins, ties and studies with
no recognised platform fall back to `illumina` (Drakkar's own default), and the returned
per-platform counts let `wmw process` report a mixed study. The flag reaches only the
`preprocessing` stage, which is the only one that reads it.

`contig_to_bin_files()` collects binette's per-assembly
`cataloging/binette/<assembly>/final_contig_to_bin.tsv` tables, which
`gzip_contig_to_bin_tsv()` compresses under the sample's own name for the
Samples attachment column.

The `parse_*` functions read Drakkar's output tables back into Airtable field IDs:
`preprocessing.tsv`, `cataloging.tsv` and `profiling_genomes.tsv` at the output root,
`cataloging/final/all_bin_metadata.csv`, `amr/amr_qc.tsv`,
`annotating/genome_taxonomy.tsv`, and the
per-genome `annotating/final/<genome>_genes.tsv`. Since Drakkar 2.0 the gene table is
long-form — one row per annotation hit, with the database named in a `source` column and
EC numbers inside the `details` JSON — so the per-database Airtable counts are counts of
*distinct genes* carrying at least one hit from that source. The Drakkar 1.x wide layout
(one row per gene, one column per database) is still detected from the header and parsed,
so an output directory written by an older Drakkar can still be finalised.

### `transfer.py`
ERDA transfers over SFTP, ported from the ehio transfer layer so both tools reach ERDA
the same way. `SFTPTransfer` is a paramiko-backed context manager that creates remote
directories on demand (`ensure_remote_dir`), checks for existing files
(`remote_exists`), and writes through `upload_stream` / `upload_gzipped` /
`upload_file` (for content that is already compressed). `gzip_into`
compresses a local file straight into the open remote handle, so a multi-GB assembly is
never staged as a temporary `.gz` on local disk. Every write goes to a `.part` name that
is renamed only once the writer returns, so an interrupted transfer leaves behind no file
that looks complete. `paramiko` is imported defensively — `paramiko_available()` lets the
caller skip the transfer with a warning rather than failing the run.

### Unsplit paired runs
An SRA run whose reads were submitted already quality-trimmed loses its pairing at load
time: the two files no longer line up read-for-read, so the loader stores every read as
its own single-read spot — all of one file's reads, then all of the other's, each spot
carrying a zero-length second read. ENA mirrors that object, so the run is served as one
flat `<run>.fastq.gz` holding **both** mates concatenated instead of a `_1`/`_2` pair.

There is no per-mate URL to fetch: ENA publishes only that one file, and NCBI's original
submitted files sit in a requester-pays bucket. The reads are all present though, in two
equal halves, and each keeps the read index it was loaded under — so the split is a
read-count halving, not an SRA-format operation, and the archive URL is all a splitter
needs.

wmw therefore stays out of the data path. `_split_fastq_urls()` recognises the case (a
PAIRED layout with a single file) and routes that URL to `fastq_url_unsplit` rather than
`fastq_url_1`, because the file is not R1 and naming it so would hand drakkar half a
library under the wrong name. `build_input_tsv()` passes it through as the
`rawreads_unsplit` column with `rawreads1`/`rawreads2` blanked for that row, and drakkar
splits it before preprocessing. `metadata.unsplit_paired_runs()` is what `fetch` and
`process` report from.

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
| ERDA layout is study-first | `{base}/{code}/assemblies/` and `{base}/{code}/bins/` keeps everything for one study under one folder, so a study can be archived or shared whole. ehio's `ASB/{batch}` + `MAG/{batch}` split predates wmw and is kept there for link stability. |
| ERDA transfer runs last | Airtable writes happen first in `_finalize_cataloging_outputs()`, so a failed or slow transfer never costs the metadata. Per-file failures are collected and reported instead of aborting. |
| ERDA transfer never auto-replaces | The attachment-replacement flag exists because Airtable *appends* on upload; SFTP has no such quirk, and re-sending multi-GB assemblies on every rerun would be pure cost. Files already present are skipped; `wmw upload-erda --replace-files` is the explicit override. |
| All bins archived, not just the good ones | The Airtable Genomes table is curated (completeness > 50, contamination < 10); the ERDA copy is an archive of what binette actually produced. |
| Contig-to-bin tables attach per sample, gzipped and renamed | The contig membership of a sample's bins belongs on that sample's row, not the study's. Every assembly's binette table is called `final_contig_to_bin.tsv` and Airtable names an attachment after the file it was uploaded from, so each is compressed to `{code}_contig_to_bin.tsv.gz` first — which also keeps most of them under the ~3.7 MB attachment limit. |
| AMR runs between cataloging and profiling | It needs the assemblies cataloging produces and nothing profiling or annotating adds. Keeping it in the one sequential chain means one script, one screen session, one status field and one stop marker — running it concurrently would need a separate copy of all four, plus its own Snakemake output root to avoid locking against the other stages. |
| AMR transfer runs inline | The result tables are small compressed TSVs, unlike the multi-GB assemblies that justify a detached `screen` session for the cataloging transfer. |
| AMR result tables mirror ehio | The Airtable fields, the per-assembly `amr_qc.tsv` metrics and the study-prefixed ERDA copies follow the ehio AMR module, so results from both tools read the same way. |
| Transfer detached into its own `screen` | A multi-GB upload must not hold up status updates or the next Drakkar stage, and `{code}-erda-upload` can be killed independently by `wmw stop`. |
