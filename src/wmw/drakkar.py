"""Drakkar bridge for wmw — sample sheet generation and workflow invocation."""

from __future__ import annotations

import csv
import gzip
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from wmw import config as cfg

DRAKKAR_ENV_PATH = "/projects/alberdilab/data/environments/drakkar/"

ANNOTATION_OUTPUT_FILES = (
    Path("annotating") / "gene_annotations.tsv.xz",
    Path("annotating") / "genome_taxonomy.tsv",
)


def missing_annotation_outputs(work_dir: Path) -> list[Path]:
    """Return required annotation outputs that are absent from *work_dir*."""
    return [
        work_dir / rel_path
        for rel_path in ANNOTATION_OUTPUT_FILES
        if not (work_dir / rel_path).exists()
    ]


def annotation_outputs_present(work_dir: Path) -> bool:
    """Return True when all required annotation outputs exist."""
    return not missing_annotation_outputs(work_dir)


# Aggregate tables 'drakkar amr' writes to <work_dir>/amr/. They are already
# xz-compressed, so they are transferred and attached as they are. Each maps to
# the config key holding the Studies attachment field it is uploaded to; a blank
# key disables that upload.
AMR_TABLE_FILES: dict[str, str] = {
    "amr_hits.tsv.xz":         "STUDIES_COL_FILE_AMR_HITS",
    "amr_loci.tsv.xz":         "STUDIES_COL_FILE_AMR_LOCI",
    "amr_drug_classes.tsv.xz": "STUDIES_COL_FILE_AMR_DRUG_CLASSES",
    "amr_mobility.tsv.xz":     "STUDIES_COL_FILE_AMR_MOBILITY",
    "mobility_regions.tsv.xz": "STUDIES_COL_FILE_AMR_MOBILITY_REGIONS",
}

# Plain-text summaries of the same run. They are gzipped on the way to ERDA and
# are not attached to Airtable: amr_qc.tsv is parsed into the Samples rows
# instead, and assembly_summary.tsv repeats those numbers per assembly.
AMR_PLAIN_FILES = ("amr_qc.tsv", "assembly_summary.tsv")

# Provenance record of the run: database releases, tool versions, row counts.
AMR_MANIFEST_FILE = "manifest.yaml"
AMR_MANIFEST_CONFIG_KEY = "STUDIES_COL_FILE_AMR_MANIFEST"

AMR_QC_FILE = Path("amr") / "amr_qc.tsv"

# Binette's per-assembly contig membership table, attached to the Samples row.
CONTIG_TO_BIN_FILE = "final_contig_to_bin.tsv"


def amr_results_dir(work_dir: Path) -> Path:
    """Return the folder holding the aggregate AMR result tables."""
    return Path(work_dir) / "amr"


def amr_qc_path(work_dir: Path) -> Path:
    """Return the per-assembly AMR summary of a drakkar amr run."""
    return Path(work_dir) / AMR_QC_FILE


def amr_result_files(work_dir: Path) -> list[Path]:
    """Return the aggregate AMR tables and manifest a run left in <work_dir>/amr/.

    Unlike `amr_outputs_present`, this asks what is there to upload rather than
    whether the run finished, so a study whose per-assembly summary is gone can
    still have its result tables archived.
    """
    amr_dir = amr_results_dir(work_dir)
    return [
        amr_dir / name
        for name in (*AMR_TABLE_FILES, AMR_MANIFEST_FILE)
        if (amr_dir / name).is_file()
    ]


def amr_outputs_present(work_dir: Path) -> bool:
    """Return True when a drakkar amr run left its per-assembly summary behind.

    drakkar exits 0 on some of its own error paths, so the missing summary is
    the only reliable sign that the AMR run did not finish.
    """
    return amr_qc_path(work_dir).exists()


def _amr_output_check_lines(work_dir: Path) -> list[str]:
    qc_path = amr_qc_path(work_dir)
    return [
        f"if [ ! -f {shlex.quote(str(qc_path))} ]; then",
        f"    echo \"Missing required AMR output: {qc_path}\" >&2",
        "    exit 1",
        "fi",
    ]


def _annotation_output_check_lines(work_dir: Path) -> list[str]:
    checks = [
        f"[ ! -f {shlex.quote(str(work_dir / rel_path))} ]"
        for rel_path in ANNOTATION_OUTPUT_FILES
    ]
    rendered_files = " and ".join(
        str(work_dir / rel_path) for rel_path in ANNOTATION_OUTPUT_FILES
    )
    return [
        f"if {' || '.join(checks)}; then",
        f"    echo \"Missing required annotation outputs: {rendered_files}\" >&2",
        "    exit 1",
        "fi",
    ]

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
    # A PAIRED run the archive serves as one flat FASTQ holding both mates
    # concatenated (see metadata.unsplit_paired_runs). The file is not R1, so
    # it travels in its own column for drakkar to split before preprocessing,
    # and rawreads1/rawreads2 are left empty for that row.
    ("rawreads_unsplit", "fastq_url_unsplit"),
]

# Emitting the unsplit URL in rawreads1 would hand drakkar half a library under
# the wrong name, so a row that has one carries no rawreads1/rawreads2 at all.
_UNSPLIT_SUPPRESSES = ("rawreads1", "rawreads2")


# ---------------------------------------------------------------------------
# Sequencing platform (drakkar --platform)
# ---------------------------------------------------------------------------

