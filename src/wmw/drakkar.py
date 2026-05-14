"""Drakkar bridge for wmw — sample sheet generation and workflow invocation."""

from __future__ import annotations

import csv
import gzip
import shlex
import shutil
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
        if fields.get("status") != "use":
            continue
        row = [str(fields.get(fn, "") or "") for fn in field_names]
        lines.append("\t".join(row))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Script generation
# ---------------------------------------------------------------------------

def _rename_workflow_tsv_line(code: str, work_dir: Path, workflow: str) -> str:
    src = shlex.quote(str(work_dir / f"{workflow}.tsv"))
    dst = shlex.quote(str(work_dir / f"{code}_{workflow}.tsv"))
    return f"if [ -f {src} ]; then mv -f {src} {dst}; fi"


def generate_full_pipeline_script(
    code: str,
    tsv_path: Path,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
    memory_multiplier: str | float | None = None,
    time_multiplier: str | float | None = None,
) -> str:
    """Return a bash script that runs the full pipeline for *code*:
    preprocessing → cataloging → profiling → annotation."""
    preprocessing_flags = f"-f {tsv_path} -o {work_dir} --fraction --nonpareil"
    if slurm:
        preprocessing_flags += " -p slurm"
    if memory_multiplier not in (None, "", "1", 1, 1.0):
        preprocessing_flags += f" --memory-multiplier {memory_multiplier}"
    if time_multiplier not in (None, "", "1", 1, 1.0):
        preprocessing_flags += f" --time-multiplier {time_multiplier}"

    cataloging_flags = f"-f {tsv_path} -o {work_dir} --multicoverage"
    if slurm:
        cataloging_flags += " -p slurm"

    bin_paths = shlex.quote(str(work_dir / "cataloging" / "final" / "all_bin_paths.txt"))
    reads_dir = shlex.quote(str(work_dir / "preprocessing" / "final"))
    bin_metadata = shlex.quote(str(work_dir / "cataloging" / "final" / "all_bin_metadata.csv"))
    out_dir = shlex.quote(str(work_dir))

    profiling_flags = f"-B {bin_paths} -r {reads_dir} -a 0.98 -t genomes -q {bin_metadata} -o {out_dir}"
    if slurm:
        profiling_flags += " -p slurm"

    annotation_flags = f"-B {bin_paths} -o {out_dir}"
    if slurm:
        annotation_flags += " -p slurm"

    if conda_env:
        c_flag = "-p" if str(conda_env).startswith(("/", "~", ".")) else "-n"
        preprocessing_cmd = f"conda run {c_flag} {conda_env} drakkar preprocessing {preprocessing_flags}"
        cataloging_cmd  = f"conda run {c_flag} {conda_env} drakkar cataloging {cataloging_flags}"
        profiling_cmd   = f"conda run {c_flag} {conda_env} drakkar profiling {profiling_flags}"
        annotation_cmd  = f"conda run {c_flag} {conda_env} drakkar annotating {annotation_flags}"
        conda_lines = [
            'if [ -f "$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh" ]; then',
            '    source "$(conda info --base)/etc/profile.d/conda.sh"',
            f"    conda activate {conda_env}",
            "fi",
            "",
        ]
    else:
        preprocessing_cmd = f"drakkar preprocessing {preprocessing_flags}"
        cataloging_cmd  = f"drakkar cataloging {cataloging_flags}"
        profiling_cmd   = f"drakkar profiling {profiling_flags}"
        annotation_cmd  = f"drakkar annotating {annotation_flags}"
        conda_lines = []

    if wmw_conda_env:
        w_flag = "-p" if str(wmw_conda_env).startswith(("/", "~", ".")) else "-n"
        wmw_cmd = f"conda run {w_flag} {wmw_conda_env} wmw"
    else:
        wmw_cmd = "wmw"

    stop_file = shlex.quote(str(work_dir / ".wmw-stop"))
    output_dir_arg = f" --output-dir {shlex.quote(str(work_dir.parent))}"

    lines = [
        "#!/usr/bin/env bash",
        f"# wmw-generated script — batch {code} (preprocessing → cataloging → profiling → annotation)",
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
        f"_WMW_STOP_FILE={stop_file}",
        'rm -f "$_WMW_STOP_FILE"',
        # preprocessing
        "_WMW_SUCCESS=0",
        "_on_exit_preprocessing() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow preprocessing --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow preprocessing --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit_preprocessing EXIT",
        "",
        f"{wmw_cmd} set-status --study {code} --workflow preprocessing --status preprocessing{output_dir_arg}",
        preprocessing_cmd,
        _rename_workflow_tsv_line(code, work_dir, "preprocessing"),
        f"{wmw_cmd} set-status --study {code} --workflow preprocessing --status preprocessed{output_dir_arg}",
        "_WMW_SUCCESS=1",
        "",
        # cataloging
        "_WMW_SUCCESS=0",
        "_on_exit_cataloging() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow cataloging --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow cataloging --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit_cataloging EXIT",
        f"{wmw_cmd} set-status --study {code} --workflow cataloging --status cataloging{output_dir_arg}",
        cataloging_cmd,
        _rename_workflow_tsv_line(code, work_dir, "cataloging"),
        f"{wmw_cmd} set-status --study {code} --workflow cataloging --status cataloged{output_dir_arg}",
        "_WMW_SUCCESS=1",
        "",
        # profiling
        "_WMW_SUCCESS=0",
        "_on_exit_profiling() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow profiling --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow profiling --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit_profiling EXIT",
        "",
        f"cd {shlex.quote(str(work_dir))}",
        f"{wmw_cmd} set-status --study {code} --workflow profiling --status quantifying{output_dir_arg}",
        profiling_cmd,
        f"{wmw_cmd} set-status --study {code} --workflow profiling --status quantified{output_dir_arg}",
        "_WMW_SUCCESS=1",
        "",
        # annotation
        "_WMW_SUCCESS=0",
        "_on_exit_annotation() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow annotating --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow annotating --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit_annotation EXIT",
        "",
        f"{wmw_cmd} set-status --study {code} --workflow annotating --status annotating{output_dir_arg}",
        annotation_cmd,
        f"{wmw_cmd} set-status --study {code} --workflow annotating --status completed{output_dir_arg}",
        "_WMW_SUCCESS=1",
        "",
    ]
    return "\n".join(lines)


