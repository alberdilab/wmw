# CLI Reference

Entry point: `wmw.cli:main`  
Pattern: `cmd_*()` functions dispatched via `args.func`.  
Shared helpers: `_conf(args, cli_attr, config_key)`, `_die(msg)`, `_resolve_token(args)`, `_airtable_client(args)`.

---

## Two-phase workflow

```
wmw scan   → discover studies → Airtable Studies table
               ↓  (user reviews; sets status = "approved")
wmw fetch  → fetch run data for approved studies → Airtable Samples table
               ↓
wmw process → generate and launch Drakkar workflow scripts for ready studies
wmw stop    → stop an ongoing processing run
```

---

## wmw scan

Search ENA (or GSA) for wild-animal studies and populate the **Studies** table. Run-level sample data is **not** fetched — that is deferred to `wmw fetch` after manual review.

```
wmw scan [--source ARCHIVE]              # ena (default) | gsa; config: SOURCE
         [--from DATE] [--to DATE]
         [--year YEAR[,YEAR]]             # e.g. 2025 or 2024,2026
         [--month MONTH[,MONTH]]          # e.g. March or March,June
         [--study ACCESSION]
         [--host-tax-id TAXON_ID]
         [--date-field FIELD]             # first_public|last_updated; config: DATE_FIELD
         [--keyword TEXT]
         [--gsa-organism NAME]            # GSA only; config: GSA_ORGANISM
         [--include GROUPS]              # Human,Livestock,Aquaculture,Laboratory or All
         [--dry-run]
         [--no-publications]
         [--airtable-token TOKEN]         # or $AIRTABLE_TOKEN env var
         [--base-id BASE_ID]              # or config WMW_BASE
         [--studies-table TABLE]          # or config STUDIES_TABLE
```

**Modes:**
- Date-range: `--from`/`--to` (explicit ISO dates) **or** `--year`/`--month` (shorthand)
- Single-study: `--study PRJEB12345` (overrides date window). Accepts a BioProject accession or its secondary (`ERP…`/`SRP…`); runs are counted under the same `LIBRARY_STRATEGY`/`LIBRARY_SOURCE` and host-exclusion filters as a windowed scan, so **Runs** and **Host taxa** are filled in.

**Date shorthand (`--year` / `--month`):**

| Flags | Resolved window |
|---|---|
| `--year 2025` | `2025-01-01` → `2025-12-31` |
| `--year 2024,2026` | `2024-01-01` → `2026-12-31` |
| `--month March` | `{current_year}-03-01` → `{current_year}-03-31` |
| `--month March,June` | `{current_year}-03-01` → `{current_year}-06-30` |
| `--year 2025 --month March` | `2025-03-01` → `2025-03-31` |
| `--year 2025,2026 --month March,June` | `2025-03-01` → `2026-06-30` |

Month names are full English names, case-insensitive (e.g. `January`, `march`). When `--month` is used without `--year`, the current calendar year is assumed. `--year`/`--month` take precedence over `--from`/`--to` when both are supplied.

**Notes:**
- Uses the ENA Portal `result=study` endpoint — returns study-level metadata including `study_description`.
- `--host-tax-id` is matched against the ENA study `tax_id` field, which is an approximate host filter at study level.
- `collection_date` is a run-level field not available in the study index; use `first_public` or `last_updated`.
- New studies are written with `status = "new"`.
- By default, studies whose `tax_id` matches any entry in `EXCLUDED_HOST_TAX_IDS` (Human, Livestock, Aquaculture, Laboratory) are removed before writing to Airtable. Use `--include` to re-enable specific groups or `--include All` to disable all exclusions.

**Execution order:**
1. Query ENA study endpoint (date range + optional host_tax_id, keyword)
2. Normalize → deduplicate by study_accession
3. Filter by excluded host taxa (unless `--include All`)
4. Resolve publications via PubMed/CrossRef and fetch OA PDF URL via Unpaywall (unless `--no-publications`)
5. Print summary table
6. Upsert Studies into Airtable (unless `--dry-run`)

### Scanning GSA (`--source gsa`)

Queries the Genome Sequence Archive (NGDC/CNCB) instead of ENA. Studies are written with
`source = "GSA"`, `study_accession` set to the GSA accession (`CRA…`) and
`secondary_study_accession` to the NGDC BioProject (`PRJCA…`).

```
wmw scan --source gsa --from 2025-09-01 --to 2025-09-30
wmw scan --source gsa --study CRA028180
```

**Differences from the ENA path:**

