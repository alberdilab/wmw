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


def test_screen_sessions_for_code_includes_genome_upload_session():
    sessions = cli._screen_sessions_for_code(
        "\t123.ST001\t(Detached)\n"
        "\t124.ST001-genome-upload\t(Detached)\n"
        "\t125.ST001-other\t(Detached)\n",
        "ST001",
    )

    assert sessions == ["123.ST001", "124.ST001-genome-upload"]


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
    preprocessing_tsv = work_dir / "ST001_preprocessing.tsv"
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


def test_workflow_tsv_path_prefers_prefixed_and_falls_back_to_legacy(tmp_path):
    work_dir = tmp_path / "ST001"
    work_dir.mkdir()
    legacy = work_dir / "preprocessing.tsv"
    preferred = work_dir / "ST001_preprocessing.tsv"

    legacy.write_text("legacy\n", encoding="utf-8")
    assert cli._existing_workflow_tsv_path(work_dir, "ST001", "preprocessing") == legacy

    preferred.write_text("preferred\n", encoding="utf-8")
    assert cli._existing_workflow_tsv_path(work_dir, "ST001", "preprocessing") == preferred


def test_cmd_set_status_annotating_completed_sets_done(tmp_path):
    args = argparse.Namespace(
        study="ST001",
        workflow="annotating",
        status="completed",
        output_dir=str(tmp_path),
        studies_table="Studies",
        samples_table="Samples",
        genomes_table="Genomes",
        airtable_token="",
        base_id="",
    )

    client = MagicMock()
    client.fetch_study_by_code.return_value = {"id": "recStudy", "fields": {"code": "ST001"}}

    with patch("wmw.cli._require_airtable", return_value=client):
        assert cli.cmd_set_status(args) == 0

    client.set_study_status.assert_called_once_with("Studies", ["recStudy"], "Done")


def test_cmd_set_status_annotating_completed_uploads_annotation_stats_and_file(tmp_path):
    work_dir = tmp_path / "ST001"
    final_dir = work_dir / "annotating" / "final"
    final_dir.mkdir(parents=True)
    annotation_path = final_dir / "SA000022_bin_339957_genes.tsv"
    annotation_path.write_text(
        "gene\tstart\tend\tstrand\tkegg\tec\tpfam\tcazy\tresistance_type\t"
        "resistance_target\tvf\tvf_type\tsignalp\tdefense\tdefense_type\t"
        "antidefense\tantidefense_type\n"
        "gene1\t1\t100\t+\tK00001\t\tPF00001\t\t\t\t\t\t\t\t\t\t\n"
        "gene2\t2\t200\t-\t\t\t\t\t\t\t\t\t\t\t\t\t\n"
        "gene3\t3\t300\t+\t\t1.1.1.1\t\tGH1\tDrug\tTarget\tVF1\tType\tSec\tDef\tType\tAnti\tType\n",
        encoding="utf-8",
    )
    (work_dir / "annotating" / "genome_taxonomy.tsv").write_text(
        "genome\tclassification\tclosest_genome_ani\tclosest_placement_ani\tclosest_placement_af\n"
        "SA000022_bin_339957.fa\t"
        "d__Bacteria;p__Pseudomonadota;c__Gammaproteobacteria;"
        "o__Enterobacterales;f__Aeromonadaceae;g__Aeromonas;"
        "s__Aeromonas rivipollensis\t"
        "99.8123\t97.4567\t0.823456\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        study="ST001",
        workflow="annotating",
        status="completed",
        output_dir=str(tmp_path),
        studies_table="Studies",
        samples_table="Samples",
        genomes_table="Genomes",
        airtable_token="",
        base_id="",
    )

    name_fid = str(cfg.get("GENOMES_COL_NAME"))
    file_fid = str(cfg.get("GENOMES_COL_FILE_ANNOTATION"))
    client = MagicMock()
    client.fetch_study_by_code.return_value = {"id": "recStudy", "fields": {"code": "ST001"}}
    client.fetch_genome_records_by_name.return_value = {
        "SA000022_bin_339957": {
            "id": "recGenome",
            "fields": {name_fid: "SA000022_bin_339957"},
        }
    }
    client.update_genome_records.return_value = 1

    with patch("wmw.cli._require_airtable", return_value=client):
        assert cli.cmd_set_status(args) == 0

    client.set_study_status.assert_called_once_with("Studies", ["recStudy"], "Done")
    client.fetch_genome_records_by_name.assert_called_once_with(
        "Genomes",
        ["SA000022_bin_339957"],
        name_fid,
    )
    client.update_genome_records.assert_called_once()
    genomes_table, updates = client.update_genome_records.call_args[0]
    assert genomes_table == "Genomes"
    assert updates[0]["id"] == "recGenome"
    fields = updates[0]["fields"]
    assert fields[str(cfg.get("GENOMES_COL_NUMBER_GENES"))] == 3
    assert fields[str(cfg.get("GENOMES_COL_NUMBER_ANNOTATED"))] == 2
    assert fields[str(cfg.get("GENOMES_COL_NUMBER_KEGG"))] == 1
    assert fields[str(cfg.get("GENOMES_COL_NUMBER_CAZY"))] == 1
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_DIVISION"))] == "Bacteria"
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_PHYLUM"))] == "Pseudomonadota"
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_CLASS"))] == "Gammaproteobacteria"
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_ORDER"))] == "Enterobacterales"
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_FAMILY"))] == "Aeromonadaceae"
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_GENUS"))] == "Aeromonas"
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_SPECIES"))] == "Aeromonas rivipollensis"
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_FASTANI_ANI"))] == 99.8123
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_CLOSEST_ANI"))] == 97.4567
    assert fields[str(cfg.get("GENOMES_COL_TAXONOMY_CLOSEST_AF"))] == 0.823456

    gz_path = annotation_path.with_suffix(".tsv.gz")
    assert gz_path.exists()
    client.upload_genome_file.assert_called_once_with(
        "Genomes",
        "recGenome",
        file_fid,
        gz_path,
    )


