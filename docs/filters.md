# Filter and Exclusion System

## Overview

Filters operate at two layers: **query-time** (sent to ENA/SRA APIs) and **post-fetch**
(applied to normalised records in Python). ENA supports most filters natively; SRA has gaps.

## Filter parameters

| Parameter | CLI flag | Config key | ENA | SRA | Notes |
|---|---|---|---|---|---|
| Date range | `--from` / `--to` | — | `{date_field}>=X AND {date_field}<=Y` | `("X"[PDAT] : "Y"[PDAT])` | |
| Date field | `--date-field` | `DATE_FIELD` | `first_public`, `collection_date`, `last_updated` | always PDAT | SRA ignores date_field |
| Library strategy | `--library-strategy` | — | `library_strategy="WGS" OR ...` | `"WGS"[Strategy] OR ...` | default: WGS,METAGENOMIC |
| Library source | `--library-source` | `LIBRARY_SOURCE` | `library_source="METAGENOMIC" OR ...` | `"METAGENOMIC"[Source] OR ...` | |
| Instrument platform | `--instrument-platform` | `INSTRUMENT_PLATFORM` | `instrument_platform="ILLUMINA"` | `"illumina"[Platform]` | |
| Host taxon inclusion | `--host-tax-id` | — | `host_tax_id=7742` | `txid7742[Organism:exp]` | ENA: host field; SRA: metagenome organism |
| Minimum base count | `--min-bases` | `MIN_BASES` | `base_count>=N` | post-fetch only | Entrez has no base-count index |
| Keyword | `--keyword` | — | `study_title="*fox*"` | `"fox"[Title]` | |
| Host taxon exclusion | `--exclude-taxa` + config | `EXCLUDED_HOST_TAX_IDS` | `NOT host_tax_id=X` per ID | `NOT txidX[Organism:exp]` per ID | see below |

## Exclusion system in detail

### Config default list (`EXCLUDED_HOST_TAX_IDS`)
Defined in `src/wmw/data/config.yaml` as a YAML list of strings. Covers 28 taxa:
- Human (9606)
- Livestock: cattle, pig, sheep, goat, horse, water buffalo, camel, llama, alpaca
- Poultry: chicken, turkey, duck
- Aquaculture: Atlantic salmon, rainbow trout, Nile tilapia, common carp, sea bass, sea bream
- Lab animals: mouse, rat, rabbit, guinea pig, golden hamster, Mongolian gerbil, rhesus macaque, crab-eating macaque, zebrafish

### Runtime merging
```python
# In _resolve_scan_params():
config_list = cfg.get("EXCLUDED_HOST_TAX_IDS") or []   # from config.yaml
cli_extra   = args.exclude_taxa or ""                   # --exclude-taxa 9615,9685
# merged, deduplicated, cleared entirely by --no-exclude
```

### Two-layer application

**Layer 1 — query time (ENA):**
```
NOT host_tax_id=9606 AND NOT host_tax_id=9913 AND ...
```
Server-side; most efficient; only works when ENA has `host_tax_id` populated.

**Layer 1 — query time (SRA):**
```
NOT txid9606[Organism:exp] AND NOT txid9913[Organism:exp] AND ...
```
Filters on the organism taxonomy tree, **not** the host. SRA does not index host_tax_id
as a searchable field; host information lives in BioSample attributes.

**Layer 2 — post-fetch (`metadata.filter_runs()`):**
Applied to normalised records after both databases are queried and merged.
Checks `host_tax_id` on each record — catches:
- ENA records where `host_tax_id` was blank in the portal index but populated in the record
- Any SRA records that happen to carry `host_tax_id` in their XML

Runs with `host_tax_id = ""` are **never** excluded (unknown host ≠ excluded host).
Runs with `base_count = ""` are **never** excluded by `min_bases` (unknown ≠ too small).

### Adding or removing taxa from the default list
Edit `src/wmw/data/config.yaml` directly (or via `wmw config --edit`).
Comment out a line to re-enable that taxon; add new lines as `- "TAXON_ID"`.

### Per-run overrides
```bash
wmw scan --from 2024-01-01 --to 2024-12-31 --exclude-taxa 9615,9685  # also exclude dogs+cats
wmw scan --from 2024-01-01 --to 2024-12-31 --no-exclude              # no exclusions at all
```
