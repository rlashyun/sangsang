from __future__ import annotations

import unittest
from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


class SchemaMigrationTests(unittest.TestCase):
    def test_core_schema_and_runtime_contract_are_reproducible(self) -> None:
        baseline = (
            MIGRATIONS / "20260826133434_create_lost_found_core_schema.sql"
        ).read_text(encoding="utf-8")
        hardening = (
            MIGRATIONS / "20260827231537_harden_runtime_database_contracts.sql"
        ).read_text(encoding="utf-8")

        for table in (
            "item_sources",
            "location_sources",
            "storage_locations",
            "storage_location_aliases",
            "item_categories",
            "category_mappings",
            "found_items",
            "ingestion_runs",
        ):
            self.assertIn(f"create table public.{table}", baseline.lower())

        self.assertIn("map_locations_with_item_counts", hardening)
        self.assertIn("ingestion_runs_one_running_per_source", hardening)
        self.assertIn("found_items_set_seen_at", hardening)
        self.assertIn("security invoker", hardening.lower())
        self.assertIn("grant execute", hardening.lower())


if __name__ == "__main__":
    unittest.main()
