"""Command-line interface for wmw — Wild Microbiome Watch."""

from __future__ import annotations

import argparse
import calendar
import os
import shlex
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

try:
    from rich_argparse import RawDescriptionRichHelpFormatter as _RichFmt
    from rich.style import Style
    _RichFmt.styles.update(  # type: ignore[attr-defined]
        {
            "argparse.prog":    Style(bold=True, color="#7fb069"),
            "argparse.groups":  Style(bold=True, color="#5f9ea0"),
            "argparse.help":    Style(color="#e6edf3"),
            "argparse.metavar": Style(color="#b7c7d3"),
            "argparse.syntax":  Style(color="#7fb069"),
            "argparse.args":    Style(color="#5f9ea0"),
        }
    )
    _RICH_ARGPARSE = True
except ImportError:
    _RichFmt = argparse.RawDescriptionHelpFormatter  # type: ignore[misc,assignment]
    _RICH_ARGPARSE = False

from wmw import __version__
from wmw import config as cfg
from wmw import output as out

_GENOME_UPLOAD_SCREEN_SUFFIX = "-genome-upload"
_GENOME_MIN_COMPLETENESS = 50.0
_GENOME_MAX_CONTAMINATION = 10.0
_LOW_PRIORITY_SLURM_PARTITION = "lazyqueue"
_LOW_PRIORITY_SLURM_QOS = "lazy"


class _WmwParser(argparse.ArgumentParser):
    """argparse.ArgumentParser with wmw-themed Rich help and raw-description formatting."""

    def __init__(self, *args, formatter_class: type = _RichFmt, **kwargs) -> None:
        super().__init__(*args, formatter_class=formatter_class, **kwargs)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _die(msg: str) -> None:
    out.error(msg)
    sys.exit(1)


def _pl(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _conf(args: argparse.Namespace, cli_attr: str, config_key: str, required: bool = False) -> str:
    """Return first non-empty value from: CLI flag → config file → ''."""
    value = (getattr(args, cli_attr, None) or "").strip()
    if not value:
        value = str(cfg.get(config_key) or "").strip()
    if required and not value:
        flag = "--" + cli_attr.replace("_", "-")
        _die(
            f"{flag} is not set. "
            f"Provide it as a flag or set {config_key} in the config (wmw config --edit)."
        )
    return value


def _resolve_token(args: argparse.Namespace) -> str:
    token = (getattr(args, "airtable_token", None) or "").strip()
    if not token:
        token = os.environ.get("AIRTABLE_TOKEN", "").strip()
    if not token:
        _die(
            "Airtable token not found. "
            "Provide --airtable-token or export AIRTABLE_TOKEN."
        )
    return token


def _airtable_client(args: argparse.Namespace):
    from wmw.airtable import AirtableClient
    token = _resolve_token(args)
    base_id = _conf(args, "base_id", "WMW_BASE", required=True)
    studies_fm = _field_map_from_config("STUDIES_COL_")
    samples_fm = _field_map_from_config("SAMPLES_COL_")
    return AirtableClient(
        token,
        base_id,
        studies_field_map=studies_fm or None,
        samples_field_map=samples_fm or None,
    )


def _require_airtable(args: argparse.Namespace, *table_names: str):
    """Create AirtableClient, verify read access to each table, and return the client."""
    client = _airtable_client(args)
    try:
        client.check_access(list(table_names))
    except RuntimeError as exc:
        _die(str(exc))
    return client


_EXCLUDE_GROUPS = {"Human", "Livestock", "Aquaculture", "Laboratory"}


def _field_map_from_config(prefix: str) -> dict[str, str]:
    """Return {python_snake_name: field_id} from all config keys starting with *prefix*.

    The python name is the key suffix lowercased, e.g.
    STUDIES_COL_STUDY_ACCESSION → study_accession.
    Only entries with a non-empty value are included.
    """
    return {
        key[len(prefix):].lower(): str(value).strip()
        for key, value in cfg.load_config().items()
        if key.startswith(prefix) and isinstance(value, str) and value.strip()
    }


def _build_exclude_ids(args: argparse.Namespace) -> list[str]:
    """Return host tax IDs to exclude, honouring --include group names."""
    include_arg = (getattr(args, "include", None) or "").strip()
    if include_arg.lower() == "all":
        return []

    included_groups = {g.strip().title() for g in include_arg.split(",") if g.strip()}

    config_val = cfg.get("EXCLUDED_HOST_TAX_IDS") or {}
    exclude_ids: list[str] = []

    if isinstance(config_val, dict):
        for group, ids in config_val.items():
            if group in included_groups:
                continue
            for tid in (ids or []):
                tid = str(tid).strip()
                if tid and tid not in exclude_ids:
                    exclude_ids.append(tid)
    else:
        # Legacy flat-list fallback
        for tid in config_val:
            tid = str(tid).strip()
            if tid and tid not in exclude_ids:
                exclude_ids.append(tid)

    return exclude_ids


_MONTH_NAMES: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_month_name(s: str) -> int:
    key = s.strip().lower()
    if key not in _MONTH_NAMES:
        _die(f"--month: unrecognised month name {s!r}. Use full names, e.g. January, March.")
    return _MONTH_NAMES[key]


def _resolve_scan_dates(args: argparse.Namespace) -> None:
    """Populate args.date_from / args.date_to from --year / --month if provided."""
    year_str = (getattr(args, "year", "") or "").strip()
    month_str = (getattr(args, "month", "") or "").strip()
    if not year_str and not month_str:
        return

    today = date.today()

    if year_str:
        parts = [p.strip() for p in year_str.split(",") if p.strip()]
        if len(parts) not in (1, 2):
            _die(f"--year: provide one or two comma-separated years, got {year_str!r}.")
        try:
            y_start = int(parts[0])
            y_end = int(parts[-1])
        except ValueError:
            _die(f"--year: expected integer year(s), got {year_str!r}.")
    else:
        y_start = y_end = today.year

    if month_str:
        parts = [p.strip() for p in month_str.split(",") if p.strip()]
        if len(parts) not in (1, 2):
            _die(f"--month: provide one or two comma-separated month names, got {month_str!r}.")
        m_start = _parse_month_name(parts[0])
        m_end = _parse_month_name(parts[-1])
    else:
        m_start, m_end = 1, 12

    last_day = calendar.monthrange(y_end, m_end)[1]
    args.date_from = f"{y_start:04d}-{m_start:02d}-01"
    args.date_to = f"{y_end:04d}-{m_end:02d}-{last_day:02d}"


# ---------------------------------------------------------------------------
# wmw scan  (phase 1 — study discovery, ENA only)
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> int:
    from wmw import ena, metadata
    ena.DEBUG = getattr(args, "debug", False)
    _resolve_scan_dates(args)

    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    dry_run = args.dry_run
    date_field = _conf(args, "date_field", "DATE_FIELD") or "first_public"
    host_tax_id = (args.host_tax_id or "").strip()
    # --keyword replaces SCAN_KEYWORDS config default; empty CLI arg falls back to config
    keyword = (getattr(args, "keyword", "") or "").strip()
    if not keyword:
        keyword = str(cfg.get("SCAN_KEYWORDS") or "").strip()
    taxonomy = (getattr(args, "taxonomy", "") or "").strip()
    library_strategy_str = (
        (getattr(args, "library_strategy", "") or "").strip()
        or _conf(args, "library_strategy", "LIBRARY_STRATEGY")
        or "WGS,METAGENOMIC"
    )
    library_source_str = _conf(args, "library_source", "LIBRARY_SOURCE") or ""

    from wmw.ena import VALID_DATE_FIELDS
    if date_field not in VALID_DATE_FIELDS:
        _die(
            f"--date-field for wmw scan must be one of "
            f"{sorted(VALID_DATE_FIELDS)}, got: {date_field!r}."
        )

    # Resolve --taxonomy name → tax_id early so we can report it and fail fast
    taxonomy_tax_id = ""
    taxonomy_sci_name = ""
    if taxonomy:
        out.info(f"Resolving taxonomy name: {taxonomy!r}…")
        try:
            taxonomy_tax_id, taxonomy_sci_name = ena.resolve_taxonomy_name(taxonomy)
        except Exception as exc:
            _die(f"Could not resolve taxonomy {taxonomy!r}: {exc}")

    # Verify Airtable access before starting any ENA work
    client = _require_airtable(args, studies_table) if not dry_run else None

    # --- single-study mode ---
    if args.study:
        return _scan_single_study(args, args.study, studies_table, dry_run, client=client)

    # --- date-range mode ---
    if not args.date_from or not args.date_to:
        _die("Provide --from and --to dates, or a --study accession.")

    exclude_ids = _build_exclude_ids(args)

    out.section("WMW SCAN")
    out.info(f"Date range: {args.date_from} → {args.date_to}  (field: {date_field})")
    out.info(f"Library strategy: {library_strategy_str}")
    if library_source_str:
        out.info(f"Library source: {library_source_str}")
    if taxonomy_tax_id:
        out.info(f"Taxonomy filter: {taxonomy_sci_name} (tax_id: {taxonomy_tax_id}, applied to runs via host lineage)")
    elif host_tax_id:
        out.info(f"Host taxon filter: {host_tax_id}")
    if keyword:
        out.info(f"Keyword filter: \"{keyword}\" (study title and description)")
    out.info("Database: ENA (study endpoint pre-filter → run endpoint → study discovery)")

    # tax_tree() and study_description keyword search are only valid in result=study.
    # When taxonomy or keywords are set, first fetch matching study accessions via the
    # study endpoint, then use that set to filter the run results.
    from wmw.ena import VALID_STUDY_DATE_FIELDS
    study_date_field = date_field if date_field in VALID_STUDY_DATE_FIELDS else "first_public"

    study_accession_filter: set[str] | None = None
    if keyword:
        out.info(
            f"Querying ENA study endpoint for keyword \"{keyword}\" filter"
            f" (date field: {study_date_field})…"
        )
        try:
            study_raw = ena.search_studies(
                date_from=args.date_from,
                date_to=args.date_to,
                date_field=study_date_field,
                keyword=keyword,
            )
            study_accession_filter = {
                s.get("study_accession") for s in study_raw
                if s.get("study_accession")
            }
            out.info(f"  {len(study_accession_filter)} studies match study-level filter(s).")
        except Exception as exc:
            _die(f"ENA study query failed: {exc}")
            return 1

    run_batch = getattr(args, "run_batch", 20) or 20
    raw_runs: list[dict] = []

    if study_accession_filter is not None:
        study_list = sorted(study_accession_filter)
        n_batches = (len(study_list) + run_batch - 1) // run_batch
        out.info(
            f"Querying ENA runs in {n_batches} batch(es) of up to {run_batch} studies…"
        )
        _pg = None
        _task_id = None
        try:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
            )
            from rich.console import Console as _Console
            if sys.stdout.isatty():
                _pg = Progress(
                    SpinnerColumn(style="#5f9ea0"),
                    TextColumn("[#5f9ea0]Querying ENA runs"),
                    BarColumn(bar_width=None, style="#b7c7d3", complete_style="#5f9ea0"),
                    MofNCompleteColumn(),
                    TextColumn("[#b7c7d3]studies  ·  [#e6edf3]{task.fields[summary]}"),
                    console=_Console(theme=out.WMW_THEME, highlight=False, soft_wrap=True),
                    transient=False,
                )
                _pg.start()
                _task_id = _pg.add_task("", total=len(study_list), summary="")
        except ImportError:
            pass
        failed_batches: list[int] = []
        for i in range(0, len(study_list), run_batch):
            batch = study_list[i : i + run_batch]
            batch_num = i // run_batch + 1
            if _pg is None:
                out.info(f"  Batch {batch_num}/{n_batches}: querying {len(batch)} studies…")
            try:
                batch_runs = ena.search_runs(
                    host_tax_id=host_tax_id,
                    library_strategy=library_strategy_str,
                    library_source=library_source_str,
                    study_accessions=batch,
                )
                raw_runs.extend(batch_runs)
            except Exception as exc:
                failed_batches.append(batch_num)
                if _pg is None:
                    out.warn(f"ENA run query failed (batch {batch_num}): {exc} — skipping.")
                n_studies = len({r.get('study_accession') for r in raw_runs if r.get('study_accession')})
                if _pg is not None:
                    _pg.update(_task_id, advance=len(batch), summary=(
                        f"{_pl(len(raw_runs), 'run')} across"
                        f" {_pl(n_studies, 'study', 'studies')} [{len(failed_batches)} batch(es) skipped]"
                    ))
                continue
            n_studies = len({r.get('study_accession') for r in raw_runs if r.get('study_accession')})
            if _pg is not None:
                _pg.update(
                    _task_id,
                    advance=len(batch),
                    summary=(
                        f"{_pl(len(raw_runs), 'run')} across"
                        f" {_pl(n_studies, 'study', 'studies')}"
                    ),
                )
            else:
                out.info(
                    f"    → {_pl(len(batch_runs), 'run')} found"
                    f"  |  cumulative: {_pl(len(raw_runs), 'run')} across"
                    f" {_pl(n_studies, 'study', 'studies')}."
                )
        if _pg is not None:
            _pg.stop()
        if failed_batches:
            out.warn(
                f"{len(failed_batches)} batch(es) failed and were skipped"
                f" (batch numbers: {', '.join(map(str, failed_batches))})."
                " Results may be incomplete — consider re-running."
            )
    else:
        out.info("Querying ENA Portal API for runs…")
        try:
            raw_runs = ena.search_runs(
                date_from=args.date_from,
                date_to=args.date_to,
                host_tax_id=host_tax_id,
                library_strategy=library_strategy_str,
                library_source=library_source_str,
                date_field=date_field,
            )
        except Exception as exc:
            _die(f"ENA run query failed: {exc}")
            return 1

    n_host_excluded = 0
    if exclude_ids:
        raw_runs, n_host_excluded = metadata.filter_runs(raw_runs, exclude_host_tax_ids=exclude_ids)

    if taxonomy_tax_id and taxonomy_sci_name:
        unique_host_ids = {r.get("host_tax_id", "") for r in raw_runs if r.get("host_tax_id")}
        out.info(
            f"Checking host taxonomy ({taxonomy_sci_name}) for"
            f" {len(unique_host_ids)} unique host taxon ID(s)…"
        )
        valid_host_ids: set[str] = set()
        for hid in unique_host_ids:
            lineage = ena.get_lineage(hid)
            if taxonomy_sci_name in lineage or hid == taxonomy_tax_id:
                valid_host_ids.add(hid)
        before = len(raw_runs)
        raw_runs = [
            r for r in raw_runs
            if not r.get("host_tax_id") or r.get("host_tax_id") in valid_host_ids
        ]
        removed = before - len(raw_runs)
        if removed:
            out.info(
                f"  Host taxonomy filter removed {_pl(removed, 'run')} outside {taxonomy_sci_name}."
            )

    study_accessions = ena.unique_studies(raw_runs)
    out.success(
        f"ENA: {_pl(len(raw_runs), 'qualifying run')} → {_pl(len(study_accessions), 'unique study', 'unique studies')}."
    )

    if not study_accessions:
        out.info("Nothing to insert.")
        return 0

    out.info(f"Fetching study metadata for {len(study_accessions)} studies…")
    raw_study_records = ena.fetch_studies_batch(study_accessions)
    studies = [metadata.normalize_ena_study(s) for s in raw_study_records]

    seen: set[str] = set()
    deduped: list[dict] = []
    for s in studies:
        acc = s.get("study_accession", "")
        if acc and acc not in seen:
            seen.add(acc)
            deduped.append(s)
    studies = deduped

    if not studies:
        out.info("Nothing to insert.")
        return 0

    if not args.no_publications:
        from wmw import publications
        api_key = os.environ.get("NCBI_TOKEN", "").strip() or cfg.get("NCBI_API_KEY", "").strip() or None
        email = cfg.get("NCBI_EMAIL", "").strip() or None
        out.info(f"Resolving publication metadata for {len(studies)} studies…")
        _pub_pg = None
        _pub_task = None
        try:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
            )
            from rich.console import Console as _PubConsole
            if sys.stdout.isatty():
                _pub_pg = Progress(
                    SpinnerColumn(style="#5f9ea0"),
                    TextColumn("[#5f9ea0]Resolving publications"),
                    BarColumn(bar_width=None, style="#b7c7d3", complete_style="#5f9ea0"),
                    MofNCompleteColumn(),
                    TextColumn("[#b7c7d3]studies  ·  [#e6edf3]{task.fields[summary]}"),
                    console=_PubConsole(theme=out.WMW_THEME, highlight=False, soft_wrap=True),
                    transient=False,
                )
                _pub_pg.start()
                _pub_task = _pub_pg.add_task("", total=len(studies), summary="")
        except ImportError:
            pass
        _pub_found = [0]

        def _pub_progress(study: dict) -> None:
            if study.get("pub_title"):
                _pub_found[0] += 1
            if _pub_pg is not None:
                _pub_pg.update(
                    _pub_task,
                    advance=1,
                    summary=f"{_pub_found[0]} resolved",
                )

        publications.resolve_batch(studies, api_key=api_key, email=email, on_progress=_pub_progress)
        if _pub_pg is not None:
            _pub_pg.stop()
        out.success(f"Publications resolved: {_pub_found[0]}/{len(studies)}.")

    run_stats: dict[str, dict] = {}
    for run in raw_runs:
        acc = run.get("study_accession", "")
        if not acc:
            continue
        if acc not in run_stats:
            run_stats[acc] = {"runs": 0, "host_taxids": set()}
        run_stats[acc]["runs"] += 1
        htid = run.get("host_tax_id", "")
        if htid:
            run_stats[acc]["host_taxids"].add(htid)
    final_run_stats = {
        acc: {"runs": d["runs"], "host_taxa": len(d["host_taxids"])}
        for acc, d in run_stats.items()
    }

    studies_with_hosts = [
        s for s in studies
        if final_run_stats.get(s.get("study_accession", ""), {}).get("host_taxa", 0) > 0
    ]
    n_no_host = len(studies) - len(studies_with_hosts)
    if n_no_host:
        out.info(
            f"Excluded {_pl(n_no_host, 'study', 'studies')} from summary table: no host taxon ID in any run (Host taxa = 0)."
        )

    if exclude_ids:
        include_arg = (getattr(args, "include", None) or "").strip()
        out.info(
            f"Excluding {len(exclude_ids)} host taxon ID(s)"
            + (f" (included: {include_arg})" if include_arg else "")
            + ". Use --include All to disable."
        )
        if n_host_excluded:
            out.info(f"  Host exclusion filter removed {_pl(n_host_excluded, 'run')}.")

    _print_scan_summary(studies_with_hosts, run_stats=final_run_stats)

    for s in studies:
        acc = s.get("study_accession", "")
        stats = final_run_stats.get(acc, {})
        if stats.get("runs"):
            s["detected_runs"] = stats["runs"]
        if stats.get("host_taxa"):
            s["detected_host_taxa"] = stats["host_taxa"]

    if dry_run:
        out.info("Dry-run mode — no changes written to Airtable.")
        return 0

    s_inserted, s_updated = client.upsert_studies(studies_table, studies)
    out.success(f"Studies: {s_inserted} inserted, {s_updated} updated.")

    species_table_id = str(cfg.get("SPECIES_TABLE") or "").strip()
    taxid_field_id = str(cfg.get("SPECIES_TAXID_FIELD") or "").strip()
    link_field_id = str(cfg.get("SPECIES_STUDIES_LINK_FIELD") or "").strip()
    if species_table_id and taxid_field_id and link_field_id:
        host_taxids_by_study = {
            acc: d["host_taxids"]
            for acc, d in run_stats.items()
            if d["host_taxids"]
        }
        n_linked = client.link_studies_to_species(
            studies_table, species_table_id, taxid_field_id, link_field_id, host_taxids_by_study,
        )
        if n_linked:
            out.success(f"Linked {n_linked} species record(s) to studies.")

    return 0


