"""Command-line interface for wmw — Wild Microbiome Watch."""

from __future__ import annotations

import argparse
import calendar
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

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
        all_runs, n_host_excluded = metadata.filter_runs(
            all_runs,
            exclude_host_tax_ids=params["exclude_ids"],
        )
        out.info(
            f"Excluding {len(params['exclude_ids'])} host taxon ID(s). Use --include All to disable."
        )
        if n_host_excluded:
            out.info(f"  Host exclusion filter removed {_pl(n_host_excluded, 'run')}.")

    all_runs, n_other_excluded = metadata.filter_runs(
        all_runs,
        min_bases=params["min_bases"],
        library_strategies=params["library_strategies"],
        library_sources=params["library_sources"],
        instrument_platform=params["instrument_platform"],
    )
    if n_other_excluded:
        out.info(f"Run filter(s) removed {_pl(n_other_excluded, 'run')}.")

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

def cmd_process(args: argparse.Namespace) -> int:
    from wmw import drakkar

    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
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
    for study in actionable_studies:
        fields = study["fields"]
        code = fields.get("code", "")
        study_accession = fields.get("study_accession", "")
        study_status = fields.get("status", "ready")
        if not code:
            out.warn(f"Study {study['id']} has no code — skipping.")
            continue

        work_dir = output_dir / code

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

        # Resume shortcut: if the preprocessing output already exists, finalise
        # (upload stats + update study status) without relaunching Drakkar.
        if study_status == "resume" and workflow == "preprocessing":
            preprocessing_tsv = work_dir / "preprocessing.tsv"
            if preprocessing_tsv.exists():
                out.info(f"{code}: preprocessing.tsv found — finalising without relaunch.")
                client.set_study_status(studies_table, [study["id"]], "preprocessed")
                out.success(f"{code}: study status → 'preprocessed'.")
                stats = drakkar.parse_preprocessing_tsv(preprocessing_tsv)
                if not stats:
                    out.warn(f"{code}: preprocessing.tsv empty or unparseable — stats not uploaded.")
                else:
                    n = client.update_sample_preprocessing_stats(samples_table, stats)
                    if n:
                        out.success(f"{code}: uploaded preprocessing stats for {_pl(n, 'sample')}.")
                    else:
                        out.warn(
                            f"{code}: preprocessing.tsv parsed ({len(stats)} sample(s)) but no matching "
                            f"Airtable records found — stats not uploaded. "
                            f"Check that the sample 'code' field values match the TSV."
                        )
                n_generated += 1
                continue

        input_tsv = work_dir / f"{code}.tsv"
        drakkar.build_input_tsv(samples, input_tsv)
        out.info(f"  Input TSV:     {input_tsv}")

        script_path = work_dir / f"{code}.sh"
        if workflow == "preprocessing":
            script = drakkar.generate_preprocessing_script(
                code=code,
                tsv_path=input_tsv,
                work_dir=work_dir,
                conda_env=conda_env,
                slurm=slurm,
                wmw_conda_env=wmw_conda_env,
                memory_multiplier=fields.get("memory_boost") or None,
                time_multiplier=fields.get("time_boost") or None,
            )
        else:
            _die(f"Workflow {workflow!r} script generation is not yet implemented.")
            return 1

        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o755)
        out.info(f"  Launch script: {script_path}")

        import subprocess as sp
        if shutil.which("screen") is None:
            out.warn(f"  'screen' not found — script written but not launched.")
        else:
            sp.run(["screen", "-dmS", code, "bash", str(script_path)], check=True)
            out.success(f"  Screen session '{code}' started.")

        n_generated += 1

    out.success(f"Generated scripts for {_pl(n_generated, 'study', 'studies')}.")
    return 0


# ---------------------------------------------------------------------------
# wmw set-status
# ---------------------------------------------------------------------------

_PROCESS_STATUS_MAP: dict[tuple[str, str], str] = {
    ("preprocessing", "preprocessing"): "preprocessing",
    ("preprocessing", "running"):       "preprocessing",  # legacy compat
    ("preprocessing", "completed"):     "preprocessed",   # legacy compat
    ("preprocessing", "preprocessed"):  "preprocessed",
    ("preprocessing", "error"):         "error",
    ("cataloging",    "cataloging"):    "cataloging",
    ("cataloging",    "cataloged"):     "cataloged",
    ("cataloging",    "error"):         "error",
}


def cmd_set_status(args: argparse.Namespace) -> int:
    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
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
        from wmw import drakkar
        output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR")
        if output_dir_str:
            tsv_path = Path(output_dir_str).expanduser().resolve() / study_code / "preprocessing.tsv"
            if not tsv_path.exists():
                out.warn(f"preprocessing.tsv not found at {tsv_path} — stats not uploaded.")
            else:
                stats = drakkar.parse_preprocessing_tsv(tsv_path)
                if not stats:
                    out.warn(f"preprocessing.tsv at {tsv_path} could not be parsed — stats not uploaded.")
                else:
                    n = client.update_sample_preprocessing_stats(samples_table, stats)
                    if n:
                        out.success(f"Uploaded preprocessing stats for {_pl(n, 'sample')}.")
                    else:
                        out.warn(
                            f"preprocessing.tsv parsed ({len(stats)} sample(s)) but no matching "
                            f"Airtable records found — stats not uploaded. "
                            f"Check that the sample 'code' field values match the TSV."
                        )

    return 0


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
            "For each study with status 'ready', fetch its ready samples from Airtable, "
            "create a working directory under DRAKKAR_OUTPUT_DIR/<code>/, write a "
            "<code>.tsv input file, and write a <code>.sh launch script that runs "
            "Drakkar and logs progress back to Airtable."
        ),
    )
    _add_airtable_flags(p_process)
    p_process.add_argument(
        "--batch",
        metavar="CODE",
        default="",
        help="Process only the study whose code matches CODE (default: all ready studies).",
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
    p_process.set_defaults(func=cmd_process)

    # ---- set-status ----
    p_setstatus = sub.add_parser(
        "set-status",
        help="Update study and sample statuses in Airtable (called by generated scripts).",
        description=(
            "Update the status of a study (and all its samples) in Airtable. "
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
        choices=["preprocessing", "running", "completed", "preprocessed", "cataloging", "cataloged", "error"],
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
    p_setstatus.set_defaults(func=cmd_set_status)

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
