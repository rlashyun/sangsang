from __future__ import annotations

import unittest
from datetime import timedelta

from alert_fakes import NOW, TEST_SETTINGS, FakeAlertStore, FakeEmailSender, FakeMonotonic
from retriever_lost_found.alerts.dispatch import run_send_pass
from retriever_lost_found.alerts.sender import (
    DailyLimitExceeded,
    LoginFailed,
    PermanentSendError,
    TemporarySendError,
)
from retriever_lost_found.alerts.templates import CONFIRM_SUBJECT, EXPIRY_SUBJECT, MATCH_SUBJECT


class SendPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.store.add_location(1, 37.5547, 126.9707)
        self.sender = FakeEmailSender()
        self._next_item = 100

    def run_pass(self, clock=None) -> dict:
        return run_send_pass(
            self.store,
            self.sender,
            TEST_SETTINGS,
            deadline=100.0,
            clock=clock or FakeMonotonic(),
            now=lambda: self.store.now,
        )

    def reservation_with_alert(self, email: str, **reservation) -> tuple[int, int]:
        reservation_id = self.store.add_active_reservation(email=email, **reservation)
        self._next_item += 1
        self.store.add_found_item(self._next_item, storage_location_id=1)
        return reservation_id, self.store.queue_match(reservation_id, [self._next_item])

    def match_statuses(self) -> list[str]:
        return sorted(m["status"] for m in self.store.messages.values() if m["kind"] == "match")

    def new_pending_reservation(self, email: str) -> dict:
        return self.store.create_reservation(
            email=email, query="에어팟", place_name="서울역", latitude=37.5547, longitude=126.9707,
            radius_m=1000, ip_hash=None, link_nonce="nonce-new", expires_at=self.store.now + timedelta(days=7),
        )

    def test_sends_the_waiting_alert_mail(self) -> None:
        self.reservation_with_alert("a@example.com")

        result = self.run_pass()

        self.assertEqual((result["status"], result["messages_sent"]), ("succeeded", 1))
        self.assertEqual([email.subject for email in self.sender.sent], [MATCH_SUBJECT])
        self.assertEqual(self.match_statuses(), ["sent"])

    def test_daily_cap_counts_the_last_24_hours_only(self) -> None:
        self.store.add_sent_messages(449, sent_at=self.store.now - timedelta(hours=1))
        for index in range(3):
            self.reservation_with_alert(f"u{index}@example.com")

        self.assertEqual(self.run_pass()["messages_sent"], 1)
        self.assertEqual(self.match_statuses(), ["pending", "pending", "sent"])

        self.store.advance(hours=24)  # 449통이 24시간 밖으로 나간다
        self.assertEqual(self.run_pass()["messages_sent"], 2)

    def test_confirmation_then_alerts_then_expiry_notices(self) -> None:
        self.store.add_active_reservation(email="expiring@example.com", expires_at=self.store.now + timedelta(hours=24))
        self.reservation_with_alert("match@example.com")
        self.new_pending_reservation("new@example.com")

        self.run_pass()

        self.assertEqual([email.subject for email in self.sender.sent], [CONFIRM_SUBJECT, MATCH_SUBJECT, EXPIRY_SUBJECT])

    def test_permanent_failure_fails_one_mail_and_the_next_still_goes(self) -> None:
        self.reservation_with_alert("bad@example.com")
        self.reservation_with_alert("good@example.com")
        self.sender.failures["bad@example.com"] = [PermanentSendError("SMTP 550")]

        result = self.run_pass()

        self.assertEqual((result["messages_failed"], result["messages_sent"]), (1, 1))
        self.assertEqual([email.to for email in self.sender.sent], ["good@example.com"])

    def test_gmail_daily_limit_stops_everything_and_keeps_the_rest_waiting(self) -> None:
        for index in range(3):
            self.reservation_with_alert(f"u{index}@example.com")
        self.sender.failures["u1@example.com"] = [DailyLimitExceeded("SMTP 550 하루 발송 한도 초과")]

        result = self.run_pass()

        self.assertEqual((result["status"], result["messages_sent"]), ("partial", 1))
        self.assertEqual(self.match_statuses(), ["pending", "pending", "sent"])
        self.assertEqual([email.to for email in self.sender.sent], ["u0@example.com"])

    def test_temporary_failures_wait_for_the_next_run_and_fail_on_the_third_try(self) -> None:
        _, message_id = self.reservation_with_alert("slow@example.com")
        self.sender.failures["slow@example.com"] = [TemporarySendError("SMTP 421")] * 3

        for expected in ("pending", "pending", "failed"):
            self.run_pass()
            self.assertEqual(self.store.messages[message_id]["status"], expected)
        self.assertEqual(self.sender.sent, [])

    def test_mail_stuck_in_sending_is_retried_after_ten_minutes(self) -> None:
        _, message_id = self.reservation_with_alert("a@example.com")
        self.store.messages[message_id].update(status="sending", claimed_at=self.store.now - timedelta(minutes=5))

        self.assertEqual(self.run_pass()["messages_sent"], 0)
        self.store.advance(minutes=6)
        self.assertEqual(self.run_pass()["messages_sent"], 1)

    def test_mail_for_a_reservation_cancelled_meanwhile_is_dropped(self) -> None:
        reservation_id, message_id = self.reservation_with_alert("a@example.com")
        self.store.reservations[reservation_id].update(status="cancelled", email=None, ip_hash=None)

        self.assertEqual(self.run_pass()["messages_sent"], 0)
        self.assertNotIn(message_id, self.store.messages)
        self.assertEqual(self.sender.sent, [])

    def test_empty_alert_mail_is_dropped(self) -> None:
        reservation_id = self.store.add_active_reservation(email="a@example.com")
        message_id = self.store.add_pending_match(reservation_id)

        self.assertEqual(self.run_pass()["messages_sent"], 0)
        self.assertNotIn(message_id, self.store.messages)

    def test_expiry_notice_once_per_expiry_date(self) -> None:
        reservation_id = self.store.add_active_reservation(
            email="a@example.com", expires_at=self.store.now + timedelta(hours=24)
        )

        self.assertEqual(self.run_pass()["expiry_notices_queued"], 1)
        self.assertEqual(self.run_pass()["expiry_notices_queued"], 0)
        self.store.extend_reservation(reservation_id)  # 만료일 +7일
        self.store.advance(days=7)
        self.assertEqual(self.run_pass()["expiry_notices_queued"], 1)
        self.assertEqual([email.subject for email in self.sender.sent], [EXPIRY_SUBJECT, EXPIRY_SUBJECT])

    def test_no_expiry_notice_two_days_before(self) -> None:
        self.store.add_active_reservation(email="a@example.com", expires_at=self.store.now + timedelta(hours=48))

        self.assertEqual(self.run_pass()["expiry_notices_queued"], 0)

    def test_cleanup_removes_emails_but_keeps_rows(self) -> None:
        expired = self.store.add_active_reservation(
            email="old@example.com", expires_at=self.store.now - timedelta(minutes=30)
        )
        created = self.new_pending_reservation("unconfirmed@example.com")
        # 이 테스트는 정리만 본다 — 확인 메일은 이미 나간 것으로 둔다.
        self.store.messages[created["message_id"]].update(status="sent", sent_at=self.store.now)
        self.store.advance(hours=24, minutes=1)

        result = self.run_pass()

        self.assertEqual((result["reservations_expired"], result["reservations_unconfirmed_closed"]), (1, 1))
        self.assertEqual(self.store.reservations[expired]["status"], "expired")
        self.assertIsNone(self.store.reservations[expired]["email"])
        unconfirmed = self.store.reservations[created["reservation_id"]]
        self.assertEqual(unconfirmed["status"], "unconfirmed_expired")
        self.assertIsNone(unconfirmed["email"])

    def test_unconfirmed_reservation_younger_than_a_day_is_kept(self) -> None:
        created = self.new_pending_reservation("unconfirmed@example.com")
        self.store.messages[created["message_id"]].update(status="sent", sent_at=self.store.now)
        self.store.advance(hours=23)

        self.run_pass()

        self.assertEqual(self.store.reservations[created["reservation_id"]]["email"], "unconfirmed@example.com")

    def test_expiry_day_morning_matches_go_out_before_cleanup(self) -> None:
        # 11:00 만료, 발송 크론은 11:30에 돌았다 — 그날 아침 매칭분을 보낸 뒤 정리한다(설계 5.3).
        reservation_id, _ = self.reservation_with_alert(
            "last@example.com", expires_at=self.store.now - timedelta(minutes=30)
        )

        result = self.run_pass()

        self.assertEqual(result["messages_sent"], 1)
        self.assertEqual(self.store.reservations[reservation_id]["status"], "expired")
        self.assertNotIn("/alerts/extend", self.sender.sent[0].text)

    def test_time_budget_leaves_mail_waiting(self) -> None:
        self.reservation_with_alert("a@example.com")

        result = self.run_pass(clock=FakeMonotonic(start=100.0))

        self.assertEqual((result["status"], result["messages_sent"]), ("partial", 0))
        self.assertEqual(self.match_statuses(), ["pending"])

    def test_login_failure_is_recorded_without_the_password(self) -> None:
        self.reservation_with_alert("a@example.com")
        self.sender.open_error = LoginFailed(f"SMTP 로그인 실패 {TEST_SETTINGS.mail.password}")

        result = self.run_pass()

        self.assertEqual(result["status"], "failed")
        self.assertNotIn(TEST_SETTINGS.mail.password, result["error_message"])
        self.assertIn("[REDACTED]", result["error_message"])
        self.assertEqual(self.match_statuses(), ["pending"])
        (run,) = [run for run in self.store.runs.values() if run["kind"] == "send"]
        self.assertEqual(run["status"], "failed")


if __name__ == "__main__":
    unittest.main()
