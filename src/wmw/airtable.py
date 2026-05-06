"""Airtable client for wmw — studies and samples tables."""

from __future__ import annotations

import sys
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
        self._api_fid = (
            Api(api_key, use_field_ids=True)
            if (self._studies_fm or self._samples_fm)
            else None
        )

    def _tbl(self, table_name: str, field_map: dict[str, str]):
        """Return pyairtable Table, using field-ID API when a field map is present."""
        api = self._api_fid if field_map else self._api
        return api.table(self._base_id, table_name)

    @staticmethod
    def _enc(fields: dict[str, Any], fm: dict[str, str]) -> dict[str, Any]:
        """Translate python field names to Airtable field IDs in an outgoing payload."""
        if not fm:
            return fields
        return {fm.get(k, k): v for k, v in fields.items()}

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

    def existing_study_accessions(self, studies_table: str) -> set[str]:
        """Return the set of study_accession values already in the Studies table."""
        tbl = self._tbl(studies_table, self._studies_fm)
        records = tbl.all()
        key = self._fld("study_accession", self._studies_fm)
        return {
            r["fields"].get(key, "")
            for r in records
            if r["fields"].get(key)
        }

    def upsert_studies(
        self,
        studies_table: str,
        studies: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Insert new studies; skip accessions that already exist.

        Returns (inserted, skipped).
        """
        existing = self.existing_study_accessions(studies_table)
        new = [s for s in studies if s.get("study_accession") not in existing]
        if new:
            tbl = self._tbl(studies_table, self._studies_fm)
            tbl.batch_create([self._enc(s, self._studies_fm) for s in new])
        return len(new), len(studies) - len(new)

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