def generate_preprocessing_script(
    code: str,
    tsv_path: Path,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
    memory_multiplier: str | float | None = None,
    time_multiplier: str | float | None = None,
) -> str:
    """Return a bash script that runs drakkar preprocessing for *code* and updates Airtable."""
    drakkar_flags = f"-f {tsv_path} -o {work_dir} --fraction --nonpareil"
    if slurm:
        drakkar_flags += " -p slurm"
    if memory_multiplier not in (None, "", "1", 1, 1.0):
        drakkar_flags += f" --memory-multiplier {memory_multiplier}"
    if time_multiplier not in (None, "", "1", 1, 1.0):
        drakkar_flags += f" --time-multiplier {time_multiplier}"

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

    cataloging_flags = f"-f {tsv_path} -o {work_dir} --multicoverage"
    if slurm:
        cataloging_flags += " -p slurm"
    if conda_env:
        cataloging_cmd = f"conda run {c_flag} {conda_env} drakkar cataloging {cataloging_flags}"
    else:
        cataloging_cmd = f"drakkar cataloging {cataloging_flags}"

    stop_file = shlex.quote(str(work_dir / ".wmw-stop"))
    output_dir_arg = f" --output-dir {shlex.quote(str(work_dir.parent))}"

    lines = [
        "#!/usr/bin/env bash",
        f"# wmw-generated script — batch {code} (preprocessing → cataloging)",
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
        f"_WMW_STOP_FILE={stop_file}",
        'rm -f "$_WMW_STOP_FILE"',
        "_WMW_SUCCESS=0",
        "_on_exit_preprocessing() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow preprocessing --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow preprocessing --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit_preprocessing EXIT",
        "",
        f"{wmw_cmd} set-status --study {code} --workflow preprocessing --status preprocessing{output_dir_arg}",
        drakkar_cmd,
        _rename_workflow_tsv_line(code, work_dir, "preprocessing"),
        f"{wmw_cmd} set-status --study {code} --workflow preprocessing --status preprocessed{output_dir_arg}",
        "_WMW_SUCCESS=1",
        "",
        "_WMW_SUCCESS=0",
        "_on_exit_cataloging() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow cataloging --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow cataloging --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit_cataloging EXIT",
        f"{wmw_cmd} set-status --study {code} --workflow cataloging --status cataloging{output_dir_arg}",
        cataloging_cmd,
        _rename_workflow_tsv_line(code, work_dir, "cataloging"),
        f"{wmw_cmd} set-status --study {code} --workflow cataloging --status cataloged{output_dir_arg}",
        "_WMW_SUCCESS=1",
        "",
    ]
    return "\n".join(lines)