| Flag | Behaviour under `--source gsa` |
|---|---|
| `--from` / `--to` | Matched against GSA's release date. `--date-field` has no effect. |
| `--gsa-organism` | Organism label to match; default `organismal metagenomes`, the label GSA files metagenome submissions under. Comma- or pipe-separated values are OR'd. |
| `--keyword` | Applied **after** study lookup, against study title and description. GSA's searchable `title` field holds experiment titles (per-sample aliases), so querying it directly would not match study-level keywords. |
| `--taxonomy` | Ignored with a warning — GSA has no `tax_tree()` subtree expansion. |
| `--host-tax-id` | Ignored — GSA's search does not expose host taxonomy. Host filtering happens at fetch time. |
| `--library-strategy`, `--library-source`, `--instrument-platform` | Applied in the query. |

Every GSA query is restricted to `"NGDC"[center]`, so the INSDC records GSA mirrors from
SRA are excluded and ENA-sourced studies are not duplicated.

**Execution order:**
1. `POST /gsa/search/` with the composed query; page through the experiment hits and collect
   the distinct parent study of each
2. For each study, merge `GET /gsa/browse/<CRA>` with the linked `/bioproject/browse/<PRJCA>`
   record for title, description, organism, release date and submitting organization
3. Apply the `--keyword` filter to the resolved study records
4. Resolve publications (unless `--no-publications`)
5. Print summary table
6. Upsert Studies into Airtable (unless `--dry-run`)

---

## wmw fetch

Fetch run/sample data from ENA (or GSA) for studies the user has approved, and populate the **Samples** table. Study status is updated to `"indexed"` after a successful fetch.

```
wmw fetch [--source ARCHIVE]             # ena (default) | gsa; config: SOURCE
          [--status VALUE]               # default: "approved"
          [--study ACCESSION]            # bypass status filter; fetch one study directly
          [--refresh-metadata]           # also rewrite BioSample columns on existing rows
          [--fill-missing]               # fill only the empty cells on existing rows
          [--library-strategy STRATEGY]  # default: WGS,METAGENOMIC
          [--library-source SOURCE]      # e.g. METAGENOMIC; config: LIBRARY_SOURCE
          [--instrument-platform PLATFORM] # e.g. ILLUMINA; config: INSTRUMENT_PLATFORM
          [--min-bases N]                # config: MIN_BASES
          [--include GROUPS]             # Human,Livestock,Aquaculture,Laboratory or All
          [--exclude-taxa IDS]           # comma-sep extra taxon IDs to also exclude
          [--dry-run]
          [--debug]                      # print every archive request
          [--airtable-token TOKEN]       # or $AIRTABLE_TOKEN env var
          [--base-id BASE_ID]            # or config WMW_BASE
          [--studies-table TABLE]        # or config STUDIES_TABLE
          [--samples-table TABLE]        # or config SAMPLES_TABLE
```

**Modes:**
- Batch (default): reads all studies where `status = --status` from Airtable, then fetches each
- Single-study: `--study PRJEB12345` (bypasses Airtable status filter; does not update study status)

**Notes:**
- Host taxon exclusions (`EXCLUDED_HOST_TAX_IDS` groups) are active by default. Use `--include Human` etc. to re-enable specific groups, or `--include All` to disable all exclusions.
- `--exclude-taxa` appends individual taxon IDs on top of the group-based exclusions.
- Each run record carries the registered sample's BioSample/MIxS attributes — collection date, country/sea, latitude, longitude, broad-scale environmental context, environmental medium and host sex. ENA returns them on the same `read_run` call, so they cost no extra request. See [schema.md](schema.md#biosample-metadata) for the opt-in `SAMPLES_COL_*` keys they need.

**Execution order:**
1. Read approved studies from Airtable Studies table (or use `--study` directly)
2. For each study, call `ena.search_study(accession)` — or `gsa.search_study(accession)` under
   `--source gsa` — to fetch all run records
3. Normalize → deduplicate
4. Post-fetch `filter_runs()`: host taxon exclusions (`--include` / `--exclude-taxa`), min_bases, library_strategy, library_source, instrument_platform
5. Upsert Samples into Airtable (unless `--dry-run`)
6. With `--refresh-metadata`, rewrite the BioSample columns on rows that already existed
7. With `--fill-missing`, fill the empty cells on rows that already existed
8. Update study `status = "indexed"` for successfully fetched studies

### Backfilling empty columns (`--fill-missing`)

`fetch` skips run accessions already in the Samples table, so rows written before a column
existed keep their blanks — and they stay blank however often `fetch` runs.
`--fill-missing` adds a pass over those rows that writes **only where the Airtable cell is
empty**:

