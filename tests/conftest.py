"""Shared pytest fixtures for wmw tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_real_erda_transfers():
    """Keep the suite off the network.

    Finalising cataloging or AMR outputs ends in an ERDA transfer, and the
    shipped config points at a real host. Without this, any test that reaches a
    finaliser opens an SFTP connection to io.erda.dk and blocks for the 300 s
    connect timeout when the host is unreachable. Reporting paramiko as absent
    makes every transfer a no-op that warns and returns False; the tests that
    exercise the transfer patch this back to True themselves.
    """
    with patch("wmw.transfer.paramiko_available", return_value=False):
        yield


@pytest.fixture()
def ena_run_record():
    return {
        "study_accession": "PRJEB12345",
        "secondary_study_accession": "ERP012345",
        "sample_accession": "SAMEA1234567",
        "experiment_accession": "ERX1234567",
        "run_accession": "ERR1234567",
        "scientific_name": "gut metagenome",
        "tax_id": "749906",
        "instrument_platform": "ILLUMINA",
        "instrument_model": "Illumina NovaSeq 6000",
        "library_strategy": "WGS",
        "library_source": "METAGENOMIC",
        "library_layout": "PAIRED",
        "base_count": "15000000000",
        "read_count": "100000000",
        "fastq_ftp": "ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/ERR1234567_1.fastq.gz;ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/ERR1234567_2.fastq.gz",
        "fastq_md5": "abc123;def456",
        "collection_date": "2023-06-15",
        "first_public": "2024-01-10",
        "geo_loc_name": "Denmark",
        "host": "Vulpes vulpes",
        "host_tax_id": "9627",
        "host_scientific_name": "Vulpes vulpes",
        "country": "Denmark",
        "center_name": "SUND-KU",
        "study_title": "Gut metagenomes of red foxes",
        "host_sex": "female",
        "lat": "55.6761",
        "lon": "12.5683",
        "broad_scale_environmental_context": "temperate broadleaf forest biome",
        "environmental_medium": "feces",
    }


@pytest.fixture()
def sra_run_record():
    return {
        "source": "SRA",
        "run_accession": "SRR12345678",
        "study_accession": "PRJNA12345",
        "sample_accession": "SAMN12345678",
        "experiment_accession": "SRX12345678",
        "scientific_name": "gut metagenome",
        "tax_id": "749906",
        "instrument_platform": "Illumina NovaSeq 6000",
        "instrument_model": "Illumina NovaSeq 6000",
        "library_strategy": "WGS",
        "library_source": "METAGENOMIC",
        "library_layout": "PAIRED",
        "base_count": "12000000000",
        "read_count": "80000000",
        "fastq_ftp": "",
        "fastq_md5": "",
        "collection_date": "",
        "first_public": "2024-02-20",
        "study_title": "Wild animal gut metagenomes",
        "center_name": "NCBI",
    }
