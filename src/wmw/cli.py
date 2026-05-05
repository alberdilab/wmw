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
# wmw scan
# ---------------------------------------------------------------------------

def _resolve_scan_params(args: argparse.Namespace) -> dict:
    """Collect all scan filter parameters from CLI flags and config fallbacks."""
    library_source    = _conf(args, "library_source",    "LIBRARY_SOURCE")
    instrument_platform = _conf(args, "instrument_platform", "INSTRUMENT_PLATFORM")
    date_field        = _conf(args, "date_field",         "DATE_FIELD") or "first_public"
    min_bases_str     = _conf(args, "min_bases",          "MIN_BASES")

    min_bases: int | None = None
    if min_bases_str:
        try:
            min_bases = int(min_bases_str)
        except ValueError:
            _die(f"--min-bases / MIN_BASES must be an integer, got: {min_bases_str!r}")

    from wmw.ena import VALID_DATE_FIELDS
    if date_field not in VALID_DATE_FIELDS:
        _die(f"--date-field must be one of {sorted(VALID_DATE_FIELDS)}, got: {date_field!r}")

    # Build exclusion list: config default + CLI additions, cleared by --no-exclude
    exclude_ids: list[str] = []
    if not args.no_exclude:
        config_list = cfg.get("EXCLUDED_HOST_TAX_IDS") or []
        exclude_ids = [str(t).strip() for t in config_list if str(t).strip()]
        cli_extra = getattr(args, "exclude_taxa", "") or ""
        for tid in cli_extra.split(","):
            tid = tid.strip()
            if tid and tid not in exclude_ids:
                exclude_ids.append(tid)

    return {
        "host_tax_id":        (args.host_tax_id or "").strip(),
        "library_strategy":   args.library_strategy,
        "library_source":     library_source,
        "instrument_platform": instrument_platform,
        "date_field":         date_field,
        "min_bases":          min_bases,
        "keyword":            (getattr(args, "keyword", "") or "").strip(),
        "exclude_ids":        exclude_ids,
    }


