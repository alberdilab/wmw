# Airtable Schema

Two tables: **Studies** (one row per ENA/SRA project) and **Samples** (one row per run,
linked to Studies by `study_accession`).

## Studies table

| Field | Type | Source | Notes |
|---|---|---|---|
| `study_accession` | text | ENA + SRA + GSA | Primary key; PRJEB…, PRJNA… or CRA… |
| `secondary_study_accession` | text | ENA + GSA | ERP… accession; the PRJCA… BioProject for GSA |
| `study_title` | text | ENA + SRA | |
| `study_description` | text | ENA + GSA | Full abstract; from the BioProject record for GSA |
| `source` | text | derived | `"ENA"`, `"SRA"` or `"GSA"` |
| `scientific_name` | text | ENA + SRA | Organism of the metagenome |
| `tax_id` | text | ENA + SRA | NCBI taxon ID of the metagenome |
| `first_public` | text | ENA + SRA | ISO date |
| `center_name` | text | ENA + SRA | Submitting institution |
| `status` | text | derived | Default `"new"`; processing states include `ready`, `preprocessing`, `preprocessed`, `cataloging`, `cataloged`, `error`, and `stopped` |
| `pubmed_id` | text | ENA | From ENA study record; used to seed PubMed lookup |
| `pub_doi` | text | PubMed / CrossRef | |
| `pub_url` | text | resolved | `https://doi.org/{doi}` or PubMed URL |
| `pub_title` | text | PubMed / CrossRef | |
| `pub_year` | text | PubMed / CrossRef | 4-digit string |
| `pub_journal` | text | PubMed / CrossRef | Full journal name |
| `pub_authors` | text | PubMed / CrossRef | Up to 5 names, then "et al." |
| `pub_pdf` | attachment | Unpaywall | OA PDF; Airtable Attachment field; set only when an open-access PDF is found |
| `file_amr_hits` | attachment | drakkar amr | `{code}_amr_hits.tsv.xz`; config `STUDIES_COL_FILE_AMR_HITS` |
| `file_amr_loci` | attachment | drakkar amr | `{code}_amr_loci.tsv.xz`; config `STUDIES_COL_FILE_AMR_LOCI` |
| `file_amr_drug_classes` | attachment | drakkar amr | `{code}_amr_drug_classes.tsv.xz`; config `STUDIES_COL_FILE_AMR_DRUG_CLASSES` |
| `file_amr_mobility` | attachment | drakkar amr | `{code}_amr_mobility.tsv.xz`; config `STUDIES_COL_FILE_AMR_MOBILITY` |
| `file_amr_mobility_regions` | attachment | drakkar amr | `{code}_mobility_regions.tsv.xz`; config `STUDIES_COL_FILE_AMR_MOBILITY_REGIONS` |
| `file_amr_manifest` | attachment | drakkar amr | `{code}_amr_manifest.yaml` provenance record; config `STUDIES_COL_FILE_AMR_MANIFEST` |

Every AMR field above is blank in the shipped config, which disables that write.
Fill in the Airtable field ID to switch it on. A table over Airtable's 5 MB
encoded attachment limit is reported and left on ERDA only.

## Samples table