def _scan_single_study(
    args: argparse.Namespace,
    study_accession: str,
    studies_table: str,
    dry_run: bool,
    client=None,
) -> int:
    from wmw import ena, metadata

    out.section(f"WMW SCAN — {study_accession}")
    out.info("Fetching study metadata from ENA…")

    study_record = ena.fetch_study_metadata(study_accession)
    if not study_record:
        out.warn(f"Study {study_accession} not found in ENA.")
        return 0

    study = metadata.normalize_ena_study(study_record)

    if not args.no_publications:
        from wmw import publications
        api_key = os.environ.get("NCBI_TOKEN", "").strip() or cfg.get("NCBI_API_KEY", "").strip() or None
        email = cfg.get("NCBI_EMAIL", "").strip() or None
        publications.resolve_batch([study], api_key=api_key, email=email)

    _print_scan_summary([study])

    if dry_run:
        out.info("Dry-run — no Airtable writes.")
        return 0

    if client is None:
        client = _require_airtable(args, studies_table)
    s_inserted, s_updated = client.upsert_studies(studies_table, [study])
    out.success(f"Studies: {s_inserted} inserted, {s_updated} updated.")
    return 0


# ---------------------------------------------------------------------------
# wmw fetch  (phase 2 — sample fetch for approved studies)
# ---------------------------------------------------------------------------

def _resolve_fetch_params(args: argparse.Namespace) -> dict:
    """Collect run-level filter parameters from CLI flags and config fallbacks."""
    library_source_str  = _conf(args, "library_source",    "LIBRARY_SOURCE")
    instrument_platform = _conf(args, "instrument_platform", "INSTRUMENT_PLATFORM")
    min_bases_str       = _conf(args, "min_bases",          "MIN_BASES")

    min_bases: int | None = None
    if min_bases_str:
        try:
            min_bases = int(min_bases_str)
        except ValueError:
            _die(f"--min-bases / MIN_BASES must be an integer, got: {min_bases_str!r}")

    exclude_ids = _build_exclude_ids(args)
    cli_extra = getattr(args, "exclude_taxa", "") or ""
    for tid in cli_extra.split(","):
        tid = tid.strip()
        if tid and tid not in exclude_ids:
            exclude_ids.append(tid)

    library_strategy_str = args.library_strategy or "WGS,METAGENOMIC"
    library_strategies = [s.strip() for s in library_strategy_str.split(",") if s.strip()] or None
    library_sources = (
        [s.strip() for s in library_source_str.split(",") if s.strip()]
        if library_source_str else None
    )
    platform = instrument_platform.strip().upper() if instrument_platform else None

    return {
        "library_strategies": library_strategies,
        "library_sources":    library_sources,
        "instrument_platform": platform,
        "min_bases":          min_bases,
        "exclude_ids":        exclude_ids,
    }


def _show_run_filter_exclusions(
    exclusions: list[dict[str, str]],
    *,
    max_examples: int = 20,
) -> None:
    """Print the first failed filter criterion for excluded runs."""
    if not exclusions:
        return

    grouped: dict[tuple[str, str, str], list[str]] = {}
    for exclusion in exclusions:
        key = (
            exclusion.get("criterion", ""),
            exclusion.get("value", ""),
            exclusion.get("expected", ""),
        )
        accession = exclusion.get("run_accession") or "(unknown run)"
        grouped.setdefault(key, []).append(accession)

    out.info("  Exclusion criteria used:")
    for (criterion, value, expected), accessions in sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), item[0]),
    ):
        shown = ", ".join(accessions[:max_examples])
        more = len(accessions) - max_examples
        if more > 0:
            shown = f"{shown}, +{more} more"
        out.info(
            f"    {criterion}={value or '(blank)'}; expected {expected}: "
            f"{_pl(len(accessions), 'run')} ({shown})"
        )