```
wmw fetch --fill-missing --status indexed              # every already-indexed study
wmw fetch --fill-missing --study PRJEB12345            # one study
wmw fetch --fill-missing --status indexed --dry-run    # count the cells first
```

It covers every column wmw can supply from an archive run record — accessions, FASTQ paths
and checksums, instrument and library fields, counts, dates, the BioSample/MIxS attributes,
and the link to the parent study — but never `status`, and never a cell that already holds
a value. A curator's correction and every Drakkar result column therefore survive
untouched, which makes the pass safe to run against studies that are already processed. A
cell holding `0` or a latitude of `0.0` counts as filled, not empty. New runs are still
inserted as usual, and the summary line reports how many cells were filled on how many
rows.

Use `--fill-missing` after adding a column in Airtable, or after upgrading wmw to a version
that fetches a field it did not fetch before. Use `--refresh-metadata` instead when the
archive record is what you trust and you want existing values replaced.

### Rewriting BioSample metadata (`--refresh-metadata`)

`fetch` skips run accessions already in the Samples table, so rows written before a
BioSample column existed keep their blanks. `--refresh-metadata` adds an update pass over
those rows:

```
wmw fetch --refresh-metadata --status indexed      # every already-indexed study
wmw fetch --refresh-metadata --study PRJEB12345    # one study
```

Only the BioSample fields are written — `collection_date`, `geo_loc_name`, `country`,
`lat`, `lon`, `broad_scale_environmental_context`, `environmental_medium`, `host`,
`host_tax_id`, `host_scientific_name`, `host_sex`. Accessions, FASTQ paths, batch
assignment, status and Drakkar results are left untouched, so the pass is safe against
studies that are already processed. New runs are still inserted as usual.

Unlike `--fill-missing`, this pass **overwrites** BioSample values already in the base. The
two can be combined: the refresh runs first, then the fill covers whatever is still empty.

### Fetching from GSA (`--source gsa`)

```
wmw fetch --source gsa --study CRA028180
```

Run records come from the study's metadata workbook
(`POST /gsa/file/exportExcelFile`), whose Run, Experiment and Sample sheets are joined to
produce the same sample schema as ENA — including file names, sizes, MD5 checksums, host,
collection date and geographic location.

- **Taxonomy is resolved first.** GSA publishes taxon *names* but no taxon IDs, and the
  host-exclusion filter keys off `host_tax_id`. Each distinct organism and host name is
  resolved through the NCBI/ENA taxonomy service (cached per run of the command) before
  filtering, so the `EXCLUDED_HOST_TAX_IDS` groups behave as they do for ENA. A name that
  will not resolve leaves the ID blank, and blank fields are never excluded.
- **`--min-bases` does not apply.** GSA publishes no base counts, so `base_count` stays
  empty. The command warns when the filter is set rather than filtering silently.
- **Download URLs are HTTPS.** GSA's FTP paths are rewritten to
  `https://download.cncb.ac.cn/…`, which supports range requests and so resumes. The raw
  FTP paths are kept in `fastq_ftp`. `wmw process` consumes these like any other URL.

---

## wmw process

Pull ready studies from Airtable and launch a Drakkar workflow in detached `screen` sessions.

```
wmw process [--batch BATCH]
            [--workflow {preprocessing,cataloging,amr,profiling,annotating}]   # default: preprocessing
            [--slurm]
            [--output-dir DIR]   # or config DRAKKAR_OUTPUT_DIR
            [--studies-table TABLE] [--samples-table TABLE] [--genomes-table TABLE]
            [--airtable-token TOKEN] [--base-id BASE_ID]
```

**Execution order:**
1. Fetch studies where `status` is `"ready"`, `"resume"`, or `"rerun"` (filtered by `--batch` if given)
2. For `status="resume"`, upload any existing preprocessing/cataloging/profiling outputs to Airtable
3. If resume still has a pending Drakkar task, launch only the earliest missing task whose dependencies are satisfied
4. For `status="ready"` or `"rerun"`, fetch the study's samples and write `{output_dir}/{code}/{code}.tsv` with samples whose status is `"use"`
5. Write `{output_dir}/{code}/{code}.sh`
6. Studies with `Priority = Low` add `--slurm-partition lazyqueue --slurm-qos lazy` to generated Drakkar commands
7. Launch the script in a detached `screen` session named `{code}`
8. Generated scripts update the Study status through `preprocessing`, `preprocessed`, `cataloging`, `cataloged`, `amring`, `amred`, `quantifying`, `quantified`, `annotating`, `Done`, `error`, or `stopped`
9. Genome FASTA attachment uploads are detached into a `{code}-genome-upload`
   `screen` session when finalization is run outside an existing `screen`; if
   `screen` is unavailable, upload falls back to the current process
