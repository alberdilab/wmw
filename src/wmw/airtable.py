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
    def __init__(self, api_key: str, base_id: str) -> None:
        _require()
        self._api = Api(api_key, use_field_ids=False)
        self._base_id = base_id

    def _table(self, table_name: str):
        return self._api.table(self._base_id, table_name)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def fetch_all(self, table_name: str, formula: str | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {}
        if formula:
            kwargs["formula"] = formula
        return self._table(table_name).all(**kwargs)

    def fetch_by_field(
        self,
        table_name: str,
        field: str,
        value: str,
    ) -> dict[str, Any] | None:
        formula = f'{{{field}}} = "{value}"'
        records = self._table(table_name).all(formula=formula)
        return records[0] if records else None

    def create_records(
        self,
        table_name: str,
        fields_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not fields_list:
            return []
        return self._table(table_name).batch_create(fields_list)

    def update_records(
        self,
        table_name: str,
        updates: list[dict[str, Any]],
    ) -> None:
        """updates: list of {"id": recXXX, "fields": {...}}."""
        if not updates:
            return
        self._table(table_name).batch_update(updates)

    # ------------------------------------------------------------------
    # Studies table
    # ------------------------------------------------------------------

    def existing_study_accessions(self, studies_table: str) -> set[str]:
        """Return the set of study_accession values already in the Studies table."""
        records = self.fetch_all(studies_table)
        return {
            r["fields"].get("study_accession", "")
            for r in records
            if r["fields"].get("study_accession")
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
            self.create_records(studies_table, new)
        return len(new), len(studies) - len(new)

    # ------------------------------------------------------------------
    # Samples table
    # ------------------------------------------------------------------

    def existing_run_accessions(self, samples_table: str) -> set[str]:
        """Return the set of run_accession values already in the Samples table."""
        records = self.fetch_all(samples_table)
        return {
            r["fields"].get("run_accession", "")
            for r in records
            if r["fields"].get("run_accession")
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
            self.create_records(samples_table, new)
        return len(new), len(samples) - len(new)

    def fetch_samples_for_processing(
        self,
        samples_table: str,
        batch: str | None = None,
        status: str = "pending",
    ) -> list[dict[str, Any]]:
        """Return samples ready for Drakkar processing."""
        parts = [f'{{status}} = "{status}"']
        if batch:
            parts.append(f'{{batch}} = "{batch}"')
        formula = "AND(" + ", ".join(parts) + ")" if len(parts) > 1 else parts[0]
        return self.fetch_all(samples_table, formula=formula)

    def set_sample_status(
        self,
        samples_table: str,
        record_ids: list[str],
        status: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        updates = [
            {"id": rid, "fields": {"status": status, **(extra_fields or {})}}
            for rid in record_ids
        ]
        self.update_records(samples_table, updates)