def cmd_fetch(args: argparse.Namespace) -> int:
    from wmw import ena, metadata

    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
    dry_run = args.dry_run
    params = _resolve_fetch_params(args)

    out.section("WMW FETCH")

    record_id_map: dict[str, str] = {}

    # Verify Airtable access early; for --study + --dry-run Airtable is not used at all
    if args.study and dry_run:
        client = None
    else:
        client = _require_airtable(args, studies_table, samples_table)

    if args.study:
        out.info(f"Single-study mode: {args.study}")
        studies_to_fetch = [args.study]
        if client is not None:
            rec_id = client.fetch_study_record_id(studies_table, args.study)
            if rec_id:
                record_id_map = {args.study: rec_id}
    else:
        status_filter = args.status or "approved"
        out.info(f"Reading studies with status='{status_filter}' from Airtable…")
        approved_records = client.fetch_studies_by_status(studies_table, status=status_filter)
        if not approved_records:
            out.info(f"No studies with status '{status_filter}' found.")
            return 0
        studies_to_fetch = [r["fields"].get("study_accession", "") for r in approved_records]
        studies_to_fetch = [acc for acc in studies_to_fetch if acc]
        record_id_map = {r["fields"].get("study_accession", ""): r["id"] for r in approved_records}
        out.success(f"{_pl(len(studies_to_fetch), 'approved study', 'approved studies')} to fetch.")

    if params["library_strategies"]:
        out.info(f"Library strategy filter: {','.join(params['library_strategies'])}")
    if params["library_sources"]:
        out.info(f"Library source filter: {','.join(params['library_sources'])}")
    if params["instrument_platform"]:
        out.info(f"Instrument platform filter: {params['instrument_platform']}")
    if params["min_bases"]:
        out.info(f"Minimum base count: {params['min_bases']:,}")

    all_runs: list[dict] = []
    fetched_accessions: list[str] = []

    for acc in studies_to_fetch:
        out.info(f"Fetching runs for {acc}…")
        try:
            runs = ena.search_study(acc)
            normalized = metadata.normalize_runs(runs, "ENA")
            study_rec_id = record_id_map.get(acc)
            if study_rec_id:
                for run in normalized:
                    run["parent_study"] = [study_rec_id]
            out.info(f"  {acc}: {_pl(len(normalized), 'run')}.")
            all_runs.extend(normalized)
            fetched_accessions.append(acc)
        except Exception as exc:
            out.warn(f"  {acc}: fetch failed — {exc}")

    if not all_runs:
        out.info("No runs found.")
        return 0

    all_runs = metadata.deduplicate_runs(all_runs)

    if params["exclude_ids"]:
        all_runs, n_host_excluded, host_exclusions = metadata.filter_runs(
            all_runs,
            exclude_host_tax_ids=params["exclude_ids"],
            include_exclusions=True,
        )
        out.info(
            f"Excluding {len(params['exclude_ids'])} host taxon ID(s). Use --include All to disable."
        )
        if n_host_excluded:
            out.info(f"  Host exclusion filter removed {_pl(n_host_excluded, 'run')}.")
            _show_run_filter_exclusions(host_exclusions)

    all_runs, n_other_excluded, other_exclusions = metadata.filter_runs(
        all_runs,
        min_bases=params["min_bases"],
        library_strategies=params["library_strategies"],
        library_sources=params["library_sources"],
        instrument_platform=params["instrument_platform"],
        include_exclusions=True,
    )
    if n_other_excluded:
        out.info(f"Run filter(s) removed {_pl(n_other_excluded, 'run')}.")
        _show_run_filter_exclusions(other_exclusions)

    out.info(f"Total runs after filtering: {len(all_runs)}")

    if dry_run:
        out.info("Dry-run mode — no changes written to Airtable.")
        return 0

    r_inserted, r_skipped = client.upsert_samples(samples_table, all_runs)
    out.success(f"Samples/runs: {r_inserted} inserted, {r_skipped} already existed.")

    if record_id_map and fetched_accessions:
        record_ids = [record_id_map[acc] for acc in fetched_accessions if acc in record_id_map]
        if record_ids:
            client.set_study_status(studies_table, record_ids, "indexed")
            out.success(f"Updated {len(record_ids)} study status(es) to 'indexed'.")

    return 0


def _print_scan_summary(
    studies: list[dict],
    run_stats: dict[str, dict] | None = None,
) -> None:
    tbl = out.make_table("Study accession", "Runs", "Host taxa", "Title", "First public")
    if tbl is None:
        for s in studies:
            acc = s.get("study_accession", "")
            stats = (run_stats or {}).get(acc, {})
            runs = str(stats.get("runs", "—")) if run_stats is not None else "—"
            taxa = str(stats.get("host_taxa", "—")) if run_stats is not None else "—"
            print(
                f"  {acc}  runs={runs}  taxa={taxa}  "
                f"{s.get('study_title', '')[:50]}"
            )
        return
    for s in studies:
        acc = s.get("study_accession", "")
        stats = (run_stats or {}).get(acc, {})
        runs = str(stats.get("runs", "—")) if run_stats is not None else "—"
        taxa = str(stats.get("host_taxa", "—")) if run_stats is not None else "—"
        tbl.add_row(
            acc,
            runs,
            taxa,
            (s.get("study_title") or "")[:50],
            s.get("first_public", ""),
        )
    out.render_table(tbl)


# ---------------------------------------------------------------------------
# wmw process
# ---------------------------------------------------------------------------

def _write_and_maybe_launch_script(code: str, script_path: Path, script: str) -> None:
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    out.info(f"  Launch script: {script_path}")

    import shutil
    import subprocess as sp

    if shutil.which("screen") is None:
        out.warn(f"  'screen' not found — script written but not launched.")
    else:
        sp.run(["screen", "-dmS", code, "bash", str(script_path)], check=True)
        out.success(f"  Screen session '{code}' started.")


def _workflow_tsv_path(work_dir: Path, study_code: str, workflow: str) -> Path:
    return work_dir / f"{study_code}_{workflow}.tsv"


def _legacy_workflow_tsv_path(work_dir: Path, workflow: str) -> Path:
    return work_dir / f"{workflow}.tsv"


def _existing_workflow_tsv_path(work_dir: Path, study_code: str, workflow: str) -> Path:
    preferred = _workflow_tsv_path(work_dir, study_code, workflow)
    if preferred.exists():
        return preferred
    legacy = _legacy_workflow_tsv_path(work_dir, workflow)
    if legacy.exists():
        return legacy
    return preferred


def _study_priority_drakkar_kwargs(fields: dict[str, Any]) -> dict[str, str]:
    priority = str(fields.get("priority", "") or "").strip().lower()
    if priority != "low":
        return {}
    return {
        "slurm_partition": _LOW_PRIORITY_SLURM_PARTITION,
        "slurm_qos": _LOW_PRIORITY_SLURM_QOS,
    }


