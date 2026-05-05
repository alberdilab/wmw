# CLI Reference

Entry point: `wmw.cli:main`  
Pattern: `cmd_*()` functions dispatched via `args.func`.  
Shared helpers: `_conf(args, cli_attr, config_key)`, `_die(msg)`, `_resolve_token(args)`, `_airtable_client(args)`.

---

## wmw scan

Search ENA/SRA for wild-animal metagenomes and populate Airtable.

```
wmw scan [--from DATE] [--to DATE] [--study ACCESSION]
         [--db {ena,sra,both}]
         [--host-tax-id TAXON_ID]
         [--library-strategy STRATEGY]   # default: WGS,METAGENOMIC
         [--library-source SOURCE]        # e.g. METAGENOMIC; config: LIBRARY_SOURCE
         [--instrument-platform PLATFORM] # e.g. ILLUMINA; config: INSTRUMENT_PLATFORM
         [--date-field FIELD]             # first_public|collection_date|last_updated; config: DATE_FIELD
         [--min-bases N]                  # config: MIN_BASES
         [--keyword TEXT]
         [--exclude-taxa IDS]             # comma-sep, appended to config EXCLUDED_HOST_TAX_IDS
         [--no-exclude]                   # disables all exclusions
         [--dry-run]
         [--no-publications]
         [--airtable-token TOKEN]         # or $AIRTABLE_TOKEN or config AIRTABLE_TOKEN
         [--base-id BASE_ID]              # or config WMW_BASE
         [--studies-table TABLE]          # or config STUDIES_TABLE
         [--samples-table TABLE]          # or config SAMPLES_TABLE
```

**Modes:**
- Date-range: requires `--from` and `--to`
- Single-study: `--study PRJEB12345` (overrides date window; exclusions applied post-fetch)

**Execution order:**
1. Build filter params via `_resolve_scan_params()`
2. Query ENA (filters at query time) and/or SRA (min_bases post-fetch)
3. Normalize → deduplicate → post-fetch `filter_runs()`
4. Resolve publications via PubMed/CrossRef (unless `--no-publications`)
5. Print summary table
6. Upsert Studies then Samples into Airtable (unless `--dry-run`)

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
