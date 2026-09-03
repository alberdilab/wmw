# Filter and Exclusion System

## Overview

Filters are divided between two commands that operate at different levels:

- **`wmw scan`** — study-level filters applied at query time via the ENA study endpoint, plus post-query host taxon exclusions
- **`wmw fetch`** — run-level filters applied post-fetch in Python (`metadata.filter_runs()`)

Both commands share the same `--include` flag for controlling host taxon exclusions.

Under `--source gsa` the same two levels apply, but the query-time half is expressed in GSA's
own grammar and some filters have no GSA equivalent — see [GSA differences](#gsa-differences).

---

## Study-level filters (`wmw scan`)

| Parameter | CLI flag | Config key | Applied as |
|---|---|---|---|
| Date range | `--from` / `--to` | — | `{date_field}>=X AND {date_field}<=Y` at ENA query time |
| Date field | `--date-field` | `DATE_FIELD` | `first_public` or `last_updated` only |
| Organism | `--host-tax-id` | — | `tax_id=N` (approximate; study-level organism) |
| Keyword | `--keyword` | — | `study_title="*fox*"` at ENA query time |
| Host taxon exclusion | `--include` | `EXCLUDED_HOST_TAX_IDS` | drop studies where `tax_id` is in exclusion set |

`collection_date` is a run-level field and is not available in the study index.
`--host-tax-id` at study level matches the ENA `tax_id` field, which for host-associated
metagenomes may represent the host taxon or the metagenome taxon — treat it as an
approximate inclusive filter. Host taxon exclusions (`--include`) use the same `tax_id`
field but remove unwanted groups after normalization.

---

## Run-level filters (`wmw fetch`)

Applied post-fetch by `metadata.filter_runs()` after `ena.search_study()` (or
`gsa.search_study()`) returns all runs for each approved study.

| Parameter | CLI flag | Config key | Applied as |
|---|---|---|---|
| Library strategy | `--library-strategy` | — | match against `library_strategy` field; default WGS,METAGENOMIC |
| Library source | `--library-source` | `LIBRARY_SOURCE` | match against `library_source` field |
| Instrument platform | `--instrument-platform` | `INSTRUMENT_PLATFORM` | match against `instrument_platform` field |
| Minimum base count | `--min-bases` | `MIN_BASES` | `base_count >= N` |
| Host taxon exclusion | `--include` | `EXCLUDED_HOST_TAX_IDS` | exclude runs where `host_tax_id` is in exclusion set |
| Extra exclusions | `--exclude-taxa` | — | additional taxon IDs appended to the exclusion set |

**Unknown fields are never excluded.** Runs where `library_strategy`, `library_source`,
`instrument_platform`, `host_tax_id`, or `base_count` is blank are kept regardless of
the filter settings — unknown ≠ excluded.

---

## Host taxon exclusion system

### Config groups (`EXCLUDED_HOST_TAX_IDS`)

Defined in `src/wmw/data/config.yaml` as a YAML mapping of named groups. All groups are
active by default; use `--include` at runtime to re-enable specific groups.

| Group | Taxa (28 total) |
|---|---|
| `Human` | Homo sapiens (9606) |
| `Livestock` | cattle, pig, sheep, goat, horse, water buffalo, camel, llama, alpaca, chicken, turkey, duck |
| `Aquaculture` | Atlantic salmon, rainbow trout, Nile tilapia, common carp, European sea bass, gilthead sea bream |
| `Laboratory` | mouse, rat, rabbit, guinea pig, golden hamster, Mongolian gerbil, rhesus macaque, crab-eating macaque, zebrafish |

### `--include` flag

The `--include` flag accepts a comma-separated list of group names and removes those groups
from the active exclusion set. Use `All` to disable exclusions entirely.

```bash
wmw scan --from 2024-01-01 --to 2024-12-31           # excludes all 4 groups (default)
wmw scan --from 2024-01-01 --to 2024-12-31 \
         --include Human                              # Human samples kept; others excluded
wmw scan --from 2024-01-01 --to 2024-12-31 \
         --include Human,Livestock                    # Human + Livestock kept; others excluded
wmw scan --from 2024-01-01 --to 2024-12-31 \
         --include All                                # no host taxon exclusions

wmw fetch --include Human                             # same logic at run level
wmw fetch --include All                               # disable all run-level exclusions
wmw fetch --exclude-taxa 9615,9685                    # also exclude dogs + cats
wmw fetch --include Human --exclude-taxa 9615,9685    # combine: keep Human, also exclude dogs+cats
```

### Implementation

`_build_exclude_ids(args)` in `cli.py` constructs the exclusion list:

```python
# --include All  →  empty list (no exclusions)
# --include Human,Livestock  →  only Aquaculture + Laboratory IDs
# (default)  →  all group IDs

config_groups = cfg.get("EXCLUDED_HOST_TAX_IDS")   # dict of {group: [ids]}
included = {g.strip().title() for g in args.include.split(",")}
exclude_ids = [
    tid
    for group, ids in config_groups.items()
    if group not in included
    for tid in ids
]
```

For `wmw scan`, exclusions are applied to the `tax_id` field of normalized study records
after deduplication. For `wmw fetch`, exclusions are passed to `metadata.filter_runs()`
which checks the `host_tax_id` field on each run.

Under `--source gsa`, `gsa.resolve_taxonomy()` runs first and fills `host_tax_id` from the
host *name* GSA supplies, so the exclusion set works unchanged. A host name that cannot be
resolved leaves the field blank, and blank fields are never excluded.

### GSA differences

| Filter | Under `--source gsa` |
|---|---|
| Date range | Matched against GSA's release date. `--date-field` has no effect — GSA indexes only the release date. |
| Organism | `--gsa-organism` (config `GSA_ORGANISM`, default `organismal metagenomes`) replaces `--host-tax-id` at scan time; GSA's search has no host-taxonomy field. |
| Keyword | Applied **after** study lookup, against study title and description. GSA's `title` field indexes experiment titles (per-sample aliases), not study titles. |
| Taxonomy subtree | `--taxonomy` is ignored with a warning — GSA has no `tax_tree()` equivalent. |
| Library strategy / source / platform | Applied in the GSA query and again post-fetch, as for ENA. |
| Minimum base count | **Not applicable.** GSA publishes no base counts, so `base_count` is always blank and — by the unknown-≠-excluded rule — `--min-bases` would silently keep every run. `wmw fetch` warns instead. |
| Host taxon exclusion | Works as for ENA, after `gsa.resolve_taxonomy()` fills `host_tax_id` from the host name. |

Every GSA query also carries an implicit `"NGDC"[center]` clause, which excludes the INSDC
records GSA mirrors from SRA so ENA-sourced studies are not fetched twice.

### Adding or removing taxa

Edit `src/wmw/data/config.yaml` directly (or via `wmw config --edit`). Add new taxon IDs
under the appropriate group key, or create a new group key to keep things organized.
