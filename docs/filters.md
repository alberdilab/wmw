# Filter and Exclusion System

## Overview

Filters are divided between two commands that operate at different levels:

- **`wmw scan`** — study-level filters applied at query time via the ENA study endpoint
- **`wmw fetch`** — run-level filters applied post-fetch in Python (`metadata.filter_runs()`)

## Study-level filters (`wmw scan`)

| Parameter | CLI flag | Config key | ENA study endpoint |
|---|---|---|---|
| Date range | `--from` / `--to` | — | `{date_field}>=X AND {date_field}<=Y` |
| Date field | `--date-field` | `DATE_FIELD` | `first_public` or `last_updated` only |
| Organism | `--host-tax-id` | — | `tax_id=N` (approximate; study-level organism) |
| Keyword | `--keyword` | — | `study_title="*fox*"` |

`collection_date` is a run-level field and is not available in the study index.
`--host-tax-id` at study level matches the ENA `tax_id` field, which for host-associated
metagenomes may represent the host taxon or the metagenome taxon — treat it as an
approximate filter.

## Run-level filters (`wmw fetch`)

Applied post-fetch by `metadata.filter_runs()` after `ena.search_study()` returns all runs
for each approved study.

| Parameter | CLI flag | Config key | Applied as |
|---|---|---|---|
| Library strategy | `--library-strategy` | — | match against `library_strategy` field; default WGS,METAGENOMIC |
| Library source | `--library-source` | `LIBRARY_SOURCE` | match against `library_source` field |
| Instrument platform | `--instrument-platform` | `INSTRUMENT_PLATFORM` | match against `instrument_platform` field |
| Minimum base count | `--min-bases` | `MIN_BASES` | `base_count >= N` |
| Host taxon exclusion | `--exclude-taxa` + config | `EXCLUDED_HOST_TAX_IDS` | exclude runs where `host_tax_id` is in exclusion set |
| Disable exclusions | `--no-exclude` | — | clears the exclusion set entirely |

**Unknown fields are never excluded.** Runs where `library_strategy`, `library_source`,
`instrument_platform`, `host_tax_id`, or `base_count` is blank are kept regardless of
the filter settings — unknown ≠ excluded.

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
# In _resolve_fetch_params():
config_list = cfg.get("EXCLUDED_HOST_TAX_IDS") or []   # from config.yaml
cli_extra   = args.exclude_taxa or ""                   # --exclude-taxa 9615,9685
# merged, deduplicated, cleared entirely by --no-exclude
```

### Application layer

`wmw fetch` applies exclusions post-fetch in `metadata.filter_runs()`:

```
NOT host_tax_id in {9606, 9913, ...}
```

Checks `host_tax_id` on each normalized run record — this is the only reliable layer
because `ena.search_study()` fetches all runs for a study without query-time filtering.

### Adding or removing taxa from the default list
Edit `src/wmw/data/config.yaml` directly (or via `wmw config --edit`).
Comment out a line to re-enable that taxon; add new lines as `- "TAXON_ID"`.

### Per-run overrides
```bash
wmw fetch --exclude-taxa 9615,9685  # also exclude dogs+cats
wmw fetch --no-exclude              # no exclusions at all
```
