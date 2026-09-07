"""Tests for `wmw redump` — recovering R1/R2 for runs served unsplit."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest
from wmw import cli, sratools


def _sample(run_accession, *, layout="PAIRED", url2="", status="use"):
    return {
        "id": f"rec_{run_accession}",
        "fields": {
            "run_accession": run_accession,
            "library_layout": layout,
            "fastq_url_1": f"ftp://host/{run_accession}.fastq.gz",
            "fastq_url_2": url2,
            "status": status,
        },
    }


class _FakeClient:
    def __init__(self, samples, study_accession="PRJNA556790"):
        self._samples = samples
        self._study_accession = study_accession
        self.written: dict[str, tuple[str, str]] = {}

    def fetch_study_by_code(self, table, code):
        return {"id": "recS", "fields": {"study_accession": self._study_accession}}

    def fetch_samples_for_study(self, table, study_accession, status=None):
        return self._samples

    def set_sample_fastq_paths(self, table, pairs):
        self.written.update(pairs)
        return len(pairs)


def _args(**kwargs):
    base = dict(
        study="D556790",
        run=[],
        output_dir="",
        studies_table="",
        samples_table="",
        threads=2,
        tmp_dir="",
        fasterq_dump="",
        no_gzip=False,
        verify=False,
        force=False,
        dry_run=False,
        airtable_token="",
        base_id="",
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli.cfg, "get",
        lambda key, default=None: str(tmp_path) if key == "DRAKKAR_OUTPUT_DIR" else "",
    )
    return tmp_path


def test_redump_dry_run_lists_only_unsplit_runs(out_dir, capsys):
    samples = [
        _sample("SRR1"),                                      # unsplit
        _sample("SRR2", url2="ftp://host/SRR2_2.fastq.gz"),   # already paired
        _sample("SRR3", layout="SINGLE"),                     # genuinely single
    ]
    client = _FakeClient(samples)
    with patch.object(cli, "_require_airtable", return_value=client):
        rc = cli.cmd_redump(_args(dry_run=True))

    output = capsys.readouterr().out
    assert rc == 0
    assert "1 run to recover" in output
    assert "SRR1_1.fastq.gz" in output
    assert "SRR2" not in output
    assert "SRR3" not in output
    assert client.written == {}


def test_redump_reports_when_nothing_is_affected(out_dir, capsys):
    client = _FakeClient([_sample("SRR2", url2="ftp://host/SRR2_2.fastq.gz")])
    with patch.object(cli, "_require_airtable", return_value=client):
        rc = cli.cmd_redump(_args(dry_run=True))

    assert rc == 0
    assert "no run is served as an unsplit FASTQ" in capsys.readouterr().out


def test_redump_writes_local_pair_to_airtable(out_dir, capsys):
    client = _FakeClient([_sample("SRR1")])

    def fake_split(acc, reads_dir, **kwargs):
        reads_dir.mkdir(parents=True, exist_ok=True)
        r1, r2 = sratools.pair_paths(acc, reads_dir, gzipped=True)
        r1.write_bytes(b"")
        r2.write_bytes(b"")
        return r1, r2

    with patch.object(cli, "_require_airtable", return_value=client), \
         patch.object(sratools, "require_fasterq_dump", return_value="/bin/fasterq-dump"), \
         patch.object(sratools, "split_run", side_effect=fake_split):
        rc = cli.cmd_redump(_args())

    assert rc == 0
    r1, r2 = client.written["SRR1"]
    assert r1.endswith("D556790/rawreads/SRR1_1.fastq.gz")
    assert r2.endswith("D556790/rawreads/SRR1_2.fastq.gz")


def test_redump_skips_already_recovered_pair(out_dir, capsys):
    reads_dir = out_dir / "D556790" / "rawreads"
    reads_dir.mkdir(parents=True)
    for path in sratools.pair_paths("SRR1", reads_dir, gzipped=True):
        path.write_bytes(b"")

    client = _FakeClient([_sample("SRR1")])
    with patch.object(cli, "_require_airtable", return_value=client), \
         patch.object(sratools, "require_fasterq_dump", return_value="/bin/fasterq-dump"), \
         patch.object(sratools, "split_run", side_effect=AssertionError("must not dump")):
        rc = cli.cmd_redump(_args())

    assert rc == 0
    assert "already recovered" in capsys.readouterr().out
    assert "SRR1" in client.written


def test_redump_reports_a_failed_run_without_writing_it(out_dir, capsys):
    client = _FakeClient([_sample("SRR1")])
    with patch.object(cli, "_require_airtable", return_value=client), \
         patch.object(sratools, "require_fasterq_dump", return_value="/bin/fasterq-dump"), \
         patch.object(
             sratools, "split_run",
             side_effect=sratools.SraToolsError("cannot be split by mate"),
         ):
        rc = cli.cmd_redump(_args())

    assert rc == 1
    assert "cannot be split by mate" in capsys.readouterr().out
    assert client.written == {}


def test_redump_verify_rejects_mismatched_mates(out_dir, capsys):
    client = _FakeClient([_sample("SRR1")])

    def fake_split(acc, reads_dir, **kwargs):
        reads_dir.mkdir(parents=True, exist_ok=True)
        r1, r2 = sratools.pair_paths(acc, reads_dir, gzipped=True)
        r1.write_bytes(b"")
        r2.write_bytes(b"")
        return r1, r2

    with patch.object(cli, "_require_airtable", return_value=client), \
         patch.object(sratools, "require_fasterq_dump", return_value="/bin/fasterq-dump"), \
         patch.object(sratools, "split_run", side_effect=fake_split), \
         patch.object(
             sratools, "verify_pair",
             side_effect=sratools.SraToolsError("halves are not mates"),
         ):
        rc = cli.cmd_redump(_args(verify=True))

    assert rc == 1
    assert "not mates" in capsys.readouterr().out
    assert client.written == {}


def test_redump_run_flag_overrides_detection(out_dir, capsys):
    # SRR2 already has a mate, so detection would skip it; --run forces it.
    client = _FakeClient([_sample("SRR2", url2="ftp://host/SRR2_2.fastq.gz")])
    with patch.object(cli, "_require_airtable", return_value=client):
        rc = cli.cmd_redump(_args(run=["SRR2"], dry_run=True))

    assert rc == 0
    assert "SRR2_1.fastq.gz" in capsys.readouterr().out


def test_redump_warns_about_unknown_run_accession(out_dir, capsys):
    client = _FakeClient([_sample("SRR1")])
    with patch.object(cli, "_require_airtable", return_value=client):
        cli.cmd_redump(_args(run=["SRR1", "SRR999"], dry_run=True))

    assert "not in this study, ignored — SRR999" in capsys.readouterr().out