def generate_profiling_script(
    code: str,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
) -> str:
    """Return a bash script that runs drakkar profiling for *code* and updates Airtable."""
    bin_paths = shlex.quote(str(work_dir / "cataloging" / "final" / "all_bin_paths.txt"))
    reads_dir = shlex.quote(str(work_dir / "preprocessing" / "final"))
    bin_metadata = shlex.quote(str(work_dir / "cataloging" / "final" / "all_bin_metadata.csv"))
    out_dir = shlex.quote(str(work_dir))

    profiling_flags = f"-B {bin_paths} -r {reads_dir} -a 0.98 -t genomes -q {bin_metadata} -o {out_dir}"
    if slurm:
        profiling_flags += " -p slurm"

    if conda_env:
        c_flag = "-p" if str(conda_env).startswith(("/", "~", ".")) else "-n"
        profiling_cmd = f"conda run {c_flag} {conda_env} drakkar profiling {profiling_flags}"
        conda_lines = [
            'if [ -f "$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh" ]; then',
            '    source "$(conda info --base)/etc/profile.d/conda.sh"',
            f"    conda activate {conda_env}",
            "fi",
            "",
        ]
    else:
        profiling_cmd = f"drakkar profiling {profiling_flags}"
        conda_lines = []

    if wmw_conda_env:
        w_flag = "-p" if str(wmw_conda_env).startswith(("/", "~", ".")) else "-n"
        wmw_cmd = f"conda run {w_flag} {wmw_conda_env} wmw"
    else:
        wmw_cmd = "wmw"

    stop_file = shlex.quote(str(work_dir / ".wmw-stop"))
    output_dir_arg = f" --output-dir {shlex.quote(str(work_dir.parent))}"

    lines = [
        "#!/usr/bin/env bash",
        f"# wmw-generated script — batch {code} (profiling only)",
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
        f"_WMW_STOP_FILE={stop_file}",
        'rm -f "$_WMW_STOP_FILE"',
        "_WMW_SUCCESS=0",
        "_on_exit() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow profiling --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow profiling --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit EXIT",
        "",
        f"cd {shlex.quote(str(work_dir))}",
        f"{wmw_cmd} set-status --study {code} --workflow profiling --status quantifying{output_dir_arg}",
        profiling_cmd,
        f"{wmw_cmd} set-status --study {code} --workflow profiling --status quantified{output_dir_arg}",
        "_WMW_SUCCESS=1",
        "",
    ]
    return "\n".join(lines)


