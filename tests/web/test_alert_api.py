from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "alerts"))

from alert_fakes import NOW, TEST_SETTINGS, FakeAlertStore, FakeEmailSender  # noqa: E402
from retriever_lost_found.alerts.links import link_token  # noqa: E402
from retriever_lost_found.alerts.reservations import AlertService  # noqa: E402
from retriever_lost_found.alerts.sender import TemporarySendError  # noqa: E402
from retriever_lost_found.integrations.kakao import KakaoLocalClient  # noqa: E402
from retriever_lost_found.web.alerts import client_ip  # noqa: E402
from retriever_lost_found.web.app import create_app  # noqa: E402

PAYLOAD = {
    "email": "user@example.com",
    "query": "에어팟",
    "places": [{"name": "서울역", "latitude": 37.5547, "longitude": 126.9707}],
    "radius_m": 1500,
    "consent": True,
}


class AlertApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.sender = FakeEmailSender()
        service = AlertService(
            store=self.store, sender=self.sender, settings=TEST_SETTINGS, clock=lambda: self.store.now
        )
        self.client = TestClient(
            create_app(KakaoLocalClient("test-rest-key"), "test-js-key", alert_service=service)
        )

    def post(self, payload=None):
        return self.client.post("/api/alerts", json=payload or PAYLOAD)

    def test_create_returns_202_and_is_not_cached(self) -> None:
        response = self.post()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.json()["reservation_id"], next(iter(self.store.reservations)))
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_mail_link_paths_open_the_app_without_leaking_the_token(self) -> None:
        # 설계 5.4 — 메일 링크가 404가 아니라 React 앱(index.html)을 연다. 토큰이 든 주소가
        # 외부 요청의 Referer로 나가지 않고, 캐시되지 않는다.
        for path in ("/alerts/confirm", "/alerts/extend", "/alerts/cancel", "/alerts/results"):
            with self.subTest(path=path):
                response = self.client.get(f"{path}?token=7.{'0' * 32}")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                self.assertEqual(response.headers["cache-control"], "no-store")
        # 정한 네 경로 밖은 그대로 404다.
        self.assertEqual(self.client.get("/alerts/other").status_code, 404)

    def test_opening_a_link_page_changes_nothing(self) -> None:
        self.post()
        before = {rid: dict(row) for rid, row in self.store.reservations.items()}

        self.client.get("/alerts/confirm?token=1.0123456789abcdef0123456789abcdef")

        self.assertEqual(self.store.reservations, before)

    def test_validation_errors_are_korean_400(self) -> None:
        response = self.post({**PAYLOAD, "consent": False})

        self.assertEqual(response.status_code, 400)
        self.assertIn("동의", response.json()["error"])

    def test_broken_json_is_400(self) -> None:
        response = self.client.post(
            "/api/alerts", content=b"{not json", headers={"Content-Type": "application/json"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "요청 형식이 올바르지 않습니다.")

    def test_fourth_reservation_for_one_email_is_429(self) -> None:
        for _ in range(3):
            self.assertEqual(self.post().status_code, 202)

        response = self.post()

        self.assertEqual(response.status_code, 429)
        self.assertIn("3개", response.json()["error"])

    def test_mail_failure_is_502(self) -> None:
        self.sender.failures["user@example.com"] = [TemporarySendError("SMTP 421")]

        self.assertEqual(self.post().status_code, 502)

    def test_link_endpoints_work_with_the_mailed_token(self) -> None:
        self.post()
        (reservation,) = self.store.reservations.values()
        token = link_token(TEST_SETTINGS.secret, reservation["id"], reservation["link_nonce"])

        summary = self.client.get("/api/alerts/summary", params={"token": token})
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["status"], "pending")
        self.assertEqual(self.client.post("/api/alerts/confirm", json={"token": token}).json()["result"], "confirmed")
        self.assertEqual(self.client.post("/api/alerts/extend", json={"token": token}).json()["result"], "extended")
        self.assertEqual(self.client.post("/api/alerts/cancel", json={"token": token}).json()["result"], "cancelled")

    def test_bad_token_is_400(self) -> None:
        response = self.client.post("/api/alerts/confirm", json={"token": "garbage"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "링크가 올바르지 않습니다.")

    def queue_mail(self, *, description: str = "") -> tuple[str, int]:
        self.store.add_location(1, 37.5547, 126.9707)
        reservation_id = self.store.add_active_reservation(query="블랙 에어팟")
        self.store.add_found_item(1, storage_location_id=1, item_name="검정 에어팟", description=description)
        message_id = self.store.queue_match(reservation_id, [1], score=0.9)
        return link_token(TEST_SETTINGS.secret, reservation_id, "nonce-1"), message_id

    def test_results_return_the_mailed_items_in_the_chat_item_shape(self) -> None:
        # PRD 4.3-E(2026-11-24) — 메일 링크 화면이 같은 물품 카드로 그 메일의 물품을 보여 준다.
        token, message_id = self.queue_mail(description="주인 연락처 010-1234-5678")

        response = self.client.get("/api/alerts/results", params={"token": token, "message": str(message_id)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["query"], "블랙 에어팟")
        (item,) = body["items"]
        self.assertEqual((item["item_id"], item["institution_id"], item["item_name"]), (1, 1, "검정 에어팟"))
        # PRD 3.3 · Q5 — 브라우저로 나가는 물품은 가린 사본이다. D2 — 점수는 내보내지 않는다.
        self.assertNotIn("010-1234-5678", json.dumps(body, ensure_ascii=False))
        self.assertNotIn("score", item)

    def test_results_with_a_bad_link_are_400(self) -> None:
        token, message_id = self.queue_mail()

        self.assertEqual(
            self.client.get("/api/alerts/results", params={"token": token, "message": str(message_id)}).status_code, 200
        )
        for params in ({"token": token, "message": "999"}, {"token": token}, {"token": "garbage", "message": "1"}):
            with self.subTest(params=params):
                response = self.client.get("/api/alerts/results", params=params)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "링크가 올바르지 않습니다.")


class AlertApiWithoutSettingsTests(unittest.TestCase):
    def test_every_alert_endpoint_is_503_when_mail_is_not_configured(self) -> None:
        client = TestClient(create_app(KakaoLocalClient("test-rest-key"), "test-js-key"))

        self.assertEqual(client.post("/api/alerts", json=PAYLOAD).status_code, 503)
        self.assertEqual(client.get("/api/alerts/summary", params={"token": "1." + "0" * 32}).status_code, 503)
        self.assertEqual(
            client.get("/api/alerts/results", params={"token": "1." + "0" * 32, "message": "1"}).status_code, 503
        )
        for action in ("confirm", "extend", "cancel"):
            self.assertEqual(client.post(f"/api/alerts/{action}", json={"token": "x"}).status_code, 503)


class ClientIpTests(unittest.TestCase):
    @staticmethod
    def request(headers: dict[str, str]) -> Request:
        return Request(
            {
                "type": "http",
                "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
                "client": ("10.0.0.1", 1234),
            }
        )

    def test_on_vercel_uses_the_forwarded_for_header_vercel_overwrites(self) -> None:
        # Vercel은 들어온 X-Forwarded-For를 덮어써 위조를 막는다 — Vercel 위에서만 헤더를 믿는다.
        with patch.dict(os.environ, {"VERCEL": "1"}):
            self.assertEqual(
                client_ip(self.request({"x-forwarded-for": "203.0.113.7, 10.0.0.2", "x-real-ip": "198.51.100.1"})),
                "203.0.113.7",
            )
            self.assertEqual(client_ip(self.request({"x-real-ip": "198.51.100.1"})), "198.51.100.1")
            self.assertEqual(client_ip(self.request({})), "10.0.0.1")

    def test_off_vercel_forwarding_headers_are_ignored(self) -> None:
        # 헤더를 바꿔 가며 IP 제한을 피하지 못하게 한다.
        with patch.dict(os.environ, {"VERCEL": ""}):
            self.assertEqual(
                client_ip(self.request({"x-forwarded-for": "203.0.113.7", "x-real-ip": "198.51.100.1"})),
                "10.0.0.1",
            )


if __name__ == "__main__":
    unittest.main()
