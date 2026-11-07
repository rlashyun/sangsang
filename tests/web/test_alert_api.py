from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
    "latitude": 37.5547,
    "longitude": 126.9707,
    "radius_m": 3000,
    "place_name": "서울역",
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
        self.assertEqual(response.headers["cache-control"], "no-store")

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


class AlertApiWithoutSettingsTests(unittest.TestCase):
    def test_every_alert_endpoint_is_503_when_mail_is_not_configured(self) -> None:
        client = TestClient(create_app(KakaoLocalClient("test-rest-key"), "test-js-key"))

        self.assertEqual(client.post("/api/alerts", json=PAYLOAD).status_code, 503)
        self.assertEqual(client.get("/api/alerts/summary", params={"token": "1." + "0" * 32}).status_code, 503)
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

    def test_prefers_vercel_real_ip_then_forwarded_for_then_the_socket(self) -> None:
        self.assertEqual(
            client_ip(self.request({"x-real-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.1"})),
            "203.0.113.7",
        )
        self.assertEqual(client_ip(self.request({"x-forwarded-for": "198.51.100.1, 10.0.0.2"})), "198.51.100.1")
        self.assertEqual(client_ip(self.request({})), "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