def cmd_scan(args: argparse.Namespace) -> int:
    from wmw import ena, sra, metadata

    studies_table = _conf(args, "studies_table", "STUDIES_TABLE") or "Studies"
    samples_table = _conf(args, "samples_table", "SAMPLES_TABLE") or "Samples"
    db = args.db
    dry_run = args.dry_run
    params = _resolve_scan_params(args)

    # --- single-study mode ---
    if args.study:
        return _scan_single_study(args, args.study, db, studies_table, samples_table,
                                  dry_run, params)

    # --- date-range mode ---
    if not args.date_from or not args.date_to:
        _die("Provide --from and --to dates, or a --study accession.")

    out.section("WMW SCAN")
    out.info(f"Date range: {args.date_from} → {args.date_to}  (field: {params['date_field']})")
    if params["host_tax_id"]:
        out.info(f"Host taxon inclusion: {params['host_tax_id']}")
    if params["exclude_ids"]:
        out.info(f"Excluding {len(params['exclude_ids'])} host taxon ID(s).")
    if params["library_source"]:
        out.info(f"Library source filter: {params['library_source']}")
    if params["instrument_platform"]:
        out.info(f"Instrument platform filter: {params['instrument_platform']}")
    if params["min_bases"]:
        out.info(f"Minimum base count: {params['min_bases']:,}")
    if params["keyword"]:
        out.info(f"Keyword filter: \"{params['keyword']}\"")
    out.info(f"Databases: {db}")

    all_runs: list[dict] = []

    if db in ("ena", "both"):
        out.info("Querying ENA Portal API…")
        try:
            ena_runs = ena.search_runs(
                date_from=args.date_from,
                date_to=args.date_to,
                host_tax_id=params["host_tax_id"],
                library_strategy=params["library_strategy"],
                library_source=params["library_source"],
                instrument_platform=params["instrument_platform"],
                date_field=params["date_field"],
                min_bases=params["min_bases"],
                keyword=params["keyword"],
                exclude_host_tax_ids=params["exclude_ids"],
            )
            out.success(f"ENA: {len(ena_runs)} run records found.")
            all_runs.extend(metadata.normalize_runs(ena_runs, "ENA"))
        except Exception as exc:
            out.warn(f"ENA query failed: {exc}")

    if db in ("sra", "both"):
        email = cfg.get("NCBI_EMAIL", "").strip()
        if not email:
            out.warn("NCBI_EMAIL not set — skipping SRA (set it with wmw config --edit).")
        else:
            out.info("Querying NCBI SRA via Entrez…")
            try:
                api_key = cfg.get("NCBI_API_KEY", "").strip() or None
                sra.configure(email, api_key)
                sra_runs = sra.search_runs(
                    date_from=args.date_from,
                    date_to=args.date_to,
                    host_tax_id=params["host_tax_id"],
                    library_strategy=params["library_strategy"],
                    library_source=params["library_source"],
                    instrument_platform=params["instrument_platform"],
                    min_bases=params["min_bases"],
                    keyword=params["keyword"],
                    exclude_tax_ids=params["exclude_ids"],
                )
                out.success(f"SRA: {len(sra_runs)} run records found.")
                all_runs.extend(metadata.normalize_runs(sra_runs, "SRA"))
            except Exception as exc:
                out.warn(f"SRA query failed: {exc}")

    all_runs = metadata.deduplicate_runs(all_runs)

    # Post-fetch filter: catches SRA runs (no host_tax_id at query time) and
    # any ENA records where host_tax_id was blank in the index.
    all_runs, n_excluded = metadata.filter_runs(
        all_runs,
        exclude_host_tax_ids=params["exclude_ids"],
        min_bases=params["min_bases"],
    )
    if n_excluded:
        out.info(f"Post-fetch filter removed {n_excluded} run(s).")

    derived_studies = metadata.studies_from_runs(all_runs, "ENA")
    out.info(f"Total unique runs: {len(all_runs)} across {len(derived_studies)} studies.")

    if not all_runs:
        out.info("Nothing to insert.")
        return 0

    # --- publication resolution ---
    if not args.no_publications:
        email = cfg.get("NCBI_EMAIL", "").strip()
        if not email:
            out.warn("NCBI_EMAIL not set — skipping publication lookup (set it with wmw config --edit).")
        else:
            from wmw import publications
            api_key = cfg.get("NCBI_API_KEY", "").strip() or None
            out.info(f"Resolving publication metadata for {len(derived_studies)} studies…")
            publications.resolve_batch(derived_studies, email=email, api_key=api_key)
            found = sum(1 for s in derived_studies if s.get("pub_title"))
            out.success(f"Publications resolved: {found}/{len(derived_studies)}.")

    _print_scan_summary(derived_studies)

    if dry_run:
        out.info("Dry-run mode — no changes written to Airtable.")
        return 0

    client = _airtable_client(args)
    s_inserted, s_skipped = client.upsert_studies(studies_table, derived_studies)
    out.success(f"Studies: {s_inserted} inserted, {s_skipped} already existed.")
    r_inserted, r_skipped = client.upsert_samples(samples_table, all_runs)
    out.success(f"Samples/runs: {r_inserted} inserted, {r_skipped} already existed.")
    return 0


