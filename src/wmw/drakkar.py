"""Drakkar bridge for wmw — sample sheet generation and workflow invocation."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from wmw import config as cfg


# ---------------------------------------------------------------------------
# Sample sheet
# ---------------------------------------------------------------------------

MANIFEST_HEADER = "\t".join(["sample", "R1", "R2"])


def build_manifest(
    samples: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    """Write a Drakkar-compatible TSV manifest from Airtable sample records.

    Each record must have: run_accession, fastq_url_1, fastq_url_2 (or fastq_ftp).
    Returns the path to the written manifest.
    """
    lines = [MANIFEST_HEADER]
    for rec in samples:
        fields = rec.get("fields", rec)
        sample_id = fields.get("run_accession", "")
        r1 = fields.get("fastq_url_1", "") or _first_url(fields.get("fastq_ftp", ""))
        r2 = fields.get("fastq_url_2", "") or _second_url(fields.get("fastq_ftp", ""))
        if not sample_id or not r1:
            continue
        lines.append(f"{sample_id}\t{r1}\t{r2}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _first_url(fastq_ftp: str) -> str:
    parts = [p.strip() for p in fastq_ftp.split(";") if p.strip()]
    url = parts[0] if parts else ""
    return ("ftp://" + url) if url and "://" not in url else url


def _second_url(fastq_ftp: str) -> str:
    parts = [p.strip() for p in fastq_ftp.split(";") if p.strip()]
    url = parts[1] if len(parts) > 1 else ""
    return ("ftp://" + url) if url and "://" not in url else url


# ---------------------------------------------------------------------------
# Drakkar invocation
# ---------------------------------------------------------------------------

def _drakkar_cmd(subcommand: str, extra: list[str]) -> list[str]:
    """Build the drakkar command, optionally wrapped in conda run."""
    conda_env = str(cfg.get("DRAKKAR_CONDA_ENV") or "").strip()
    if conda_env:
        flag = "-p" if conda_env.startswith(("/", "~", ".")) else "-n"
        prefix = ["conda", "run", flag, conda_env]
    else:
        prefix = []
    return [*prefix, "drakkar", subcommand, *extra]


def run_workflow(
    workflow: str,
    manifest: Path,
    output_dir: Path,
    *,
    slurm: bool = False,
    extra_args: list[str] | None = None,
) -> int:
    """Invoke drakkar <workflow> and return the process exit code."""
    cmd = _drakkar_cmd(
        workflow,
        [
            "--manifest", str(manifest),
            "--output", str(output_dir),
            *(["--slurm"] if slurm else []),
            *(extra_args or []),
        ],
    )
    rendered = shlex.join(cmd)
    print(f"Running: {rendered}", file=sys.stderr)
    result = subprocess.run(cmd, check=False)
    return result.returncode


def get_drakkar_version() -> str:
    """Return the installed drakkar version string, or 'unknown'."""
    import re
    cmd = _drakkar_cmd("--version", [])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        raw = res.stdout.strip() or res.stderr.strip() or ""
        m = re.search(r"(\d+\.\d+[\.\d]*)", raw)
        return m.group(1) if m else (raw or "unknown")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

def parse_preprocessing_stats(output_dir: Path, run_accession: str) -> dict[str, Any]:
    """Extract key QC metrics from drakkar preprocessing output for a given run.

    Returns a dict suitable for updating Airtable fields; empty dict on failure.
    """
    stats_file = output_dir / "preprocessing" / run_accession / "stats.tsv"
    if not stats_file.exists():
        return {}
    try:
        with stats_file.open(encoding="utf-8") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            values = fh.readline().rstrip("\n").split("\t")
        return dict(zip(header, values))
    except Exception:
        return {}