# `drakkar preprocessing --platform` picks the fastp adapter fallback sequences,
# whether polyG tails are trimmed, and the seqkit read-name regexp used under
# --sanitize. It takes one of these two values and defaults to illumina.
PLATFORM_ILLUMINA = "illumina"
PLATFORM_BGI = "bgi"
DRAKKAR_PLATFORMS: tuple[str, ...] = (PLATFORM_ILLUMINA, PLATFORM_BGI)
DEFAULT_PLATFORM = PLATFORM_ILLUMINA

# Reported for samples whose instrument_platform is blank or names an archive
# platform drakkar has no preprocessing profile for (nanopore, PacBio, …).
UNKNOWN_PLATFORM = "unknown"

# Matched as substrings, so an instrument *model* ("DNBSEQ-T7", "BGISEQ-500",
# "Illumina NovaSeq 6000") resolves as readily as ENA's platform name.
_PLATFORM_NEEDLES: tuple[tuple[str, str], ...] = (
    ("bgiseq", PLATFORM_BGI),
    ("dnbseq", PLATFORM_BGI),
    ("mgiseq", PLATFORM_BGI),
    ("illumina", PLATFORM_ILLUMINA),
)


def platform_from_instrument(value: str) -> str | None:
    """Map an ENA/GSA instrument_platform (or model) onto a drakkar --platform value.

    Returns None when *value* is blank or names a platform drakkar does not
    preprocess, so the caller can tell "no information" apart from "illumina".
    """
    lowered = str(value or "").strip().lower()
    if not lowered:
        return None
    for needle, platform in _PLATFORM_NEEDLES:
        if needle in lowered:
            return platform
    return None


def resolve_batch_platform(
    samples: list[dict[str, Any]],
) -> tuple[str, dict[str, int]]:
    """Return the drakkar --platform value for a batch, plus its per-platform counts.

    drakkar takes --platform once per run rather than per sample, so a batch
    whose samples disagree has to settle on one: the most common wins, and both
    a tie and a batch with no usable platform fall back to illumina, which is
    drakkar's own default. The counts (keyed by the two platforms and
    UNKNOWN_PLATFORM) let the caller report what it saw before deciding.

    Only rows with status 'use' are counted — the same rows build_input_tsv
    writes to the sample sheet.
    """
    counts = {PLATFORM_ILLUMINA: 0, PLATFORM_BGI: 0, UNKNOWN_PLATFORM: 0}
    for rec in samples:
        fields = rec.get("fields", rec)
        if fields.get("status") != "use":
            continue
        resolved = platform_from_instrument(fields.get("instrument_platform", ""))
        counts[resolved or UNKNOWN_PLATFORM] += 1

    platform = max(
        DRAKKAR_PLATFORMS,
        key=lambda p: (counts[p], p == DEFAULT_PLATFORM),
    )
    return platform, counts