| Field | Type | Source | Notes |
|---|---|---|---|
| `run_accession` | text | ENA + SRA | Primary key; ERR… or SRR… |
| `study_accession` | text | ENA + SRA | Foreign key → Studies |
| `sample_accession` | text | ENA + SRA | SAMEA… or SAMN… |
| `experiment_accession` | text | ENA + SRA | ERX… or SRX… |
| `scientific_name` | text | ENA + SRA | e.g. "gut metagenome" |
| `tax_id` | text | ENA + SRA | NCBI taxon ID of the metagenome |
| `instrument_platform` | text | ENA + SRA | e.g. `ILLUMINA` |
| `instrument_model` | text | ENA + SRA | e.g. `Illumina NovaSeq 6000` |
| `library_strategy` | text | ENA + SRA | `WGS`, `METAGENOMIC`, … |
| `library_source` | text | ENA + SRA | `METAGENOMIC`, `METATRANSCRIPTOMIC`, … |
| `library_layout` | text | ENA + SRA | `PAIRED` or `SINGLE` |
| `base_count` | text | ENA + SRA | Total bases; string (empty for SRA and always empty for GSA) |
| `read_count` | text | ENA + SRA | Total reads (always empty for GSA) |
| `fastq_ftp` | text | ENA + SRA | Raw semicolon-delimited FTP string |
| `fastq_md5` | text | ENA + GSA | Semicolon-delimited MD5s |
| `fastq_url_1` | text | derived | Parsed R1 URL; `ftp://` for ENA/SRA, `https://download.cncb.ac.cn/…` for GSA |
| `fastq_url_2` | text | derived | Parsed R2 URL; empty for single-end |
| `collection_date` | text | ENA + GSA | ISO date sample was collected |
| `first_public` | text | ENA + SRA | ISO date run was made public |
| `geo_loc_name` | text | ENA + GSA | Free-text location |
| `host` | text | ENA + GSA | Host common name, e.g. "red fox"; a Latin name for GSA |
| `host_tax_id` | text | ENA + GSA | NCBI taxon ID of the host; resolved from the host name for GSA, always `""` for SRA |
| `host_scientific_name` | text | ENA + GSA | Host Latin name |
| `country` | text | ENA + GSA | Standardised country name; the part before `:` in the GSA location |
| `center_name` | text | ENA + SRA | Submitting institution |
| `source` | text | derived | `"ENA"`, `"SRA"` or `"GSA"` |
| `status` | text | derived | Default `"pending"`; user-controlled processing inclusion uses `use`, `pending`, or `ignore` |
| `amr_amrfinder_hits` | number | drakkar amr | From `amr/amr_qc.tsv`; config `SAMPLES_COL_AMR_AMRFINDER_HITS` |
| `amr_rgi_hits` | number | drakkar amr | config `SAMPLES_COL_AMR_RGI_HITS` |
| `amr_mobility_regions` | number | drakkar amr | config `SAMPLES_COL_AMR_MOBILITY_REGIONS` |
| `amr_loci` | number | drakkar amr | config `SAMPLES_COL_AMR_LOCI` |
| `amr_multi_tool_loci` | number | drakkar amr | Loci backed by both AMRFinderPlus and RGI; config `SAMPLES_COL_AMR_MULTI_TOOL_LOCI` |
| `amr_mobility_links` | number | drakkar amr | config `SAMPLES_COL_AMR_MOBILITY_LINKS` |
| `amr_mobile_loci` | number | drakkar amr | config `SAMPLES_COL_AMR_MOBILE_LOCI` |

AMR rows are matched by the `amr_qc.tsv` `assembly_id` column against the sample
`code`, the same way cataloging assembly stats are. The two
`*_without_coordinates` columns of `amr_qc.tsv` are caller diagnostics rather
than results and are never written. Field names above are illustrative — only
the config key matters, and each ships blank so the write is opt-in.

## Status lifecycle

**Studies:** `new` → (manual review) → `approved` → `indexed` → `ready` → `preprocessing` → `preprocessed` → `cataloging` → `cataloged` → `amr` → `amr_done`. Failed or externally cancelled runs use `error` or `stopped`.

**Samples:** `use` rows are included in Drakkar input TSVs. `pending` and `ignore` rows are excluded.

## Notes on SRA gaps

SRA records arrive with `host_tax_id = ""`, `geo_loc_name = ""`, `host = ""`, `country = ""`,
`collection_date = ""`, `fastq_md5 = ""`. These fields require separate BioSample lookups
not currently implemented. ENA is the preferred source for host-annotated records.

## Notes on GSA gaps

GSA (`--source gsa`) supplies host, collection date, geographic location and MD5 checksums
through its metadata workbook, so it is comparable to ENA on host annotation. Two fields
have no GSA equivalent:

- `base_count` and `read_count` are always empty — GSA does not compute them. `MIN_BASES`
  therefore cannot exclude a GSA run, and `wmw fetch` warns when the filter is set.
- `tax_id` and `host_tax_id` are not published; they are resolved from the organism and host
  *names* through the NCBI/ENA taxonomy service at fetch time. A name that will not resolve
  leaves the ID blank, and a blank field is never excluded by a filter.

Accessions differ in shape: `study_accession` holds the GSA study (`CRA…`) and
`secondary_study_accession` the NGDC BioProject (`PRJCA…`), mirroring the ENA PRJEB/ERP
pairing. Runs are `CRR…`, experiments `CRX…` and samples `SAMC…`.
