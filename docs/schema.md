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
| `geo_loc_name` | text | GSA | Free-text location; ENA has no such run-level field, so it is empty for ENA records |
| `host` | text | ENA + GSA | Host common name, e.g. "red fox"; a Latin name for GSA |
| `host_tax_id` | text | ENA + GSA | NCBI taxon ID of the host; resolved from the host name for GSA, always `""` for SRA |
| `host_scientific_name` | text | ENA + GSA | Host Latin name |
| `host_sex` | text | ENA + GSA | Physical sex of the host; config `SAMPLES_COL_HOST_SEX` |
| `country` | text | ENA + GSA | MIxS *geographic location (country and/or sea)* — a country name, ocean or sea, optionally followed by a region after `:`. GSA takes the part before `:` |
| `lat` | number | ENA + GSA | MIxS *geographic location (latitude)*, decimal degrees; config `SAMPLES_COL_LAT` |
| `lon` | number | ENA + GSA | MIxS *geographic location (longitude)*, decimal degrees; config `SAMPLES_COL_LON` |
| `broad_scale_environmental_context` | text | ENA + GSA | MIxS *broad-scale environmental context*, usually an EnvO biome term; config `SAMPLES_COL_BROAD_SCALE_ENVIRONMENTAL_CONTEXT` |
| `environmental_medium` | text | ENA + GSA | MIxS *environmental medium* — the material surrounding the sample at collection; config `SAMPLES_COL_ENVIRONMENTAL_MEDIUM` |
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

## BioSample metadata

ENA joins each registered sample's attributes onto every run record it returns, so wmw
reads the MIxS fields from the same `read_run` call that supplies the FASTQ paths — no
separate BioSample lookup, and no extra request per sample.

The MIxS v5 column names are used. `broad_scale_environmental_context` and
`environmental_medium` are what ENA resolves the older v4 `environment_biome` and
`environment_material` names to, so a record registered under either checklist version
comes back the same way.

Five of these columns ship **opt-in**, the way the AMR columns do: `SAMPLES_COL_LAT`,
`SAMPLES_COL_LON`, `SAMPLES_COL_HOST_SEX`,
`SAMPLES_COL_BROAD_SCALE_ENVIRONMENTAL_CONTEXT` and
`SAMPLES_COL_ENVIRONMENTAL_MEDIUM` are blank in the shipped config. While a key is blank
wmw never sends that column, so a base without the field keeps working — one unknown field
name makes Airtable reject the entire batch. Add the column in Airtable (`LAT`/`LON` as
Number with 6 decimal places, the rest as single line text), paste its field ID into the
config, and the writes begin.

`collection_date` and `country` were already fetched and are unchanged.

### Backfilling existing rows

`wmw fetch` inserts new runs and skips run accessions already in the table, so rows written
before these columns existed keep their blanks. Two flags fill them in.

`--fill-missing` writes every column wmw can supply from the archive record, but only into
cells that are currently **empty** — a value already in the base, including a curator's
correction, is never overwritten, and `status` is never written at all:

```bash
wmw fetch --fill-missing --status indexed
wmw fetch --fill-missing --status indexed --dry-run   # count the cells first
```

This is the flag for "rows created before the latest update": it is not limited to the
BioSample fields, so it also picks up any other column that was added later. A cell holding
`0` (or a latitude of `0.0`) counts as filled, not empty.

`--refresh-metadata` instead **rewrites** exactly the BioSample fields — `collection_date`, `geo_loc_name`, `country`,
`lat`, `lon`, `broad_scale_environmental_context`, `environmental_medium`, `host`,
`host_tax_id`, `host_scientific_name`, `host_sex` — on rows that already exist:

```bash
wmw fetch --refresh-metadata --status indexed
```

Accessions, FASTQ paths, batch assignment, status and every Drakkar result column are left
untouched, so a refresh is safe to run against studies that are already processed — but
unlike `--fill-missing` it replaces BioSample values that are already there. Use it when
the archive record is the one you trust.

`collection_date` still only accepts a full `YYYY-MM-DD` value. Submitters also register
year-only (`2019`) and year-month (`2019-06`) dates, and those are dropped rather than
written to what is a date-typed Airtable column.

## Status lifecycle

**Studies:** `new` → (manual review) → `approved` → `indexed` → `ready` → `preprocessing` → `preprocessed` → `cataloging` → `cataloged` → `amring` → `amred`. Failed or externally cancelled runs use `error` or `stopped`.

**Samples:** `use` rows are included in Drakkar input TSVs. `pending` and `ignore` rows are excluded.

## Notes on SRA gaps

SRA records arrive with `host_tax_id = ""`, `geo_loc_name = ""`, `host = ""`, `country = ""`,
`collection_date = ""`, `fastq_md5 = ""`, and every BioSample attribute blank
(`lat`, `lon`, `host_sex`, `broad_scale_environmental_context`, `environmental_medium`).
These fields require separate BioSample lookups not currently implemented. ENA is the
preferred source for host-annotated records.

## Notes on GSA gaps

GSA (`--source gsa`) supplies host, collection date, geographic location and MD5 checksums
through its metadata workbook, so it is comparable to ENA on host annotation. Two fields
have no GSA equivalent:

- `base_count` and `read_count` are always empty — GSA does not compute them. `MIN_BASES`
  therefore cannot exclude a GSA run, and `wmw fetch` warns when the filter is set.
- `tax_id` and `host_tax_id` are not published; they are resolved from the organism and host
  *names* through the NCBI/ENA taxonomy service at fetch time. A name that will not resolve
  leaves the ID blank, and a blank field is never excluded by a filter.

GSA's BioSample attributes come from the same metadata workbook, but the Sample sheet
follows whichever sample type the submitter chose, so the columns vary between studies.
`lat`/`lon` are parsed from a single `Latitude longitude` cell (`"26.075 N 119.297 E"`,
also accepted signed or comma-separated), and host sex, broad-scale environmental context
and environmental medium are looked up case-insensitively across the few headings the GSA
templates use. A study whose template omits an attribute leaves it blank, as does GSA's
`NA` placeholder.

Accessions differ in shape: `study_accession` holds the GSA study (`CRA…`) and
`secondary_study_accession` the NGDC BioProject (`PRJCA…`), mirroring the ENA PRJEB/ERP
pairing. Runs are `CRR…`, experiments `CRX…` and samples `SAMC…`.
