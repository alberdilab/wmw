"""Tests for wmw.drakkar — manifest generation and version detection."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
from wmw import config as cfg
from wmw import drakkar


def _sample_record(run_acc: str, r1: str = "", r2: str = "", ftp: str = "") -> dict:
    return {
        "id": f"rec_{run_acc}",
        "fields": {
            "run_accession": run_acc,
            "fastq_url_1": r1,
            "fastq_url_2": r2,
            "fastq_ftp": ftp,
        },
    }


def test_build_manifest_paired(tmp_path):
    samples = [
        _sample_record("ERR001", r1="ftp://host/ERR001_1.fastq.gz", r2="ftp://host/ERR001_2.fastq.gz"),
        _sample_record("ERR002", r1="ftp://host/ERR002_1.fastq.gz", r2="ftp://host/ERR002_2.fastq.gz"),
    ]
    out = tmp_path / "manifest.tsv"
    drakkar.build_manifest(samples, out)

    lines = out.read_text().splitlines()
    assert lines[0] == "sample\tR1\tR2"
    assert lines[1].startswith("ERR001\t")
    assert "ERR001_1.fastq.gz" in lines[1]
    assert lines[2].startswith("ERR002\t")


def test_build_manifest_skips_missing_r1(tmp_path):
    samples = [
        _sample_record("ERR003"),  # no URLs at all
        _sample_record("ERR004", r1="ftp://host/ERR004_1.fastq.gz"),
    ]
    out = tmp_path / "manifest.tsv"
    drakkar.build_manifest(samples, out)
    lines = out.read_text().splitlines()
    # Only ERR004 should appear
    assert len(lines) == 2  # header + 1 row
    assert "ERR004" in lines[1]


def test_build_manifest_falls_back_to_fastq_ftp(tmp_path):
    samples = [
        _sample_record(
            "ERR005",
            ftp="ftp.sra.ebi.ac.uk/vol1/ERR005_1.fastq.gz;ftp.sra.ebi.ac.uk/vol1/ERR005_2.fastq.gz",
        )
    ]
    out = tmp_path / "manifest.tsv"
    drakkar.build_manifest(samples, out)
    lines = out.read_text().splitlines()
    assert "ERR005" in lines[1]
    assert "ERR005_1.fastq.gz" in lines[1]


def test_build_manifest_creates_parent_dirs(tmp_path):
    samples = [_sample_record("ERR006", r1="ftp://host/ERR006_1.fastq.gz")]
    out = tmp_path / "nested" / "deep" / "manifest.tsv"
    drakkar.build_manifest(samples, out)
    assert out.exists()


# ---------------------------------------------------------------------------
# build_input_tsv
# ---------------------------------------------------------------------------

def _input_sample(
    code: str = "S001",
    r1: str = "ftp://host/S001_1.fastq.gz",
    r2: str = "ftp://host/S001_2.fastq.gz",
    ref_name: str = "ref_hg38",
    ref_path: str = "/refs/hg38.fa",
    assembly: str = "",
    coverage: str = "",
) -> dict:
    return {
        "id": f"rec_{code}",
        "fields": {
            "code": code,
            "status": "use",
            "fastq_url_1": r1,
            "fastq_url_2": r2,
            "reference_name": ref_name,
            "reference_path": ref_path,
            "assembly": assembly,
            "coverage": coverage,
        },
    }


def test_build_input_tsv_required_columns(tmp_path):
    samples = [_input_sample("S001"), _input_sample("S002")]
    out = tmp_path / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    lines = out.read_text().splitlines()
    assert lines[0] == "sample\trawreads1\trawreads2\treference_name\treference_path"
    assert lines[1].startswith("S001\t")
    assert "S001_1.fastq.gz" in lines[1]


def test_build_input_tsv_optional_columns_excluded_when_empty(tmp_path):
    samples = [_input_sample("S001", assembly="", coverage="")]
    out = tmp_path / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    header = out.read_text().splitlines()[0]
    assert "assembly" not in header
    assert "coverage" not in header


def test_build_input_tsv_optional_columns_included_when_nonempty(tmp_path):
    samples = [
        _input_sample("S001", assembly="/path/assembly.fa", coverage=""),
        _input_sample("S002", assembly="", coverage=""),
    ]
    out = tmp_path / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    header = out.read_text().splitlines()[0]
    assert "assembly" in header
    assert "coverage" not in header


def test_build_input_tsv_both_optional_columns(tmp_path):
    samples = [_input_sample("S001", assembly="/asm.fa", coverage="10")]
    out = tmp_path / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    lines = out.read_text().splitlines()
    assert "assembly" in lines[0]
    assert "coverage" in lines[0]
    assert "/asm.fa" in lines[1]
    assert "10" in lines[1]


def test_build_input_tsv_creates_parent_dirs(tmp_path):
    samples = [_input_sample()]
    out = tmp_path / "deep" / "nested" / "batch.tsv"
    drakkar.build_input_tsv(samples, out)
    assert out.exists()


# ---------------------------------------------------------------------------
# generate_preprocessing_script
# ---------------------------------------------------------------------------

def test_generate_preprocessing_script_contains_key_elements(tmp_path):
    script = drakkar.generate_preprocessing_script(
        code="PRJ001",
        tsv_path=tmp_path / "PRJ001.tsv",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
    )
    assert "#!/usr/bin/env bash" in script
    assert "PRJ001" in script
    assert "drakkar preprocessing" in script
    assert "wmw set-status --study PRJ001 --workflow preprocessing --status preprocessing" in script
    assert "wmw set-status --study PRJ001 --workflow preprocessing --status preprocessed" in script
    assert "wmw set-status --study PRJ001 --workflow preprocessing --status error" in script
    assert "wmw set-status --study PRJ001 --workflow preprocessing --status stopped" in script
    assert "wmw set-status --study PRJ001 --workflow cataloging --status cataloging" in script
    assert "drakkar cataloging" in script
    assert "mv -f" in script
    assert "preprocessing.tsv" in script
    assert "PRJ001_preprocessing.tsv" in script
    assert "cataloging.tsv" in script
    assert "PRJ001_cataloging.tsv" in script
    assert "wmw set-status --study PRJ001 --workflow cataloging --status cataloged" in script
    assert "wmw set-status --study PRJ001 --workflow cataloging --status stopped" in script
    assert "trap _on_exit_preprocessing EXIT" in script
    assert "trap _on_exit_cataloging EXIT" in script
    assert f"--output-dir {tmp_path.parent}" in script


def test_generate_preprocessing_script_slurm_flag(tmp_path):
    script = drakkar.generate_preprocessing_script(
        code="PRJ002",
        tsv_path=tmp_path / "PRJ002.tsv",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
        slurm=True,
    )
    assert "-p slurm" in script


def test_generate_preprocessing_script_no_conda(tmp_path):
    script = drakkar.generate_preprocessing_script(
        code="PRJ003",
        tsv_path=tmp_path / "PRJ003.tsv",
        work_dir=tmp_path,
        conda_env="",
    )
    assert "conda run" not in script
    assert "drakkar preprocessing" in script


def test_generate_preprocessing_script_wmw_conda_env(tmp_path):
    script = drakkar.generate_preprocessing_script(
        code="PRJ004",
        tsv_path=tmp_path / "PRJ004.tsv",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
        wmw_conda_env="/envs/wmw",
    )
    assert "conda run -p /envs/wmw wmw set-status" in script
    assert "conda run -p /envs/drakkar drakkar preprocessing" in script


# generate_cataloging_script
# ---------------------------------------------------------------------------

def test_generate_cataloging_script_contains_key_elements(tmp_path):
    script = drakkar.generate_cataloging_script(
        code="PRJ001",
        tsv_path=tmp_path / "PRJ001.tsv",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
    )
    assert "#!/usr/bin/env bash" in script
    assert "cataloging only" in script
    assert "drakkar cataloging" in script
    assert "cataloging.tsv" in script
    assert "PRJ001_cataloging.tsv" in script
    assert "wmw set-status --study PRJ001 --workflow cataloging --status cataloging" in script
    assert "wmw set-status --study PRJ001 --workflow cataloging --status cataloged" in script
    assert "wmw set-status --study PRJ001 --workflow cataloging --status error" in script
    assert "wmw set-status --study PRJ001 --workflow cataloging --status stopped" in script
    assert "trap _on_exit EXIT" in script
    assert "drakkar preprocessing" not in script
    assert f"--output-dir {tmp_path.parent}" in script


def test_generate_cataloging_script_slurm_flag(tmp_path):
    script = drakkar.generate_cataloging_script(
        code="PRJ002",
        tsv_path=tmp_path / "PRJ002.tsv",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
        slurm=True,
    )
    assert "-p slurm" in script


def test_generate_cataloging_script_no_conda(tmp_path):
    script = drakkar.generate_cataloging_script(
        code="PRJ003",
        tsv_path=tmp_path / "PRJ003.tsv",
        work_dir=tmp_path,
        conda_env="",
    )
    assert "conda run" not in script
    assert "drakkar cataloging" in script


def test_generate_cataloging_script_wmw_conda_env(tmp_path):
    script = drakkar.generate_cataloging_script(
        code="PRJ004",
        tsv_path=tmp_path / "PRJ004.tsv",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
        wmw_conda_env="/envs/wmw",
    )
    assert "conda run -p /envs/wmw wmw set-status" in script
    assert "conda run -p /envs/drakkar drakkar cataloging" in script


def test_annotation_outputs_present_requires_gene_annotations_and_taxonomy(tmp_path):
    annotation_dir = tmp_path / "annotating"
    annotation_dir.mkdir()
    (annotation_dir / "gene_annotations.tsv.xz").write_bytes(b"")

    assert not drakkar.annotation_outputs_present(tmp_path)

    (annotation_dir / "genome_taxonomy.tsv").write_text("", encoding="utf-8")
    assert drakkar.annotation_outputs_present(tmp_path)


def test_generate_annotation_script_checks_required_outputs(tmp_path):
    script = drakkar.generate_annotation_script(
        code="PRJ001",
        work_dir=tmp_path,
        conda_env="/envs/drakkar",
    )

    assert "drakkar annotating" in script
    assert "gene_annotations.tsv.xz" in script
    assert "genome_taxonomy.tsv" in script
    assert "Missing required annotation outputs" in script
    assert "wmw set-status --study PRJ001 --workflow annotating --status completed" in script


def test_parse_genome_annotation_tsv_counts_annotation_types(tmp_path):
    path = tmp_path / "SA000022_bin_1_genes.tsv"
    path.write_text(
        "\t".join(
            [
                "gene",
                "start",
                "end",
                "strand",
                "kegg",
                "ec",
                "pfam",
                "cazy",
                "resistance_type",
                "resistance_target",
                "vf",
                "vf_type",
                "signalp",
                "defense",
                "defense_type",
                "antidefense",
                "antidefense_type",
            ]
        )
        + "\n"
        + "gene1\t1\t100\t+\tK00001\t\tPF00001\t\t\t\t\t\t\t\t\t\t\n"
        + "gene2\t2\t200\t-\t\t\t\t\t\t\t\t\t\t\t\t\t\n"
        + "gene3\t3\t300\t+\t\t1.1.1.1\t\tGH1\tDrug\tTarget\tVF1\tType\tSec\tDef\tType\tAnti\tType\n",
        encoding="utf-8",
    )

    stats = drakkar.parse_genome_annotation_tsv(path)

    assert stats[str(cfg.get("GENOMES_COL_NUMBER_GENES"))] == 3
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_ANNOTATED"))] == 2
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_KEGG"))] == 1
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_CAZY"))] == 1
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_EC"))] == 1
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_VF"))] == 1
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_AMR"))] == 1
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_PFAM"))] == 1
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_SIGNALP"))] == 1
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_DEFENCE"))] == 1
    assert stats[str(cfg.get("GENOMES_COL_NUMBER_ANTIDEFENCE"))] == 1


def test_parse_genome_taxonomy_tsv_extracts_classification_and_ani(tmp_path):
    path = tmp_path / "genome_taxonomy.tsv"
    path.write_text(
        "genome\tclassification\tclosest_genome_ani\tclosest_placement_ani\tclosest_placement_af\n"
        "SA000022_bin_1.fa\t"
        "d__Bacteria;p__Pseudomonadota;c__Gammaproteobacteria;"
        "o__Enterobacterales;f__Aeromonadaceae;g__Aeromonas;"
        "s__Aeromonas rivipollensis\t"
        "99.8123\t97.4567\t0.823456\n",
        encoding="utf-8",
    )

    taxonomy = drakkar.parse_genome_taxonomy_tsv(path)

    fields = taxonomy["SA000022_bin_1"]
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


def test_gzip_annotation_tsv_writes_tsv_gz(tmp_path):
    path = tmp_path / "SA000022_bin_1_genes.tsv"
    path.write_text("gene\tkegg\ngene1\tK00001\n", encoding="utf-8")

    gz_path = drakkar.gzip_annotation_tsv(path)

    assert gz_path == tmp_path / "SA000022_bin_1_genes.tsv.gz"
    with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
        assert fh.read() == "gene\tkegg\ngene1\tK00001\n"


# parse_cataloging_tsv
# ---------------------------------------------------------------------------

def test_parse_cataloging_tsv_extracts_assembly_stats_and_focal_mapping(tmp_path):
    path = tmp_path / "cataloging.tsv"
    path.write_text(
        "\t".join(
            [
                "assembly",
                "samples",
                "assembly_contigs",
                "assembly_total_length",
                "assembly_largest_contig",
                "assembly_gc_percent",
                "assembly_N50",
                "assembly_L50",
                "mapping_rate_percent",
                "sample_mapping_rates",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "SA000004",
                "SA000004",
                "6228",
                "47624249",
                "231296",
                "41.694",
                "21438",
                "472",
                "60.81750890370336",
                "SA000004:91.03;SA000005:47.65",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stats = drakkar.parse_cataloging_tsv(path)
    fields = stats["SA000004"]
    l50_fid = str(
        cfg.get("SAMPLES_COL_ASSEMBLY_L50")
        or cfg.get("SAMPLES_COL_ASSEMBLTY_L50")
    )

    assert fields[str(cfg.get("SAMPLES_COL_ASSEMBLY_CONTIGS"))] == 6228
    assert fields[str(cfg.get("SAMPLES_COL_ASSEMBLY_LENGTH"))] == 47624249
    assert fields[str(cfg.get("SAMPLES_COL_ASSEMBLY_LARGEST_CONTIG"))] == 231296
    assert fields[str(cfg.get("SAMPLES_COL_ASSEMBLY_N50"))] == 21438
    assert fields[l50_fid] == 472
    assert fields[str(cfg.get("SAMPLES_COL_ASSEMBLY_GC"))] == 41.69
    assert fields[str(cfg.get("SAMPLES_COL_ASSEMBLY_MAPPING_RATE_ALL"))] == 60.82
    assert fields[str(cfg.get("SAMPLES_COL_ASSEMBLY_MAPPING_RATE_FOCAL"))] == 91.03


def test_parse_cataloging_tsv_skips_na_values_and_missing_focal_mapping(tmp_path):
    path = tmp_path / "cataloging.tsv"
    path.write_text(
        "assembly\tassembly_N50\tassembly_L50\tsample_mapping_rates\n"
        "SA000004\tNA\t472\tSA000005:47.65\n",
        encoding="utf-8",
    )

    fields = drakkar.parse_cataloging_tsv(path)["SA000004"]

    assert str(cfg.get("SAMPLES_COL_ASSEMBLY_N50")) not in fields
    assert str(cfg.get("SAMPLES_COL_ASSEMBLY_MAPPING_RATE_FOCAL")) not in fields


# parse_bin_metadata_csv
# ---------------------------------------------------------------------------

def test_parse_bin_metadata_csv_extracts_genome_fields(tmp_path):
    path = tmp_path / "all_bin_metadata.csv"
    path.write_text(
        "genome,completeness,contamination,score,size,N50,contig_count\n"
        "SA000022_bin_339957.fa,99.984,0.054,99.88,2585871,91721,50\n",
        encoding="utf-8",
    )

    genomes = drakkar.parse_bin_metadata_csv(path)

    assert len(genomes) == 1
    assert genomes[0]["sample_code"] == "SA000022"
    fields = genomes[0]["fields"]
    assert fields[str(cfg.get("GENOMES_COL_NAME"))] == "SA000022_bin_339957"
    assert fields[str(cfg.get("GENOMES_COL_COMPLETENESS"))] == 99.98
    assert fields[str(cfg.get("GENOMES_COL_CONTAMINATION"))] == 0.05
    assert fields[str(cfg.get("GENOMES_COL_LENGTH"))] == 2585871
    assert fields[str(cfg.get("GENOMES_COL_N50"))] == 91721
    assert fields[str(cfg.get("GENOMES_COL_CONTIGS"))] == 50
    assert "score" not in fields


def test_parse_bin_metadata_csv_includes_genome_name(tmp_path):
    path = tmp_path / "all_bin_metadata.csv"
    path.write_text(
        "genome,completeness,contamination,score,size,N50,contig_count\n"
        "SA000022_bin_339957.fa,99.984,0.054,99.88,2585871,91721,50\n",
        encoding="utf-8",
    )

    genomes = drakkar.parse_bin_metadata_csv(path)

    assert genomes[0]["genome_name"] == "SA000022_bin_339957"


def test_parse_bin_paths_txt_resolves_study_relative_paths(tmp_path):
    study_dir = tmp_path / "ST001"
    final_dir = study_dir / "cataloging" / "final"
    bin_path = final_dir / "SA000022" / "SA000022_bin_339957.fa"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text(">contig1\nACGT\n", encoding="utf-8")
    paths_file = final_dir / "all_bin_paths.txt"
    paths_file.write_text(
        "cataloging/final/SA000022/SA000022_bin_339957.fa\n",
        encoding="utf-8",
    )

    paths = drakkar.parse_bin_paths_txt(paths_file)

    assert paths == {"SA000022_bin_339957": bin_path}


def test_gzip_fasta_writes_fa_gz(tmp_path):
    fasta_path = tmp_path / "SA000022_bin_339957.fa"
    fasta_path.write_text(">contig1\nACGT\n", encoding="utf-8")

    gz_path = drakkar.gzip_fasta(fasta_path)

    assert gz_path.name == "SA000022_bin_339957.fa.gz"
    with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
        assert fh.read() == ">contig1\nACGT\n"