def test_cmd_set_status_cataloged_uploads_genome_metadata(tmp_path):
    work_dir = tmp_path / "ST001"
    final_dir = work_dir / "cataloging" / "final"
    final_dir.mkdir(parents=True)
    cataloging_tsv = work_dir / "ST001_cataloging.tsv"
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

    with (
        patch("wmw.cli._require_airtable", return_value=client) as require_airtable,
        patch("shutil.which", return_value=None),
    ):
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
    completeness_fid = str(cfg.get("GENOMES_COL_COMPLETENESS"))
    length_fid = str(cfg.get("GENOMES_COL_LENGTH"))
    client.create_genome_records_with_response.assert_called_once()
    genomes_table, records = client.create_genome_records_with_response.call_args[0]
    assert genomes_table == "Genomes"
    assert records[0][link_fid] == ["recSample"]
    assert "fldY23Xw8FIRa0T8b" not in records[0]
    assert "fldQzo5sFYPe5k1Aj" not in records[0]
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


def test_cmd_set_status_cataloged_launches_genome_upload_screen(tmp_path):
    work_dir = tmp_path / "ST001"
    final_dir = work_dir / "cataloging" / "final"
    final_dir.mkdir(parents=True)
    cataloging_tsv = work_dir / "ST001_cataloging.tsv"
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
        airtable_token="secret-token",
        base_id="appBase",
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

    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("shutil.which", return_value="/usr/bin/screen"),
        patch("subprocess.run") as run,
    ):
        assert cli.cmd_set_status(args) == 0

    script_path = work_dir / "ST001_upload_genomes.sh"
    assert script_path.exists()
    script_text = script_path.read_text(encoding="utf-8")
    assert "upload-genome-files" in script_text
    assert "secret-token" not in script_text
    assert "--base-id appBase" in script_text

    client.create_genome_records_with_response.assert_called_once()
    client.upload_genome_file.assert_not_called()
    assert not bin_path.with_suffix(".fa.gz").exists()
    run.assert_called_once()
    assert run.call_args[0][0] == [
        "screen",
        "-dmS",
        "ST001-genome-upload",
        "bash",
        str(script_path),
    ]
    assert run.call_args.kwargs["check"] is True
    assert run.call_args.kwargs["env"]["AIRTABLE_TOKEN"] == "secret-token"


