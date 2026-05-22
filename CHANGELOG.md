# Changelog

All notable changes to wmw are documented here.

## [Unreleased]

### Fixed

- `_split_fastq_urls` now correctly handles ENA runs with three FASTQ files (merged singleton + paired `_1`/`_2`). The unsuffixed file is ignored and the `_1`/`_2` files are assigned as R1/R2 respectively.

## [0.5.1] - 2026-05-21

### Fixed

- **Resume no longer re-uploads profiling attachments** (`file_quantifying`, `file_bases`, `file_counts`) that already exist in the Studies table; they are now properly skipped, matching the behaviour already in place for preprocessing and cataloging attachments.
- **Rerun (via `wmw set-status`) now replaces rather than accumulates attachments**: when a pipeline stage completes and uploads its TSV to the Studies table, any existing attachment in that field is cleared first so files never pile up across multiple runs.
- **Genome FASTA and annotation attachments** in the Genomes table follow the same rules: skipped on resume (unchanged), replaced on rerun via `wmw set-status`.
- Added `AirtableClient.clear_study_file` and `AirtableClient.clear_genome_file` to support clearing attachment fields before re-uploading.
- Added `--replace-files` flag to `wmw upload-genome-files` for explicit replacement of existing genome FASTA attachments (propagated automatically when invoked via a rerun screen session).

## [0.5.0] - 2026-05-20

### Added

- `wmw process` now reads the Studies table `priority` field from
  `STUDIES_COL_PRIORITY`; studies marked `Low` add
  `--slurm-partition lazyqueue --slurm-qos lazy` to generated Drakkar commands.
- `wmw fetch` now reports the first filter criterion that excluded each run,
  grouped by criterion/value, so removed runs are easier to diagnose.

## [0.4.11] - 2026-05-19

### Added

- Annotation finalization now reads per-genome `annotating/final/*_genes.tsv`
  files, writes annotation-count fields to matching Genomes records, compresses
  each TSV to `.tsv.gz`, and uploads it to `GENOMES_COL_FILE_ANNOTATION`.
- Annotation finalization now reads `annotating/genome_taxonomy.tsv`, extracts
  rank fields from `classification`, captures ANI/AF taxonomy metrics, and writes
  them to the configured Genomes taxonomy columns in Airtable.

## [0.4.10] - 2026-05-19

### Changed

- Annotation completion now requires both `annotating/gene_annotations.tsv.xz` and
  `annotating/genome_taxonomy.tsv`. Resume mode launches annotation when profiling
  exists but either annotation output is missing, and marks the Airtable study
  status as `Done` only when both files are present.

## [0.4.9] - 2026-05-14

### Changed

- All generated drakkar scripts (preprocessing, cataloging, profiling, annotating) and direct `run_workflow` calls now include `--env_path /projects/alberdilab/data/environments/drakkar/` so Snakemake conda environments are resolved from the correct shared directory.

## [0.4.8] - 2026-05-14

### Fixed

- `wmw process` resume path now correctly launches an annotation script when profiling is
  complete (`profiling_genomes.tsv` or alternative output files present) regardless of whether
  cataloging output files are still detectable locally. Previously the check required
  `has_cataloging` to be true as well; if cataloging files were absent or not at the expected
  path the code fell through and generated a cataloging/preprocessing script instead of annotation.
- Annotation scripts now call `drakkar annotating -B <bin_paths> -o <work_dir>` instead of
  the incorrect `drakkar profiling -B <bin_paths> -q <bin_metadata> -o <work_dir>`. This
  affects both `wmw process --workflow annotating` and the annotation stage of the full
  pipeline script.

## [0.4.7] - 2026-05-12

### Fixed

- `wmw process` resume path now correctly detects completed profiling when
  `profiling_genomes.tsv` is absent but `profiling_genomes/final/bases.tsv` or
  `profiling_genomes/final/counts.tsv` exist, preventing it from re-launching a
  profiling script instead of an annotation script.

## [0.4.6] - 2026-05-12

### Changed

- `wmw process` with `rerun` status now generates a full-pipeline script
  (preprocessing → cataloging → profiling → annotation) instead of stopping after
  cataloging. Previously, studies set to `rerun` would only produce a
  preprocessing+cataloging script and require additional manual resume steps to
  reach profiling and annotation.

