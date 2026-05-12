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

Search ENA for wild-animal studies and populate the **Studies** table. Run-level sample data is **not** fetched — that is deferred to `wmw fetch` after manual review.

```
wmw scan [--from DATE] [--to DATE]
         [--year YEAR[,YEAR]]             # e.g. 2025 or 2024,2026
         [--month MONTH[,MONTH]]          # e.g. March or March,June
         [--study ACCESSION]
         [--host-tax-id TAXON_ID]
         [--date-field FIELD]             # first_public|last_updated; config: DATE_FIELD
         [--keyword TEXT]
         [--include GROUPS]              # Human,Livestock,Aquaculture,Laboratory or All
         [--dry-run]
         [--no-publications]
         [--airtable-token TOKEN]         # or $AIRTABLE_TOKEN env var
         [--base-id BASE_ID]              # or config WMW_BASE
         [--studies-table TABLE]          # or config STUDIES_TABLE
```

**Modes:**
- Date-range: `--from`/`--to` (explicit ISO dates) **or** `--year`/`--month` (shorthand)
- Single-study: `--study PRJEB12345` (overrides date window)

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

---

## wmw fetch

Fetch run/sample data from ENA for studies the user has approved, and populate the **Samples** table. Study status is updated to `"indexed"` after a successful fetch.

```
wmw fetch [--status VALUE]               # default: "approved"
          [--study ACCESSION]            # bypass status filter; fetch one study directly
          [--library-strategy STRATEGY]  # default: WGS,METAGENOMIC
          [--library-source SOURCE]      # e.g. METAGENOMIC; config: LIBRARY_SOURCE
          [--instrument-platform PLATFORM] # e.g. ILLUMINA; config: INSTRUMENT_PLATFORM
          [--min-bases N]                # config: MIN_BASES
          [--include GROUPS]             # Human,Livestock,Aquaculture,Laboratory or All
          [--exclude-taxa IDS]           # comma-sep extra taxon IDs to also exclude
          [--dry-run]
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

**Execution order:**
1. Read approved studies from Airtable Studies table (or use `--study` directly)
2. For each study, call `ena.search_study(accession)` to fetch all run records
3. Normalize → deduplicate
4. Post-fetch `filter_runs()`: host taxon exclusions (`--include` / `--exclude-taxa`), min_bases, library_strategy, library_source, instrument_platform
5. Upsert Samples into Airtable (unless `--dry-run`)
6. Update study `status = "indexed"` for successfully fetched studies

---

## wmw process

Pull ready studies from Airtable and launch a Drakkar workflow in detached `screen` sessions.

```
wmw process [--batch BATCH]
            [--workflow {preprocessing,cataloging,profiling,annotating}]   # default: preprocessing
            [--slurm]
            [--output-dir DIR]   # or config DRAKKAR_OUTPUT_DIR
            [--studies-table TABLE] [--samples-table TABLE] [--genomes-table TABLE]
            [--airtable-token TOKEN] [--base-id BASE_ID]
```

**Execution order:**
1. Fetch studies where `status` is `"ready"`, `"resume"`, or `"rerun"` (filtered by `--batch` if given)
2. For `status="resume"`, upload any existing preprocessing/cataloging outputs to Airtable
3. If resume still has a pending Drakkar task, launch only the earliest missing task whose dependencies are satisfied
4. For `status="ready"` or `"rerun"`, fetch the study's samples and write `{output_dir}/{code}/{code}.tsv` with samples whose status is `"use"`
5. Write `{output_dir}/{code}/{code}.sh`
6. Launch the script in a detached `screen` session named `{code}`
7. Generated scripts update the Study status through `preprocessing`, `preprocessed`, `cataloging`, `cataloged`, `error`, or `stopped`

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
3. Stop the detached `screen` session named `{code}`
4. Best-effort cancel matching Slurm jobs. Matches include the study output path and Drakkar COMMENT values like `rule_fastp_wildcards_SA000022`, where `SA000022` is a sample `code` in that study.

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
