"""Airtable client for wmw — studies and samples tables."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from pyairtable import Api
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _require() -> None:
    if not _AVAILABLE:
        print("Error: pyairtable is required. Run: pip install pyairtable", file=sys.stderr)
        sys.exit(1)


class AirtableClient:
    def __init__(
        self,
        api_key: str,
        base_id: str,
        studies_field_map: dict[str, str] | None = None,
        samples_field_map: dict[str, str] | None = None,
    ) -> None:
        _require()
        self._base_id = base_id
        self._studies_fm: dict[str, str] = studies_field_map or {}
        self._samples_fm: dict[str, str] = samples_field_map or {}
        self._api = Api(api_key, use_field_ids=False)
        self._api_fid = Api(api_key, use_field_ids=True)

    def _tbl(self, table_name: str, field_map: dict[str, str]):
        """Return pyairtable Table, using field-ID API when a field map is present."""
        api = self._api_fid if field_map else self._api
        return api.table(self._base_id, table_name)

    def _fid_tbl(self, table_id: str):
        """Return a field-ID-mode pyairtable Table for a table accessed by raw table ID."""
        return self._api_fid.table(self._base_id, table_id)

    @staticmethod
    def _enc(fields: dict[str, Any], fm: dict[str, str]) -> dict[str, Any]:
        """Translate python field names to Airtable field IDs in an outgoing payload."""
        clean = {k: v for k, v in fields.items() if v is not None and v != ""}
        if not fm:
            return clean
        return {fm.get(k, k): v for k, v in clean.items()}

    @staticmethod
    def _dec(record: dict[str, Any], fm: dict[str, str]) -> dict[str, Any]:
        """Translate Airtable field IDs back to python names in a received record."""
        if not fm:
            return record
        rev = {v: k for k, v in fm.items()}
        return {
            **record,
            "fields": {rev.get(k, k): v for k, v in record.get("fields", {}).items()},
        }

    @staticmethod
    def _fld(python_name: str, fm: dict[str, str]) -> str:
        """Return the field ID (or the python name as fallback) for formula use."""
        return fm.get(python_name, python_name)

    def check_access(self, table_names: list[str]) -> None:
        """Verify connectivity and read access to each table. Raises RuntimeError on failure."""
        for name in table_names:
            try:
                self._api.table(self._base_id, name).all(max_records=1)
            except Exception as exc:
                raise RuntimeError(
                    f"Airtable access check failed for table {name!r}: {exc}"
                ) from exc

    # ------------------------------------------------------------------
    # Generic helpers (public, use field names — no field map translation)
    # ------------------------------------------------------------------

    def fetch_all(self, table_name: str, formula: str | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {}
        if formula:
            kwargs["formula"] = formula
        return self._api.table(self._base_id, table_name).all(**kwargs)

    def fetch_by_field(
        self,
        table_name: str,
        field: str,
        value: str,
    ) -> dict[str, Any] | None:
        formula = f'{{{field}}} = "{value}"'
        records = self._api.table(self._base_id, table_name).all(formula=formula)
        return records[0] if records else None

    def create_records(
        self,
        table_name: str,
        fields_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not fields_list:
            return []
        return self._api.table(self._base_id, table_name).batch_create(fields_list)

    def update_records(
        self,
        table_name: str,
        updates: list[dict[str, Any]],
    ) -> None:
        """updates: list of {"id": recXXX, "fields": {...}}."""
        if not updates:
            return
        self._api.table(self._base_id, table_name).batch_update(updates)

    # ------------------------------------------------------------------
    # Studies table
    # ------------------------------------------------------------------

    def _existing_study_map(self, studies_table: str) -> dict[str, str]:
        """Return {study_accession: record_id} for all records in the Studies table."""
        tbl = self._tbl(studies_table, self._studies_fm)
        records = tbl.all()
        key = self._fld("study_accession", self._studies_fm)
        return {
            r["fields"][key]: r["id"]
            for r in records
            if r["fields"].get(key)
        }

    def existing_study_accessions(self, studies_table: str) -> set[str]:
        """Return the set of study_accession values already in the Studies table."""
        return set(self._existing_study_map(studies_table).keys())

    def fetch_study_record_id(self, studies_table: str, study_accession: str) -> str | None:
        """Return the Airtable record ID for *study_accession*, or None if not found."""
        key = self._fld("study_accession", self._studies_fm)
        formula = f'{{{key}}} = "{study_accession}"'
        tbl = self._tbl(studies_table, self._studies_fm)
        records = tbl.all(formula=formula, max_records=1)
        return records[0]["id"] if records else None

    def upsert_studies(
        self,
        studies_table: str,
        studies: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Insert new studies; update existing ones.

        Returns (inserted, updated).
        """
        existing_map = self._existing_study_map(studies_table)
        new = [s for s in studies if s.get("study_accession") not in existing_map]
        to_update = [s for s in studies if s.get("study_accession") in existing_map]
        tbl = self._tbl(studies_table, self._studies_fm)
        if new:
            tbl.batch_create([self._enc(s, self._studies_fm) for s in new])
        if to_update:
            updates = [
                {
                    "id": existing_map[s["study_accession"]],
                    "fields": self._enc(
                        {k: v for k, v in s.items() if k != "status"},
                        self._studies_fm,
                    ),
                }
                for s in to_update
            ]
            tbl.batch_update(updates)
        return len(new), len(to_update)

    # ------------------------------------------------------------------
    # Samples table
    # ------------------------------------------------------------------

    def existing_run_accessions(self, samples_table: str) -> set[str]:
        """Return the set of run_accession values already in the Samples table."""
        tbl = self._tbl(samples_table, self._samples_fm)
        records = tbl.all()
        key = self._fld("run_accession", self._samples_fm)
        return {
            r["fields"].get(key, "")
            for r in records
            if r["fields"].get(key)
        }

    def upsert_samples(
        self,
        samples_table: str,
        samples: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Insert new samples; skip run accessions that already exist.

        Returns (inserted, skipped).
        """
        existing = self.existing_run_accessions(samples_table)
        new = [s for s in samples if s.get("run_accession") not in existing]
        if new:
            tbl = self._tbl(samples_table, self._samples_fm)
            tbl.batch_create([self._enc(s, self._samples_fm) for s in new])
        return len(new), len(samples) - len(new)

    def fetch_study_by_code(
        self,
        studies_table: str,
        code: str,
    ) -> dict[str, Any] | None:
        """Return the decoded study record whose CODE field equals *code*, or None."""
        code_key = self._fld("code", self._studies_fm)
        formula = f'{{{code_key}}} = "{code}"'
        tbl = self._tbl(studies_table, self._studies_fm)
        records = tbl.all(formula=formula, max_records=1)
        return self._dec(records[0], self._studies_fm) if records else None

    def fetch_samples_for_study(
        self,
        samples_table: str,
        study_accession: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return decoded sample records for *study_accession*, optionally filtered by *status*."""
        sa_key = self._fld("study_accession", self._samples_fm)
        parts = [f'{{{sa_key}}} = "{study_accession}"']
        if status:
            status_key = self._fld("status", self._samples_fm)
            parts.append(f'{{{status_key}}} = "{status}"')
        formula = "AND(" + ", ".join(parts) + ")" if len(parts) > 1 else parts[0]
        tbl = self._tbl(samples_table, self._samples_fm)
        records = tbl.all(formula=formula)
        return [self._dec(r, self._samples_fm) for r in records]

    def fetch_samples_for_processing(
        self,
        samples_table: str,
        batch: str | None = None,
        status: str = "pending",
    ) -> list[dict[str, Any]]:
        """Return samples ready for Drakkar processing."""
        status_key = self._fld("status", self._samples_fm)
        batch_key = self._fld("batch", self._samples_fm)
        parts = [f'{{{status_key}}} = "{status}"']
        if batch:
            parts.append(f'{{{batch_key}}} = "{batch}"')
        formula = "AND(" + ", ".join(parts) + ")" if len(parts) > 1 else parts[0]
        tbl = self._tbl(samples_table, self._samples_fm)
        return tbl.all(formula=formula)

    def set_sample_status(
        self,
        samples_table: str,
        record_ids: list[str],
        status: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        status_key = self._fld("status", self._samples_fm)
        updates = [
            {"id": rid, "fields": {status_key: status, **(extra_fields or {})}}
            for rid in record_ids
        ]
        tbl = self._tbl(samples_table, self._samples_fm)
        tbl.batch_update(updates)

    # ------------------------------------------------------------------
    # Studies table — status helpers
    # ------------------------------------------------------------------

    def fetch_studies_by_status(
        self,
        studies_table: str,
        status: str = "approved",
    ) -> list[dict[str, Any]]:
        """Return all study records whose status field equals *status*."""
        status_key = self._fld("status", self._studies_fm)
        formula = f'{{{status_key}}} = "{status}"'
        tbl = self._tbl(studies_table, self._studies_fm)
        records = tbl.all(formula=formula)
        return [self._dec(r, self._studies_fm) for r in records]

    def _update_sample_stats_by_code(
        self,
        samples_table: str,
        stats_by_code: dict[str, dict[str, Any]],
    ) -> int:
        """Write stats to sample records, keyed by the sample code.

        stats_by_code: {code: {airtable_field_id: value}}
        Returns the number of records updated.
        """
        if not stats_by_code:
            return 0

        code_to_id = self.fetch_sample_record_ids_by_code(
            samples_table,
            stats_by_code.keys(),
        )
        tbl = self._tbl(samples_table, self._samples_fm)

        updates = [
            {"id": code_to_id[code], "fields": fields}
            for code, fields in stats_by_code.items()
            if code in code_to_id
        ]
        if updates:
            tbl.batch_update(updates)
        return len(updates)

    def update_sample_preprocessing_stats(
        self,
        samples_table: str,
        stats_by_run: dict[str, dict[str, Any]],
    ) -> int:
        """Write preprocessing stats to sample records, keyed by the sample code."""
        return self._update_sample_stats_by_code(samples_table, stats_by_run)

    def update_sample_cataloging_stats(
        self,
        samples_table: str,
        stats_by_assembly: dict[str, dict[str, Any]],
    ) -> int:
        """Write cataloging assembly stats to sample records, keyed by sample code."""
        return self._update_sample_stats_by_code(samples_table, stats_by_assembly)

    def update_sample_profiling_stats(
        self,
        samples_table: str,
        stats_by_sample: dict[str, dict[str, Any]],
    ) -> int:
        """Write profiling stats to sample records, keyed by sample code."""
        return self._update_sample_stats_by_code(samples_table, stats_by_sample)

    def fetch_sample_record_ids_by_code(
        self,
        samples_table: str,
        sample_codes: Iterable[Any],
    ) -> dict[str, str]:
        """Return {sample_code: Airtable record ID} for matching sample code values."""
        codes = list(
            dict.fromkeys(str(c).strip() for c in sample_codes if str(c).strip())
        )
        if not codes:
            return {}

        code_key = self._fld("code", self._samples_fm)
        record_id_key = self._fld("record_id", self._samples_fm)
        tbl = self._tbl(samples_table, self._samples_fm)
        code_to_id: dict[str, str] = {}

        # Batch formula lookups to stay within Airtable's formula length limit.
        for i in range(0, len(codes), 50):
            batch = codes[i : i + 50]
            if len(batch) == 1:
                formula = f'{{{code_key}}} = "{batch[0]}"'
            else:
                parts = [f'{{{code_key}}} = "{c}"' for c in batch]
                formula = "OR(" + ", ".join(parts) + ")"
            for record in tbl.all(formula=formula):
                code_val = record["fields"].get(code_key, "")
                if code_val:
                    code_to_id[code_val] = str(
                        record["fields"].get(record_id_key) or record["id"]
                    )
        return code_to_id

    def create_genome_records(
        self,
        genomes_table: str,
        fields_list: list[dict[str, Any]],
    ) -> int:
        """Create genome records using field-ID payloads."""
        return len(self.create_genome_records_with_response(genomes_table, fields_list))

    def create_genome_records_with_response(
        self,
        genomes_table: str,
        fields_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Create genome records using field-ID payloads and return created rows."""
        if not fields_list:
            return []
        tbl = self._fid_tbl(genomes_table)
        return tbl.batch_create(fields_list)

    def fetch_genome_records_by_name(
        self,
        genomes_table: str,
        genome_names: Iterable[Any],
        name_field_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Return existing Genomes-table records keyed by genome name."""
        names = list(
            dict.fromkeys(str(name).strip() for name in genome_names if str(name).strip())
        )
        if not names or not name_field_id:
            return {}

        tbl = self._fid_tbl(genomes_table)
        records_by_name: dict[str, dict[str, Any]] = {}
        for i in range(0, len(names), 50):
            batch = names[i : i + 50]
            if len(batch) == 1:
                formula = f'{{{name_field_id}}} = "{batch[0]}"'
            else:
                parts = [f'{{{name_field_id}}} = "{name}"' for name in batch]
                formula = "OR(" + ", ".join(parts) + ")"
            for record in tbl.all(formula=formula):
                genome_name = str(record.get("fields", {}).get(name_field_id) or "").strip()
                if genome_name:
                    records_by_name[genome_name] = record
        return records_by_name

    def update_genome_records(
        self,
        genomes_table: str,
        updates: list[dict[str, Any]],
    ) -> int:
        """Update existing genome records using field-ID payloads."""
        if not updates:
            return 0
        tbl = self._fid_tbl(genomes_table)
        tbl.batch_update(updates)
        return len(updates)

    def upload_genome_file(
        self,
        genomes_table: str,
        record_id: str,
        field_id: str,
        file_path: str | Path,
    ) -> dict[str, Any]:
        """Upload a local gzip archive to a Genomes-table attachment field."""
        tbl = self._fid_tbl(genomes_table)
        return tbl.upload_attachment(
            record_id,
            field_id,
            Path(file_path),
            content_type="application/gzip",
        )

    def clear_genome_file(
        self,
        genomes_table: str,
        record_id: str,
        field_id: str,
    ) -> None:
        """Clear an attachment field in the Genomes table."""
        tbl = self._fid_tbl(genomes_table)
        tbl.update(record_id, {field_id: []})

    def set_study_status(
        self,
        studies_table: str,
        record_ids: list[str],
        status: str,
    ) -> None:
        status_key = self._fld("status", self._studies_fm)
        updates = [{"id": rid, "fields": {status_key: status}} for rid in record_ids]
        tbl = self._tbl(studies_table, self._studies_fm)
        tbl.batch_update(updates)

    def upload_study_file(
        self,
        studies_table: str,
        record_id: str,
        field_name: str,
        file_path: str | Path,
    ) -> dict[str, Any]:
        """Upload a local file to a Studies-table attachment field."""
        field = self._fld(field_name, self._studies_fm)
        tbl = self._tbl(studies_table, self._studies_fm)
        return tbl.upload_attachment(
            record_id,
            field,
            Path(file_path),
            content_type="text/tab-separated-values",
        )

    def clear_study_file(
        self,
        studies_table: str,
        record_id: str,
        field_name: str,
    ) -> None:
        """Clear an attachment field in the Studies table."""
        field = self._fld(field_name, self._studies_fm)
        tbl = self._tbl(studies_table, self._studies_fm)
        tbl.update(record_id, {field: []})

    # ------------------------------------------------------------------
    # Species table — link studies via host taxid
    # ------------------------------------------------------------------

    def link_studies_to_species(
        self,
        studies_table: str,
        species_table_id: str,
        taxid_field_id: str,
        link_field_id: str,
        host_taxids_by_study: dict[str, set[str]],
    ) -> int:
        """Append study record IDs to the Species table's linked-record field.

        For each study accession in *host_taxids_by_study*, looks up every Species
        record whose *taxid_field_id* value matches one of the study's host taxids,
        then adds the study's Airtable record ID to *link_field_id* without
        removing any existing links.

        Returns the number of Species records that were updated.
        """
        if not host_taxids_by_study:
            return 0

        study_record_map = self._existing_study_map(studies_table)

        species_tbl = self._fid_tbl(species_table_id)
        species_records = species_tbl.all()

        species_by_taxid: dict[str, dict] = {}
        for rec in species_records:
            taxid = str(rec["fields"].get(taxid_field_id, "") or "").strip()
            if taxid:
                species_by_taxid[taxid] = {
                    "id": rec["id"],
                    "links": set(rec["fields"].get(link_field_id, []) or []),
                }

        pending: dict[str, set[str]] = {}
        for study_acc, host_taxids in host_taxids_by_study.items():
            study_rec_id = study_record_map.get(study_acc)
            if not study_rec_id:
                continue
            for taxid in host_taxids:
                species = species_by_taxid.get(str(taxid))
                if not species:
                    continue
                if study_rec_id not in species["links"]:
                    if species["id"] not in pending:
                        pending[species["id"]] = set(species["links"])
                    pending[species["id"]].add(study_rec_id)

        if not pending:
            return 0

        updates = [
            {"id": rec_id, "fields": {link_field_id: list(links)}}
            for rec_id, links in pending.items()
        ]
        species_tbl.batch_update(updates)
        return len(pending)