## [0.4.5] - 2026-05-12

### Added

- New `annotating` workflow: `wmw process --workflow annotating` generates a script that runs
  `drakkar profiling -B <bin_paths> -q <bin_metadata> -o <work_dir>` (genome annotation mode,
  without reads mapping). Study status transitions: `annotating` while running → `completed`
  on success.
- `annotating` and `completed` are now valid `--status` values for `wmw set-status`.
- `wmw process` resume path now finalizes profiling outputs (uploads `profiling_genomes.tsv`
  and sample mapping rates) when `profiling_genomes.tsv` is present.
- `wmw process` resume path now detects when profiling is complete but annotation is absent
  (`annotating/gene_annotations.tsv.xz` missing) and automatically launches the annotation
  script.

- After the profiling workflow completes (`wmw set-status --workflow profiling --status quantified`),
  wmw now parses `profiling_genomes.tsv` and writes each sample's `mapping_percentage` to
  `SAMPLES_COL_MAGS_MAPPING_RATE` in Airtable.
- `profiling_genomes.tsv` is uploaded to the study's `STUDIES_COL_FILE_QUANTIFYING` attachment field.
- `profiling_genomes/final/bases.tsv` and `profiling_genomes/final/counts.tsv` are uploaded to
  `STUDIES_COL_FILE_BASES` and `STUDIES_COL_FILE_COUNTS` respectively.

## [0.4.4] - 2026-05-12

### Fixed

- Profiling script now `cd`s to the study work directory before invoking
  `drakkar profiling`, so relative paths inside `all_bin_paths.txt` (e.g.
  `cataloging/final/SA000039/SA000039_bin_100159.fa`) are resolved correctly
  instead of against the shell's launch directory.

## [0.4.3] - 2026-05-12

### Fixed

- `wmw set-status --status quantifying` and `--status quantified` were rejected
  as invalid choices; added both to the `--status` argparse allowlist.

## [0.4.2] - 2026-05-12

### Added

- `wmw process --workflow profiling` now generates a bash script that runs
  `drakkar profiling` for a study, using the cataloging bin paths/metadata and
  preprocessing reads as inputs (`-B`, `-r`, `-q`, `-a 0.98`, `-t genomes`).
  Airtable study status transitions: `quantifying` while running, `quantified`
  on success, `error`/`stopped` on failure.
- Added `profiling` workflow entries to `_PROCESS_STATUS_MAP`
  (`quantifying`, `quantified`, `stopped`, `error`).
- `wmw process` resume mode now automatically launches a profiling script when
  cataloging outputs exist but `profiling_genomes.tsv` is absent; if
  `profiling_genomes.tsv` already exists the study is skipped as fully done.

### Changed

- Genome FASTA attachment uploads now detach into a per-study
  `<code>-genome-upload` `screen` session when cataloging outputs are finalized
  outside an existing `screen`; if `screen` is unavailable, uploads fall back to
  the current process.
- Genomes are only created/updated in Airtable and uploaded as attachments when
  completeness is above 50 and contamination is below 10.

## [0.4.1] - 2026-05-12

### Added

- No unreleased changes yet.

### Changed

- Drakkar run summaries are now stored and uploaded as
  `<code>_preprocessing.tsv` and `<code>_cataloging.tsv`; existing legacy
  `preprocessing.tsv` / `cataloging.tsv` files are still accepted on resume.

### Fixed

- Genomes uploads no longer write Airtable-computed `Code` or `Record ID`
  fields, avoiding `INVALID_VALUE_FOR_COLUMN` errors during row creation.

## [0.4.0] - 2026-05-12

### Added

- `wmw set-status --workflow cataloging --status cataloged`: now reads
  `<DRAKKAR_OUTPUT_DIR>/<code>/cataloging.tsv` and uploads assembly cataloging
  stats to Samples, including focal/all mapping rates rounded to 2 decimals.
- `wmw set-status --workflow cataloging --status cataloged`: now reads
  `<DRAKKAR_OUTPUT_DIR>/<code>/cataloging/final/all_bin_metadata.csv` and creates
  linked Genomes records with bin name, completeness, contamination, length, N50,
  and contig count.