def test_populate_genomes_skips_records_and_files_below_quality_thresholds(tmp_path):
    final_dir = tmp_path / "ST001" / "cataloging" / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "all_bin_metadata.csv").write_text(
        "genome,completeness,contamination,score,size,N50,contig_count\n"
        "SA000022_bin_good.fa,50.01,9.99,99.88,2585871,91721,50\n"
        "SA000023_bin_low_completeness.fa,50,0.05,99.88,2585871,91721,50\n"
        "SA000024_bin_high_contamination.fa,99.98,10,99.88,2585871,91721,50\n",
        encoding="utf-8",
    )
    good_bin = final_dir / "SA000022" / "SA000022_bin_good.fa"
    low_bin = final_dir / "SA000023" / "SA000023_bin_low_completeness.fa"
    high_bin = final_dir / "SA000024" / "SA000024_bin_high_contamination.fa"
    for bin_path in (good_bin, low_bin, high_bin):
        bin_path.parent.mkdir()
        bin_path.write_text(">contig1\nACGT\n", encoding="utf-8")
    (final_dir / "all_bin_paths.txt").write_text(
        "cataloging/final/SA000022/SA000022_bin_good.fa\n"
        "cataloging/final/SA000023/SA000023_bin_low_completeness.fa\n"
        "cataloging/final/SA000024/SA000024_bin_high_contamination.fa\n",
        encoding="utf-8",
    )

    client = MagicMock()
    client.fetch_sample_record_ids_by_code.return_value = {"SA000022": "recSample"}
    client.fetch_genome_records_by_name.return_value = {}
    client.update_genome_records.return_value = 0
    name_fid = str(cfg.get("GENOMES_COL_NAME"))
    client.create_genome_records_with_response.return_value = [
        {"id": "recGenome", "fields": {name_fid: "SA000022_bin_good"}}
    ]

    assert cli._populate_genome_records_from_outputs(
        client,
        "Samples",
        "Genomes",
        final_dir / "all_bin_metadata.csv",
        final_dir / "all_bin_paths.txt",
    )

    client.fetch_sample_record_ids_by_code.assert_called_once_with("Samples", {"SA000022"})
    client.fetch_genome_records_by_name.assert_called_once_with(
        "Genomes",
        ["SA000022_bin_good"],
        name_fid,
    )
    client.create_genome_records_with_response.assert_called_once()
    _, records = client.create_genome_records_with_response.call_args[0]
    assert len(records) == 1
    assert records[0][name_fid] == "SA000022_bin_good"

    good_gz = good_bin.with_suffix(".fa.gz")
    assert good_gz.exists()
    assert not low_bin.with_suffix(".fa.gz").exists()
    assert not high_bin.with_suffix(".fa.gz").exists()
    client.upload_genome_file.assert_called_once_with(
        "Genomes",
        "recGenome",
        str(cfg.get("GENOMES_COL_FILE_GENOME")),
        good_gz,
    )


def test_cmd_process_resume_finalizes_airtable_without_launching_drakkar(tmp_path):
    work_dir = tmp_path / "ST001"
    final_dir = work_dir / "cataloging" / "final"
    final_dir.mkdir(parents=True)
    (work_dir / "ST001_preprocessing.tsv").write_text(
        "sample\treads_pre_fastp\nSA000022\t1000\n",
        encoding="utf-8",
    )
    (work_dir / "ST001_cataloging.tsv").write_text(
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
    (work_dir / "profiling_genomes.tsv").write_text(
        "genome\tcoverage\nSA000022_bin_339957\t10.5\n",
        encoding="utf-8",
    )
    annotation_dir = work_dir / "annotating"
    annotation_dir.mkdir()
    (annotation_dir / "gene_annotations.tsv.xz").write_bytes(b"")
    (annotation_dir / "genome_taxonomy.tsv").write_text("", encoding="utf-8")

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
        patch("shutil.which", return_value=None),
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
            call("Studies", ["recStudy"], "Done"),
        ]
    )
    assert client.upload_study_file.call_count == 3
    client.update_sample_preprocessing_stats.assert_called_once()
    client.update_sample_cataloging_stats.assert_called_once()
    client.create_genome_records_with_response.assert_called_once()
    client.upload_genome_file.assert_called_once()