def build_input_tsv(
    samples: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    """Write the Drakkar input TSV for a batch of decoded Airtable sample records.

    Required columns: sample, rawreads1, rawreads2, reference_name, reference_path.
    Optional columns (assembly, coverage, rawreads_unsplit) are included only
    when at least one row has a non-empty value.

    A row with a `rawreads_unsplit` URL gets empty rawreads1/rawreads2: that
    single file holds both mates concatenated, so naming it as R1 would feed
    drakkar half a library under the wrong name.
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
        unsplit = str(fields.get("fastq_url_unsplit", "") or "").strip()
        row = [
            ""
            if unsplit and col in _UNSPLIT_SUPPRESSES
            else str(fields.get(fn, "") or "")
            for col, fn in zip(header_cols, field_names)
        ]
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


def _with_slurm_options(
    flags: str,
    slurm_partition: str | None = None,
    slurm_qos: str | None = None,
) -> str:
    if slurm_partition:
        flags += f" --slurm-partition {shlex.quote(str(slurm_partition))}"
    if slurm_qos:
        flags += f" --slurm-qos {shlex.quote(str(slurm_qos))}"
    return flags


PIPELINE_STAGES: tuple[str, ...] = (
    "preprocessing",
    "cataloging",
    "amr",
    "profiling",
    "annotating",
)

# (status reported when a stage starts, status reported when it finishes)
_STAGE_STATUSES: dict[str, tuple[str, str]] = {
    "preprocessing": ("preprocessing", "preprocessed"),
    "cataloging":    ("cataloging", "cataloged"),
    "amr":           ("amr", "amr_done"),
    "profiling":     ("quantifying", "quantified"),
    "annotating":    ("annotating", "completed"),
}

# (stage, the status it reports when it starts). Mapped through the CLI's
# status table, these are what Airtable shows while a launch script is running.
STAGE_START_STATUSES: tuple[tuple[str, str], ...] = tuple(
    (stage, start) for stage, (start, _done) in _STAGE_STATUSES.items()
)

_STAGE_LABELS: dict[str, str] = {
    "preprocessing": "preprocessing",
    "cataloging": "cataloging",
    "amr": "amr",
    "profiling": "profiling",
    "annotating": "annotation",
}

# Stages driven by the sample TSV rather than by what an earlier stage wrote.
TSV_STAGES: tuple[str, ...] = ("preprocessing", "cataloging")

# Stages drakkar runs from inside the working directory.
_WORK_DIR_STAGES: tuple[str, ...] = ("amr", "profiling", "annotating")

# Stages whose resource envelope the Airtable boost columns widen.
_BOOSTED_STAGES: tuple[str, ...] = ("preprocessing", "amr")


def stages_from(stage: str) -> tuple[str, ...]:
    """Return *stage* plus every pipeline stage that follows it."""
    if stage not in PIPELINE_STAGES:
        raise ValueError(f"Unknown pipeline stage: {stage!r}")
    return PIPELINE_STAGES[PIPELINE_STAGES.index(stage):]


def _stage_drakkar_flags(
    stage: str,
    work_dir: Path,
    tsv_path: Path | None,
    slurm: bool,
    memory_multiplier: str | float | None,
    time_multiplier: str | float | None,
    slurm_partition: str | None,
    slurm_qos: str | None,
    platform: str | None = None,
) -> str:
    out_dir = shlex.quote(str(work_dir))
    bin_paths = shlex.quote(str(work_dir / "cataloging" / "final" / "all_bin_paths.txt"))
    reads_dir = shlex.quote(str(work_dir / "preprocessing" / "final"))
    bin_metadata = shlex.quote(str(work_dir / "cataloging" / "final" / "all_bin_metadata.csv"))

    if stage == "preprocessing":
        flags = f"-f {tsv_path} -o {work_dir} --fraction --nonpareil --env_path {DRAKKAR_ENV_PATH}"
        # preprocessing is the only stage that reads --platform: it is what picks
        # the adapter fallbacks, polyG trimming and read-name handling.
        if platform:
            flags += f" --platform {shlex.quote(str(platform))}"
    elif stage == "cataloging":
        flags = f"-f {tsv_path} -o {work_dir} --multicoverage --env_path {DRAKKAR_ENV_PATH}"
    elif stage == "amr":
        # drakkar amr -i discovers the assemblies cataloging wrote under
        # cataloging/megahit, naming each one after its folder (the sample code).
        flags = f"-i {out_dir} -o {out_dir} --env_path {DRAKKAR_ENV_PATH}"
    elif stage == "profiling":
        flags = (
            f"-B {bin_paths} -r {reads_dir} -a 0.98 -t genomes -q {bin_metadata} "
            f"-o {out_dir} --env_path {DRAKKAR_ENV_PATH}"
        )
    else:  # annotating
        flags = f"-B {bin_paths} -o {out_dir} --env_path {DRAKKAR_ENV_PATH}"

    if slurm:
        flags += " -p slurm"
    if stage in _BOOSTED_STAGES:
        if memory_multiplier not in (None, "", "1", 1, 1.0):
            flags += f" --memory-multiplier {memory_multiplier}"
        if time_multiplier not in (None, "", "1", 1, 1.0):
            flags += f" --time-multiplier {time_multiplier}"
    return _with_slurm_options(
        flags,
        slurm_partition=slurm_partition,
        slurm_qos=slurm_qos,
    )


def _stage_post_lines(stage: str, code: str, work_dir: Path) -> list[str]:
    """Return the lines that run between a stage's command and its 'done' status."""
    if stage in ("preprocessing", "cataloging"):
        return [_rename_workflow_tsv_line(code, work_dir, stage)]
    if stage == "amr":
        return _amr_output_check_lines(work_dir)
    if stage == "annotating":
        return _annotation_output_check_lines(work_dir)
    return []


def _set_status_line(
    wmw_cmd: str,
    code: str,
    stage: str,
    status: str,
    output_dir_arg: str,
    guarded: bool = True,
) -> str:
    """Return one `wmw set-status` call.

    Guarded calls hand a failure to `_wmw_bookkeeping_failed` instead of letting
    `set -e` abort the run: an Airtable hiccup must not cost the pipeline the
    stages it has not run yet.
    """
    line = f"{wmw_cmd} set-status --study {code} --workflow {stage} --status {status}{output_dir_arg}"
    if guarded:
        line += f" || _wmw_bookkeeping_failed {stage} {status}"
    return line


def _stage_block(
    stage: str,
    code: str,
    work_dir: Path,
    drakkar_cmd: str,
    wmw_cmd: str,
    output_dir_arg: str,
) -> list[str]:
    start_status, done_status = _STAGE_STATUSES[stage]
    trap_fn = f"_on_exit_{stage}"
    return [
        "_WMW_SUCCESS=0",
        f"{trap_fn}() {{",
        "    # Bookkeeping must not depend on the work dir still being readable.",
        "    cd / 2>/dev/null || true",
        '    if [ "$_WMW_SUCCESS" -ne 1 ]; then',
        '        if [ -f "$_WMW_STOP_FILE" ]; then',
        f"            {_set_status_line(wmw_cmd, code, stage, 'stopped', output_dir_arg, guarded=False)}",
        "        else",
        f"            {_set_status_line(wmw_cmd, code, stage, 'error', output_dir_arg, guarded=False)}",
        "        fi",
        "    fi",
        "}",
        f"trap {trap_fn} EXIT",
        "",
        _set_status_line(wmw_cmd, code, stage, start_status, output_dir_arg),
        drakkar_cmd,
        *_stage_post_lines(stage, code, work_dir),
        _set_status_line(wmw_cmd, code, stage, done_status, output_dir_arg),
        "_WMW_SUCCESS=1",
        "",
    ]


def _env_command(env: str, binary: str) -> str:
    """Return the command that runs *binary* from conda environment *env*.

    An env given as a path is called through its own ``bin/`` directory instead
    of through ``conda run``: conda chdirs into the current working directory
    before it execs the child, so a work dir that has become unreadable takes the
    command down with it — including the ``wmw set-status`` call whose whole job
    is to report that failure. Bash needs no access to the cwd to exec a binary,
    so the direct call survives it. A named env still needs conda to resolve it,
    and streams its output instead of letting conda buffer it until the end.
    """
    env = str(env).strip()
    if not env:
        return binary
    if env.startswith(("/", "~", ".")):
        return shlex.quote(str(Path(env).expanduser() / "bin" / binary))
    return f"conda run --no-capture-output -n {shlex.quote(env)} {binary}"


def generate_pipeline_script(
    code: str,
    work_dir: Path,
    conda_env: str,
    stages: Sequence[str] = PIPELINE_STAGES,
    tsv_path: Path | None = None,
    slurm: bool = False,
    wmw_conda_env: str = "",
    memory_multiplier: str | float | None = None,
    time_multiplier: str | float | None = None,
    slurm_partition: str | None = None,
    slurm_qos: str | None = None,
    platform: str | None = None,
) -> str:
    """Return a bash script that runs *stages* back to back for *code*.

    Every stage reports its own start/end status to Airtable and installs its own
    EXIT trap, so a failure is attributed to the stage that caused it. A stage
    that fails stops the script — the stages after it need what it did not write —
    but a stage that succeeds always flows straight into the next one.

    *platform* is passed to `drakkar preprocessing --platform`; it is ignored by
    every other stage. Leaving it None omits the flag and lets drakkar apply its
    own illumina default.
    """
    stages = tuple(stages)
    if not stages:
        raise ValueError("At least one pipeline stage is required.")
    unknown = [s for s in stages if s not in PIPELINE_STAGES]
    if unknown:
        raise ValueError(f"Unknown pipeline stage(s): {', '.join(unknown)}")
    if tsv_path is None and any(s in TSV_STAGES for s in stages):
        raise ValueError("tsv_path is required to run the preprocessing or cataloging stage.")
    if platform is not None and platform not in DRAKKAR_PLATFORMS:
        raise ValueError(
            f"Unknown sequencing platform: {platform!r} "
            f"(drakkar accepts {' or '.join(DRAKKAR_PLATFORMS)})."
        )

    if conda_env:
        drakkar_prefix = _env_command(conda_env, "drakkar")
        conda_lines = [
            'if [ -f "$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh" ]; then',
            '    source "$(conda info --base)/etc/profile.d/conda.sh"',
            f"    conda activate {conda_env}",
            "fi",
            "",
        ]
    else:
        drakkar_prefix = "drakkar"
        conda_lines = []

    wmw_cmd = _env_command(wmw_conda_env, "wmw")

    stop_file = shlex.quote(str(work_dir / ".wmw-stop"))
    output_dir_arg = f" --output-dir {shlex.quote(str(work_dir.parent))}"

    labels = [_STAGE_LABELS[s] for s in stages]
    header = " → ".join(labels) if len(labels) > 1 else f"{labels[0]} only"

    lines = [
        "#!/usr/bin/env bash",
        f"# wmw-generated script — batch {code} ({header})",
        "# Do not edit manually; re-run wmw process to regenerate.",
        "# AIRTABLE_TOKEN must be exported in the environment before launching.",
        "",
        "set -euo pipefail",
        # A detached screen session still gives conda a tty, on which it blocks
        # for 40s asking to upload a crash report. Nothing here is interactive.
        "export CONDA_REPORT_ERRORS=false",
        f"exec < /dev/null >> {work_dir}/{code}.out 2>> {work_dir}/{code}.err",
        'echo ""',
        "echo \"=== $(date '+%Y-%m-%d %H:%M:%S') ===\"",
        "echo \"=== $(date '+%Y-%m-%d %H:%M:%S') ===\" >&2",
        "",
        *conda_lines,
        f"_WMW_STOP_FILE={stop_file}",
        'rm -f "$_WMW_STOP_FILE"',
        "_WMW_BOOKKEEPING_FAILED=0",
        "_wmw_bookkeeping_failed() {",
        '    echo "wmw: Airtable update failed ($1 -> $2); continuing the pipeline." >&2',
        "    _WMW_BOOKKEEPING_FAILED=1",
        "}",
        "",
    ]

    cd_emitted = False
    for stage in stages:
        if not cd_emitted and stage in _WORK_DIR_STAGES:
            lines.append(f"cd {shlex.quote(str(work_dir))}")
            lines.append("")
            cd_emitted = True
        drakkar_flags = _stage_drakkar_flags(
            stage,
            work_dir,
            tsv_path,
            slurm,
            memory_multiplier,
            time_multiplier,
            slurm_partition,
            slurm_qos,
            platform,
        )
        lines.extend(
            _stage_block(
                stage,
                code,
                work_dir,
                f"{drakkar_prefix} {stage} {drakkar_flags}",
                wmw_cmd,
                output_dir_arg,
            )
        )

    # A stage whose Airtable bookkeeping failed still produced its outputs, so the
    # run carries on; parking the study in 'resume' makes the next `wmw process`
    # replay the finalisation that was missed.
    lines.extend([
        'if [ "$_WMW_BOOKKEEPING_FAILED" -ne 0 ]; then',
        '    echo "wmw: some Airtable updates failed — leaving the study in '
        "'resume' so wmw process can finish them.\" >&2",
        "    cd / 2>/dev/null || true",
        f"    {_set_status_line(wmw_cmd, code, stages[-1], 'resume', output_dir_arg, guarded=False)} || true",
        "fi",
        "",
    ])
    return "\n".join(lines)


def generate_full_pipeline_script(
    code: str,
    tsv_path: Path,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
    memory_multiplier: str | float | None = None,
    time_multiplier: str | float | None = None,
    slurm_partition: str | None = None,
    slurm_qos: str | None = None,
    platform: str | None = None,
) -> str:
    """Return a bash script that runs the full pipeline for *code*:
    preprocessing → cataloging → amr → profiling → annotation."""
    return generate_pipeline_script(
        code=code,
        work_dir=work_dir,
        conda_env=conda_env,
        stages=PIPELINE_STAGES,
        tsv_path=tsv_path,
        slurm=slurm,
        wmw_conda_env=wmw_conda_env,
        memory_multiplier=memory_multiplier,
        time_multiplier=time_multiplier,
        slurm_partition=slurm_partition,
        slurm_qos=slurm_qos,
        platform=platform,
    )


def generate_preprocessing_script(
    code: str,
    tsv_path: Path,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
    memory_multiplier: str | float | None = None,
    time_multiplier: str | float | None = None,
    slurm_partition: str | None = None,
    slurm_qos: str | None = None,
    platform: str | None = None,
) -> str:
    """Return a bash script that runs preprocessing then cataloging for *code*."""
    return generate_pipeline_script(
        code=code,
        work_dir=work_dir,
        conda_env=conda_env,
        stages=("preprocessing", "cataloging"),
        tsv_path=tsv_path,
        slurm=slurm,
        wmw_conda_env=wmw_conda_env,
        memory_multiplier=memory_multiplier,
        time_multiplier=time_multiplier,
        slurm_partition=slurm_partition,
        slurm_qos=slurm_qos,
        platform=platform,
    )


def generate_cataloging_script(
    code: str,
    tsv_path: Path,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
    slurm_partition: str | None = None,
    slurm_qos: str | None = None,
) -> str:
    """Return a bash script that runs drakkar cataloging only for *code*."""
    return generate_pipeline_script(
        code=code,
        work_dir=work_dir,
        conda_env=conda_env,
        stages=("cataloging",),
        tsv_path=tsv_path,
        slurm=slurm,
        wmw_conda_env=wmw_conda_env,
        slurm_partition=slurm_partition,
        slurm_qos=slurm_qos,
    )


def generate_amr_script(
    code: str,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
    memory_multiplier: str | float | None = None,
    time_multiplier: str | float | None = None,
    slurm_partition: str | None = None,
    slurm_qos: str | None = None,
) -> str:
    """Return a bash script that runs drakkar amr only for *code*.

    AMR runs between cataloging and profiling: it needs the assemblies
    cataloging produces and nothing profiling or annotating adds.
    """
    return generate_pipeline_script(
        code=code,
        work_dir=work_dir,
        conda_env=conda_env,
        stages=("amr",),
        slurm=slurm,
        wmw_conda_env=wmw_conda_env,
        memory_multiplier=memory_multiplier,
        time_multiplier=time_multiplier,
        slurm_partition=slurm_partition,
        slurm_qos=slurm_qos,
    )


def generate_profiling_script(
    code: str,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
    slurm_partition: str | None = None,
    slurm_qos: str | None = None,
) -> str:
    """Return a bash script that runs drakkar profiling only for *code*."""
    return generate_pipeline_script(
        code=code,
        work_dir=work_dir,
        conda_env=conda_env,
        stages=("profiling",),
        slurm=slurm,
        wmw_conda_env=wmw_conda_env,
        slurm_partition=slurm_partition,
        slurm_qos=slurm_qos,
    )


def generate_annotation_script(
    code: str,
    work_dir: Path,
    conda_env: str,
    slurm: bool = False,
    wmw_conda_env: str = "",
    slurm_partition: str | None = None,
    slurm_qos: str | None = None,
) -> str:
    """Return a bash script that runs drakkar annotating only for *code*."""
    return generate_pipeline_script(
        code=code,
        work_dir=work_dir,
        conda_env=conda_env,
        stages=("annotating",),
        slurm=slurm,
        wmw_conda_env=wmw_conda_env,
        slurm_partition=slurm_partition,
        slurm_qos=slurm_qos,
    )


# ---------------------------------------------------------------------------
# Drakkar invocation
# ---------------------------------------------------------------------------

def get_drakkar_version() -> str:
    """Return the installed drakkar version string, or 'unknown'."""
    import re

    conda_env = str(cfg.get("DRAKKAR_CONDA_ENV") or "").strip()
    if conda_env:
        flag = "-p" if conda_env.startswith(("/", "~", ".")) else "-n"
        prefix = ["conda", "run", flag, conda_env]
    else:
        prefix = []
    cmd = [*prefix, "drakkar", "--version"]
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

# One row of amr/amr_qc.tsv per assembly. The two '*_without_coordinates'
# columns are diagnostics of the callers rather than results, so they are not
# written to Airtable.
_AMR_QC_TSV_COLS: list[tuple[str, str, str]] = [
    # (tsv_column,        config_key,                            type)
    ("amrfinder_hits",    "SAMPLES_COL_AMR_AMRFINDER_HITS",      "int"),
    ("rgi_hits",          "SAMPLES_COL_AMR_RGI_HITS",            "int"),
    ("mobility_regions",  "SAMPLES_COL_AMR_MOBILITY_REGIONS",    "int"),
    ("amr_loci",          "SAMPLES_COL_AMR_LOCI",                "int"),
    ("multi_tool_loci",   "SAMPLES_COL_AMR_MULTI_TOOL_LOCI",     "int"),
    ("mobility_links",    "SAMPLES_COL_AMR_MOBILITY_LINKS",      "int"),
    ("mobile_loci",       "SAMPLES_COL_AMR_MOBILE_LOCI",         "int"),
]

_BIN_METADATA_CSV_COLS: list[tuple[str, str, str]] = [
    # (csv_column,     config_key,                   type)
    ("completeness",  "GENOMES_COL_COMPLETENESS",   "float2"),
    ("contamination", "GENOMES_COL_CONTAMINATION",  "float2"),
    ("size",          "GENOMES_COL_LENGTH",         "int"),
    ("N50",           "GENOMES_COL_N50",            "int"),
    ("contig_count",  "GENOMES_COL_CONTIGS",        "int"),
]

# Drakkar >= 2.0 writes ``<genome>_genes.tsv`` as a long-form hit table: one row
# per annotation hit, naming the database that produced it in a ``source``
# column. Each Airtable count is therefore the number of distinct genes carrying
# at least one hit from that source.
_GENOME_ANNOTATION_SOURCES: list[tuple[str, str]] = [
    # (config_key,                      `source` value)
    ("GENOMES_COL_NUMBER_KEGG",         "kegg"),
    ("GENOMES_COL_NUMBER_CAZY",         "cazy"),
    ("GENOMES_COL_NUMBER_VF",           "vfdb"),
    ("GENOMES_COL_NUMBER_AMR",          "ncbi_amrfinder"),
    ("GENOMES_COL_NUMBER_PFAM",         "pfam"),
    ("GENOMES_COL_NUMBER_SIGNALP",      "signalp"),
    ("GENOMES_COL_NUMBER_DEFENCE",      "defensefinder"),
]

# Drakkar keeps every predicted gene in the table under this source, whether or
# not it carries a functional annotation, so unannotated genes still get a row.
_GENE_CALL_SOURCE = "prodigal"

# EC numbers no longer have a column of their own; they ride in the ``details``
# JSON of each KEGG hit.
_EC_SOURCE = "kegg"
_EC_DETAILS_KEY = "ec"

# Drakkar 1.x wrote one row per gene with a column per database. Output
# directories written by that version are still parsed so an in-flight study can
# be finalised without rerunning annotation.
_LEGACY_ANNOTATION_COUNT_COLS: list[tuple[str, tuple[str, ...]]] = [
    # (config_key,                         annotation TSV column(s))
    ("GENOMES_COL_NUMBER_KEGG",           ("kegg",)),
    ("GENOMES_COL_NUMBER_CAZY",           ("cazy",)),
    ("GENOMES_COL_NUMBER_EC",             ("ec",)),
    ("GENOMES_COL_NUMBER_VF",             ("vf", "vf_type")),
    ("GENOMES_COL_NUMBER_AMR",            ("resistance_type", "resistance_target")),
    ("GENOMES_COL_NUMBER_PFAM",           ("pfam",)),
    ("GENOMES_COL_NUMBER_SIGNALP",        ("signalp",)),
    ("GENOMES_COL_NUMBER_DEFENCE",        ("defense", "defense_type")),
    ("GENOMES_COL_NUMBER_ANTIDEFENCE",    ("antidefense", "antidefense_type")),
]

_GENOME_TAXONOMY_RANKS: list[tuple[str, str]] = [
    # (classification prefix, config_key)
    ("d", "GENOMES_COL_TAXONOMY_DIVISION"),
    ("p", "GENOMES_COL_TAXONOMY_PHYLUM"),
    ("c", "GENOMES_COL_TAXONOMY_CLASS"),
    ("o", "GENOMES_COL_TAXONOMY_ORDER"),
    ("f", "GENOMES_COL_TAXONOMY_FAMILY"),
    ("g", "GENOMES_COL_TAXONOMY_GENUS"),
    ("s", "GENOMES_COL_TAXONOMY_SPECIES"),
]

_GENOME_TAXONOMY_STAT_COLS: list[tuple[tuple[str, ...], str, str]] = [
    # (tsv_column(s),                         config_key,                          type)
    (("closest_genome_ani", "fastani_ani"),   "GENOMES_COL_TAXONOMY_FASTANI_ANI",  "float"),
    (("closest_placement_ani",),              "GENOMES_COL_TAXONOMY_CLOSEST_ANI",  "float"),
    (("closest_placement_af",),               "GENOMES_COL_TAXONOMY_CLOSEST_AF",   "float"),
]

_GENOME_TAXONOMY_NAME_COLS = (
    "genome",
    "user_genome",
    "name",
    "bin",
    "mag",
)

_LEGACY_NON_ANNOTATION_COLS = {"gene", "start", "end", "strand"}

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
        if typ == "float":
            return float(raw)
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


def parse_amr_qc_tsv(tsv_path: Path) -> dict[str, dict[str, Any]]:
    """Parse drakkar's amr/amr_qc.tsv; return {sample_code: {field_id: value}}.

    Rows are keyed by the ``assembly_id`` column. ``drakkar amr -i`` names each
    assembly after its cataloging/megahit folder, which is the wmw sample code,
    so the keys line up with the Samples table the same way cataloging stats do.
    """
    if not Path(tsv_path).exists():
        return {}

    col_map: list[tuple[str, str, str]] = []
    for tsv_col, config_key, typ in _AMR_QC_TSV_COLS:
        fid = _config_field_id(config_key)
        if fid:
            col_map.append((tsv_col, fid, typ))
    if not col_map:
        return {}

    result: dict[str, dict[str, Any]] = {}
    with Path(tsv_path).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row_dict in reader:
            assembly = (row_dict.get("assembly_id") or "").strip()
            if not assembly:
                continue
            fields: dict[str, Any] = {}
            for tsv_col, fid, typ in col_map:
                value = _coerce_stat(row_dict.get(tsv_col, ""), typ)
                if value is not None:
                    fields[fid] = value
            if fields:
                result[assembly] = fields
    return result


def genome_annotation_files(work_dir: Path) -> list[Path]:
    """Return genome-specific annotation TSVs produced by drakkar annotating."""
    final_dir = Path(work_dir) / "annotating" / "final"
    if not final_dir.exists():
        return []
    return sorted(final_dir.glob("*_genes.tsv"))


def annotation_file_genome_name(annotation_path: Path) -> str:
    """Return the genome name encoded in ``<genome>_genes.tsv``."""
    name = Path(annotation_path).name
    lower = name.lower()
    for suffix in ("_genes.tsv.gz", "_genes.tsv"):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return ""


def _row_has_value(row: dict[str, Any], columns: tuple[str, ...]) -> bool:
    for column in columns:
        value = row.get(column)
        if value is None:
            continue
        if not _is_missing(str(value)):
            return True
    return False


def _row_has_any_annotation(row: dict[str, Any], columns: list[str]) -> bool:
    for column in columns:
        if column in _LEGACY_NON_ANNOTATION_COLS:
            continue
        value = row.get(column)
        if value is None:
            continue
        if not _is_missing(str(value)):
            return True
    return False


def _row_value(row: dict[str, Any], *columns: str) -> str:
    fallback = ""
    for column in columns:
        value = row.get(column)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
            fallback = text

    lower_row = {str(key).strip().lower(): value for key, value in row.items()}
    for column in columns:
        value = lower_row.get(column.lower())
        if value is not None:
            text = str(value).strip()
            if text:
                return text
            fallback = text
    return fallback


def _parse_taxonomy_classification(raw: str) -> dict[str, str]:
    rank_fids = {
        prefix: fid
        for prefix, config_key in _GENOME_TAXONOMY_RANKS
        if (fid := _config_field_id(config_key))
    }
    if not rank_fids:
        return {}

    fields: dict[str, str] = {}
    parts = [part.strip() for part in raw.split(";") if part.strip()]
    for part in parts:
        if "__" not in part:
            continue
        prefix, value = part.split("__", 1)
        prefix = prefix.strip().lower()
        value = value.strip()
        fid = rank_fids.get(prefix)
        if fid and not _is_missing(value):
            fields[fid] = value

    if fields or "__" in raw:
        return fields

    for value, (_prefix, config_key) in zip(parts, _GENOME_TAXONOMY_RANKS):
        value = value.strip()
        fid = _config_field_id(config_key)
        if fid and not _is_missing(value):
            fields[fid] = value
    return fields


def _details_has_value(raw: Any, key: str) -> bool:
    """Return True when *raw* is a Drakkar ``details`` JSON object holding *key*."""
    text = str(raw or "").strip()
    if not text:
        return False
    try:
        details = json.loads(text)
    except ValueError:
        return False
    if not isinstance(details, dict):
        return False
    value = details.get(key)
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return not _is_missing(str(value))


def _parse_long_annotation_rows(reader: csv.DictReader) -> dict[str, int]:
    """Count genes and per-source annotations in a Drakkar >= 2.0 hit table."""
    genes_fid = _config_field_id("GENOMES_COL_NUMBER_GENES")
    annotated_fid = _config_field_id("GENOMES_COL_NUMBER_ANNOTATED")
    ec_fid = _config_field_id("GENOMES_COL_NUMBER_EC")
    source_fields = [
        (fid, source)
        for config_key, source in _GENOME_ANNOTATION_SOURCES
        if (fid := _config_field_id(config_key))
    ]

    genes: set[str] = set()
    annotated: set[str] = set()
    ec_genes: set[str] = set()
    genes_by_source: dict[str, set[str]] = {}

    for row in reader:
        gene = str(row.get("gene") or "").strip()
        if not gene:
            continue
        genes.add(gene)

        source = str(row.get("source") or "").strip().lower()
        if not source or source == _GENE_CALL_SOURCE:
            continue
        annotated.add(gene)
        genes_by_source.setdefault(source, set()).add(gene)

        if source == _EC_SOURCE and _details_has_value(row.get("details"), _EC_DETAILS_KEY):
            ec_genes.add(gene)

    fields: dict[str, int] = {}
    if genes_fid:
        fields[genes_fid] = len(genes)
    if annotated_fid:
        fields[annotated_fid] = len(annotated)
    if ec_fid:
        fields[ec_fid] = len(ec_genes)
    for fid, source in source_fields:
        fields[fid] = len(genes_by_source.get(source, ()))
    return fields


def _parse_wide_annotation_rows(
    reader: csv.DictReader,
    columns: list[str],
) -> dict[str, int]:
    """Count genes and annotations in a Drakkar 1.x one-row-per-gene table."""
    genes_fid = _config_field_id("GENOMES_COL_NUMBER_GENES")
    annotated_fid = _config_field_id("GENOMES_COL_NUMBER_ANNOTATED")
    count_fields = [
        (fid, annotation_columns)
        for config_key, annotation_columns in _LEGACY_ANNOTATION_COUNT_COLS
        if (fid := _config_field_id(config_key))
    ]

    n_genes = 0
    n_annotated = 0
    counts: dict[str, int] = {fid: 0 for fid, _columns in count_fields}

    for row in reader:
        if not str(row.get("gene") or "").strip():
            continue
        n_genes += 1
        if _row_has_any_annotation(row, columns):
            n_annotated += 1
        for fid, annotation_columns in count_fields:
            if _row_has_value(row, annotation_columns):
                counts[fid] += 1

    fields: dict[str, int] = {}
    if genes_fid:
        fields[genes_fid] = n_genes
    if annotated_fid:
        fields[annotated_fid] = n_annotated
    fields.update(counts)
    return fields


def parse_genome_annotation_tsv(tsv_path: Path) -> dict[str, int]:
    """Parse a per-genome ``*_genes.tsv`` and return Airtable field-ID counts.

    Drakkar >= 2.0 writes one row per annotation hit and names the database in a
    ``source`` column; Drakkar 1.x wrote one row per gene with a column per
    database. The layout is detected from the header so both can be read.
    """
    if not tsv_path.exists():
        return {}

    with tsv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        columns = list(reader.fieldnames or [])
        if not columns:
            return {}
        if "source" in columns:
            return _parse_long_annotation_rows(reader)
        return _parse_wide_annotation_rows(reader, columns)


def parse_genome_taxonomy_tsv(tsv_path: Path) -> dict[str, dict[str, Any]]:
    """Parse drakkar's genome taxonomy TSV into Genomes-table Airtable fields.

    Returns ``{genome_name: {field_id: value}}``. Genome names are normalized in
    the same way as bin FASTA and annotation filenames, so ``*.fa`` suffixes and
    path components are removed before matching Airtable Genomes rows.
    """
    if not tsv_path.exists():
        return {}

    stat_map = [
        (columns, fid, typ)
        for columns, config_key, typ in _GENOME_TAXONOMY_STAT_COLS
        if (fid := _config_field_id(config_key))
    ]

    result: dict[str, dict[str, Any]] = {}
    with tsv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if not reader.fieldnames:
            return {}

        for row in reader:
            genome_raw = _row_value(row, *_GENOME_TAXONOMY_NAME_COLS)
            genome_name = _strip_fasta_suffix(genome_raw)
            if not genome_name:
                continue

            fields: dict[str, Any] = dict(
                _parse_taxonomy_classification(_row_value(row, "classification"))
            )
            for columns, fid, typ in stat_map:
                value = _coerce_stat(_row_value(row, *columns), typ)
                if value is not None:
                    fields[fid] = value

            if fields:
                result[genome_name] = fields
    return result


def _gzip_beside(source: Path, gz_path: Path) -> Path:
    """Compress *source* to *gz_path*, reusing an archive that is already current."""
    if gz_path.exists() and gz_path.stat().st_size > 0:
        if gz_path.stat().st_mtime >= source.stat().st_mtime:
            return gz_path

    with source.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return gz_path


def gzip_annotation_tsv(tsv_path: Path) -> Path:
    """Compress a per-genome annotation TSV beside the source and return .tsv.gz."""
    tsv_path = Path(tsv_path)
    lower = tsv_path.name.lower()
    if lower.endswith(".gz"):
        return tsv_path
    if lower.endswith(".tsv"):
        gz_path = tsv_path.with_suffix(".tsv.gz")
    else:
        gz_path = tsv_path.with_name(f"{tsv_path.name}.tsv.gz")
    return _gzip_beside(tsv_path, gz_path)


def contig_to_bin_files(work_dir: Path) -> dict[str, Path]:
    """Return {assembly: final_contig_to_bin.tsv} for a drakkar cataloging run.

    Binette writes one table per assembly as
    cataloging/binette/{assembly}/final_contig_to_bin.tsv, listing the bin each
    binned contig ended up in. The folder is named after the assembly, which is
    the wmw sample code — the same convention megahit's output follows.
    """
    binette_dir = Path(work_dir) / "cataloging" / "binette"
    if not binette_dir.is_dir():
        return {}
    return {
        path.parent.name: path
        for path in sorted(binette_dir.glob(f"*/{CONTIG_TO_BIN_FILE}"))
        if path.is_file() and path.parent.name
    }


def gzip_contig_to_bin_tsv(tsv_path: Path, sample_code: str) -> Path:
    """Compress a contig-to-bin table beside the source, named after the sample.

    Every assembly's table carries the same file name, and Airtable takes the
    attachment name from the path, so the archive is named
    {sample_code}_contig_to_bin.tsv.gz to keep the samples apart in the base.
    """
    tsv_path = Path(tsv_path)
    if tsv_path.name.lower().endswith(".gz"):
        return tsv_path
    code = str(sample_code).strip()
    stem = f"{code}_contig_to_bin" if code else tsv_path.stem
    return _gzip_beside(tsv_path, tsv_path.with_name(f"{stem}.tsv.gz"))


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

    return _gzip_beside(fasta_path, gz_path)


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