- `wmw set-status --workflow cataloging --status cataloged`: now reads
  `<DRAKKAR_OUTPUT_DIR>/<code>/cataloging/final/all_bin_paths.txt`, compresses
  matched bin FASTA files to `.fa.gz`, and uploads them to the corresponding
  Genomes attachment field.

### Changed

- Generated Drakkar launch scripts now pass their output root to `wmw set-status`,
  so stats uploads still work when `wmw process --output-dir` is used.
- `wmw process` now treats Studies with status `resume` as pending-task
  recovery: it first uploads any existing preprocessing/cataloging outputs and
  fills missing sample stats and Genomes records/files, then launches only the
  earliest missing Drakkar task whose dependencies are satisfied.
  
## [0.3.12] - 2026-05-10

### Added

- `wmw stop --batch CODE` / `--study CODE`: sets the matching Study status to `stopped`, stops the matching detached `screen` session, and best-effort cancels matching Slurm jobs. Slurm matching now inspects the job COMMENT field for Drakkar wildcards such as `rule_fastp_wildcards_SA000022`.

### Changed

- Generated Drakkar launch scripts now recognise a `.wmw-stop` marker in the study output directory and report `stopped` instead of `error` from their exit traps when stopped externally.

## [0.3.11] - 2026-05-07

### Fixed

- `drakkar.generate_cataloging_script()` and the cataloging step in `generate_preprocessing_script()` now pass `-o {work_dir}` to `drakkar cataloging`, ensuring output is written to the study folder rather than the current working directory.

## [0.3.10] - 2026-05-07

### Changed

- `wmw process` resume behaviour: when a study has status `resume` and `preprocessing.tsv` already exists, preprocessing stats are finalised and a cataloging-only script is generated and launched — instead of stopping after finalising preprocessing. If `preprocessing.tsv` is absent, the full preprocessing → cataloging script is generated as before (Snakemake resumes from checkpoints).
- Added `drakkar.generate_cataloging_script()` to support standalone cataloging script generation.

## [0.3.9] - 2026-05-07

### Added

- `wmw process` (preprocessing): generated scripts now run `drakkar cataloging -f {tsv} --multicoverage` automatically after preprocessing completes and Airtable is updated with preprocessing stats. Status flow: `preprocessing` → `preprocessed` (after Airtable update) → `cataloging` (before cataloging) → `cataloged` (after cataloging).
- `wmw set-status`: added `preprocessed`, `cataloging`, and `cataloged` as valid `--status` choices; added `cataloging` workflow mappings to the status map.
- Sample status model simplified to three user-controlled values: `use` (included in TSV), `pending`, `ignore` (both excluded). Workflow progress (`wmw set-status`, `wmw process` resume) no longer modifies sample statuses — only study status changes during processing.
- `build_input_tsv`: only rows with `status == "use"` are written to the Drakkar input TSV.

## [0.3.8] - 2026-05-07

### Fixed

- `wmw set-status` / `wmw process` (resume): preprocessing stats from `preprocessing.tsv` are now looked up in Airtable by the sample `code` field (matching the `sample` column written by `build_input_tsv`) instead of `run_accession`. The previous behaviour silently updated 0 records when `code` ≠ `run_accession`.
- `airtable.update_sample_preprocessing_stats`: OR formula lookups are now batched in groups of 50 to stay within Airtable's formula length limit for large studies.
- `wmw set-status` / `wmw process` (resume): improved diagnostics — "file not found" and "no matching Airtable records" are now reported as separate warnings instead of a single misleading "not found" message.

## [0.3.7] - 2026-05-07

### Added

- `wmw process`: generated preprocessing scripts now pass `--memory-multiplier` and `--time-multiplier` to `drakkar preprocessing` when the study's `memory_boost` / `time_boost` Airtable fields are set to a value other than 1.

### Fixed

- `build_input_tsv`: samples with `status == "discarded"` are now skipped when writing the Drakkar input TSV, so discarded samples are never included in processing.
- `wmw process`: now also picks up studies with status `"resume"` and `"rerun"` in addition to `"ready"`. `"rerun"` wipes the local output directory and reprocesses all non-discarded samples from scratch. `"resume"` fetches all non-discarded samples; if `preprocessing.tsv` already exists in the work directory it finalises the run (sets study + sample statuses to `"preprocessed"` and uploads stats) without relaunching Drakkar, otherwise it generates and launches the script as normal.

## [0.3.6] - 2026-05-07

