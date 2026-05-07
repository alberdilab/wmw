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
# Drakkar input TSV (new format for wmw process)
# ---------------------------------------------------------------------------

_REQUIRED_COLS = [
    ("sample",          "code"),
    ("rawreads1",       "fastq_url_1"),
    ("rawreads2",       "fastq_url_2"),
    ("reference_name",  "reference_name"),
    ("reference_path",  "reference_path"),
]
_OPTIONAL_COLS = [
    ("assembly", "assembly"),
    ("coverage", "coverage"),
]


def build_input_tsv(
    samples: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    """Write the Drakkar input TSV for a batch of decoded Airtable sample records.

    Required columns: sample, rawreads1, rawreads2, reference_name, reference_path.
    Optional columns (assembly, coverage) are included only when at least one row
    has a non-empty value.
    """
    include_optional: dict[str, bool] = {}
    for col_name, field_name in _OPTIONAL_COLS:
        include_optional[col_name] = any(
            str(rec.get("fields", rec).get(field_name, "") or "").strip()
            for rec in samples
        )

    header_cols = [c for c, _ in _REQUIRED_COLS] + [
        c for c, _ in _OPTIONAL_COLS if include_optional.get(c)
    ]
    field_names = [f for _, f in _REQUIRED_COLS] + [
        f for c, f in _OPTIONAL_COLS if include_optional.get(c)
    ]

    lines = ["\t".join(header_cols)]
    for rec in samples:
        fields = rec.get("fields", rec)
        row = [str(fields.get(fn, "") or "") for fn in field_names]
        lines.append("\t".join(row))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Script generation
# ---------------------------------------------------------------------------

def generate_preprocessing_script(
    code: str,
    tsv_path: Path,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
) -> str:
    """Return a bash script that runs drakkar preprocessing for *code* and updates Airtable."""
    drakkar_flags = f"-f {tsv_path} -o {work_dir} --fraction --nonpareil"
    if slurm:
        drakkar_flags += " -p slurm"

    if conda_env:
        c_flag = "-p" if str(conda_env).startswith(("/", "~", ".")) else "-n"
        drakkar_cmd = f"conda run {c_flag} {conda_env} drakkar preprocessing {drakkar_flags}"
        conda_lines = [
            'if [ -f "$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh" ]; then',
            '    source "$(conda info --base)/etc/profile.d/conda.sh"',
            f"    conda activate {conda_env}",
            "fi",
            "",
        ]
    else:
        drakkar_cmd = f"drakkar preprocessing {drakkar_flags}"
        conda_lines = []

    if wmw_conda_env:
        w_flag = "-p" if str(wmw_conda_env).startswith(("/", "~", ".")) else "-n"
        wmw_cmd = f"conda run {w_flag} {wmw_conda_env} wmw"
    else:
        wmw_cmd = "wmw"

    lines = [
        "#!/usr/bin/env bash",
        f"# wmw-generated script — batch {code} (preprocessing)",
        "# Do not edit manually; re-run wmw process to regenerate.",
        "# AIRTABLE_TOKEN must be exported in the environment before launching.",
        "",
        "set -euo pipefail",
        f"exec >> {work_dir}/{code}.out 2>> {work_dir}/{code}.err",
        'echo ""',
        "echo \"=== $(date '+%Y-%m-%d %H:%M:%S') ===\"",
        "echo \"=== $(date '+%Y-%m-%d %H:%M:%S') ===\" >&2",
        "",
        *conda_lines,
        "_WMW_SUCCESS=0",
        "_on_exit() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        f"        {wmw_cmd} set-status --study {code} --workflow preprocessing --status error",
        "    fi",
        "}",
        "trap _on_exit EXIT",
        "",
        f"{wmw_cmd} set-status --study {code} --workflow preprocessing --status preprocessing",
        drakkar_cmd,
        f"{wmw_cmd} set-status --study {code} --workflow preprocessing --status completed",
        "_WMW_SUCCESS=1",
        "",
    ]
    return "\n".join(lines)


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

_PREPROCESSING_TSV_COLS: list[tuple[str, str, str]] = [
    # (tsv_column,              config_key,                          type)
    ("reads_pre_fastp",        "SAMPLES_COL_READS_PRE_FASTP",        "int"),
    ("reads_post_fastp",       "SAMPLES_COL_READS_POST_FASTP",       "int"),
    ("bases_pre_fastp",        "SAMPLES_COL_BASES_PRE_FASTP",        "int"),
    ("bases_post_fastp",       "SAMPLES_COL_BASES_POST_FASTP",       "int"),
    ("adapter_trimmed_reads",  "SAMPLES_COL_ADAPTER_TRIMMED_READS",  "int"),
    ("adapter_trimmed_bases",  "SAMPLES_COL_ADAPTER_TRIMMED_BASES",  "int"),
    ("host_reads",             "SAMPLES_COL_HOST_READS",             "int"),
    ("host_bases",             "SAMPLES_COL_HOST_BASES",             "int"),
    ("metagenomic_reads",      "SAMPLES_COL_METAGENOMIC_READS",      "int"),
    ("metagenomic_bases",      "SAMPLES_COL_METAGENOMNIC_BASES",     "int"),
    ("singlem_fraction",       "SAMPLES_COL_SINGLEM_FRACTION",       "float2"),
    ("nonpareil_C",            "SAMPLES_COL_C",                      "float4"),
    ("nonpareil_LR",           "SAMPLES_COL_LR",                     "float4"),
]


def parse_preprocessing_tsv(tsv_path: Path) -> dict[str, dict[str, Any]]:
    """Parse drakkar's preprocessing.tsv; return {run_accession: {field_id: value}}.

    Field IDs come from config. Returns empty dict if the file is absent.
    """
    if not tsv_path.exists():
        return {}

    col_map: list[tuple[str, str, str]] = []
    for tsv_col, config_key, typ in _PREPROCESSING_TSV_COLS:
        fid = str(cfg.get(config_key) or "").strip()
        if fid:
            col_map.append((tsv_col, fid, typ))

    result: dict[str, dict[str, Any]] = {}
    with tsv_path.open(encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            row = line.rstrip("\n").split("\t")
            if not row or not row[0]:
                continue
            row_dict = dict(zip(header, row))
            sample_id = row_dict.get("sample", "").strip()
            if not sample_id:
                continue
            fields: dict[str, Any] = {}
            for tsv_col, fid, typ in col_map:
                raw = row_dict.get(tsv_col, "").strip()
                if not raw:
                    continue
                try:
                    if typ == "int":
                        fields[fid] = int(float(raw))
                    elif typ == "float2":
                        fields[fid] = round(float(raw), 2)
                    else:  # float4
                        fields[fid] = round(float(raw), 4)
                except ValueError:
                    pass
            if fields:
                result[sample_id] = fields
    return result


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