def generate_annotation_script(
    code: str,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
) -> str:
    """Return a bash script that runs drakkar annotating for *code* and updates Airtable."""
    bin_paths = shlex.quote(str(work_dir / "cataloging" / "final" / "all_bin_paths.txt"))
    out_dir = shlex.quote(str(work_dir))

    annotation_flags = f"-B {bin_paths} -o {out_dir}"
    if slurm:
        annotation_flags += " -p slurm"

    if conda_env:
        c_flag = "-p" if str(conda_env).startswith(("/", "~", ".")) else "-n"
        annotation_cmd = f"conda run {c_flag} {conda_env} drakkar annotating {annotation_flags}"
        conda_lines = [
            'if [ -f "$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh" ]; then',
            '    source "$(conda info --base)/etc/profile.d/conda.sh"',
            f"    conda activate {conda_env}",
            "fi",
            "",
        ]
    else:
        annotation_cmd = f"drakkar annotating {annotation_flags}"
        conda_lines = []

    if wmw_conda_env:
        w_flag = "-p" if str(wmw_conda_env).startswith(("/", "~", ".")) else "-n"
        wmw_cmd = f"conda run {w_flag} {wmw_conda_env} wmw"
    else:
        wmw_cmd = "wmw"

    stop_file = shlex.quote(str(work_dir / ".wmw-stop"))
    output_dir_arg = f" --output-dir {shlex.quote(str(work_dir.parent))}"

    lines = [
        "#!/usr/bin/env bash",
        f"# wmw-generated script — batch {code} (annotation only)",
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
        f"_WMW_STOP_FILE={stop_file}",
        'rm -f "$_WMW_STOP_FILE"',
        "_WMW_SUCCESS=0",
        "_on_exit() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow annotating --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow annotating --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit EXIT",
        "",
        f"cd {shlex.quote(str(work_dir))}",
        f"{wmw_cmd} set-status --study {code} --workflow annotating --status annotating{output_dir_arg}",
        annotation_cmd,
        f"{wmw_cmd} set-status --study {code} --workflow annotating --status completed{output_dir_arg}",
        "_WMW_SUCCESS=1",
        "",
    ]
    return "\n".join(lines)