### Fixed

- `wmw process`: generated scripts now prefix all `wmw set-status` calls with `conda run` using the `WMW_CONDA_ENV` config key (path or name), so `wmw` is found when the script runs inside a screen session without an active conda environment.
## [0.3.5] - 2026-05-07

### Added

- `wmw set-status --workflow preprocessing --status completed`: after updating Airtable statuses, now reads `<DRAKKAR_OUTPUT_DIR>/<code>/preprocessing.tsv` and uploads per-sample stats (reads/bases pre/post fastp, adapter-trimmed reads/bases, host reads/bases, metagenomic reads/bases, singleM fraction, nonpareil C and LR) to the corresponding Airtable sample fields using the field IDs from config.

### Changed

- `drakkar.generate_preprocessing_script()`: now always passes `--fraction` and `--nonpareil` to `drakkar preprocessing`.
- `drakkar.generate_preprocessing_script()`: generated script now calls `wmw set-status --status preprocessing` (instead of `--status running`) when preprocessing starts, so Airtable shows `preprocessing` immediately.
- `wmw set-status`: `--status` now accepts `preprocessing` as an explicit choice in addition to `running`, `completed`, and `error`.
- `wmw process`: after writing the `<code>.sh` script, now launches it inside a detached `screen` session named after the batch code (`screen -dmS <code> bash <code>.sh`). If `screen` is not on PATH the script is still written but a warning is printed instead of launching.

## [0.3.4] - 2026-05-07

### Changed

- `wmw update`: now installs directly from GitHub (`https://github.com/alberdilab/wmw.git`) using `pip install --force-reinstall git+<repo>` instead of upgrading from PyPI. Prints the current version before updating. Accepts `--repo URL` to override the source repository.

## [0.3.3] - 2026-05-07

### Added

- `wmw process`: completely redesigned. Instead of running Drakkar inline, it now generates a working directory (`DRAKKAR_OUTPUT_DIR/<code>/`) for each study with `status=ready`, writes a `<code>.tsv` input file with columns `sample`, `rawreads1`, `rawreads2`, `reference_name`, `reference_path`, and optionally `assembly` and `coverage` (included only when at least one row is non-empty), and writes a `<code>.sh` bash launch script that runs `drakkar preprocessing`, logs stdout/stderr, and calls back into `wmw set-status` to track progress in Airtable. Use `--batch CODE` to generate for a single study, `--slurm` to add the Drakkar slurm flag.
- `wmw set-status`: new subcommand called by generated launch scripts. Updates study and all its samples in Airtable. `--study CODE --workflow preprocessing --status running|completed|error` maps to Airtable status values `preprocessing`, `preprocessed`, or `error`.
- `drakkar.build_input_tsv()`: new function writing the Drakkar-format input TSV from decoded Airtable sample records; replaces the old `build_manifest` for the process workflow.
- `drakkar.generate_preprocessing_script()`: generates a self-contained bash script that activates conda, runs `drakkar preprocessing`, and calls `wmw set-status` on start, completion, and error via a shell trap.
- `airtable.AirtableClient.fetch_study_by_code()`: find a study record by its `code` field (STUDIES_COL_CODE).
- `airtable.AirtableClient.fetch_samples_for_study()`: fetch decoded sample records for a given study accession, with optional `status` filter.
- `config.yaml`: four new `SAMPLES_COL_*` field-ID keys — `SAMPLES_COL_REFERENCE_NAME`, `SAMPLES_COL_REFERENCE_PATH`, `SAMPLES_COL_ASSEMBLY`, `SAMPLES_COL_COVERAGE` — used in the new `wmw process` input TSV.

- `wmw fetch`: each inserted sample now has its `parent_study` linked-record field set to the Airtable record ID of the parent study, enabling direct record links from Samples to Studies in Airtable. Works in both batch mode (studies from status filter) and single-study mode (`--study ACC`).
- `airtable.AirtableClient.fetch_study_record_id()`: new method for targeted lookup of a study's Airtable record ID by accession (avoids fetching all records in single-study mode).

## [0.3.2] - 2026-05-06

### Added

