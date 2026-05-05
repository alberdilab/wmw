"""Command-line interface for wmw — Wild Microbiome Watch."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from wmw import __version__
from wmw import config as cfg
from wmw import output as out


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _die(msg: str) -> None:
    out.error(msg)
    sys.exit(1)


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
    return AirtableClient(token, base_id)


# ---------------------------------------------------------------------------
# wmw scan  (phase 1 — study discovery, ENA only)
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> int:
    from wmw import ena, metadata

    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    dry_run = args.dry_run
    date_field = _conf(args, "date_field", "DATE_FIELD") or "first_public"
    host_tax_id = (args.host_tax_id or "").strip()
    keyword = (getattr(args, "keyword", "") or "").strip()

    from wmw.ena import VALID_STUDY_DATE_FIELDS
    if date_field not in VALID_STUDY_DATE_FIELDS:
        _die(
            f"--date-field for wmw scan must be one of "
            f"{sorted(VALID_STUDY_DATE_FIELDS)}, got: {date_field!r}. "
            f"Use wmw fetch for run-level date fields."
        )

    # --- single-study mode ---
    if args.study:
        return _scan_single_study(args, args.study, studies_table, dry_run)

    # --- date-range mode ---
    if not args.date_from or not args.date_to:
        _die("Provide --from and --to dates, or a --study accession.")

    out.section("WMW SCAN")
    out.info(f"Date range: {args.date_from} → {args.date_to}  (field: {date_field})")
    if host_tax_id:
        out.info(f"Host taxon filter: {host_tax_id}")
    if keyword:
        out.info(f"Keyword filter: \"{keyword}\"")
    out.info("Database: ENA (study level)")

    out.info("Querying ENA Portal API for studies…")
    try:
        raw_studies = ena.search_studies(
            date_from=args.date_from,
            date_to=args.date_to,
            host_tax_id=host_tax_id,
            date_field=date_field,
            keyword=keyword,
        )
        out.success(f"ENA: {len(raw_studies)} study record(s) found.")
    except Exception as exc:
        _die(f"ENA study query failed: {exc}")
        return 1  # unreachable; satisfies type checker

    studies = [metadata.normalize_ena_study(s) for s in raw_studies]

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
        email = cfg.get("NCBI_EMAIL", "").strip()
        if not email:
            out.warn("NCBI_EMAIL not set — skipping publication lookup (set it with wmw config --edit).")
        else:
            from wmw import publications
            api_key = cfg.get("NCBI_API_KEY", "").strip() or None
            out.info(f"Resolving publication metadata for {len(studies)} studies…")
            publications.resolve_batch(studies, email=email, api_key=api_key)
            found = sum(1 for s in studies if s.get("pub_title"))
            out.success(f"Publications resolved: {found}/{len(studies)}.")

    _print_scan_summary(studies)

    if dry_run:
        out.info("Dry-run mode — no changes written to Airtable.")
        return 0

    client = _airtable_client(args)
    s_inserted, s_skipped = client.upsert_studies(studies_table, studies)
    out.success(f"Studies: {s_inserted} inserted, {s_skipped} already existed.")
    return 0


def _scan_single_study(
    args: argparse.Namespace,
    study_accession: str,
    studies_table: str,
    dry_run: bool,
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
        email = cfg.get("NCBI_EMAIL", "").strip()
        if email:
            from wmw import publications
            api_key = cfg.get("NCBI_API_KEY", "").strip() or None
            publications.resolve_batch([study], email=email, api_key=api_key)

    _print_scan_summary([study])

    if dry_run:
        out.info("Dry-run — no Airtable writes.")
        return 0

    client = _airtable_client(args)
    s_inserted, s_skipped = client.upsert_studies(studies_table, [study])
    out.success(f"Studies: {s_inserted} inserted, {s_skipped} already existed.")
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

    exclude_ids: list[str] = []
    if not args.no_exclude:
        config_list = cfg.get("EXCLUDED_HOST_TAX_IDS") or []
        exclude_ids = [str(t).strip() for t in config_list if str(t).strip()]
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
    client = None

    if args.study:
        out.info(f"Single-study mode: {args.study}")
        studies_to_fetch = [args.study]
    else:
        status_filter = args.status or "approved"
        client = _airtable_client(args)
        out.info(f"Reading studies with status='{status_filter}' from Airtable…")
        approved_records = client.fetch_studies_by_status(studies_table, status=status_filter)
        if not approved_records:
            out.info(f"No studies with status '{status_filter}' found.")
            return 0
        studies_to_fetch = [r["fields"].get("study_accession", "") for r in approved_records]
        studies_to_fetch = [acc for acc in studies_to_fetch if acc]
        record_id_map = {r["fields"].get("study_accession", ""): r["id"] for r in approved_records}
        out.success(f"{len(studies_to_fetch)} approved study/studies to fetch.")

    if params["library_strategies"]:
        out.info(f"Library strategy filter: {','.join(params['library_strategies'])}")
    if params["library_sources"]:
        out.info(f"Library source filter: {','.join(params['library_sources'])}")
    if params["instrument_platform"]:
        out.info(f"Instrument platform filter: {params['instrument_platform']}")
    if params["min_bases"]:
        out.info(f"Minimum base count: {params['min_bases']:,}")
    if params["exclude_ids"]:
        out.info(f"Excluding {len(params['exclude_ids'])} host taxon ID(s).")

    all_runs: list[dict] = []
    fetched_accessions: list[str] = []

    for acc in studies_to_fetch:
        out.info(f"Fetching runs for {acc}…")
        try:
            runs = ena.search_study(acc)
            normalized = metadata.normalize_runs(runs, "ENA")
            out.info(f"  {acc}: {len(normalized)} run(s).")
            all_runs.extend(normalized)
            fetched_accessions.append(acc)
        except Exception as exc:
            out.warn(f"  {acc}: fetch failed — {exc}")

    if not all_runs:
        out.info("No runs found.")
        return 0

    all_runs = metadata.deduplicate_runs(all_runs)

    all_runs, n_excluded = metadata.filter_runs(
        all_runs,
        exclude_host_tax_ids=params["exclude_ids"],
        min_bases=params["min_bases"],
        library_strategies=params["library_strategies"],
        library_sources=params["library_sources"],
        instrument_platform=params["instrument_platform"],
    )
    if n_excluded:
        out.info(f"Post-fetch filter removed {n_excluded} run(s).")

    out.info(f"Total runs after filtering: {len(all_runs)}")

    if dry_run:
        out.info("Dry-run mode — no changes written to Airtable.")
        return 0

    if client is None:
        client = _airtable_client(args)
    r_inserted, r_skipped = client.upsert_samples(samples_table, all_runs)
    out.success(f"Samples/runs: {r_inserted} inserted, {r_skipped} already existed.")

    if record_id_map and fetched_accessions:
        record_ids = [record_id_map[acc] for acc in fetched_accessions if acc in record_id_map]
        if record_ids:
            client.set_study_status(studies_table, record_ids, "indexed")
            out.success(f"Updated {len(record_ids)} study status(es) to 'indexed'.")

    return 0


def _print_scan_summary(studies: list[dict]) -> None:
    tbl = out.make_table("Study accession", "Title", "Source", "First public")
    if tbl is None:
        for s in studies:
            print(f"  {s.get('study_accession')}  {s.get('study_title', '')[:60]}")
        return
    for s in studies:
        tbl.add_row(
            s.get("study_accession", ""),
            (s.get("study_title") or "")[:60],
            s.get("source", ""),
            s.get("first_public", ""),
        )
    out.render_table(tbl)


# ---------------------------------------------------------------------------
# wmw process
# ---------------------------------------------------------------------------

def cmd_process(args: argparse.Namespace) -> int:
    from wmw import drakkar

    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
    output_dir_str = _conf(args, "output_dir", "DRAKKAR_OUTPUT_DIR", required=True)
    output_dir = Path(output_dir_str).expanduser().resolve()
    batch = args.batch or None
    workflow = args.workflow
    slurm = args.slurm

    out.section("WMW PROCESS")
    out.info(f"Fetching samples from Airtable (batch={batch or 'all'}, status=pending)…")

    client = _airtable_client(args)
    samples = client.fetch_samples_for_processing(samples_table, batch=batch)

    if not samples:
        out.info("No pending samples found.")
        return 0

    out.success(f"{len(samples)} samples ready for processing.")

    # Build manifest
    batch_label = batch or "wmw"
    manifest_path = output_dir / batch_label / "manifest.tsv"
    drakkar.build_manifest(samples, manifest_path)
    out.info(f"Manifest written: {manifest_path}")

    # Mark samples as running
    record_ids = [r["id"] for r in samples]
    client.set_sample_status(samples_table, record_ids, "running")

    # Invoke drakkar
    out.info(f"Launching drakkar {workflow}…")
    run_output = output_dir / batch_label
    rc = drakkar.run_workflow(
        workflow=workflow,
        manifest=manifest_path,
        output_dir=run_output,
        slurm=slurm,
    )

    if rc == 0:
        client.set_sample_status(samples_table, record_ids, "completed")
        out.success("Drakkar workflow finished successfully.")
    else:
        client.set_sample_status(samples_table, record_ids, "failed")
        out.error(f"Drakkar exited with code {rc}.")
        return rc

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
    out.info("Upgrading wmw via pip…")
    result = sp.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "wmw"],
        check=False,
    )
    if result.returncode == 0:
        out.success("wmw updated successfully.")
    else:
        out.error("pip upgrade failed.")
    return result.returncode


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wmw",
        description="Wild Microbiome Watch — scan ENA/SRA and drive Drakkar metagenomics workflows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Typical workflow:\n"
            "  1. wmw scan --from 2024-01-01 --to 2024-12-31\n"
            "       → Discovers studies; populates Airtable Studies table.\n"
            "  2. Review studies in Airtable; set status = 'approved' to include.\n"
            "  3. wmw fetch\n"
            "       → Fetches run/sample data for approved studies; populates Samples.\n"
            "  4. wmw process --batch BATCH_01 --workflow preprocessing\n"
            "       → Runs Drakkar on pending samples.\n"
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

    sub = parser.add_subparsers(title="commands", metavar="<command>")

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
        "--study",
        metavar="ACCESSION",
        help="Add a single study by ENA accession (overrides date window).",
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
        "--date-field",
        metavar="FIELD",
        default="",
        choices=["", "first_public", "last_updated"],
        help="ENA date field for the date window (default from config: first_public).",
    )
    p_scan.add_argument(
        "--keyword",
        metavar="TEXT",
        default="",
        help="Free-text substring to match against study title.",
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
        "--exclude-taxa",
        metavar="IDS",
        default="",
        help=(
            "Comma-separated host taxon IDs to exclude, appended to the config exclusion list "
            "(e.g. 9615,9685 to also exclude dogs and cats)."
        ),
    )
    p_fetch.add_argument(
        "--no-exclude",
        action="store_true",
        help="Disable all host taxon exclusions (config list and --exclude-taxa).",
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
        help="Pull samples from Airtable and run the Drakkar metagenomics workflow.",
        description=(
            "Fetch samples with status 'pending' from Airtable, build a Drakkar manifest, "
            "invoke the chosen workflow stage, and update sample statuses on completion."
        ),
    )
    _add_airtable_flags(p_process)
    p_process.add_argument(
        "--batch",
        metavar="BATCH",
        default="",
        help="Filter samples by batch label. Processes all pending samples if omitted.",
    )
    p_process.add_argument(
        "--workflow",
        metavar="STAGE",
        default="preprocessing",
        choices=[
            "complete",
            "preprocessing",
            "cataloging",
            "profiling",
            "dereplicating",
            "annotating",
        ],
        help="Drakkar workflow stage to run (default: preprocessing).",
    )
    p_process.add_argument(
        "--slurm",
        action="store_true",
        help="Pass --slurm flag to Drakkar (for HPC cluster submission).",
    )
    p_process.add_argument(
        "--output-dir",
        metavar="DIR",
        default="",
        help="Override DRAKKAR_OUTPUT_DIR from config.",
    )
    p_process.add_argument(
        "--samples-table",
        metavar="TABLE",
        default="",
        help="Override Samples table name from config.",
    )
    p_process.set_defaults(func=cmd_process)

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
        help="Upgrade wmw to the latest PyPI release.",
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