def cmd_process(args: argparse.Namespace) -> int:
    from wmw import drakkar

    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
    genomes_table = _conf(args, "genomes_table", "GENOMES_TABLE")
    output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR", required=True)
    output_dir = Path(output_dir_str).expanduser().resolve()
    batch_filter = (args.batch or "").strip()
    workflow = args.workflow
    slurm = args.slurm
    conda_env = str(cfg.get("DRAKKAR_CONDA_ENV") or "").strip()
    wmw_conda_env = str(cfg.get("WMW_CONDA_ENV") or "").strip()

    out.section("WMW PROCESS")
    client = _require_airtable(args, studies_table, samples_table)

    import shutil

    out.info("Fetching studies with status 'ready', 'resume', or 'rerun' from Airtable…")
    actionable_studies: list[dict] = []
    for status_val in ("ready", "resume", "rerun"):
        actionable_studies.extend(
            client.fetch_studies_by_status(studies_table, status=status_val)
        )

    if batch_filter:
        actionable_studies = [
            s for s in actionable_studies
            if s["fields"].get("code", "") == batch_filter
        ]

    if not actionable_studies:
        label = f"code={batch_filter!r}" if batch_filter else "status 'ready'/'resume'/'rerun'"
        out.info(f"No studies with {label} found.")
        return 0

    out.success(f"{_pl(len(actionable_studies), 'study', 'studies')} to process.")

    n_generated = 0
    n_finalized = 0
    for study in actionable_studies:
        fields = study["fields"]
        code = fields.get("code", "")
        study_accession = fields.get("study_accession", "")
        study_status = fields.get("status", "ready")
        priority_drakkar_kwargs = _study_priority_drakkar_kwargs(fields)
        if not code:
            out.warn(f"Study {study['id']} has no code — skipping.")
            continue

        work_dir = output_dir / code

        if study_status == "resume":
            out.info(f"{code}: status 'resume' — resolving pending Drakkar and Airtable tasks.")
            preprocessing_tsv = _existing_workflow_tsv_path(work_dir, code, "preprocessing")
            cataloging_tsv = _existing_workflow_tsv_path(work_dir, code, "cataloging")
            bin_metadata_path = work_dir / "cataloging" / "final" / "all_bin_metadata.csv"
            has_preprocessing = preprocessing_tsv.exists()
            has_cataloging = cataloging_tsv.exists() or bin_metadata_path.exists()

            finalized_this_study = False
            if has_preprocessing:
                finalized_this_study = _finalize_preprocessing_outputs(
                    client,
                    studies_table,
                    samples_table,
                    study,
                    output_dir,
                    set_status=True,
                    skip_existing_attachments=True,
                    prefix=code,
                ) or finalized_this_study
            if has_cataloging:
                finalized_this_study = _finalize_cataloging_outputs(
                    client,
                    studies_table,
                    samples_table,
                    genomes_table,
                    study,
                    output_dir,
                    set_status=True,
                    skip_existing_attachments=True,
                    prefix=code,
                    screen_args=args,
                ) or finalized_this_study

            has_profiling = (
                (work_dir / "profiling_genomes.tsv").exists()
                or (work_dir / "profiling_genomes" / "final" / "bases.tsv").exists()
                or (work_dir / "profiling_genomes" / "final" / "counts.tsv").exists()
            )
            missing_annotation_outputs = drakkar.missing_annotation_outputs(work_dir)
            has_annotation = not missing_annotation_outputs

            if has_profiling:
                finalized_this_study = _finalize_profiling_outputs(
                    client,
                    studies_table,
                    samples_table,
                    study,
                    output_dir,
                    prefix=code,
                ) or finalized_this_study

            if has_profiling and has_annotation:
                finalized_this_study = _finalize_annotation_outputs(
                    client,
                    genomes_table,
                    study,
                    output_dir,
                    prefix=code,
                ) or finalized_this_study
                client.set_study_status(studies_table, [study["id"]], "Done")
                out.success(f"{code}: annotation outputs complete; study status → 'Done'.")
                finalized_this_study = True

            if finalized_this_study:
                n_finalized += 1

            if has_profiling and has_annotation:
                continue

            if has_profiling and missing_annotation_outputs:
                missing = ", ".join(
                    str(path.relative_to(work_dir))
                    for path in missing_annotation_outputs
                )
                out.info(
                    f"{code}: profiling done, annotation outputs incomplete ({missing}) "
                    "— launching annotation task."
                )
                script_path = work_dir / f"{code}.sh"
                script = drakkar.generate_annotation_script(
                    code=code,
                    work_dir=work_dir,
                    conda_env=conda_env,
                    slurm=slurm,
                    wmw_conda_env=wmw_conda_env,
                    **priority_drakkar_kwargs,
                )
                _write_and_maybe_launch_script(code, script_path, script)
                n_generated += 1
                continue

            if has_cataloging and not has_profiling:
                out.info(f"{code}: cataloging done, profiling output absent — launching profiling task.")
                script_path = work_dir / f"{code}.sh"
                script = drakkar.generate_profiling_script(
                    code=code,
                    work_dir=work_dir,
                    conda_env=conda_env,
                    slurm=slurm,
                    wmw_conda_env=wmw_conda_env,
                    **priority_drakkar_kwargs,
                )
                _write_and_maybe_launch_script(code, script_path, script)
                n_generated += 1
                continue

            samples = client.fetch_samples_for_study(samples_table, study_accession)
            use_samples = [r for r in samples if r.get("fields", r).get("status") == "use"]
            if not use_samples:
                out.warn(f"{code}: no samples with status 'use' — cannot launch pending Drakkar task.")
                continue

            out.info(f"{code}: {_pl(len(use_samples), 'sample')} with status 'use' available for resume.")
            work_dir.mkdir(parents=True, exist_ok=True)
            input_tsv = work_dir / f"{code}.tsv"
            drakkar.build_input_tsv(samples, input_tsv)
            out.info(f"  Input TSV:     {input_tsv}")

            script_path = work_dir / f"{code}.sh"
            if has_preprocessing:
                out.info(f"{code}: preprocessing output exists; launching pending cataloging task.")
                script = drakkar.generate_cataloging_script(
                    code=code,
                    tsv_path=input_tsv,
                    work_dir=work_dir,
                    conda_env=conda_env,
                    slurm=slurm,
                    wmw_conda_env=wmw_conda_env,
                    **priority_drakkar_kwargs,
                )
            else:
                out.info(f"{code}: preprocessing output is missing; launching preprocessing followed by cataloging.")
                script = drakkar.generate_preprocessing_script(
                    code=code,
                    tsv_path=input_tsv,
                    work_dir=work_dir,
                    conda_env=conda_env,
                    slurm=slurm,
                    wmw_conda_env=wmw_conda_env,
                    memory_multiplier=fields.get("memory_boost") or None,
                    time_multiplier=fields.get("time_boost") or None,
                    **priority_drakkar_kwargs,
                )
            _write_and_maybe_launch_script(code, script_path, script)
            n_generated += 1
            continue

        if study_status == "rerun":
            if work_dir.exists():
                shutil.rmtree(work_dir)
                out.info(f"{code}: wiped local directory for rerun.")
        samples = client.fetch_samples_for_study(samples_table, study_accession)
        use_samples = [r for r in samples if r.get("fields", r).get("status") == "use"]

        if not use_samples:
            out.warn(f"{code}: no samples with status 'use' — skipping.")
            continue

        out.info(f"{code}: {_pl(len(use_samples), 'sample')} with status 'use' to process.")

        work_dir.mkdir(parents=True, exist_ok=True)

        input_tsv = work_dir / f"{code}.tsv"
        drakkar.build_input_tsv(samples, input_tsv)
        out.info(f"  Input TSV:     {input_tsv}")

        script_path = work_dir / f"{code}.sh"
        if workflow == "preprocessing":
            script = drakkar.generate_full_pipeline_script(
                code=code,
                tsv_path=input_tsv,
                work_dir=work_dir,
                conda_env=conda_env,
                slurm=slurm,
                wmw_conda_env=wmw_conda_env,
                memory_multiplier=fields.get("memory_boost") or None,
                time_multiplier=fields.get("time_boost") or None,
                **priority_drakkar_kwargs,
            )
        elif workflow == "profiling":
            script = drakkar.generate_profiling_script(
                code=code,
                work_dir=work_dir,
                conda_env=conda_env,
                slurm=slurm,
                wmw_conda_env=wmw_conda_env,
                **priority_drakkar_kwargs,
            )
        elif workflow == "annotating":
            script = drakkar.generate_annotation_script(
                code=code,
                work_dir=work_dir,
                conda_env=conda_env,
                slurm=slurm,
                wmw_conda_env=wmw_conda_env,
                **priority_drakkar_kwargs,
            )
        else:
            _die(f"Workflow {workflow!r} script generation is not yet implemented.")
            return 1

        _write_and_maybe_launch_script(code, script_path, script)
        n_generated += 1

    if n_generated:
        out.success(f"Generated scripts for {_pl(n_generated, 'study', 'studies')}.")
    if n_finalized:
        out.success(f"Finalised Airtable outputs for {_pl(n_finalized, 'resume study', 'resume studies')}.")
    if not n_generated and not n_finalized:
        out.info("No scripts generated and no existing outputs finalised.")
    return 0


# ---------------------------------------------------------------------------
# wmw stop
# ---------------------------------------------------------------------------

def _screen_session_name(session: str) -> str:
    """Return the user-provided screen name from a `screen -ls` session token."""
    prefix, sep, suffix = session.partition(".")
    if sep and prefix.isdigit():
        return suffix
    return session


def _genome_upload_screen_name(code: str) -> str:
    return f"{code}{_GENOME_UPLOAD_SCREEN_SUFFIX}"


def _screen_session_matches_code(session_name: str, code: str) -> bool:
    return session_name in {code, _genome_upload_screen_name(code)}


def _screen_sessions_for_code(screen_ls_output: str, code: str) -> list[str]:
    sessions: list[str] = []
    for line in screen_ls_output.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        token = parts[0]
        if _screen_session_matches_code(_screen_session_name(token), code):
            sessions.append(token)
    return sessions