- `airtable.AirtableClient.link_studies_to_species()`: new method that, given a mapping of study accessions to sets of host taxon IDs, looks up matching records in a Species table (by `taxid` field) and appends the study's Airtable record ID to a linked-record field — without overwriting existing links.
- `wmw scan`: after upserting studies, automatically links each study to its matching Species table records via host taxids resolved from the associated ENA runs. Controlled by three new config keys (`SPECIES_TABLE`, `SPECIES_TAXID_FIELD`, `SPECIES_STUDIES_LINK_FIELD`); linking is skipped if any of these keys are blank.
- `config.yaml`: new `SPECIES_TABLE`, `SPECIES_TAXID_FIELD`, and `SPECIES_STUDIES_LINK_FIELD` config keys for the Species table integration.

- `publications`: new `find_pubmed_ids(bioproject_accession)` function that resolves a BioProject accession to PubMed IDs using three strategies in order: (1) NCBI elink BioProject→PubMed and BioProject→SRA→PubMed, (2) NCBI esearch text search in PubMed and PMC databases, (3) Europe PMC text search. `resolve()` also falls back to a Europe PMC DOI when all PMID strategies return nothing. `resolve_batch` now automatically uses this for any study that lacks a pre-existing `pubmed_id` or DOI.
- `publications`: NCBI authentication now reads `NCBI_TOKEN` from the environment (`export NCBI_TOKEN="…"`); falls back to `NCBI_API_KEY` in config. Setting this enables 10 req/s instead of 3 req/s.

### Changed

- `publications.fetch_from_pubmed`: replaced Biopython `Entrez` with direct `urllib` + NCBI E-utils calls; `email` parameter removed (no longer required for NCBI lookups). `NCBI_EMAIL` in config is now used only as the contact address for Unpaywall PDF requests.
- `publications.resolve_batch`: signature changed from `(studies, email, api_key=None, delay=0.35)` to `(studies, api_key=None, email=None, delay=0.34, on_progress=None)`. `email` is now optional and only affects Unpaywall PDF lookups. `on_progress` is an optional callable invoked after each study to support progress reporting.
- `wmw scan`: publication lookup no longer skips when `NCBI_EMAIL` is unset; it always runs (using unauthenticated NCBI rate limits if no token is configured). When stdout is a TTY, publication resolution now shows a Rich progress bar ("Resolving publications · N/M studies · K resolved") matching the style of the ENA run-query bar.

- `config.yaml`: new `STUDIES_COL_DETECTED_RUNS` and `STUDIES_COL_DETECTED_HOST_TAXA` field-ID keys; `wmw scan` now writes the run count and unique host-taxon count per study into those Airtable columns.

### Changed

- `airtable.upsert_studies`: existing study records are now **updated** (all fields refreshed) instead of skipped; return value changed from `(inserted, skipped)` to `(inserted, updated)`.
- `wmw scan`: success message now reads "N inserted, N updated" instead of "N inserted, N already existed".

### Fixed

- `metadata.normalize_ena_run`, `normalize_sra_run`: `base_count` and `read_count` are now cast to `int` (or `None` when blank/invalid) instead of strings, matching Airtable's integer field type. `collection_date` and `first_public` are now validated as `YYYY-MM-DD` and set to `None` for partial or non-conforming values (e.g. `"2023-06"`, `"2023"`), matching Airtable's date field type. `airtable._enc` already omits `None` values, so no-data cases leave those columns empty rather than triggering a type error.
- `airtable.upsert_studies()`: re-scanning an existing study no longer overwrites its `status` field. Updates now omit `status` so a study manually set to `approved` or `indexed` stays at that status after a subsequent scan.
- `publications.find_pubmed_ids`: removed the `BioProject→SRA→PubMed` indirect elink path. SRA runs are often shared across studies, so this path was returning PMIDs for papers that cited the same runs in earlier publications (papers from 2001–2005 appearing for 2026 studies). The direct `BioProject→PubMed` elink, NCBI esearch, and Europe PMC strategies are sufficient and far more accurate.
- `publications.resolve_batch`: added a year-plausibility guard — if a resolved paper's `pub_year` is more than 2 years before the study's `first_public` date, the publication result is discarded. This catches any residual false positives not eliminated by the elink fix above.
- `publications.find_pubmed_ids`: with the SRA indirect path removed, strategies 2 (NCBI esearch) and 3 (Europe PMC) are now reliably reached when there is no direct BioProject→PubMed link, fixing cases where EuropePMC held the correct paper but was never queried (e.g. `PRJNA1337465`).
- `publications._europe_pmc_search`: removed double-quotes wrapping the accession in the API query (`query="PRJNA1337465"` → `query=PRJNA1337465`). Both forms return the same results for BioProject accessions, but the bare form is more consistent with how the EuropePMC web interface queries.
- `publications.resolve`: moved the year-plausibility guard (discard papers >2 years older than `first_public`) from `resolve_batch` into `resolve()` itself, where it can immediately retry via Europe PMC when an auto-discovered PMID points to an old paper. Previously the guard discarded the stale result but had no recovery path, so the correct EuropePMC paper was never fetched. The guard does not apply when `pubmed_id` is supplied directly by the caller.

