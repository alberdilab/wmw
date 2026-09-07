"""fasterq-dump bridge: recover R1/R2 for runs the archive serves unsplit.

When a submitter uploads reads that were already quality-trimmed, the two
FASTQ files no longer line up read-for-read, so the SRA loader gives up on
pairing them: it stores every read as its own single-read spot — all of one
file's reads, then all of the other's, each spot carrying a zero-length
second read. ENA mirrors that object, so its `fastq_ftp` names one flat
`<run>.fastq.gz` holding both mates concatenated instead of a `_1`/`_2` pair.

The reads are all still there, and each keeps the read index it was loaded
under, so `fasterq-dump --split-files` writes them back out as the
submitter's original R1 and R2. Which half is R1 varies per run; splitting
by read index rather than by position handles both orientations.

`metadata.unsplit_paired_runs()` is what identifies the affected runs.
"""

from __future__ import annotations

import gzip
import shutil
import subprocess
from pathlib import Path

FASTERQ_DUMP = "fasterq-dump"

# fasterq-dump names its outputs after the accession; --split-files gives one
# file per read index of the spot.
_R1_SUFFIX = "_1.fastq"
_R2_SUFFIX = "_2.fastq"

# Written when a spot holds a single read fasterq-dump could not assign to
# either mate. An unsplit run should not produce one; if it does, the run does
# not have the structure this module assumes and the caller is told.
_ORPHAN_SUFFIX = ".fastq"


class SraToolsError(RuntimeError):
    """fasterq-dump is missing, failed, or produced something unexpected."""


def fasterq_dump_path(binary: str = FASTERQ_DUMP) -> str | None:
    """Return the resolved path to fasterq-dump, or None when it is not installed."""
    return shutil.which(binary)


def require_fasterq_dump(binary: str = FASTERQ_DUMP) -> str:
    """Return the path to fasterq-dump, raising SraToolsError when it is absent."""
    resolved = fasterq_dump_path(binary)
    if resolved is None:
        raise SraToolsError(
            f"{binary} not found on PATH. Recovering an unsplit run needs the NCBI "
            "SRA Toolkit — install it (conda install -c bioconda sra-tools) or pass "
            "--fasterq-dump with its path."
        )
    return resolved


def pair_paths(run_accession: str, out_dir: Path, gzipped: bool = False) -> tuple[Path, Path]:
    """Return the (R1, R2) paths a split of *run_accession* writes into *out_dir*."""
    suffix = ".gz" if gzipped else ""
    return (
        out_dir / f"{run_accession}{_R1_SUFFIX}{suffix}",
        out_dir / f"{run_accession}{_R2_SUFFIX}{suffix}",
    )


def existing_pair(run_accession: str, out_dir: Path) -> tuple[Path, Path] | None:
    """Return an already-recovered (R1, R2) pair in *out_dir*, gzipped or not."""
    for gzipped in (True, False):
        r1, r2 = pair_paths(run_accession, out_dir, gzipped=gzipped)
        if r1.exists() and r2.exists():
            return r1, r2
    return None


def build_command(
    run_accession: str,
    out_dir: Path,
    *,
    binary: str = FASTERQ_DUMP,
    threads: int = 6,
    tmp_dir: Path | None = None,
) -> list[str]:
    """Return the fasterq-dump argv that splits *run_accession* into R1/R2."""
    cmd = [
        binary,
        "--split-files",
        "--outdir", str(out_dir),
        "--threads", str(threads),
        "--force",
    ]
    if tmp_dir is not None:
        cmd += ["--temp", str(tmp_dir)]
    cmd.append(run_accession)
    return cmd


def count_reads(path: Path) -> int:
    """Return the number of FASTQ records in *path* (plain or gzipped).

    Streams the file, so it costs a full read — the caller decides whether the
    check is worth it.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    lines = 0
    with opener(path, "rb") as handle:          # type: ignore[operator]
        for _ in handle:
            lines += 1
    if lines % 4:
        raise SraToolsError(
            f"{path.name} holds {lines} lines, which is not a whole number of "
            "FASTQ records — the file is truncated."
        )
    return lines // 4


def gzip_in_place(path: Path, *, threads: int = 6) -> Path:
    """Compress *path* to <path>.gz, removing the original. Uses pigz when present."""
    target = path.with_name(path.name + ".gz")
    pigz = shutil.which("pigz")
    if pigz:
        result = subprocess.run(
            [pigz, "-f", "-p", str(threads), str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SraToolsError(
                f"pigz failed on {path.name}: {result.stderr.strip() or result.returncode}"
            )
    else:
        with open(path, "rb") as src, gzip.open(target, "wb") as dst:
            shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
        path.unlink()
    if not target.exists():
        raise SraToolsError(f"compressing {path.name} produced no {target.name}.")
    return target


def split_run(
    run_accession: str,
    out_dir: Path,
    *,
    binary: str = FASTERQ_DUMP,
    threads: int = 6,
    tmp_dir: Path | None = None,
    compress: bool = True,
) -> tuple[Path, Path]:
    """Recover the R1/R2 pair of *run_accession* into *out_dir*.

    Returns the (R1, R2) paths, gzipped unless *compress* is False. Raises
    SraToolsError when fasterq-dump is missing, exits non-zero, or writes
    something other than the expected two files.
    """
    resolved = require_fasterq_dump(binary)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = build_command(
        run_accession,
        out_dir,
        binary=resolved,
        threads=threads,
        tmp_dir=tmp_dir,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SraToolsError(
            f"fasterq-dump failed on {run_accession} "
            f"(exit {result.returncode}): {detail or 'no output'}"
        )

    r1, r2 = pair_paths(run_accession, out_dir)
    missing = [p.name for p in (r1, r2) if not p.exists()]
    if missing:
        orphan = out_dir / f"{run_accession}{_ORPHAN_SUFFIX}"
        hint = (
            f" fasterq-dump wrote {orphan.name} instead, so this run's reads carry "
            "a single read index and cannot be split by mate."
            if orphan.exists()
            else ""
        )
        raise SraToolsError(
            f"fasterq-dump produced no {', '.join(missing)} for {run_accession}.{hint}"
        )

    if compress:
        r1 = gzip_in_place(r1, threads=threads)
        r2 = gzip_in_place(r2, threads=threads)
    return r1, r2


def verify_pair(r1: Path, r2: Path) -> int:
    """Check that *r1* and *r2* hold the same number of reads, and return it.

    The two halves are the submitter's original files in their original order,
    so a mismatch means the run does not have the structure this module
    assumes and the pair must not be trusted as mates.
    """
    n1 = count_reads(r1)
    n2 = count_reads(r2)
    if n1 != n2:
        raise SraToolsError(
            f"{r1.name} holds {n1:,} reads but {r2.name} holds {n2:,} — "
            "the halves are not mates, so the pair was not written to Airtable."
        )
    return n1
