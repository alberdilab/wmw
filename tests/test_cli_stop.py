"""Tests for wmw stop command helpers."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from wmw import cli


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_parse_squeue_jobs_includes_comment_column():
    jobs = cli._parse_squeue_jobs(
        "111|snakejob|/work/ST001|bash run|rule_fastp_wildcards_SA000022\n"
    )
    assert jobs == [
        {
            "id": "111",
            "name": "snakejob",
            "workdir": "/work/ST001",
            "command": "bash run",
            "comment": "rule_fastp_wildcards_SA000022",
        }
    ]


def test_slurm_job_matches_drakkar_wildcards_comment():
    job = {
        "id": "111",
        "name": "snakejob",
        "workdir": "/tmp/elsewhere",
        "command": "bash run",
        "comment": "rule_fastp_wildcards_SA000022",
    }
    assert cli._slurm_job_matches("ST001", Path("/work/ST001"), {"SA000022"}, job)
    assert not cli._slurm_job_matches("ST001", Path("/work/ST001"), {"SA999999"}, job)


def test_stop_parser_accepts_study_alias():
    args = cli._build_parser().parse_args(["stop", "--study", "ST001"])
    assert args.batch == "ST001"
    assert args.func is cli.cmd_stop


def test_cmd_stop_sets_status_stops_screen_and_cancels_matching_slurm_jobs(tmp_path):
    work_dir = tmp_path / "ST001"
    work_dir.mkdir()
    args = argparse.Namespace(
        batch="ST001",
        output_dir=str(tmp_path),
        studies_table="Studies",
        samples_table="Samples",
        airtable_token="",
        base_id="",
    )

    client = MagicMock()
    client.fetch_study_by_code.return_value = {
        "id": "recStudy",
        "fields": {"code": "ST001", "study_accession": "PRJEB001"},
    }
    client.fetch_samples_for_study.return_value = [
        {"id": "recS1", "fields": {"code": "SA000022"}},
        {"id": "recS2", "fields": {"code": "SA000023"}},
    ]

    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if cmd == ["screen", "-ls"]:
            return _completed(stdout="\t123.ST001\t(Detached)\n")
        if cmd == ["screen", "-S", "123.ST001", "-X", "quit"]:
            return _completed()
        if cmd[0] == "squeue":
            return _completed(
                stdout=(
                    "111|snakejob|/tmp/other|bash run|rule_fastp_wildcards_SA000022\n"
                    f"222|snakejob|{work_dir}|bash run|\n"
                    "333|snakejob|/tmp/other|bash run|rule_fastp_wildcards_OTHER\n"
                )
            )
        if cmd[0] == "scancel":
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if cmd in {"screen", "squeue", "scancel"} else None

    with (
        patch("wmw.cli._require_airtable", return_value=client) as require_airtable,
        patch("shutil.which", side_effect=fake_which),
        patch("subprocess.run", side_effect=fake_run),
    ):
        assert cli.cmd_stop(args) == 0

    require_airtable.assert_called_once_with(args, "Studies", "Samples")
    client.fetch_samples_for_study.assert_called_once_with("Samples", "PRJEB001")
    client.set_study_status.assert_has_calls(
        [
            call("Studies", ["recStudy"], "stopped"),
            call("Studies", ["recStudy"], "stopped"),
        ]
    )
    assert client.set_study_status.call_count == 2
    assert (work_dir / ".wmw-stop").read_text(encoding="utf-8") == "stopped\n"
    assert ["screen", "-S", "123.ST001", "-X", "quit"] in commands
    assert ["scancel", "111", "222"] in commands