- `ena.resolve_taxonomy_name` and `get_lineage`: ENA Taxonomy REST API is tried first; NCBI Entrez (`esearch` + `efetch`) is now used as a fallback when ENA returns an error (currently returning 404 for all requests).
- `airtable._enc`: `None` and empty-string values are now stripped from outgoing payloads before sending to Airtable, preventing 422 `INVALID_VALUE_FOR_COLUMN` errors (e.g. for `pub_year`) when a field has no value to send.
- `publications.fetch_from_pubmed` and `fetch_from_crossref` now return `pub_year` as an `int` (or `None` when unavailable) instead of a string, fixing a 422 `INVALID_VALUE_FOR_COLUMN` error when upserting studies into an Airtable numeric "Year" field.
- `ena._get`: 5xx server errors are now retried with exponential backoff (same behaviour as 429 rate-limit responses), making batch scans resilient to transient ENA server errors.
- `wmw scan`: a failed run-query batch (e.g. ENA 500 error after all retries) no longer aborts the entire scan. The failing batch is warned and skipped; the scan continues with remaining batches and reports all skipped batch numbers at the end.

## [0.3.1] - 2026-05-06

### Changed

- `wmw scan`: batch querying now shows a Rich progress bar (`XX/YY studies · N runs across K studies`) instead of accumulating log lines for each batch, when stdout is a TTY. Falls back to per-batch `INFO:` lines when output is not a TTY.
- `ena.fetch_studies_batch`: reduced default `chunk_size` from 100 to 20 to avoid excessively long ENA API query URLs when scanning large numbers of studies.
- `wmw scan`: the "Excluding N host taxon ID(s)" log message (and its run-removal count) now
  appears after "Excluded N studies from summary table: no host taxon ID in any run", making
  the output order match the logical filtering sequence — blank-host studies first, then
  explicitly excluded hosts.
- `wmw fetch`: host taxon exclusion is now applied as an explicit post-fetch run-level step
  (using `host_tax_id` from sample metadata), separate from and reported before other
  run-level filters (library strategy, platform, min bases). The early "Excluding N host
  taxon ID(s)" upfront message has been replaced by a message printed after runs are fetched.

### Fixed

- `AirtableClient` now respects the `STUDIES_COL_*` and `SAMPLES_COL_*` field IDs from config. When field IDs are configured, the client uses `use_field_ids=True` and translates python field names to Airtable field IDs in all payloads, formulas, and responses — fixing a 422 `UNKNOWN_FIELD_NAME` error caused by sending snake_case python names (e.g. `study_accession`) when the actual Airtable field names differ. Field maps are now derived entirely from `config.yaml` (`STUDIES_COL_*` / `SAMPLES_COL_*` keys): the config is the single source of truth for all Airtable column codes, with no hardcoded mapping tables in the Python code.

### Added

- Early Airtable connectivity check: `wmw scan`, `wmw fetch`, and `wmw process` now verify read access to the relevant Airtable tables immediately on startup (before any ENA queries or other network work), so misconfigured credentials or an unreachable base are reported right away rather than after a long run.


- `wmw scan --year YEAR[,YEAR]` and `--month MONTH[,MONTH]`: shorthand flags for defining
  the search date window without writing full ISO dates. A single value covers that
  year/month; two comma-separated values define start and end (e.g. `--year 2025,2026`,
  `--month March,June`). The flags can be combined — `--year 2025 --month March` resolves to
  `2025-03-01 → 2025-03-31`. When `--month` is used without `--year`, the current calendar
  year is assumed. Month names are full English names, case-insensitive.

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