def _stop_screen_sessions(code: str) -> tuple[int, str]:
    import shutil
    import subprocess as sp

    if shutil.which("screen") is None:
        return 0, "'screen' not found on PATH."

    listed = sp.run(
        ["screen", "-ls"],
        capture_output=True,
        text=True,
        check=False,
    )
    sessions = _screen_sessions_for_code(
        (listed.stdout or "") + "\n" + (listed.stderr or ""),
        code,
    )
    if not sessions:
        return 0, f"No screen session named {code!r} found."

    stopped = 0
    errors: list[str] = []
    for session in sessions:
        res = sp.run(
            ["screen", "-S", session, "-X", "quit"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            stopped += 1
        else:
            detail = (res.stderr or res.stdout or "").strip()
            errors.append(f"{session}: {detail or 'screen quit failed'}")

    return stopped, "; ".join(errors)


def _parse_squeue_jobs(stdout: str) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.rstrip("\n").split("|", 4)
        if len(parts) < 2:
            continue
        jobs.append(
            {
                "id": parts[0].strip(),
                "name": parts[1].strip(),
                "workdir": parts[2].strip() if len(parts) > 2 else "",
                "command": parts[3].strip() if len(parts) > 3 else "",
                "comment": parts[4].strip() if len(parts) > 4 else "",
            }
        )
    return jobs


def _slurm_job_matches(
    code: str,
    work_dir: Path | None,
    sample_codes: set[str],
    job: dict[str, str],
) -> bool:
    name = job.get("name", "")
    if name == code or name.startswith((f"{code}.", f"{code}_", f"{code}-")):
        return True

    comment = job.get("comment", "")
    if comment:
        for sample_code in sample_codes:
            if f"_wildcards_{sample_code}" in comment:
                return True

    markers: list[str] = []
    if work_dir is not None:
        markers.append(str(work_dir))
    markers.extend([f"/{code}/", f"/{code}.tsv", f"/{code}.sh"])

    haystacks = [job.get("workdir", ""), job.get("command", "")]
    return any(marker and marker in haystack for marker in markers for haystack in haystacks)


def _query_slurm_jobs() -> tuple[list[dict[str, str]], str]:
    import getpass
    import shutil
    import subprocess as sp

    if shutil.which("squeue") is None:
        return [], "'squeue' not found on PATH."

    formats = ["%i|%j|%Z|%o|%k", "%i|%j|%Z|%o", "%i|%j"]
    last_error = ""
    for fmt in formats:
        res = sp.run(
            ["squeue", "--noheader", "--user", getpass.getuser(), f"--format={fmt}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return _parse_squeue_jobs(res.stdout or ""), ""
        last_error = (res.stderr or res.stdout or "").strip()
    return [], last_error or "Could not query Slurm jobs with squeue."


def _cancel_slurm_jobs(
    code: str,
    work_dir: Path | None,
    sample_codes: set[str],
) -> tuple[list[str], str]:
    import shutil
    import subprocess as sp

    if shutil.which("scancel") is None:
        return [], "'scancel' not found on PATH."

    jobs, warning = _query_slurm_jobs()
    if warning:
        return [], warning

    job_ids = [
        job["id"]
        for job in jobs
        if job.get("id") and _slurm_job_matches(code, work_dir, sample_codes, job)
    ]
    if not job_ids:
        return [], ""

    res = sp.run(
        ["scancel", *job_ids],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        return [], detail or "scancel failed."
    return job_ids, ""


def _write_stop_marker(work_dir: Path | None) -> None:
    if work_dir is None or not work_dir.exists():
        return
    marker = work_dir / ".wmw-stop"
    marker.write_text("stopped\n", encoding="utf-8")


def cmd_stop(args: argparse.Namespace) -> int:
    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
    output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR")
    output_dir = Path(output_dir_str).expanduser().resolve() if output_dir_str else None
    study_code = (args.batch or "").strip()
    if not study_code:
        _die("--batch is required.")

    work_dir = output_dir / study_code if output_dir is not None else None

    out.section("WMW STOP")
    client = _require_airtable(args, studies_table, samples_table)

    study_record = client.fetch_study_by_code(studies_table, study_code)
    if not study_record:
        _die(f"No study with code {study_code!r} found in Airtable.")
    assert study_record is not None
    study_fields = study_record.get("fields", {})
    study_accession = study_fields.get("study_accession", "")
    samples = client.fetch_samples_for_study(samples_table, study_accession) if study_accession else []
    sample_codes = {
        str(rec.get("fields", rec).get("code", "") or "").strip()
        for rec in samples
        if str(rec.get("fields", rec).get("code", "") or "").strip()
    }

    _write_stop_marker(work_dir)
    client.set_study_status(studies_table, [study_record["id"]], "stopped")
    out.success(f"Study {study_code} status → 'stopped'.")

    stopped_screens, screen_warning = _stop_screen_sessions(study_code)
    if stopped_screens:
        out.success(f"Stopped {_pl(stopped_screens, 'screen session')}.")
    if screen_warning:
        out.warn(screen_warning)

    cancelled_jobs, slurm_warning = _cancel_slurm_jobs(study_code, work_dir, sample_codes)
    if cancelled_jobs:
        out.success(f"Cancelled Slurm {_pl(len(cancelled_jobs), 'job')}: {', '.join(cancelled_jobs)}.")
    elif slurm_warning:
        out.warn(slurm_warning)
    else:
        out.info("No matching Slurm jobs found.")

    # A running generated script may execute its EXIT trap while screen is being torn down.
    # Re-assert the requested terminal state after local and Slurm cancellation attempts.
    client.set_study_status(studies_table, [study_record["id"]], "stopped")
    return 0


# ---------------------------------------------------------------------------
# wmw set-status
# ---------------------------------------------------------------------------

_PROCESS_STATUS_MAP: dict[tuple[str, str], str] = {
    ("preprocessing", "preprocessing"): "preprocessing",
    ("preprocessing", "running"):       "preprocessing",  # legacy compat
    ("preprocessing", "completed"):     "preprocessed",   # legacy compat
    ("preprocessing", "preprocessed"):  "preprocessed",
    ("preprocessing", "stopped"):       "stopped",
    ("preprocessing", "error"):         "error",
    ("cataloging",    "cataloging"):    "cataloging",
    ("cataloging",    "cataloged"):     "cataloged",
    ("cataloging",    "stopped"):       "stopped",
    ("cataloging",    "error"):         "error",
    ("profiling",     "quantifying"):   "quantifying",
    ("profiling",     "quantified"):    "quantified",
    ("profiling",     "stopped"):       "stopped",
    ("profiling",     "error"):         "error",
    ("annotating",   "annotating"):    "annotating",
    ("annotating",   "completed"):     "Done",
    ("annotating",   "annotated"):     "Done",
    ("annotating",   "Done"):          "Done",
    ("annotating",   "stopped"):       "stopped",
    ("annotating",   "error"):         "error",
}


def _upload_study_tsv_attachment(
    client: Any,
    studies_table: str,
    record_id: str,
    field_name: str,
    tsv_path: Path,
    prefix: str = "",
    study_fields: dict[str, Any] | None = None,
    skip_existing: bool = False,
) -> bool:
    label = f"{prefix}: " if prefix else ""
    if skip_existing and study_fields and study_fields.get(field_name):
        out.info(f"{label}{tsv_path.name} is already attached — skipping upload.")
        return False
    try:
        client.upload_study_file(studies_table, record_id, field_name, tsv_path)
    except Exception as exc:
        out.warn(
            f"{label}{tsv_path.name} found at {tsv_path} but could not be uploaded "
            f"to the study file field: {exc}"
        )
        return False
    else:
        out.success(f"{label}uploaded {tsv_path.name} to the study file field.")
        return True


def _created_genome_record_ids(
    created_records: list[dict[str, Any]],
    created_genomes: list[dict[str, Any]],
    name_fid: str,
) -> dict[str, str]:
    record_ids: dict[str, str] = {}
    for idx, record in enumerate(created_records):
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            continue
        fields = record.get("fields", {}) or {}
        genome_name = str(fields.get(name_fid) or "").strip() if name_fid else ""
        if not genome_name and idx < len(created_genomes):
            genome_name = str(created_genomes[idx].get("genome_name") or "").strip()
        if genome_name:
            record_ids[genome_name] = record_id
    return record_ids


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _genome_passes_quality_filter(
    genome: dict[str, Any],
    completeness_fid: str,
    contamination_fid: str,
) -> bool:
    fields = genome.get("fields", {}) or {}
    completeness = _as_float(fields.get(completeness_fid)) if completeness_fid else None
    contamination = _as_float(fields.get(contamination_fid)) if contamination_fid else None
    return (
        completeness is not None
        and contamination is not None
        and completeness > _GENOME_MIN_COMPLETENESS
        and contamination < _GENOME_MAX_CONTAMINATION
    )


def _upload_genome_bin_attachments(
    client: Any,
    genomes_table: str,
    genome_record_ids: dict[str, str],
    bin_paths: dict[str, Path],
    existing_records_by_name: dict[str, dict[str, Any]] | None = None,
) -> int:
    file_fid = str(cfg.get("GENOMES_COL_FILE_GENOME") or "").strip()
    if not file_fid:
        out.warn("GENOMES_COL_FILE_GENOME is not configured — genome files not uploaded.")
        return 0

    from wmw import drakkar

    uploaded = 0
    skipped_existing = 0
    missing: list[str] = []
    failed: list[str] = []
    for genome_name, record_id in genome_record_ids.items():
        existing_fields = (
            (existing_records_by_name or {}).get(genome_name, {}).get("fields", {})
        )
        if existing_fields.get(file_fid):
            skipped_existing += 1
            continue
        fasta_path = bin_paths.get(genome_name)
        if not fasta_path or not fasta_path.exists():
            missing.append(genome_name)
            continue
        try:
            gz_path = drakkar.gzip_fasta(fasta_path)
            client.upload_genome_file(genomes_table, record_id, file_fid, gz_path)
        except Exception as exc:
            failed.append(f"{genome_name} ({exc})")
        else:
            uploaded += 1

    if uploaded:
        out.success(f"Uploaded compressed genome FASTA files for {_pl(uploaded, 'genome')}.")
    if skipped_existing:
        out.info(f"Skipped {_pl(skipped_existing, 'genome FASTA')} already attached in Airtable.")
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "…" if len(missing) > 10 else "."
        out.warn(f"No bin FASTA path found for {_pl(len(missing), 'genome')}: {preview}{suffix}")
    if failed:
        preview = "; ".join(failed[:5])
        suffix = "…" if len(failed) > 5 else "."
        out.warn(f"Could not upload genome FASTA files for {_pl(len(failed), 'genome')}: {preview}{suffix}")
    return uploaded


def _upload_genome_annotation_attachments(
    client: Any,
    genomes_table: str,
    annotations_by_name: dict[str, Path],
    existing_records_by_name: dict[str, dict[str, Any]],
    *,
    prefix: str = "",
) -> int:
    file_fid = str(cfg.get("GENOMES_COL_FILE_ANNOTATION") or "").strip()
    label = f"{prefix}: " if prefix else ""
    if not file_fid:
        out.warn(f"{label}GENOMES_COL_FILE_ANNOTATION is not configured - annotation files not uploaded.")
        return 0

    from wmw import drakkar

    uploaded = 0
    skipped_existing = 0
    failed: list[str] = []
    for genome_name, annotation_path in annotations_by_name.items():
        record = existing_records_by_name.get(genome_name, {})
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            continue
        existing_fields = record.get("fields", {}) or {}
        if existing_fields.get(file_fid):
            skipped_existing += 1
            continue
        try:
            gz_path = drakkar.gzip_annotation_tsv(annotation_path)
            client.upload_genome_file(genomes_table, record_id, file_fid, gz_path)
        except Exception as exc:
            failed.append(f"{genome_name} ({exc})")
        else:
            uploaded += 1

    if uploaded:
        out.success(f"{label}uploaded compressed annotation files for {_pl(uploaded, 'genome')}.")
    if skipped_existing:
        out.info(f"{label}skipped {_pl(skipped_existing, 'annotation file')} already attached in Airtable.")
    if failed:
        preview = "; ".join(failed[:5])
        suffix = "..." if len(failed) > 5 else "."
        out.warn(f"{label}could not upload annotation files for {_pl(len(failed), 'genome')}: {preview}{suffix}")
    return uploaded


def _finalize_annotation_outputs(
    client: Any,
    genomes_table: str,
    study_record: dict[str, Any],
    output_root: Path,
    *,
    prefix: str = "",
) -> bool:
    """Upload per-genome annotation TSVs and annotation counts to Genomes rows."""
    from wmw import drakkar

    label = f"{prefix}: " if prefix else ""
    if not genomes_table:
        out.warn(f"{label}GENOMES_TABLE is not configured - genome annotations not uploaded.")
        return False

    fields = study_record.get("fields", {}) or {}
    study_code = str(fields.get("code", "") or "").strip()
    if not study_code:
        out.warn(f"{label}study code is missing - genome annotations not uploaded.")
        return False

    name_fid = str(cfg.get("GENOMES_COL_NAME") or "").strip()
    if not name_fid:
        out.warn(f"{label}GENOMES_COL_NAME is not configured - genome annotations not uploaded.")
        return True

    work_dir = output_root / study_code
    annotation_paths = drakkar.genome_annotation_files(work_dir)
    taxonomy_path = work_dir / "annotating" / "genome_taxonomy.tsv"
    taxonomy_by_name = drakkar.parse_genome_taxonomy_tsv(taxonomy_path)

    if not annotation_paths and not taxonomy_by_name:
        final_dir = work_dir / "annotating" / "final"
        out.warn(
            f"{label}no per-genome annotation files found at {final_dir} and "
            f"no taxonomy data parsed from {taxonomy_path} - "
            "annotation stats, taxonomy, and files not uploaded."
        )
        return False

    annotations_by_name: dict[str, Path] = {}
    unrecognised: list[str] = []
    for path in annotation_paths:
        genome_name = drakkar.annotation_file_genome_name(path)
        if genome_name:
            annotations_by_name[genome_name] = path
        else:
            unrecognised.append(path.name)

    if unrecognised:
        preview = ", ".join(unrecognised[:10])
        suffix = "..." if len(unrecognised) > 10 else "."
        out.warn(f"{label}skipped annotation files with unexpected names: {preview}{suffix}")
    if annotation_paths and not annotations_by_name and not taxonomy_by_name:
        return False

    genome_names = list(dict.fromkeys([*annotations_by_name, *taxonomy_by_name]))
    if not genome_names:
        return False

    existing_records = client.fetch_genome_records_by_name(
        genomes_table,
        genome_names,
        name_fid,
    )
    matched_records = {
        name: record
        for name, record in existing_records.items()
        if str(record.get("id") or "").strip()
    }
    missing_records = [name for name in genome_names if name not in matched_records]
    if missing_records:
        preview = ", ".join(missing_records[:10])
        suffix = "..." if len(missing_records) > 10 else "."
        out.warn(
            f"{label}no matching Genomes record found for "
            f"{_pl(len(missing_records), 'annotation/taxonomy output')}: {preview}{suffix}"
        )

    updates: list[dict[str, Any]] = []
    unparsed: list[str] = []
    annotation_update_count = 0
    taxonomy_update_count = 0
    for genome_name in genome_names:
        record = matched_records.get(genome_name)
        if not record:
            continue

        fields_to_update: dict[str, Any] = {}
        annotation_path = annotations_by_name.get(genome_name)
        if annotation_path:
            stats = drakkar.parse_genome_annotation_tsv(annotation_path)
            if stats:
                fields_to_update.update(stats)
                annotation_update_count += 1
            else:
                unparsed.append(annotation_path.name)

        taxonomy_fields = taxonomy_by_name.get(genome_name)
        if taxonomy_fields:
            fields_to_update.update(taxonomy_fields)
            taxonomy_update_count += 1

        if fields_to_update:
            updates.append({"id": str(record["id"]), "fields": fields_to_update})

    if updates:
        updated = client.update_genome_records(genomes_table, updates)
        if updated:
            if annotation_update_count and taxonomy_update_count:
                out.success(f"{label}uploaded annotation counts and taxonomy for {_pl(updated, 'genome')}.")
            elif annotation_update_count:
                out.success(f"{label}uploaded annotation counts for {_pl(updated, 'genome')}.")
            else:
                out.success(f"{label}uploaded taxonomy for {_pl(updated, 'genome')}.")
    if unparsed:
        preview = ", ".join(unparsed[:10])
        suffix = "..." if len(unparsed) > 10 else "."
        out.warn(f"{label}could not parse annotation counts from: {preview}{suffix}")

    _upload_genome_annotation_attachments(
        client,
        genomes_table,
        annotations_by_name,
        matched_records,
        prefix=prefix,
    )
    return True


def _launch_genome_file_upload_screen(
    args: argparse.Namespace,
    study_code: str,
    output_root: Path,
    samples_table: str,
    genomes_table: str,
    *,
    prefix: str = "",
) -> bool:
    """Launch the slow genome FASTA attachment upload in a detached screen session."""
    label = f"{prefix}: " if prefix else ""
    if os.environ.get("STY"):
        return False

    import shutil
    import subprocess as sp

    if shutil.which("screen") is None:
        out.warn(f"{label}'screen' not found — uploading genome FASTA files inline.")
        return False

    work_dir = output_root / study_code
    work_dir.mkdir(parents=True, exist_ok=True)
    session_name = _genome_upload_screen_name(study_code)
    script_path = work_dir / f"{study_code}_upload_genomes.sh"
    stdout_path = work_dir / f"{study_code}_upload_genomes.out"
    stderr_path = work_dir / f"{study_code}_upload_genomes.err"

    cmd = [
        sys.executable,
        "-m",
        "wmw",
        "upload-genome-files",
        "--study",
        study_code,
        "--output-dir",
        str(output_root),
        "--samples-table",
        samples_table,
        "--genomes-table",
        genomes_table,
    ]
    base_id = str(getattr(args, "base_id", "") or "").strip()
    if base_id:
        cmd.extend(["--base-id", base_id])

    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            f"# wmw-generated script — batch {study_code} genome FASTA upload",
            "# Do not edit manually; re-run wmw process or wmw set-status to regenerate.",
            "",
            "set -euo pipefail",
            f"cd {shlex.quote(str(Path.cwd()))}",
            f"exec >> {shlex.quote(str(stdout_path))} 2>> {shlex.quote(str(stderr_path))}",
            'echo ""',
            "echo \"=== $(date '+%Y-%m-%d %H:%M:%S') ===\"",
            "echo \"=== $(date '+%Y-%m-%d %H:%M:%S') ===\" >&2",
            shlex.join(cmd),
            "",
        ]
    )
    try:
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o755)
    except OSError as exc:
        out.warn(
            f"{label}could not write genome upload screen script at {script_path}: {exc}. "
            "Uploading inline."
        )
        return False

    env = os.environ.copy()
    token = str(getattr(args, "airtable_token", "") or "").strip()
    if token:
        env["AIRTABLE_TOKEN"] = token

    try:
        sp.run(
            ["screen", "-dmS", session_name, "bash", str(script_path)],
            check=True,
            env=env,
        )
    except Exception as exc:
        out.warn(
            f"{label}could not start genome upload screen session {session_name!r}: {exc}. "
            "Uploading inline."
        )
        return False

    out.success(f"{label}genome FASTA upload started in screen session '{session_name}'.")
    out.info(f"{label}upload logs: {stdout_path} and {stderr_path}")
    return True


def _finalize_preprocessing_outputs(
    client: Any,
    studies_table: str,
    samples_table: str,
    study_record: dict[str, Any],
    output_root: Path,
    *,
    set_status: bool = False,
    skip_existing_attachments: bool = False,
    prefix: str = "",
) -> bool:
    """Upload preprocessing TSV and sample preprocessing stats when present."""
    from wmw import drakkar

    fields = study_record.get("fields", {}) or {}
    study_code = str(fields.get("code", "") or "").strip()
    work_dir = output_root / study_code
    tsv_path = _existing_workflow_tsv_path(work_dir, study_code, "preprocessing")
    label = f"{prefix}: " if prefix else ""
    if not tsv_path.exists():
        out.warn(
            f"{label}{tsv_path.name} not found at {tsv_path} — "
            f"file and stats not uploaded."
        )
        return False

    if set_status:
        client.set_study_status(studies_table, [study_record["id"]], "preprocessed")
        out.success(f"{label}study status → 'preprocessed'.")

    _upload_study_tsv_attachment(
        client,
        studies_table,
        study_record["id"],
        "file_preprocessing",
        tsv_path,
        prefix=prefix,
        study_fields=fields,
        skip_existing=skip_existing_attachments,
    )
    stats = drakkar.parse_preprocessing_tsv(tsv_path)
    if not stats:
        out.warn(f"{label}{tsv_path.name} at {tsv_path} could not be parsed — stats not uploaded.")
    else:
        n = client.update_sample_preprocessing_stats(samples_table, stats)
        if n:
            out.success(f"{label}uploaded preprocessing stats for {_pl(n, 'sample')}.")
        else:
            out.warn(
                f"{label}{tsv_path.name} parsed ({len(stats)} sample(s)) but no matching "
                f"Airtable records found — stats not uploaded. "
                f"Check that the sample 'code' field values match the TSV."
            )
    return True


def _populate_genome_records_from_outputs(
    client: Any,
    samples_table: str,
    genomes_table: str,
    bin_metadata_path: Path,
    bin_paths_path: Path,
    *,
    prefix: str = "",
    upload_files_in_screen: bool = False,
    screen_args: argparse.Namespace | None = None,
    study_code: str = "",
    output_root: Path | None = None,
) -> bool:
    """Create/update Genomes rows and upload bin FASTA attachments from cataloging output."""
    from wmw import drakkar

    label = f"{prefix}: " if prefix else ""
    if not genomes_table:
        out.warn(f"{label}GENOMES_TABLE is not configured — genome metadata not uploaded.")
        return False
    if not bin_metadata_path.exists():
        out.warn(
            f"{label}all_bin_metadata.csv not found at {bin_metadata_path} — "
            f"genome metadata not uploaded."
        )
        return False

    genomes = drakkar.parse_bin_metadata_csv(bin_metadata_path)
    if not genomes:
        out.warn(
            f"{label}all_bin_metadata.csv at {bin_metadata_path} could not be parsed — "
            f"genome metadata not uploaded."
        )
        return True

    name_fid = str(cfg.get("GENOMES_COL_NAME") or "").strip()
    sample_link_fid = str(cfg.get("GENOMES_COL_SAMPLE_ID") or "").strip()
    completeness_fid = str(cfg.get("GENOMES_COL_COMPLETENESS") or "").strip()
    contamination_fid = str(cfg.get("GENOMES_COL_CONTAMINATION") or "").strip()
    if not name_fid:
        out.warn(f"{label}GENOMES_COL_NAME is not configured — genome metadata not uploaded.")
        return True
    if not sample_link_fid:
        out.warn(
            f"{label}GENOMES_COL_SAMPLE_ID is not configured — "
            f"genome metadata not uploaded."
        )
        return True
    if not completeness_fid or not contamination_fid:
        out.warn(
            f"{label}GENOMES_COL_COMPLETENESS and GENOMES_COL_CONTAMINATION are required "
            f"for genome quality filtering — genome metadata and files not uploaded."
        )
        return True

    skipped_quality = [
        str(genome.get("genome_name", "") or "").strip() or str(genome.get("sample_code", "") or "").strip()
        for genome in genomes
        if not _genome_passes_quality_filter(genome, completeness_fid, contamination_fid)
    ]
    if skipped_quality:
        preview = ", ".join(skipped_quality[:10])
        suffix = "…" if len(skipped_quality) > 10 else "."
        out.warn(
            f"{label}skipped {_pl(len(skipped_quality), 'genome')} below quality thresholds "
            f"(completeness > {_GENOME_MIN_COMPLETENESS:g}, "
            f"contamination < {_GENOME_MAX_CONTAMINATION:g}): {preview}{suffix}"
        )
    genomes = [
        genome
        for genome in genomes
        if _genome_passes_quality_filter(genome, completeness_fid, contamination_fid)
    ]
    if not genomes:
        out.warn(
            f"{label}no genomes passed quality thresholds — "
            f"genome metadata and files not uploaded."
        )
        return True

    sample_codes = {str(g.get("sample_code", "")) for g in genomes}
    sample_ids = client.fetch_sample_record_ids_by_code(samples_table, sample_codes)
    genome_names = [str(g.get("genome_name", "") or "").strip() for g in genomes]
    existing_records = client.fetch_genome_records_by_name(
        genomes_table,
        genome_names,
        name_fid,
    )

    records_to_create: list[dict[str, Any]] = []
    genomes_to_create: list[dict[str, Any]] = []
    records_to_update: list[dict[str, Any]] = []
    genome_record_ids: dict[str, str] = {}
    missing_sample_codes: set[str] = set()
    missing_genomes = 0

    for genome in genomes:
        sample_code = str(genome.get("sample_code", "") or "").strip()
        genome_name = str(genome.get("genome_name", "") or "").strip()
        sample_record_id = sample_ids.get(sample_code)
        if not sample_record_id:
            missing_sample_codes.add(sample_code)
            missing_genomes += 1
            continue

        fields = dict(genome.get("fields", {}) or {})
        fields[sample_link_fid] = [sample_record_id]

        existing = existing_records.get(genome_name)
        if existing:
            record_id = str(existing.get("id") or "").strip()
            if record_id:
                records_to_update.append({"id": record_id, "fields": fields})
                genome_record_ids[genome_name] = record_id
            continue

        records_to_create.append(fields)
        genomes_to_create.append(genome)

    updated = client.update_genome_records(genomes_table, records_to_update)
    if updated:
        out.success(f"{label}updated existing genome metadata for {_pl(updated, 'genome')}.")

    created_records: list[dict[str, Any]] = []
    if records_to_create:
        created_records = client.create_genome_records_with_response(
            genomes_table,
            records_to_create,
        )
    if created_records:
        out.success(
            f"{label}created genome metadata records for "
            f"{_pl(len(created_records), 'genome')}."
        )
        genome_record_ids.update(
            _created_genome_record_ids(created_records, genomes_to_create, name_fid)
        )

    if missing_sample_codes:
        out.warn(
            f"{label}skipped {_pl(missing_genomes, 'genome')} with no matching sample code: "
            f"{', '.join(sorted(missing_sample_codes))}."
        )

    if not bin_paths_path.exists():
        out.warn(
            f"{label}all_bin_paths.txt not found at {bin_paths_path} — "
            f"genome files not uploaded."
        )
        return True

    bin_paths = drakkar.parse_bin_paths_txt(bin_paths_path)
    if not bin_paths:
        out.warn(
            f"{label}all_bin_paths.txt at {bin_paths_path} could not be parsed — "
            f"genome files not uploaded."
        )
    elif not genome_record_ids:
        out.warn(
            f"{label}genome records could not be matched by name — "
            f"genome files not uploaded."
        )
    else:
        launched = False
        if (
            upload_files_in_screen
            and screen_args is not None
            and study_code
            and output_root is not None
        ):
            launched = _launch_genome_file_upload_screen(
                screen_args,
                study_code,
                output_root,
                samples_table,
                genomes_table,
                prefix=prefix,
            )
        if not launched:
            _upload_genome_bin_attachments(
                client,
                genomes_table,
                genome_record_ids,
                bin_paths,
                existing_records_by_name=existing_records,
            )

    return True


def _finalize_profiling_outputs(
    client: Any,
    studies_table: str,
    samples_table: str,
    study_record: dict[str, Any],
    output_root: Path,
    *,
    prefix: str = "",
) -> bool:
    """Upload profiling outputs and sample MAG mapping rates when present."""
    from wmw import drakkar

    fields = study_record.get("fields", {}) or {}
    study_code = str(fields.get("code", "") or "").strip()
    work_dir = output_root / study_code
    tsv_path = work_dir / "profiling_genomes.tsv"
    bases_path = work_dir / "profiling_genomes" / "final" / "bases.tsv"
    counts_path = work_dir / "profiling_genomes" / "final" / "counts.tsv"
    label = f"{prefix}: " if prefix else ""

    if not tsv_path.exists():
        out.warn(
            f"{label}profiling_genomes.tsv not found at {tsv_path} — "
            f"file and stats not uploaded."
        )
        return False

    _upload_study_tsv_attachment(
        client,
        studies_table,
        study_record["id"],
        "file_quantifying",
        tsv_path,
        prefix=prefix,
        study_fields=fields,
    )

    stats = drakkar.parse_profiling_tsv(tsv_path)
    if not stats:
        out.warn(f"{label}profiling_genomes.tsv could not be parsed — mapping rates not uploaded.")
    else:
        n = client.update_sample_profiling_stats(samples_table, stats)
        if n:
            out.success(f"{label}uploaded profiling mapping rates for {_pl(n, 'sample')}.")
        else:
            out.warn(
                f"{label}profiling_genomes.tsv parsed ({len(stats)} sample(s)) but no matching "
                f"Airtable records found — mapping rates not uploaded. "
                f"Check that the sample 'code' field values match the TSV."
            )

    for path, field_name in [
        (bases_path, "file_bases"),
        (counts_path, "file_counts"),
    ]:
        if not path.exists():
            out.warn(f"{label}{path.name} not found at {path} — file not uploaded.")
        else:
            _upload_study_tsv_attachment(
                client,
                studies_table,
                study_record["id"],
                field_name,
                path,
                prefix=prefix,
                study_fields=fields,
            )

    return True


def _finalize_cataloging_outputs(
    client: Any,
    studies_table: str,
    samples_table: str,
    genomes_table: str,
    study_record: dict[str, Any],
    output_root: Path,
    *,
    set_status: bool = False,
    skip_existing_attachments: bool = False,
    prefix: str = "",
    screen_args: argparse.Namespace | None = None,
) -> bool:
    """Upload cataloging outputs, sample assembly stats, and Genomes rows/files."""
    from wmw import drakkar

    fields = study_record.get("fields", {}) or {}
    study_code = str(fields.get("code", "") or "").strip()
    work_dir = output_root / study_code
    tsv_path = _existing_workflow_tsv_path(work_dir, study_code, "cataloging")
    bin_metadata_path = work_dir / "cataloging" / "final" / "all_bin_metadata.csv"
    bin_paths_path = work_dir / "cataloging" / "final" / "all_bin_paths.txt"
    has_cataloging_output = tsv_path.exists() or bin_metadata_path.exists()
    label = f"{prefix}: " if prefix else ""

    if set_status and has_cataloging_output:
        client.set_study_status(studies_table, [study_record["id"]], "cataloged")
        out.success(f"{label}study status → 'cataloged'.")

    if not tsv_path.exists():
        out.warn(
            f"{label}{tsv_path.name} not found at {tsv_path} — "
            f"file and stats not uploaded."
        )
    else:
        _upload_study_tsv_attachment(
            client,
            studies_table,
            study_record["id"],
            "file_cataloging",
            tsv_path,
            prefix=prefix,
            study_fields=fields,
            skip_existing=skip_existing_attachments,
        )
        stats = drakkar.parse_cataloging_tsv(tsv_path)
        if not stats:
            out.warn(f"{label}{tsv_path.name} at {tsv_path} could not be parsed — stats not uploaded.")
        else:
            n = client.update_sample_cataloging_stats(samples_table, stats)
            if n:
                out.success(f"{label}uploaded cataloging stats for {_pl(n, 'sample')}.")
            else:
                out.warn(
                    f"{label}{tsv_path.name} parsed ({len(stats)} assembly row(s)) but no matching "
                    f"Airtable records found — stats not uploaded. "
                    f"Check that the sample 'code' field values match the assembly column."
                )

    genomes_processed = _populate_genome_records_from_outputs(
        client,
        samples_table,
        genomes_table,
        bin_metadata_path,
        bin_paths_path,
        prefix=prefix,
        upload_files_in_screen=screen_args is not None,
        screen_args=screen_args,
        study_code=study_code,
        output_root=output_root,
    )
    return has_cataloging_output or genomes_processed


def cmd_set_status(args: argparse.Namespace) -> int:
    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
    genomes_table = _conf(args, "genomes_table", "GENOMES_TABLE")
    study_code = args.study
    workflow = args.workflow
    status = args.status

    airtable_status = _PROCESS_STATUS_MAP.get((workflow, status), status)

    out.info(f"Setting status for batch {study_code} ({workflow} → {airtable_status!r})…")
    client = _require_airtable(args, studies_table, samples_table)

    study_record = client.fetch_study_by_code(studies_table, study_code)
    if not study_record:
        _die(f"No study with code {study_code!r} found in Airtable.")
    assert study_record is not None

    client.set_study_status(studies_table, [study_record["id"]], airtable_status)
    out.success(f"Study {study_code} status → {airtable_status!r}.")

    if workflow == "preprocessing" and status in ("completed", "preprocessed"):
        output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR")
        if output_dir_str:
            _finalize_preprocessing_outputs(
                client,
                studies_table,
                samples_table,
                study_record,
                Path(output_dir_str).expanduser().resolve(),
            )

    if workflow == "cataloging" and status == "cataloged":
        output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR")
        if output_dir_str:
            _finalize_cataloging_outputs(
                client,
                studies_table,
                samples_table,
                genomes_table,
                study_record,
                Path(output_dir_str).expanduser().resolve(),
                screen_args=args,
            )

    if workflow == "profiling" and status == "quantified":
        output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR")
        if output_dir_str:
            _finalize_profiling_outputs(
                client,
                studies_table,
                samples_table,
                study_record,
                Path(output_dir_str).expanduser().resolve(),
            )

    if workflow == "annotating" and status in ("completed", "annotated", "Done"):
        output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR")
        if output_dir_str:
            _finalize_annotation_outputs(
                client,
                genomes_table,
                study_record,
                Path(output_dir_str).expanduser().resolve(),
            )

    return 0


def cmd_upload_genome_files(args: argparse.Namespace) -> int:
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
    genomes_table = _conf(args, "genomes_table", "GENOMES_TABLE")
    output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR", required=True)
    study_code = args.study

    out.section("WMW GENOME FASTA UPLOAD")
    if not genomes_table:
        _die("GENOMES_TABLE is not configured — genome files not uploaded.")

    client = _require_airtable(args, samples_table, genomes_table)
    output_root = Path(output_dir_str).expanduser().resolve()
    final_dir = output_root / study_code / "cataloging" / "final"
    processed = _populate_genome_records_from_outputs(
        client,
        samples_table,
        genomes_table,
        final_dir / "all_bin_metadata.csv",
        final_dir / "all_bin_paths.txt",
        prefix=study_code,
    )
    return 0 if processed else 1


# ---------------------------------------------------------------------------
# wmw status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
    batch = args.batch or None

    out.section("WMW STATUS")
    client = _airtable_client(args)

    # Samples breakdown
    formula = f'{{batch}} = "{batch}"' if batch else None
    samples = client.fetch_all(samples_table, formula=formula)

    counts: dict[str, int] = {}
    for rec in samples:
        status = rec["fields"].get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1

    tbl = out.make_table("Status", "Count")
    if tbl is None:
        for status, count in sorted(counts.items()):
            print(f"  {status:<20} {count}")
    else:
        for status, count in sorted(counts.items()):
            tbl.add_row(status, str(count))
        out.render_table(tbl)

    out.info(f"Total samples: {len(samples)}")
    return 0


# ---------------------------------------------------------------------------
# wmw config
# ---------------------------------------------------------------------------

def cmd_config(args: argparse.Namespace) -> int:
    if args.view:
        return cfg.view_config()
    if args.edit:
        return cfg.edit_config()
    return 0


# ---------------------------------------------------------------------------
# wmw update
# ---------------------------------------------------------------------------

def cmd_update(args: argparse.Namespace) -> int:
    import subprocess as sp
    out.info(f"Current version: wmw {__version__}")
    out.info(f"Installing latest from {args.repo} …")
    result = sp.run(
        [sys.executable, "-m", "pip", "install", "--force-reinstall", f"git+{args.repo}"],
        check=False,
    )
    if result.returncode != 0:
        out.error("Update failed. Check the output above for details.")
        return result.returncode
    out.success("Update complete. Run 'wmw --version' to confirm the new version.")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = _WmwParser(
        prog="wmw",
        description="Wild Microbiome Watch — scan ENA/SRA and drive Drakkar metagenomics workflows.",
        epilog=(
            "Typical workflow:\n"
            "  1. wmw scan --from 2024-01-01 --to 2024-12-31\n"
            "       → Discovers studies; populates Airtable Studies table.\n"
            "  2. Review studies in Airtable; set status = 'approved' to include.\n"
            "  3. wmw fetch\n"
            "       → Fetches run/sample data for approved studies; populates Samples.\n"
            "  4. wmw process [--batch CODE]\n"
            "       → Generates <CODE>.tsv and <CODE>.sh; run the script to launch Drakkar.\n"
            "\n"
            "Other examples:\n"
            "  wmw scan --study PRJEB12345\n"
            "  wmw fetch --study PRJEB12345\n"
            "  wmw stop --batch BATCH_01\n"
            "  wmw status --batch BATCH_01\n"
            "  wmw config --edit\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"wmw {__version__}")

    # Common Airtable flags (available on scan, fetch, process, status)
    _add_airtable_flags(parser)

    sub = parser.add_subparsers(title="commands", metavar="<command>", parser_class=_WmwParser)

    # ---- scan ----
    p_scan = sub.add_parser(
        "scan",
        help="Search ENA for wild-animal studies and populate the Airtable Studies table.",
        description=(
            "Query the ENA Portal study endpoint for a date range (or a single accession) "
            "and upsert study records into Airtable. Run-level sample data is NOT fetched "
            "at this stage — use 'wmw fetch' after reviewing and approving studies."
        ),
    )
    _add_airtable_flags(p_scan)
    p_scan.add_argument(
        "--from",
        dest="date_from",
        metavar="DATE",
        help="Start of date window (YYYY-MM-DD).",
    )
    p_scan.add_argument(
        "--to",
        dest="date_to",
        metavar="DATE",
        help="End of date window (YYYY-MM-DD).",
    )
    p_scan.add_argument(
        "--year",
        metavar="YEAR[,YEAR]",
        default="",
        help=(
            "One or two comma-separated years that define the date window "
            "(e.g. 2025 or 2024,2026). "
            "Single year → full calendar year. Two years → first Jan to last Dec. "
            "Can be combined with --month. Overrides --from/--to."
        ),
    )
    p_scan.add_argument(
        "--month",
        metavar="MONTH[,MONTH]",
        default="",
        help=(
            "One or two comma-separated month names that define the date window "
            "(e.g. March or March,June). "
            "Single month → that month only. Two months → first month to last month. "
            "Uses the current year unless --year is also provided. Overrides --from/--to."
        ),
    )
    p_scan.add_argument(
        "--study",
        metavar="ACCESSION",
        help="Add a single study by ENA accession (overrides date window).",
    )
    p_scan.add_argument(
        "--taxonomy",
        metavar="NAME",
        default="",
        help=(
            "Restrict studies to a taxonomic group by name (e.g. Chiroptera, Mammalia). "
            "Resolved to an NCBI tax_id via the ENA Taxonomy API and applied with "
            "ENA's tax_tree() operator, which matches all organisms within the subtree."
        ),
    )
    p_scan.add_argument(
        "--host-tax-id",
        metavar="TAXON_ID",
        default="",
        help=(
            "NCBI taxon ID to filter studies by organism (e.g. 7742 for Vertebrata). "
            "Applied against the ENA study tax_id field — approximate host filter."
        ),
    )
    p_scan.add_argument(
        "--library-strategy",
        metavar="STRATEGY",
        default="",
        help=(
            "Comma-separated ENA library strategies to require (default from config or WGS,METAGENOMIC). "
            "Excludes amplicon and other non-shotgun data at query time."
        ),
    )
    p_scan.add_argument(
        "--library-source",
        metavar="SOURCE",
        default="",
        help=(
            "Comma-separated ENA library sources to require, e.g. METAGENOMIC "
            "(default from config: no filter). Excludes animal genomic libraries."
        ),
    )
    p_scan.add_argument(
        "--date-field",
        metavar="FIELD",
        default="",
        choices=["", "first_public", "last_updated", "collection_date"],
        help="ENA date field for the date window (default from config: first_public).",
    )
    p_scan.add_argument(
        "--keyword",
        metavar="TEXT",
        default="",
        help=(
            "Free-text keyword(s) matched against study title and description. "
            "Separate multiple keywords with | (OR logic). "
            "Overrides SCAN_KEYWORDS from config (default: ECOLOGY|EVOLUTION|WILD)."
        ),
    )
    p_scan.add_argument(
        "--include",
        metavar="GROUPS",
        default="",
        help=(
            "Comma-separated exclusion groups to re-enable: Human, Livestock, Aquaculture, Laboratory. "
            "Use 'All' to disable all host-taxon exclusions (default: all groups excluded)."
        ),
    )
    p_scan.add_argument(
        "--run-batch",
        metavar="N",
        type=int,
        default=20,
        help=(
            "Number of studies per run-query batch when a taxonomy/keyword filter is active "
            "(default: 20). Smaller values avoid the ENA 10 000-record limit per query."
        ),
    )
    p_scan.add_argument(
        "--debug",
        action="store_true",
        help="Print the full ENA API URL for every request (useful for diagnosing query issues).",
    )
    p_scan.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without writing to Airtable.",
    )
    p_scan.add_argument(
        "--no-publications",
        action="store_true",
        help="Skip publication metadata lookup (PubMed/CrossRef). Faster but leaves pub_* fields empty.",
    )
    p_scan.add_argument(
        "--studies-table",
        metavar="TABLE",
        default="",
        help="Override Studies table name from config.",
    )
    p_scan.set_defaults(func=cmd_scan)

    # ---- fetch ----
    p_fetch = sub.add_parser(
        "fetch",
        help="Fetch run/sample data for approved studies and populate the Airtable Samples table.",
        description=(
            "Read studies whose status equals --status (default: 'approved') from the "
            "Airtable Studies table, fetch all run records from ENA for each, apply "
            "run-level filters, and upsert into the Samples table. Study status is "
            "updated to 'indexed' after a successful fetch."
        ),
    )
    _add_airtable_flags(p_fetch)
    p_fetch.add_argument(
        "--status",
        metavar="VALUE",
        default="approved",
        help="Study status value to process (default: approved).",
    )
    p_fetch.add_argument(
        "--study",
        metavar="ACCESSION",
        help="Fetch a single study by ENA accession directly (bypasses status filter).",
    )
    p_fetch.add_argument(
        "--library-strategy",
        metavar="STRATEGY",
        default="WGS,METAGENOMIC",
        help="Comma-separated library strategies to include (default: WGS,METAGENOMIC).",
    )
    p_fetch.add_argument(
        "--library-source",
        metavar="SOURCE",
        default="",
        help="Comma-separated library sources to include, e.g. METAGENOMIC (default: no filter).",
    )
    p_fetch.add_argument(
        "--instrument-platform",
        metavar="PLATFORM",
        default="",
        help="Restrict to a single sequencing platform, e.g. ILLUMINA (default: no filter).",
    )
    p_fetch.add_argument(
        "--min-bases",
        metavar="N",
        default="",
        help="Minimum total base count per run (default: no minimum).",
    )
    p_fetch.add_argument(
        "--include",
        metavar="GROUPS",
        default="",
        help=(
            "Comma-separated exclusion groups to re-enable: Human, Livestock, Aquaculture, Laboratory. "
            "Use 'All' to disable all host-taxon exclusions (default: all groups excluded)."
        ),
    )
    p_fetch.add_argument(
        "--exclude-taxa",
        metavar="IDS",
        default="",
        help=(
            "Comma-separated host taxon IDs to exclude in addition to config groups "
            "(e.g. 9615,9685 to also exclude dogs and cats)."
        ),
    )
    p_fetch.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without writing to Airtable.",
    )
    p_fetch.add_argument(
        "--studies-table",
        metavar="TABLE",
        default="",
        help="Override Studies table name from config.",
    )
    p_fetch.add_argument(
        "--samples-table",
        metavar="TABLE",
        default="",
        help="Override Samples table name from config.",
    )
    p_fetch.set_defaults(func=cmd_fetch)

    # ---- process ----
    p_process = sub.add_parser(
        "process",
        help="Generate Drakkar input files and launch scripts for ready studies.",
        description=(
            "For each study with status 'ready' or 'rerun', fetch its ready samples "
            "from Airtable, create a working directory under DRAKKAR_OUTPUT_DIR/<code>/, "
            "write a <code>.tsv input file, and write a <code>.sh launch script that "
            "runs Drakkar and logs progress back to Airtable. Studies with status "
            "'resume' resolve pending Airtable tasks and launch the next missing "
            "Drakkar task when needed."
        ),
    )
    _add_airtable_flags(p_process)
    p_process.add_argument(
        "--batch",
        metavar="CODE",
        default="",
        help="Process only the study whose code matches CODE (default: all actionable studies).",
    )
    p_process.add_argument(
        "--workflow",
        metavar="STAGE",
        default="preprocessing",
        choices=[
            "preprocessing",
            "cataloging",
            "annotating",
            "profiling",
        ],
        help="Drakkar workflow stage to generate a script for (default: preprocessing).",
    )
    p_process.add_argument(
        "--slurm",
        action="store_true",
        help="Add the Drakkar --slurm flag to the generated script (HPC cluster submission).",
    )
    p_process.add_argument(
        "--output-dir",
        metavar="DIR",
        default="",
        help="Override DRAKKAR_OUTPUT_DIR from config.",
    )
    p_process.add_argument(
        "--studies-table",
        metavar="TABLE",
        default="",
        help="Override Studies table name from config.",
    )
    p_process.add_argument(
        "--samples-table",
        metavar="TABLE",
        default="",
        help="Override Samples table name from config.",
    )
    p_process.add_argument(
        "--genomes-table",
        metavar="TABLE",
        default="",
        help="Override Genomes table name from config.",
    )
    p_process.set_defaults(func=cmd_process)

    # ---- stop ----
    p_stop = sub.add_parser(
        "stop",
        help="Stop an ongoing wmw processing run.",
        description=(
            "Set the study status to 'stopped', stop the matching screen session, "
            "and best-effort cancel matching Slurm jobs launched by Drakkar."
        ),
    )
    _add_airtable_flags(p_stop)
    stop_target = p_stop.add_mutually_exclusive_group(required=True)
    stop_target.add_argument(
        "--batch",
        dest="batch",
        metavar="CODE",
        help="Study code / screen session name to stop.",
    )
    stop_target.add_argument(
        "--study",
        dest="batch",
        metavar="CODE",
        help="Alias for --batch.",
    )
    p_stop.add_argument(
        "--output-dir",
        metavar="DIR",
        default="",
        help="Override DRAKKAR_OUTPUT_DIR from config.",
    )
    p_stop.add_argument(
        "--studies-table",
        metavar="TABLE",
        default="",
        help="Override Studies table name from config.",
    )
    p_stop.add_argument(
        "--samples-table",
        metavar="TABLE",
        default="",
        help="Override Samples table name from config.",
    )
    p_stop.set_defaults(func=cmd_stop)

    # ---- set-status ----
    p_setstatus = sub.add_parser(
        "set-status",
        help="Update study status in Airtable (called by generated scripts).",
        description=(
            "Update the status of a study in Airtable. "
            "Intended for use inside wmw-generated launch scripts."
        ),
    )
    _add_airtable_flags(p_setstatus)
    p_setstatus.add_argument(
        "--study",
        metavar="CODE",
        required=True,
        help="Study code (batch label) to update.",
    )
    p_setstatus.add_argument(
        "--workflow",
        metavar="STAGE",
        required=True,
        choices=["preprocessing", "cataloging", "annotating", "profiling"],
        help="Workflow stage that is reporting its status.",
    )
    p_setstatus.add_argument(
        "--status",
        metavar="STATUS",
        required=True,
        choices=[
            "preprocessing",
            "running",
            "completed",
            "preprocessed",
            "cataloging",
            "cataloged",
            "quantifying",
            "quantified",
            "annotating",
            "annotated",
            "Done",
            "stopped",
            "error",
        ],
        help="New status to set.",
    )
    p_setstatus.add_argument(
        "--studies-table",
        metavar="TABLE",
        default="",
        help="Override Studies table name from config.",
    )
    p_setstatus.add_argument(
        "--samples-table",
        metavar="TABLE",
        default="",
        help="Override Samples table name from config.",
    )
    p_setstatus.add_argument(
        "--genomes-table",
        metavar="TABLE",
        default="",
        help="Override Genomes table name from config.",
    )
    p_setstatus.add_argument(
        "--output-dir",
        metavar="DIR",
        default="",
        help="Override DRAKKAR_OUTPUT_DIR from config.",
    )
    p_setstatus.set_defaults(func=cmd_set_status)

    # ---- upload-genome-files (internal) ----
    p_upload_genomes = sub.add_parser(
        "upload-genome-files",
        help="Upload generated genome FASTA attachments for one study.",
        description="Upload generated genome FASTA attachments for one study.",
    )
    _add_airtable_flags(p_upload_genomes)
    p_upload_genomes.add_argument(
        "--study",
        metavar="CODE",
        required=True,
        help="Study code (batch label) whose genome FASTA files should be uploaded.",
    )
    p_upload_genomes.add_argument(
        "--output-dir",
        metavar="DIR",
        default="",
        help="Override DRAKKAR_OUTPUT_DIR from config.",
    )
    p_upload_genomes.add_argument(
        "--samples-table",
        metavar="TABLE",
        default="",
        help="Override Samples table name from config.",
    )
    p_upload_genomes.add_argument(
        "--genomes-table",
        metavar="TABLE",
        default="",
        help="Override Genomes table name from config.",
    )
    p_upload_genomes.set_defaults(func=cmd_upload_genome_files)

    # ---- status ----
    p_status = sub.add_parser(
        "status",
        help="Show a breakdown of sample statuses in Airtable.",
    )
    _add_airtable_flags(p_status)
    p_status.add_argument(
        "--batch",
        metavar="BATCH",
        default="",
        help="Filter by batch label.",
    )
    p_status.add_argument(
        "--studies-table",
        metavar="TABLE",
        default="",
        help="Override Studies table name from config.",
    )
    p_status.add_argument(
        "--samples-table",
        metavar="TABLE",
        default="",
        help="Override Samples table name from config.",
    )
    p_status.set_defaults(func=cmd_status)

    # ---- config ----
    p_config = sub.add_parser(
        "config",
        help="View or edit the wmw configuration file.",
    )
    g = p_config.add_mutually_exclusive_group(required=True)
    g.add_argument("--view", action="store_true", help="Print the config file path and contents.")
    g.add_argument("--edit", action="store_true", help="Open the config file in $EDITOR.")
    p_config.set_defaults(func=cmd_config)

    # ---- update ----
    p_update = sub.add_parser(
        "update",
        help="Update wmw to the latest version from GitHub.",
        description="Reinstalls wmw from the main branch on GitHub using pip.",
    )
    p_update.add_argument(
        "--repo",
        default="https://github.com/alberdilab/wmw.git",
        metavar="URL",
        help="Git repository URL to install from (default: GitHub main branch).",
    )
    p_update.set_defaults(func=cmd_update)

    return parser


def _add_airtable_flags(parser: argparse.ArgumentParser) -> None:
    """Add shared Airtable connection flags (idempotent — skipped if already present)."""
    existing = {a.dest for a in parser._actions}
    if "airtable_token" not in existing:
        parser.add_argument(
            "--airtable-token",
            metavar="TOKEN",
            default="",
            help="Airtable personal access token (overrides $AIRTABLE_TOKEN and config).",
        )
    if "base_id" not in existing:
        parser.add_argument(
            "--base-id",
            metavar="BASE_ID",
            default="",
            help="Airtable base ID (overrides WMW_BASE in config).",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        out.warn("Interrupted.")
        return 130
