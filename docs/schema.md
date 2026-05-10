# Airtable Schema

Two tables: **Studies** (one row per ENA/SRA project) and **Samples** (one row per run,
linked to Studies by `study_accession`).

## Studies table

| Field | Type | Source | Notes |
|---|---|---|---|
| `study_accession` | text | ENA + SRA | Primary key; PRJEB… or PRJNA… |
| `secondary_study_accession` | text | ENA only | ERP… accession |
| `study_title` | text | ENA + SRA | |
| `study_description` | text | ENA only | Full abstract |
| `source` | text | derived | `"ENA"` or `"SRA"` |
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
| `base_count` | text | ENA + SRA | Total bases; string (can be empty for SRA) |
| `read_count` | text | ENA + SRA | Total reads |
| `fastq_ftp` | text | ENA + SRA | Raw semicolon-delimited FTP string |
| `fastq_md5` | text | ENA only | Semicolon-delimited MD5s |
| `fastq_url_1` | text | derived | Parsed R1 URL with `ftp://` prefix |
| `fastq_url_2` | text | derived | Parsed R2 URL; empty for single-end |
| `collection_date` | text | ENA only | ISO date sample was collected |
| `first_public` | text | ENA + SRA | ISO date run was made public |
| `geo_loc_name` | text | ENA only | Free-text location |
| `host` | text | ENA only | Host common name, e.g. "red fox" |
| `host_tax_id` | text | ENA only | NCBI taxon ID of the host; always `""` for SRA |
| `host_scientific_name` | text | ENA only | Host Latin name |
| `country` | text | ENA only | Standardised country name |
| `center_name` | text | ENA + SRA | Submitting institution |
| `source` | text | derived | `"ENA"` or `"SRA"` |
| `status` | text | derived | Default `"pending"`; user-controlled processing inclusion uses `use`, `pending`, or `ignore` |

## Status lifecycle

**Studies:** `new` → (manual review) → `approved` → `indexed` → `ready` → `preprocessing` → `preprocessed` → `cataloging` → `cataloged`. Failed or externally cancelled runs use `error` or `stopped`.

**Samples:** `use` rows are included in Drakkar input TSVs. `pending` and `ignore` rows are excluded.

## Notes on SRA gaps

SRA records arrive with `host_tax_id = ""`, `geo_loc_name = ""`, `host = ""`, `country = ""`,
`collection_date = ""`, `fastq_md5 = ""`. These fields require separate BioSample lookups
not currently implemented. ENA is the preferred source for host-annotated records.