10. Genomes are only created/updated and uploaded when completeness is above 50
   and contamination is below 10
11. Assemblies and the binette-refined final bins are transferred to ERDA in a
   detached `{code}-erda-upload` `screen` session (see
   [wmw upload-erda](#wmw-upload-erda)); the transfer runs after the Airtable
   writes, so a failed transfer never costs the metadata
12. AMR runs between cataloging and profiling — see
   [the AMR workflow](#the-amr-workflow)

---

## The AMR workflow

`drakkar amr` calls antimicrobial resistance loci per assembly with
AMRFinderPlus and CARD/RGI and attaches geNomad plasmid or virus mobility
context. It needs the assemblies cataloging produces and nothing profiling or
annotating adds, so it runs between the two:

```
preprocessing → cataloging → amr → profiling → annotating
```

It is an ordinary stage in every other respect: the same `{code}.sh` script, the
same `{code}` screen session, the same `{code}.out`/`.err` logs, the same
`.wmw-stop` marker, and the same study `status` field, which it moves through
`amring` → `amred` (or `stopped`/`error`). The generated scripts report these as
`--status amr` / `--status amr_done`; `_PROCESS_STATUS_MAP` translates them to
the labels the base's select actually offers. `drakkar amr -i` discovers the
assemblies under `cataloging/megahit/{assembly}/{assembly}.fna` and names each
one after its folder — the wmw sample code — so no manifest is needed.

**Running it on its own**

```
wmw process --batch CODE --workflow amr [--slurm]
```

A study with status `resume` also launches AMR automatically when cataloging
output exists and `amr/amr_qc.tsv` does not, before it would launch profiling.

**Outputs**

Per-assembly counts from `amr/amr_qc.tsv` go to the Samples rows, keyed by
`assembly_id`. The two `*_without_coordinates` columns are caller diagnostics
rather than results and are never written.

| `amr_qc.tsv` column | Samples config key |
|---|---|
| `amrfinder_hits` | `SAMPLES_COL_AMR_AMRFINDER_HITS` |
| `rgi_hits` | `SAMPLES_COL_AMR_RGI_HITS` |
| `mobility_regions` | `SAMPLES_COL_AMR_MOBILITY_REGIONS` |
| `amr_loci` | `SAMPLES_COL_AMR_LOCI` |
| `multi_tool_loci` | `SAMPLES_COL_AMR_MULTI_TOOL_LOCI` |
| `mobility_links` | `SAMPLES_COL_AMR_MOBILITY_LINKS` |
| `mobile_loci` | `SAMPLES_COL_AMR_MOBILE_LOCI` |

The five aggregate tables and the manifest are attached to the study record and
archived on ERDA — see [wmw upload-erda](#wmw-upload-erda). A table over
Airtable's 5 MB encoded attachment limit is reported and left on ERDA only.

## wmw stop

Stop an ongoing `wmw process` run for one study code.

```
wmw stop --batch CODE                 # --study CODE is an alias
         [--output-dir DIR]           # or config DRAKKAR_OUTPUT_DIR
         [--studies-table TABLE] [--samples-table TABLE]
         [--airtable-token TOKEN] [--base-id BASE_ID]
```

**Execution order:**
1. Look up the Study by its `code` field and set Study `status = "stopped"`
2. Write `{output_dir}/{code}/.wmw-stop` so generated script traps report `stopped`
3. Stop the detached `screen` session named `{code}`, any
   `{code}-genome-upload` attachment-upload session, and any
   `{code}-erda-upload` ERDA transfer session
4. Best-effort cancel matching Slurm jobs. Matches include the study output path and Drakkar COMMENT values like `rule_fastp_wildcards_SA000022`, where `SA000022` is a sample `code` in that study.

---

## wmw upload-genome-files

Upload generated genome FASTA attachments for one study. This is normally
launched automatically in a detached `{code}-genome-upload` `screen` session
when cataloging finalization finds FASTA files to attach. Genomes below the
quality thresholds (`completeness <= 50` or `contamination >= 10`) are skipped.

```
wmw upload-genome-files --study CODE
                        [--output-dir DIR]
                        [--samples-table TABLE] [--genomes-table TABLE]
                        [--airtable-token TOKEN] [--base-id BASE_ID]
```

---

## wmw upload-erda

Transfer the assemblies and the binette-refined final bins of one study to
ERDA, or its AMR result tables. The cataloging transfer is normally launched
automatically in a detached `{code}-erda-upload` `screen` session when
cataloging outputs are finalized; the AMR tables are small and are transferred
inline when AMR outputs are finalized. Both happen from `wmw process` (resume)
and from `wmw set-status`. Run this by hand to retry a failed transfer.

```
wmw upload-erda --study CODE
                [--what {cataloging,amr,all}]  # default: cataloging
                [--output-dir DIR]           # or config DRAKKAR_OUTPUT_DIR
                [--sftp-host HOST]           # or config SFTP_HOST
                [--sftp-user USER]           # or config SFTP_USER
                [--sftp-identity PATH]       # or config SFTP_IDENTITY
                [--sftp-remote-base PATH]    # or config SFTP_REMOTE_BASE
                [--sftp-amr-dir NAME]        # or config SFTP_REMOTE_AMR_DIR
                [--replace-files] [--verbose]
```

**What is transferred — `--what cataloging`**

| Local | ERDA |
|---|---|
| `cataloging/megahit/{assembly}/{assembly}.fna` | `{SFTP_REMOTE_BASE}/{code}/{SFTP_REMOTE_ASSEMBLY_DIR}/{assembly}_contigs.fasta.gz` |
| every path in `cataloging/final/all_bin_paths.txt` | `{SFTP_REMOTE_BASE}/{code}/{SFTP_REMOTE_BIN_DIR}/{genome}.fa.gz` |

With the shipped defaults that is `/WMW/{code}/assemblies/` and
`/WMW/{code}/bins/`.

**What is transferred — `--what amr`**

All from `amr/`, to `{SFTP_REMOTE_BASE}/{code}/{SFTP_REMOTE_AMR_DIR}/`
(`/WMW/{code}/amr/` by default), study-prefixed so a table stays identifiable
once it is downloaded away from its folder:

| Local | ERDA | Sent as |
|---|---|---|
| `amr_hits.tsv.xz` | `{code}_amr_hits.tsv.xz` | as-is |
| `amr_loci.tsv.xz` | `{code}_amr_loci.tsv.xz` | as-is |
| `amr_drug_classes.tsv.xz` | `{code}_amr_drug_classes.tsv.xz` | as-is |
| `amr_mobility.tsv.xz` | `{code}_amr_mobility.tsv.xz` | as-is |
| `mobility_regions.tsv.xz` | `{code}_mobility_regions.tsv.xz` | as-is |
| `amr_qc.tsv` | `{code}_amr_qc.tsv.gz` | gzipped |
| `assembly_summary.tsv` | `{code}_assembly_summary.tsv.gz` | gzipped |
| `manifest.yaml` | `{code}_amr_manifest.yaml` | as-is |

The `.tsv.xz` tables drakkar writes are already compressed, so they go up
byte-for-byte; only the plain-text summaries are gzipped into the connection.

**Notes**
- Both assemblies and bins are gzipped straight into the SFTP connection, so a
  multi-GB assembly never needs a temporary `.gz` on the local disk. Each
  transfer is staged through a `.part` name and renamed only once the write
  completes, so an interrupted upload leaves no file that looks finished.
- Every bin in `all_bin_paths.txt` is archived, including bins below the
  completeness/contamination thresholds that gate the Airtable Genomes table.
  The ERDA copy is an archive of what binette produced, not a curated set.
- Files already on ERDA are skipped. `--replace-files` clears the remote folders
  selected by `--what` first and re-sends everything; it is never set
  automatically, not even on a rerun.
- The transfer is skipped with a warning — never an error — when `SFTP_HOST`,
  `SFTP_REMOTE_BASE` or `SFTP_USER` is empty, or when `paramiko` is not
  installed. Individual file failures are collected and reported at the end
  rather than aborting the run.
- Authentication uses the SSH agent unless `SFTP_IDENTITY` names a private key.
- Exit code is 0 when the study's outputs are on ERDA, whether this run sent
  them or found them already there, and non-zero when the archive is incomplete
  (nothing found, not configured, connection refused, or a file failed) — so it
  can be driven from a retry loop.

---

## wmw status

Display sample status breakdown from Airtable.

```
wmw status [--batch BATCH] [--airtable-token TOKEN] [--base-id BASE_ID]
           [--studies-table TABLE] [--samples-table TABLE]
```

Prints a Rich table of `status → count` for samples (filtered by batch if given).

---

## wmw config

```
wmw config --view   # print config path + contents
wmw config --edit   # open in $VISUAL / $EDITOR / nano / vim
```

Config file location: `src/wmw/data/config.yaml` inside the installed package.

---

## wmw update

```
wmw update
```

Runs `pip install --upgrade wmw`.