def test_cmd_process_resume_launches_annotation_when_taxonomy_missing(tmp_path):
    work_dir = tmp_path / "ST001"
    work_dir.mkdir()
    (work_dir / "profiling_genomes.tsv").write_text(
        "sample\tmapping_percentage\nSA000022\t10.5\n",
        encoding="utf-8",
    )
    annotation_dir = work_dir / "annotating"
    annotation_dir.mkdir()
    (annotation_dir / "gene_annotations.tsv.xz").write_bytes(b"")

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
        "fields": {"code": "ST001", "study_accession": "PRJEB001", "status": "resume"},
    }

    def fetch_by_status(_table, status):
        return [resume_study] if status == "resume" else []

    client.fetch_studies_by_status.side_effect = fetch_by_status
    client.update_sample_profiling_stats.return_value = 1

    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("shutil.which", return_value=None),
        patch("subprocess.run") as run,
    ):
        assert cli.cmd_process(args) == 0

    script_path = work_dir / "ST001.sh"
    assert script_path.exists(), "annotation script should have been written"
    script_text = script_path.read_text(encoding="utf-8")
    assert "drakkar annotating" in script_text
    assert "genome_taxonomy.tsv" in script_text
    client.set_study_status.assert_not_called()
    run.assert_not_called()


def test_cmd_process_resume_launches_profiling_when_cataloging_done(tmp_path):
    """Resume with cataloging done but no profiling_genomes.tsv → generate profiling script."""
    work_dir = tmp_path / "ST001"
    final_dir = work_dir / "cataloging" / "final"
    final_dir.mkdir(parents=True)
    (work_dir / "ST001_preprocessing.tsv").write_text(
        "sample\treads_pre_fastp\nSA000022\t1000\n", encoding="utf-8"
    )
    (work_dir / "ST001_cataloging.tsv").write_text(
        "assembly\tassembly_N50\nSA000022\t12345\n", encoding="utf-8"
    )
    (final_dir / "all_bin_metadata.csv").write_text(
        "genome,completeness,contamination,score,size,N50,contig_count\n"
        "SA000022_bin_1.fa,99.0,0.5,98.5,2500000,80000,45\n",
        encoding="utf-8",
    )
    bin_path = final_dir / "SA000022" / "SA000022_bin_1.fa"
    bin_path.parent.mkdir()
    bin_path.write_text(">contig1\nACGT\n", encoding="utf-8")
    (final_dir / "all_bin_paths.txt").write_text(
        "cataloging/final/SA000022/SA000022_bin_1.fa\n", encoding="utf-8"
    )
    # profiling_genomes.tsv is intentionally absent

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
        "fields": {"code": "ST001", "study_accession": "PRJEB001", "status": "resume"},
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
        {"id": "recGenome", "fields": {name_fid: "SA000022_bin_1"}}
    ]

    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("shutil.which", return_value=None),
        patch("subprocess.run") as run,
    ):
        assert cli.cmd_process(args) == 0

    script_path = work_dir / "ST001.sh"
    assert script_path.exists(), "profiling script should have been written"
    script_text = script_path.read_text()
    assert "drakkar profiling" in script_text
    assert "all_bin_paths.txt" in script_text
    assert "preprocessing/final" in script_text
    run.assert_not_called()  # screen not available, script written but not launched


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
    preprocessing_tsv = work_dir / "ST001_preprocessing.tsv"
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
