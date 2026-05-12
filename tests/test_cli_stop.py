"""Tests for wmw stop command helpers."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from wmw import cli
from wmw import config as cfg


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


def test_cmd_set_status_preprocessed_uploads_preprocessing_tsv(tmp_path):
    work_dir = tmp_path / "ST001"
    work_dir.mkdir()
    preprocessing_tsv = work_dir / "preprocessing.tsv"
    preprocessing_tsv.write_text(
        "sample\treads_pre_fastp\nSA000022\t1000\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        study="ST001",
        workflow="preprocessing",
        status="preprocessed",
        output_dir=str(tmp_path),
        studies_table="Studies",
        samples_table="Samples",
        genomes_table="Genomes",
        airtable_token="",
        base_id="",
    )

    client = MagicMock()
    client.fetch_study_by_code.return_value = {"id": "recStudy", "fields": {"code": "ST001"}}
    client.update_sample_preprocessing_stats.return_value = 1

    with patch("wmw.cli._require_airtable", return_value=client) as require_airtable:
        assert cli.cmd_set_status(args) == 0

    require_airtable.assert_called_once_with(args, "Studies", "Samples")
    client.set_study_status.assert_called_once_with("Studies", ["recStudy"], "preprocessed")
    client.upload_study_file.assert_called_once_with(
        "Studies",
        "recStudy",
        "file_preprocessing",
        preprocessing_tsv,
    )
    client.update_sample_preprocessing_stats.assert_called_once()


def test_cmd_set_status_cataloged_uploads_genome_metadata(tmp_path):
    work_dir = tmp_path / "ST001"
    final_dir = work_dir / "cataloging" / "final"
    final_dir.mkdir(parents=True)
    cataloging_tsv = work_dir / "cataloging.tsv"
    cataloging_tsv.write_text(
        "assembly\tassembly_N50\nSA000022\t12345\n",
        encoding="utf-8",
    )
    (final_dir / "all_bin_metadata.csv").write_text(
        "genome,completeness,contamination,score,size,N50,contig_count\n"
        "SA000022_bin_339957.fa,99.984,0.054,99.88,2585871,91721,50\n",
        encoding="utf-8",
    )
    bin_path = final_dir / "SA000022" / "SA000022_bin_339957.fa"
    bin_path.parent.mkdir()
    bin_path.write_text(">contig1\nACGT\n", encoding="utf-8")
    (final_dir / "all_bin_paths.txt").write_text(
        "cataloging/final/SA000022/SA000022_bin_339957.fa\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        study="ST001",
        workflow="cataloging",
        status="cataloged",
        output_dir=str(tmp_path),
        studies_table="Studies",
        samples_table="Samples",
        genomes_table="Genomes",
        airtable_token="",
        base_id="",
    )

    client = MagicMock()
    client.fetch_study_by_code.return_value = {"id": "recStudy", "fields": {"code": "ST001"}}
    client.update_sample_cataloging_stats.return_value = 1
    client.fetch_sample_record_ids_by_code.return_value = {"SA000022": "recSample"}
    client.fetch_genome_records_by_name.return_value = {}
    client.update_genome_records.return_value = 0
    name_fid = str(cfg.get("GENOMES_COL_NAME"))
    client.create_genome_records_with_response.return_value = [
        {"id": "recGenome", "fields": {name_fid: "SA000022_bin_339957"}}
    ]

    with patch("wmw.cli._require_airtable", return_value=client) as require_airtable:
        assert cli.cmd_set_status(args) == 0

    require_airtable.assert_called_once_with(args, "Studies", "Samples")
    client.set_study_status.assert_called_once_with("Studies", ["recStudy"], "cataloged")
    client.upload_study_file.assert_called_once_with(
        "Studies",
        "recStudy",
        "file_cataloging",
        cataloging_tsv,
    )
    client.fetch_sample_record_ids_by_code.assert_called_once_with(
        "Samples",
        {"SA000022"},
    )

    link_fid = str(cfg.get("GENOMES_COL_SAMPLE_ID"))
    code_fid = str(cfg.get("GENOMES_COL_CODE"))
    completeness_fid = str(cfg.get("GENOMES_COL_COMPLETENESS"))
    length_fid = str(cfg.get("GENOMES_COL_LENGTH"))
    client.create_genome_records_with_response.assert_called_once()
    genomes_table, records = client.create_genome_records_with_response.call_args[0]
    assert genomes_table == "Genomes"
    assert records[0][link_fid] == ["recSample"]
    assert records[0][code_fid] == "SA000022_bin_339957"
    assert records[0][name_fid] == "SA000022_bin_339957"
    assert records[0][completeness_fid] == 99.98
    assert records[0][length_fid] == 2585871
    gz_path = bin_path.with_suffix(".fa.gz")
    assert gz_path.exists()
    client.upload_genome_file.assert_called_once_with(
        "Genomes",
        "recGenome",
        str(cfg.get("GENOMES_COL_FILE_GENOME")),
        gz_path,
    )


def test_cmd_process_resume_finalizes_airtable_without_launching_drakkar(tmp_path):
    work_dir = tmp_path / "ST001"
    final_dir = work_dir / "cataloging" / "final"
    final_dir.mkdir(parents=True)
    (work_dir / "preprocessing.tsv").write_text(
        "sample\treads_pre_fastp\nSA000022\t1000\n",
        encoding="utf-8",
    )
    (work_dir / "cataloging.tsv").write_text(
        "assembly\tassembly_N50\nSA000022\t12345\n",
        encoding="utf-8",
    )
    (final_dir / "all_bin_metadata.csv").write_text(
        "genome,completeness,contamination,score,size,N50,contig_count\n"
        "SA000022_bin_339957.fa,99.984,0.054,99.88,2585871,91721,50\n",
        encoding="utf-8",
    )
    bin_path = final_dir / "SA000022" / "SA000022_bin_339957.fa"
    bin_path.parent.mkdir()
    bin_path.write_text(">contig1\nACGT\n", encoding="utf-8")
    (final_dir / "all_bin_paths.txt").write_text(
        "cataloging/final/SA000022/SA000022_bin_339957.fa\n",
        encoding="utf-8",
    )

    args = argparse.Namespace(
        batch="",
        workflow="preprocessing",
        slurm=False,
        output_dir=str(tmp_path),
        studies_table="Studies",
        samples_table="Samples",
        genomes_table="Genomes",
        airtable_token="",
        base_id="",
    )

    client = MagicMock()
    resume_study = {
        "id": "recStudy",
        "fields": {
            "code": "ST001",
            "study_accession": "PRJEB001",
            "status": "resume",
        },
    }

    def fetch_by_status(_table, status):
        return [resume_study] if status == "resume" else []

    client.fetch_studies_by_status.side_effect = fetch_by_status
    client.update_sample_preprocessing_stats.return_value = 1
    client.update_sample_cataloging_stats.return_value = 1
    client.fetch_sample_record_ids_by_code.return_value = {"SA000022": "recSample"}
    client.fetch_genome_records_by_name.return_value = {}
    client.update_genome_records.return_value = 0
    name_fid = str(cfg.get("GENOMES_COL_NAME"))
    client.create_genome_records_with_response.return_value = [
        {"id": "recGenome", "fields": {name_fid: "SA000022_bin_339957"}}
    ]

    with (
        patch("wmw.cli._require_airtable", return_value=client) as require_airtable,
        patch("wmw.drakkar.build_input_tsv") as build_input_tsv,
        patch("subprocess.run") as run,
    ):
        assert cli.cmd_process(args) == 0

    require_airtable.assert_called_once_with(args, "Studies", "Samples")
    client.fetch_samples_for_study.assert_not_called()
    build_input_tsv.assert_not_called()
    run.assert_not_called()
    assert not (work_dir / "ST001.sh").exists()
    client.set_study_status.assert_has_calls(
        [
            call("Studies", ["recStudy"], "preprocessed"),
            call("Studies", ["recStudy"], "cataloged"),
        ]
    )
    assert client.upload_study_file.call_count == 2
    client.update_sample_preprocessing_stats.assert_called_once()
    client.update_sample_cataloging_stats.assert_called_once()
    client.create_genome_records_with_response.assert_called_once()
    client.upload_genome_file.assert_called_once()


def test_cmd_process_resume_without_outputs_launches_preprocessing(tmp_path):
    work_dir = tmp_path / "ST001"
    args = argparse.Namespace(
        batch="",
        workflow="preprocessing",
        slurm=False,
        output_dir=str(tmp_path),
        studies_table="Studies",
        samples_table="Samples",
        genomes_table="Genomes",
        airtable_token="",
        base_id="",
    )

    client = MagicMock()
    resume_study = {
        "id": "recStudy",
        "fields": {
            "code": "ST001",
            "study_accession": "PRJEB001",
            "status": "resume",
        },
    }

    def fetch_by_status(_table, status):
        return [resume_study] if status == "resume" else []

    client.fetch_studies_by_status.side_effect = fetch_by_status
    client.fetch_samples_for_study.return_value = [
        {"id": "recS1", "fields": {"code": "SA000022", "status": "use"}},
    ]

    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("wmw.drakkar.build_input_tsv") as build_input_tsv,
        patch("wmw.drakkar.generate_preprocessing_script", return_value="#!/usr/bin/env bash\n") as gen_pre,
        patch("wmw.drakkar.generate_cataloging_script") as gen_cat,
        patch("shutil.which", return_value="/usr/bin/screen"),
        patch("subprocess.run") as run,
    ):
        assert cli.cmd_process(args) == 0

    client.update_sample_preprocessing_stats.assert_not_called()
    client.update_sample_cataloging_stats.assert_not_called()
    build_input_tsv.assert_called_once()
    gen_pre.assert_called_once()
    gen_cat.assert_not_called()
    assert (work_dir / "ST001.sh").exists()
    run.assert_called_once_with(
        ["screen", "-dmS", "ST001", "bash", str(work_dir / "ST001.sh")],
        check=True,
    )


def test_cmd_process_resume_after_preprocessing_launches_cataloging_only(tmp_path):
    work_dir = tmp_path / "ST001"
    work_dir.mkdir()
    preprocessing_tsv = work_dir / "preprocessing.tsv"
    preprocessing_tsv.write_text(
        "sample\treads_pre_fastp\nSA000022\t1000\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        batch="",
        workflow="preprocessing",
        slurm=False,
        output_dir=str(tmp_path),
        studies_table="Studies",
        samples_table="Samples",
        genomes_table="Genomes",
        airtable_token="",
        base_id="",
    )

    client = MagicMock()
    resume_study = {
        "id": "recStudy",
        "fields": {
            "code": "ST001",
            "study_accession": "PRJEB001",
            "status": "resume",
        },
    }

    def fetch_by_status(_table, status):
        return [resume_study] if status == "resume" else []

    client.fetch_studies_by_status.side_effect = fetch_by_status
    client.fetch_samples_for_study.return_value = [
        {"id": "recS1", "fields": {"code": "SA000022", "status": "use"}},
    ]
    client.update_sample_preprocessing_stats.return_value = 1

    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("wmw.drakkar.build_input_tsv") as build_input_tsv,
        patch("wmw.drakkar.generate_cataloging_script", return_value="#!/usr/bin/env bash\n") as gen_cat,
        patch("wmw.drakkar.generate_preprocessing_script") as gen_pre,
        patch("shutil.which", return_value="/usr/bin/screen"),
        patch("subprocess.run") as run,
    ):
        assert cli.cmd_process(args) == 0

    client.set_study_status.assert_called_once_with("Studies", ["recStudy"], "preprocessed")
    client.upload_study_file.assert_called_once_with(
        "Studies",
        "recStudy",
        "file_preprocessing",
        preprocessing_tsv,
    )
    client.update_sample_preprocessing_stats.assert_called_once()
    build_input_tsv.assert_called_once()
    gen_cat.assert_called_once()
    gen_pre.assert_not_called()
    assert (work_dir / "ST001.sh").exists()
    run.assert_called_once_with(
        ["screen", "-dmS", "ST001", "bash", str(work_dir / "ST001.sh")],
        check=True,
    )


def test_populate_genomes_updates_existing_records_without_duplicate_upload(tmp_path):
    final_dir = tmp_path / "ST001" / "cataloging" / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "all_bin_metadata.csv").write_text(
        "genome,completeness,contamination,score,size,N50,contig_count\n"
        "SA000022_bin_339957.fa,99.984,0.054,99.88,2585871,91721,50\n",
        encoding="utf-8",
    )
    (final_dir / "all_bin_paths.txt").write_text("", encoding="utf-8")

    client = MagicMock()
    client.fetch_sample_record_ids_by_code.return_value = {"SA000022": "recSample"}
    name_fid = str(cfg.get("GENOMES_COL_NAME"))
    file_fid = str(cfg.get("GENOMES_COL_FILE_GENOME"))
    client.fetch_genome_records_by_name.return_value = {
        "SA000022_bin_339957": {
            "id": "recGenome",
            "fields": {
                name_fid: "SA000022_bin_339957",
                file_fid: [{"url": "https://example.test/bin.fa.gz"}],
            },
        }
    }
    client.update_genome_records.return_value = 1

    assert cli._populate_genome_records_from_outputs(
        client,
        "Samples",
        "Genomes",
        final_dir / "all_bin_metadata.csv",
        final_dir / "all_bin_paths.txt",
    )

    client.update_genome_records.assert_called_once()
    client.create_genome_records_with_response.assert_not_called()
    client.upload_genome_file.assert_not_called()
