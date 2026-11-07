from __future__ import annotations

import io
import json
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

from retriever_lost_found.alerts.store import SupabaseAlertStore


class FakeResponse:
    def __init__(self, payload) -> None:
        self.body = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return self.body


URLOPEN = "retriever_lost_found.alerts.store.urllib.request.urlopen"


class SupabaseAlertStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SupabaseAlertStore("https://example.supabase.co/", "server-secret")

    @patch(URLOPEN)
    def test_create_reservation_calls_the_server_only_rpc(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({"status": "created", "reservation_id": 3, "message_id": 4})

        result = self.store.create_reservation(
            email="user@example.com",
            query="에어팟",
            place_name="서울역",
            latitude=37.55,
            longitude=126.97,
            radius_m=1000,
            ip_hash="hash",
            link_nonce="nonce",
            expires_at=datetime(2026, 11, 13, 2, tzinfo=timezone.utc),
        )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.supabase.co/rest/v1/rpc/create_alert_reservation")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer server-secret")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["p_expires_at"], "2026-11-13T02:00:00+00:00")
        self.assertEqual(body["p_radius_m"], 1000)
        self.assertEqual(result["reservation_id"], 3)

    @patch(URLOPEN)
    def test_get_reservation_reads_one_row_or_none(self, urlopen) -> None:
        urlopen.return_value = FakeResponse([{"id": 7, "status": "active"}])
        self.assertEqual(self.store.get_reservation(7)["status"], "active")
        self.assertIn("id=eq.7", urlopen.call_args.args[0].full_url)

        urlopen.return_value = FakeResponse([])
        self.assertIsNone(self.store.get_reservation(8))

    @patch(URLOPEN)
    def test_record_match_sends_item_ids_with_scores(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({"items_queued": 2, "message_id": 9})

        result = self.store.record_match(7, "partner_api", "2026-11-06T06:00:00+00:00", [(1, 0.9), (2, 0.8)])

        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(body["p_items"], [{"found_item_id": 1, "score": 0.9}, {"found_item_id": 2, "score": 0.8}])
        self.assertEqual(result["items_queued"], 2)

    @patch(URLOPEN)
    def test_empty_response_body_is_not_an_error(self, urlopen) -> None:
        urlopen.return_value = FakeResponse(None)

        self.assertIsNone(self.store.finish_message(5, "sent"))

    @patch(URLOPEN)
    def test_http_error_hides_the_secret_key(self, urlopen) -> None:
        urlopen.side_effect = urllib.error.HTTPError(
            "https://example.supabase.co",
            500,
            "error",
            {},
            io.BytesIO(b'{"message": "bad key server-secret"}'),
        )

        with self.assertRaises(RuntimeError) as caught:
            self.store.claim_messages(limit=1, daily_cap=450)

        self.assertNotIn("server-secret", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_requires_url_and_secret(self) -> None:
        with self.assertRaises(ValueError):
            SupabaseAlertStore("", "")


if __name__ == "__main__":
    unittest.main()
