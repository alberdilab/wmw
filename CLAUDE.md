# wmw — Wild Microbiome Watch

Discovers wild-animal shotgun metagenome studies in ENA (or GSA), populates Airtable, and drives Drakkar genome-resolved metagenomic workflows.

## Two-phase workflow
1. **`wmw scan`** — queries ENA study endpoint → populates Airtable Studies table (status `new`). `--source gsa` queries GSA instead.
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
| `gsa.py` | GSA (NGDC/CNCB) via its scraped web interface; `build_query()` (PubMed-style grammar), `search_study_accessions()` (scan), `fetch_study_metadata()` (browse + BioProject pages), `search_study()` (run records from the `.xlsx` metadata workbook), `resolve_taxonomy()`, `keyword_matches()`, `to_https()` |
| `metadata.py` | `normalize_ena/sra_run/study()`, `filter_runs()` (host_tax_id, min_bases, library_strategy, library_source, instrument_platform), `deduplicate_runs()`, `studies_from_runs()` |
| `drakkar.py` | Drakkar 2.x bridge; `build_input_tsv()` → sample detail TSV; `generate_*_script()` → bash launch scripts; `parse_*_tsv()` → Airtable fields; AMR: `generate_amr_script()`, `parse_amr_qc_tsv()`, `amr_results_dir()`, `AMR_TABLE_FILES` |
| `publications.py` | `fetch_from_pubmed()`, `fetch_from_crossref()`, `fetch_pdf_url()` (Unpaywall), `resolve_batch()` |
| `transfer.py` | ERDA SFTP via paramiko; `SFTPTransfer` (`upload_stream()`, `upload_gzipped()`, `upload_file()`, `remote_exists()`, `remove_remote_dir()`), `gzip_into()` |

## Airtable schema
**Studies** — `study_accession`, `secondary_study_accession`, `study_title`, `study_description`, `source` (ENA), `scientific_name`, `tax_id`, `first_public`, `center_name`, `status` (`new`→`approved`→`indexed`), `pubmed_id`, `pub_doi`, `pub_url`, `pub_title`, `pub_year`, `pub_journal`, `pub_authors`, `pub_pdf` (Attachment; OA PDF via Unpaywall)
**Samples** — `run_accession`, `study_accession`, `sample_accession`, `experiment_accession`, `scientific_name`, `tax_id`, `instrument_platform`, `instrument_model`, `library_strategy`, `library_source`, `library_layout`, `base_count`, `read_count`, `fastq_ftp`, `fastq_md5`, `fastq_url_1`, `fastq_url_2`, `collection_date`, `first_public`, `geo_loc_name`, `host`, `host_tax_id`, `host_scientific_name`, `country`, `center_name`, `source`, `status`
**AMR (opt-in, all config keys blank by default)** — Studies: `file_amr_{hits,loci,drug_classes,mobility,mobility_regions,manifest}`; Samples: `amr_{amrfinder_hits,rgi_hits,mobility_regions,loci,multi_tool_loci,mobility_links,mobile_loci}` from `amr_qc.tsv`

## Config keys (`src/wmw/data/config.yaml`)
`SOURCE` `GSA_ORGANISM` `WMW_BASE` `STUDIES_TABLE` `SAMPLES_TABLE` `DRAKKAR_CONDA_ENV` `DRAKKAR_OUTPUT_DIR` `NCBI_EMAIL` `NCBI_API_KEY` `LIBRARY_SOURCE` `INSTRUMENT_PLATFORM` `MIN_BASES` `DATE_FIELD` `EXCLUDED_HOST_TAX_IDS` `SFTP_HOST` `SFTP_USER` `SFTP_PORT` `SFTP_IDENTITY` `SFTP_REMOTE_BASE` `SFTP_REMOTE_ASSEMBLY_DIR` `SFTP_REMOTE_BIN_DIR` `SFTP_REMOTE_AMR_DIR` `STUDIES_COL_FILE_AMR_*` `SAMPLES_COL_AMR_*`