def _scan_single_study(
    args: argparse.Namespace,
    study_accession: str,
    db: str,
    studies_table: str,
    samples_table: str,
    dry_run: bool,
    params: dict,
) -> int:
    from wmw import ena, sra, metadata

    out.section(f"WMW SCAN — {study_accession}")
    all_runs: list[dict] = []

    if db in ("ena", "both"):
        out.info(f"Fetching {study_accession} from ENA…")
        try:
            ena_runs = ena.search_study(study_accession)
            all_runs.extend(metadata.normalize_runs(ena_runs, "ENA"))
            out.success(f"ENA: {len(ena_runs)} runs.")
        except Exception as exc:
            out.warn(f"ENA lookup failed: {exc}")

    if db in ("sra", "both") and not all_runs:
        email = cfg.get("NCBI_EMAIL", "").strip()
        if email:
            try:
                api_key = cfg.get("NCBI_API_KEY", "").strip() or None
                sra.configure(email, api_key)
                sra_runs = sra.search_study(study_accession)
                all_runs.extend(metadata.normalize_runs(sra_runs, "SRA"))
                out.success(f"SRA: {len(sra_runs)} runs.")
            except Exception as exc:
                out.warn(f"SRA lookup failed: {exc}")

    if not all_runs:
        out.warn("No runs found for this study.")
        return 0

    all_runs = metadata.deduplicate_runs(all_runs)

    # Post-fetch filtering (single-study mode always filters post-fetch)
    all_runs, n_excluded = metadata.filter_runs(
        all_runs,
        exclude_host_tax_ids=params["exclude_ids"],
        min_bases=params["min_bases"],
    )
    if n_excluded:
        out.info(f"Post-fetch filter removed {n_excluded} run(s).")

    if not all_runs:
        out.warn("No runs remain after filtering.")
        return 0

    source = all_runs[0].get("source", "ENA")
    derived_studies = metadata.studies_from_runs(all_runs, source)
    out.info(f"{len(all_runs)} unique runs across {len(derived_studies)} study record(s).")

    if not args.no_publications:
        email = cfg.get("NCBI_EMAIL", "").strip()
        if email:
            from wmw import publications
            api_key = cfg.get("NCBI_API_KEY", "").strip() or None
            publications.resolve_batch(derived_studies, email=email, api_key=api_key)

    if dry_run:
        out.info("Dry-run — no Airtable writes.")
        return 0

    client = _airtable_client(args)
    s_inserted, s_skipped = client.upsert_studies(studies_table, derived_studies)
    out.success(f"Studies: {s_inserted} inserted, {s_skipped} already existed.")
    r_inserted, r_skipped = client.upsert_samples(samples_table, all_runs)
    out.success(f"Samples/runs: {r_inserted} inserted, {r_skipped} already existed.")
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
            "Examples:\n"
            "  wmw scan --from 2024-01-01 --to 2024-12-31 --db both\n"
            "  wmw scan --study PRJEB12345\n"
            "  wmw process --batch BATCH_01 --workflow preprocessing\n"
            "  wmw status --batch BATCH_01\n"
            "  wmw config --edit\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"wmw {__version__}")

    # Common Airtable flags (available on scan, process, status)
    _add_airtable_flags(parser)

    sub = parser.add_subparsers(title="commands", metavar="<command>")

    # ---- scan ----
    p_scan = sub.add_parser(
        "scan",
        help="Search ENA/SRA for wild-animal metagenomes and populate Airtable.",
        description=(
            "Query ENA Portal API and/or NCBI SRA for shotgun metagenomic datasets "
            "from wild animals, then upsert studies and samples into Airtable."
        ),
    )
    _add_airtable_flags(p_scan)
    p_scan.add_argument(
        "--from",
        dest="date_from",
        metavar="DATE",
        help="Start of date window (YYYY-MM-DD), applied to first_public.",
    )
    p_scan.add_argument(
        "--to",
        dest="date_to",
        metavar="DATE",
        help="End of date window (YYYY-MM-DD), applied to first_public.",
    )
    p_scan.add_argument(
        "--study",
        metavar="ACCESSION",
        help="Fetch a single study by ENA/SRA accession (overrides date window).",
    )
    p_scan.add_argument(
        "--host-tax-id",
        metavar="TAXON_ID",
        default="",
        help="NCBI taxon ID to filter by host organism (e.g. 7742 for Vertebrata).",
    )
    p_scan.add_argument(
        "--library-strategy",
        metavar="STRATEGY",
        default="WGS,METAGENOMIC",
        help="Comma-separated ENA library strategies to include (default: WGS,METAGENOMIC).",
    )
    p_scan.add_argument(
        "--db",
        choices=["ena", "sra", "both"],
        default="both",
        help="Which databases to query (default: both).",
    )
    p_scan.add_argument(
        "--date-field",
        metavar="FIELD",
        default="",
        choices=["", "first_public", "collection_date", "last_updated"],
        help="ENA date field for the date window (default from config: first_public).",
    )
    p_scan.add_argument(
        "--library-source",
        metavar="SOURCE",
        default="",
        help="Comma-separated library sources to include, e.g. METAGENOMIC (default: no filter).",
    )
    p_scan.add_argument(
        "--instrument-platform",
        metavar="PLATFORM",
        default="",
        help="Restrict to a single sequencing platform, e.g. ILLUMINA (default: no filter).",
    )
    p_scan.add_argument(
        "--min-bases",
        metavar="N",
        default="",
        help="Minimum total base count per run (default: no minimum).",
    )
    p_scan.add_argument(
        "--keyword",
        metavar="TEXT",
        default="",
        help="Free-text substring to match against study title.",
    )
    p_scan.add_argument(
        "--exclude-taxa",
        metavar="IDS",
        default="",
        help=(
            "Comma-separated host taxon IDs to exclude, appended to the config exclusion list "
            "(e.g. 9615,9685 to also exclude dogs and cats)."
        ),
    )
    p_scan.add_argument(
        "--no-exclude",
        action="store_true",
        help="Disable all host taxon exclusions (config list and --exclude-taxa).",
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
    p_scan.add_argument(
        "--samples-table",
        metavar="TABLE",
        default="",
        help="Override Samples table name from config.",
    )
    p_scan.set_defaults(func=cmd_scan)

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
