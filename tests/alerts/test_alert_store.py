from __future__ import annotations

import io
import json
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from unittest.mock import patch

from retriever_lost_found.alerts.store import AlertPlace, SupabaseAlertStore


class FakeResponse:
    def __init__(self, payload) -> None:
        self.body = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return self.body


URLOPEN = "retriever_lost_found.integrations.supabase_rest.urllib.request.urlopen"


class SupabaseAlertStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SupabaseAlertStore("https://example.supabase.co/", "server-secret")

    @patch(URLOPEN)
    def test_create_reservation_calls_the_server_only_rpc(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({"status": "created", "reservation_id": 3, "message_id": 4})

        result = self.store.create_reservation(
            email="user@example.com",
            query="에어팟",
            places=[AlertPlace("서울역", 37.55, 126.97), AlertPlace("시청", 37.5658, 126.9772)],
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
        # 설계 16장 — 장소는 화면 순서대로 한 번에 넘긴다.
        self.assertEqual(
            body["p_places"],
            [
                {"place_name": "서울역", "latitude": 37.55, "longitude": 126.97},
                {"place_name": "시청", "latitude": 37.5658, "longitude": 126.9772},
            ],
        )
        self.assertNotIn("p_place_name", body)
        self.assertEqual(result["reservation_id"], 3)

    @patch(URLOPEN)
    def test_get_reservation_reads_one_row_or_none(self, urlopen) -> None:
        urlopen.return_value = FakeResponse([{"id": 7, "status": "active", "places": []}])
        self.assertEqual(self.store.get_reservation(7)["status"], "active")
        self.assertIn("id=eq.7", urlopen.call_args.args[0].full_url)

        urlopen.return_value = FakeResponse([])
        self.assertIsNone(self.store.get_reservation(8))

    @patch(URLOPEN)
    def test_get_reservation_embeds_the_places_in_screen_order(self, urlopen) -> None:
        urlopen.return_value = FakeResponse(
            [{"id": 7, "places": [
                {"name": "시청", "position": 2, "latitude": 37.5663, "longitude": 126.9779},
                {"name": "서울역", "position": 1, "latitude": 37.5547, "longitude": 126.9707},
            ]}]
        )

        reservation = self.store.get_reservation(7)

        # 결과 링크 화면이 지도를 저장된 장소로 옮기도록 좌표도 함께 읽는다.
        self.assertEqual(reservation["places"], [
            {"name": "서울역", "latitude": 37.5547, "longitude": 126.9707},
            {"name": "시청", "latitude": 37.5663, "longitude": 126.9779},
        ])
        url = urllib.parse.unquote(urlopen.call_args.args[0].full_url)
        self.assertIn("places:alert_reservation_places(name:place_name,position,latitude,longitude)", url)
        self.assertNotIn("place_name,", url.split("places:")[0])

    @patch(URLOPEN)
    def test_record_match_sends_item_ids_with_scores(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({"items_queued": 2, "message_id": 9})

        result = self.store.record_match(7, "partner_api", "2026-11-06T06:00:00+00:00", [(1, 0.9), (2, 0.8)])

        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(body["p_items"], [{"found_item_id": 1, "score": 0.9}, {"found_item_id": 2, "score": 0.8}])
        self.assertEqual(result["items_queued"], 2)

    @patch(URLOPEN)
    def test_discard_calls_the_server_only_rpc_with_the_ip_hash(self, urlopen) -> None:
        urlopen.return_value = FakeResponse(None)

        self.store.discard_reservation(7, "hash-of-ip")

        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/rest/v1/rpc/discard_alert_reservation"))
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"p_reservation_id": 7, "p_ip_hash": "hash-of-ip"})

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

    @patch(URLOPEN)
    def test_message_items_are_scoped_to_the_reservation_and_skip_unmatched_items(self, urlopen) -> None:
        # PRD 4.3-E(2026-11-24) — 메일 번호는 이 예약의 결과 메일이어야 하고, 기관 끊긴 물품은 받지 않는다.
        urlopen.side_effect = [
            FakeResponse([{"id": 11}]),
            FakeResponse([{"found_item_id": 5, "score": 0.9}, {"found_item_id": 3, "score": None}]),
            FakeResponse([{"id": 5, "storage_location_id": 1, "item_name": "검정 에어팟"}]),
        ]

        rows = self.store.message_items(7, 11)

        self.assertEqual(rows, [{"id": 5, "storage_location_id": 1, "item_name": "검정 에어팟", "score": 0.9}])
        message_query, notes_query, items_query = (
            dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(call.args[0].full_url).query))
            for call in urlopen.call_args_list
        )
        self.assertEqual(message_query["reservation_id"], "eq.7")
        self.assertEqual(message_query["id"], "eq.11")
        self.assertEqual(message_query["kind"], "in.(match,expiry_notice)")
        self.assertEqual(notes_query["reservation_id"], "eq.7")
        self.assertEqual(notes_query["message_id"], "eq.11")
        self.assertEqual(items_query["id"], "in.(3,5)")
        self.assertEqual(items_query["storage_location_id"], "not.is.null")
        self.assertTrue(all(call.args[0].get_method() == "GET" for call in urlopen.call_args_list))

    @patch(URLOPEN)
    def test_message_items_of_another_reservation_return_none_without_reading_items(self, urlopen) -> None:
        urlopen.return_value = FakeResponse([])

        self.assertIsNone(self.store.message_items(7, 99))
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
