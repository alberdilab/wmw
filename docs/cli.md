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
wmw process → run Drakkar workflow on pending samples
```

---

## wmw scan

Search ENA for wild-animal studies and populate the **Studies** table. Run-level sample data is **not** fetched — that is deferred to `wmw fetch` after manual review.

```
wmw scan [--from DATE] [--to DATE] [--study ACCESSION]
         [--host-tax-id TAXON_ID]
         [--date-field FIELD]             # first_public|last_updated; config: DATE_FIELD
         [--keyword TEXT]
         [--dry-run]
         [--no-publications]
         [--airtable-token TOKEN]         # or $AIRTABLE_TOKEN env var
         [--base-id BASE_ID]              # or config WMW_BASE
         [--studies-table TABLE]          # or config STUDIES_TABLE
```

**Modes:**
- Date-range: requires `--from` and `--to`
- Single-study: `--study PRJEB12345` (overrides date window)

**Notes:**
- Uses the ENA Portal `result=study` endpoint — returns study-level metadata including `study_description`.
- `--host-tax-id` is matched against the ENA study `tax_id` field, which is an approximate host filter at study level.
- `collection_date` is a run-level field not available in the study index; use `first_public` or `last_updated`.
- New studies are written with `status = "new"`.

**Execution order:**
1. Query ENA study endpoint (date range + optional host_tax_id, keyword)
2. Normalize → deduplicate by study_accession
3. Resolve publications via PubMed/CrossRef (unless `--no-publications`)
4. Print summary table
5. Upsert Studies into Airtable (unless `--dry-run`)

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
          [--exclude-taxa IDS]           # comma-sep, appended to config EXCLUDED_HOST_TAX_IDS
          [--no-exclude]                 # disables all exclusions
          [--dry-run]
          [--airtable-token TOKEN]       # or $AIRTABLE_TOKEN env var
          [--base-id BASE_ID]            # or config WMW_BASE
          [--studies-table TABLE]        # or config STUDIES_TABLE
          [--samples-table TABLE]        # or config SAMPLES_TABLE
```

**Modes:**
- Batch (default): reads all studies where `status = --status` from Airtable, then fetches each
- Single-study: `--study PRJEB12345` (bypasses Airtable status filter; does not update study status)

**Execution order:**
1. Read approved studies from Airtable Studies table (or use `--study` directly)
2. For each study, call `ena.search_study(accession)` to fetch all run records
3. Normalize → deduplicate
4. Post-fetch `filter_runs()`: host taxon exclusions, min_bases, library_strategy, library_source, instrument_platform
5. Upsert Samples into Airtable (unless `--dry-run`)
6. Update study `status = "indexed"` for successfully fetched studies

---

## wmw process

Pull pending samples from Airtable and run a Drakkar workflow.

```
wmw process [--batch BATCH]
            [--workflow {complete,preprocessing,cataloging,profiling,
                         dereplicating,annotating}]   # default: preprocessing
            [--slurm]
            [--output-dir DIR]   # or config DRAKKAR_OUTPUT_DIR
            [--samples-table TABLE]
            [--airtable-token TOKEN] [--base-id BASE_ID]
```

**Execution order:**
1. Fetch samples where `status = "pending"` (filtered by `--batch` if given)
2. Write TSV manifest to `{output_dir}/{batch}/manifest.tsv`
3. Set sample `status = "running"` in Airtable
4. Invoke `drakkar <workflow> --manifest ... --output ...`
5. Set `status = "completed"` or `"failed"` based on exit code

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
