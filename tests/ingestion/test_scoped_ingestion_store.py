from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from retriever_lost_found.ingestion.store import SupabaseIngestionStore, SupabaseRequestError


class ScopedIngestionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SupabaseIngestionStore("https://example.supabase.co", "test-secret")

    def test_existing_identities_uses_source_and_exact_sequence(self) -> None:
        with patch.object(self.store, "_request", return_value=[
            {"atc_id": "A", "found_sequence": "1"},
            {"atc_id": "A", "found_sequence": "2"},
        ]) as request:
            found = self.store.existing_identities("police_api", {("A", "1")})
        self.assertEqual(found, {("A", "1")})
        query = dict(request.call_args.kwargs["query"])
        self.assertEqual(query["item_source_code"], "eq.police_api")
        self.assertEqual(query["atc_id"], 'in.("A")')

    def test_identity_lookup_is_bounded_and_empty_input_does_not_query(self) -> None:
        with patch.object(self.store, "_request", return_value=[]) as request:
            self.assertEqual(self.store.existing_identities("partner_api", set()), set())
            request.assert_not_called()
            self.store.existing_identities("partner_api", {(f"A{i}", "1") for i in range(101)})
        self.assertEqual(request.call_count, 2)
        sizes = []
        for call in request.call_args_list:
            query = dict(call.kwargs["query"])
            sizes.append(len(json.loads("[" + query["atc_id"][4:-1] + "]")))
            self.assertEqual(query["item_source_code"], "eq.partner_api")
        self.assertEqual(sizes, [100, 1])

    def test_upsert_preserves_db_match_and_marks_only_unresolved_rows_unmatched(self) -> None:
        # The first result represents a verified mapping applied by the DB trigger.
        saved = [{"id": 10, "location_match_status": "matched"},
                 {"id": 11, "location_match_status": "ambiguous"},
                 {"id": 12, "location_match_status": "unmatched"}]
        with patch.object(self.store, "_request", side_effect=[saved, [{"id": 11}]]) as request:
            unmatched = self.store.upsert_found_items([{}, {}, {}])
        self.assertEqual(unmatched, 2)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0], "POST")
        self.assertIn("return=representation", request.call_args_list[0].kwargs["prefer"])
        self.assertEqual(request.call_args.args[0], "PATCH")
        self.assertEqual(dict(request.call_args.kwargs["query"])["id"], "in.(11)")
        self.assertEqual(request.call_args.kwargs["payload"], {"location_match_status": "unmatched"})

    def test_matched_rows_and_empty_batch_need_no_followup_requests(self) -> None:
        with patch.object(self.store, "_request", return_value=[{"id": 10, "location_match_status": "matched"}]) as request:
            self.assertEqual(self.store.upsert_found_items([{}]), 0)
            request.assert_called_once()
            request.reset_mock()
            self.assertEqual(self.store.upsert_found_items([]), 0)
            request.assert_not_called()

    def test_incomplete_manual_review_update_is_a_failure(self) -> None:
        with patch.object(self.store, "_request", side_effect=[
            [{"id": 11, "location_match_status": "ambiguous"}], []
        ]):
            with self.assertRaises(SupabaseRequestError):
                self.store.upsert_found_items([{}])