## CLI commands
```
wmw scan   [--source ena|gsa] [--from DATE] [--to DATE] [--study ACC]
           [--host-tax-id ID] [--date-field first_public|last_updated]
           [--keyword TEXT] [--gsa-organism NAME] [--include GROUPS]
           [--run-batch N] [--dry-run] [--no-publications]
wmw fetch  [--source ena|gsa] [--status VALUE] [--study ACC]
           [--library-strategy STR] [--library-source STR]
           [--instrument-platform STR] [--min-bases N]
           [--include GROUPS] [--exclude-taxa IDs] [--dry-run] [--debug]
wmw process --batch BATCH [--workflow preprocessing|cataloging|amr|profiling|annotating]
            [--slurm] [--output-dir DIR]
wmw upload-erda --study CODE [--what cataloging|amr|all] [--output-dir DIR]
                [--sftp-host H] [--sftp-user U] [--sftp-identity PATH]
                [--sftp-remote-base PATH] [--sftp-amr-dir NAME]
                [--replace-files] [--verbose]
wmw status  [--batch BATCH]
wmw config  --view | --edit
wmw update
```

## Key patterns
- GSA (`--source gsa`): no JSON API — `gsa.py` scrapes three session-free endpoints (`POST /gsa/search/`, `GET /gsa/browse/<CRA>`, `POST /gsa/file/exportExcelFile`). The `.xlsx` workbook is the run-record source (file names, sizes, MD5s, URLs, host, collection date, geo location); the browse page supplies the release date and the download shard (`gsa`…`gsa5`, not derivable from the accession); the BioProject page supplies description, organism and submitting organization
- GSA queries are pinned to `"NGDC"[center]` and rows matched on `/gsa/browse/<CRA>/<CRX>`, excluding the INSDC mirror GSA also serves
- GSA gaps: no `base_count`/`read_count` (so `MIN_BASES` cannot apply — `fetch` warns), no `tax_tree()` (so `--taxonomy` is ignored), and `title` indexes *experiment* titles — so `--keyword` is applied post-lookup against study title + description
- `gsa.resolve_taxonomy()` turns GSA's host/organism *names* into NCBI tax IDs so `EXCLUDED_HOST_TAX_IDS` filters GSA runs too
- GSA FTP paths are rewritten to `https://download.cncb.ac.cn/` (range-request capable, so resumable); `fastq_ftp` keeps the raw FTP paths
- `_conf(args, "cli_attr", "CONFIG_KEY")` — CLI flag → config → `''`
- `_die(msg)` — error to stderr + `sys.exit(1)`
- Scan filters: study-level (date, organism tax_id, keyword, host taxon exclusions) — applied at query/normalize time
- `--run-batch N` (default 20): when a taxonomy/keyword filter is active, studies are split into batches of N and runs are queried per-batch to avoid the ENA 10 000-record API limit
- Fetch filters: run-level (library_strategy, library_source, instrument_platform, min_bases, host_tax_id exclusions) — applied post-fetch by `metadata.filter_runs()`
- `_build_exclude_ids(args)` — reads `EXCLUDED_HOST_TAX_IDS` dict groups minus any named in `--include`; `--include All` disables all exclusions
- Fields with blank values are never excluded by any filter
- ERDA archive: `_finalize_cataloging_outputs()` sends assemblies (`cataloging/megahit/<a>/<a>.fna` → `<a>_contigs.fasta.gz`) and every bin in `cataloging/final/all_bin_paths.txt` to `{SFTP_REMOTE_BASE}/<code>/{assemblies,bins}/`, gzipped into the SFTP stream, in a detached `<code>-erda-upload` screen session
- Pipeline order: `preprocessing → cataloging → amr → profiling → annotating`. AMR sits after cataloging because `drakkar amr -i <work_dir>` reads `cataloging/megahit/<a>/<a>.fna`, naming each assembly after its folder (= sample code). It is an ordinary stage: same `<code>.sh`, screen, logs, `.wmw-stop`, and `status` (`amr` → `amr_done`)
- AMR archive: `_finalize_amr_outputs()` writes `amr/amr_qc.tsv` counts to Samples, attaches the 5 aggregate tables + manifest to Studies, and sends them study-prefixed to `{SFTP_REMOTE_BASE}/<code>/{SFTP_REMOTE_AMR_DIR}/` inline (`.tsv.xz` as-is, plain summaries gzipped) — small files, so no screen session
- ERDA transfers skip files already present; `replace_existing_attachments` is deliberately **not** propagated to them (it works around Airtable appending on upload). `wmw upload-erda --replace-files` is the explicit re-transfer

## Tests & release
`pytest tests/` (307 tests) · `python scripts/release.py X.Y.Z` (add `--dry-run` first)

## Changelog policy
- Every code change must be logged under the `[Unreleased]` section of `CHANGELOG.md` before the work is considered done.
- Do NOT run `scripts/release.py` or bump the version unless the user explicitly requests a release.

## Docs
`docs/architecture.md` · `docs/schema.md` · `docs/cli.md` · `docs/filters.md` · `docs/release.md`