def generate_cataloging_script(
    code: str,
    tsv_path: Path,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
) -> str:
    """Return a bash script that runs drakkar cataloging only for *code* and updates Airtable."""
    cataloging_flags = f"-f {tsv_path} -o {work_dir} --multicoverage"
    if slurm:
        cataloging_flags += " -p slurm"

    if conda_env:
        c_flag = "-p" if str(conda_env).startswith(("/", "~", ".")) else "-n"
        cataloging_cmd = f"conda run {c_flag} {conda_env} drakkar cataloging {cataloging_flags}"
        conda_lines = [
            'if [ -f "$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh" ]; then',
            '    source "$(conda info --base)/etc/profile.d/conda.sh"',
            f"    conda activate {conda_env}",
            "fi",
            "",
        ]
    else:
        cataloging_cmd = f"drakkar cataloging {cataloging_flags}"
        conda_lines = []

    if wmw_conda_env:
        w_flag = "-p" if str(wmw_conda_env).startswith(("/", "~", ".")) else "-n"
        wmw_cmd = f"conda run {w_flag} {wmw_conda_env} wmw"
    else:
        wmw_cmd = "wmw"

    stop_file = shlex.quote(str(work_dir / ".wmw-stop"))
    output_dir_arg = f" --output-dir {shlex.quote(str(work_dir.parent))}"

    lines = [
        "#!/usr/bin/env bash",
        f"# wmw-generated script — batch {code} (cataloging only)",
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
        f"_WMW_STOP_FILE={stop_file}",
        'rm -f "$_WMW_STOP_FILE"',
        "_WMW_SUCCESS=0",
        "_on_exit() {",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {wmw_cmd} set-status --study {code} --workflow cataloging --status stopped{output_dir_arg}",
        "        else",
        f"            {wmw_cmd} set-status --study {code} --workflow cataloging --status error{output_dir_arg}",
        "        fi",
        "    fi",
        "}",
        "trap _on_exit EXIT",
        "",
        f"{wmw_cmd} set-status --study {code} --workflow cataloging --status cataloging{output_dir_arg}",
        cataloging_cmd,
        _rename_workflow_tsv_line(code, work_dir, "cataloging"),
        f"{wmw_cmd} set-status --study {code} --workflow cataloging --status cataloged{output_dir_arg}",
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


_PROFILING_TSV_COLS: list[tuple[str, str, str]] = [
    # (tsv_column,           config_key,                    type)
    ("mapping_percentage",   "SAMPLES_COL_MAGS_MAPPING_RATE", "float2"),
]

_CATALOGING_TSV_COLS: list[tuple[str, tuple[str, ...], str]] = [
    # (tsv_column,              config_key(s),                              type)
    ("assembly_contigs",        ("SAMPLES_COL_ASSEMBLY_CONTIGS",),          "int"),
    ("assembly_total_length",   ("SAMPLES_COL_ASSEMBLY_LENGTH",),           "int"),
    ("assembly_largest_contig", ("SAMPLES_COL_ASSEMBLY_LARGEST_CONTIG",),   "int"),
    ("assembly_N50",            ("SAMPLES_COL_ASSEMBLY_N50",),              "int"),
    (
        "assembly_L50",
        ("SAMPLES_COL_ASSEMBLY_L50", "SAMPLES_COL_ASSEMBLTY_L50"),
        "int",
    ),
    ("assembly_gc_percent",     ("SAMPLES_COL_ASSEMBLY_GC",),               "float2"),
    ("mapping_rate_percent",    ("SAMPLES_COL_ASSEMBLY_MAPPING_RATE_ALL",), "float2"),
]

_BIN_METADATA_CSV_COLS: list[tuple[str, str, str]] = [
    # (csv_column,     config_key,                   type)
    ("completeness",  "GENOMES_COL_COMPLETENESS",   "float2"),
    ("contamination", "GENOMES_COL_CONTAMINATION",  "float2"),
    ("size",          "GENOMES_COL_LENGTH",         "int"),
    ("N50",           "GENOMES_COL_N50",            "int"),
    ("contig_count",  "GENOMES_COL_CONTIGS",        "int"),
]

_MISSING_VALUES = {"", "NA", "N/A", "NONE", "NULL", "NAN"}


def _is_missing(raw: str) -> bool:
    return raw.strip().upper() in _MISSING_VALUES


def _coerce_stat(raw: str, typ: str) -> Any | None:
    raw = raw.strip()
    if _is_missing(raw):
        return None
    try:
        if typ == "int":
            return int(float(raw))
        if typ == "float2":
            return round(float(raw), 2)
        return round(float(raw), 4)
    except ValueError:
        return None


def _config_field_id(*keys: str) -> str:
    for key in keys:
        fid = str(cfg.get(key) or "").strip()
        if fid:
            return fid
    return ""


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
                value = _coerce_stat(raw, typ)
                if value is None:
                    continue
                fields[fid] = value
            if fields:
                result[sample_id] = fields
    return result


def parse_profiling_tsv(tsv_path: Path) -> dict[str, dict[str, Any]]:
    """Parse drakkar's profiling_genomes.tsv; return {sample_code: {field_id: value}}.

    Field IDs come from config. Returns empty dict if the file is absent.
    """
    if not tsv_path.exists():
        return {}

    col_map: list[tuple[str, str, str]] = []
    for tsv_col, config_key, typ in _PROFILING_TSV_COLS:
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
                value = _coerce_stat(raw, typ)
                if value is None:
                    continue
                fields[fid] = value
            if fields:
                result[sample_id] = fields
    return result


def _parse_sample_mapping_rates(raw: str) -> dict[str, float]:
    rates: dict[str, float] = {}
    for part in raw.split(";"):
        if ":" not in part:
            continue
        sample, value_raw = part.split(":", 1)
        sample = sample.strip()
        value = _coerce_stat(value_raw, "float2")
        if sample and value is not None:
            rates[sample] = value
    return rates


def parse_cataloging_tsv(tsv_path: Path) -> dict[str, dict[str, Any]]:
    """Parse drakkar's cataloging.tsv; return {sample_code: {field_id: value}}.

    Assembly rows are keyed by the ``assembly`` column, which matches the wmw
    sample code for focal assemblies. The focal mapping rate is extracted from
    ``sample_mapping_rates`` by selecting the entry whose sample name equals the
    assembly name.
    """
    if not tsv_path.exists():
        return {}

    col_map: list[tuple[str, str, str]] = []
    for tsv_col, config_keys, typ in _CATALOGING_TSV_COLS:
        fid = _config_field_id(*config_keys)
        if fid:
            col_map.append((tsv_col, fid, typ))

    focal_mapping_fid = _config_field_id("SAMPLES_COL_ASSEMBLY_MAPPING_RATE_FOCAL")

    result: dict[str, dict[str, Any]] = {}
    with tsv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row_dict in reader:
            assembly = (row_dict.get("assembly") or "").strip()
            if not assembly:
                continue

            fields: dict[str, Any] = {}
            for tsv_col, fid, typ in col_map:
                value = _coerce_stat(row_dict.get(tsv_col, ""), typ)
                if value is not None:
                    fields[fid] = value

            if focal_mapping_fid:
                rates = _parse_sample_mapping_rates(row_dict.get("sample_mapping_rates", ""))
                focal_mapping_rate = rates.get(assembly)
                if focal_mapping_rate is not None:
                    fields[focal_mapping_fid] = focal_mapping_rate

            if fields:
                result[assembly] = fields
    return result


def _strip_fasta_suffix(raw: str) -> str:
    name = Path(raw.strip()).name
    lower = name.lower()
    for suffix in (".fasta.gz", ".fna.gz", ".fa.gz", ".fasta", ".fna", ".fa"):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _resolve_bin_path(raw: str, paths_file: Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path

    candidates: list[Path] = []
    if len(paths_file.parents) >= 3:
        candidates.append(paths_file.parents[2] / path)
    candidates.append(paths_file.parent / path)
    candidates.append(paths_file.parent.parent / path)

    seen: set[str] = set()
    unique_candidates: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique_candidates.append(candidate)

    for candidate in unique_candidates:
        if candidate.exists():
            return candidate
    return unique_candidates[0] if unique_candidates else path


def parse_bin_paths_txt(paths_file: Path) -> dict[str, Path]:
    """Parse drakkar's all_bin_paths.txt into {genome_name: fasta_path}.

    Drakkar writes paths relative to the study work directory in current output
    (for example ``cataloging/final/SA000022/SA000022_bin_1.fa``). This parser
    also accepts absolute paths and paths relative to the ``final`` directory.
    """
    if not paths_file.exists():
        return {}

    result: dict[str, Path] = {}
    with paths_file.open(encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            path = _resolve_bin_path(raw, paths_file)
            genome_name = _strip_fasta_suffix(path.name)
            if genome_name:
                if genome_name not in result or (
                    not result[genome_name].exists() and path.exists()
                ):
                    result[genome_name] = path
    return result


def gzip_fasta(fasta_path: Path) -> Path:
    """Compress a FASTA file beside the source and return the .fa.gz path."""
    fasta_path = Path(fasta_path)
    lower = fasta_path.name.lower()
    if lower.endswith(".gz"):
        return fasta_path
    if lower.endswith((".fa", ".fasta", ".fna")):
        gz_path = fasta_path.with_suffix(".fa.gz")
    else:
        gz_path = fasta_path.with_name(f"{fasta_path.name}.fa.gz")

    if gz_path.exists() and gz_path.stat().st_size > 0:
        try:
            if gz_path.stat().st_mtime >= fasta_path.stat().st_mtime:
                return gz_path
        except FileNotFoundError:
            raise

    with fasta_path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return gz_path


def parse_bin_metadata_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Parse drakkar's all_bin_metadata.csv into genome records.

    Returns a list of ``{"sample_code": code, "fields": {field_id: value}}``.
    The caller is responsible for resolving ``sample_code`` to an Airtable
    sample record ID and adding the linked-record field before creating rows.
    """
    if not csv_path.exists():
        return []

    name_fid = _config_field_id("GENOMES_COL_NAME")
    col_map: list[tuple[str, str, str]] = []
    for csv_col, config_key, typ in _BIN_METADATA_CSV_COLS:
        fid = _config_field_id(config_key)
        if fid:
            col_map.append((csv_col, fid, typ))

    result: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row_dict in reader:
            genome_raw = (row_dict.get("genome") or "").strip()
            if not genome_raw:
                continue

            genome_name = _strip_fasta_suffix(genome_raw)
            sample_code = genome_name.split("_", 1)[0].strip()
            if not sample_code:
                continue

            fields: dict[str, Any] = {}
            if name_fid:
                fields[name_fid] = genome_name

            for csv_col, fid, typ in col_map:
                value = _coerce_stat(row_dict.get(csv_col, ""), typ)
                if value is not None:
                    fields[fid] = value

            if fields:
                result.append(
                    {
                        "sample_code": sample_code,
                        "genome_name": genome_name,
                        "fields": fields,
                    }
                )
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
