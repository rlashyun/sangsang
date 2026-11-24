from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from alert_fakes import NOW, TEST_SETTINGS, FakeAlertStore, FakeEmailSender, FakeMonotonic
from retriever_lost_found.alerts.dispatch import run_send_pass
from retriever_lost_found.alerts.links import KOREA_TIMEZONE
from retriever_lost_found.alerts.sender import (
    DailyLimitExceeded,
    LoginFailed,
    PermanentSendError,
    TemporarySendError,
)
from retriever_lost_found.alerts.store import AlertPlace
from retriever_lost_found.alerts.templates import CONFIRM_SUBJECT, EXPIRY_SUBJECT, FINAL_NOTICE, MATCH_SUBJECT


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
            email=email, query="에어팟", places=[AlertPlace("서울역", 37.5547, 126.9707)],
            radius_m=1000, ip_hash=None, link_nonce="nonce-new", expires_at=self.store.now + timedelta(days=7),
        )

    def test_sends_the_waiting_alert_mail(self) -> None:
        self.reservation_with_alert("a@example.com")

        result = self.run_pass()

        self.assertEqual((result["status"], result["messages_sent"]), ("succeeded", 1))
        self.assertEqual([email.subject for email in self.sender.sent], [MATCH_SUBJECT])
        self.assertEqual(self.match_statuses(), ["sent"])

    def test_alert_mail_view_link_opens_the_items_of_that_mail(self) -> None:
        # PRD 4.3-E(2026-11-24) — 링크가 이 메일 번호를 담아, 화면이 이 메일이 알린 물품을 그대로 연다.
        _, message_id = self.reservation_with_alert("a@example.com")

        self.run_pass()

        (email,) = self.sender.sent
        self.assertIn(f"/alerts/results?token=", email.text)
        self.assertIn(f"&message={message_id}\n", email.text)

    def test_alert_mail_names_the_nearest_of_several_places(self) -> None:
        # 설계 16장 — 장소가 여러 곳이면 물품마다 보관기관에서 가장 가까운 장소를 붙인다.
        self.store.add_location(2, 37.5658 + 0.0045, 126.9772, name="시청 유실물센터")
        reservation_id = self.store.add_active_reservation(
            email="a@example.com", radius_m=1000,
            places=[("서울역", 37.5547, 126.9707), ("시청", 37.5658, 126.9772)],
        )
        self.store.add_found_item(900, storage_location_id=2)
        self.store.add_found_item(901, storage_location_id=1)
        self.store.queue_match(reservation_id, [900, 901])

        self.run_pass()

        (email,) = self.sender.sent
        self.assertIn("장소: 서울역 · 시청 반경 1km", email.text)
        self.assertIn("시청 근처 · 11월 6일 등록 · 보관: 시청 유실물센터", email.text)
        self.assertIn("서울역 근처 · 11월 6일 등록 · 보관: 서울역 유실물센터", email.text)

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
        self.store.reservations[reservation_id].update(status="cancelled", email=None)

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

    def test_no_expiry_notice_the_day_before_the_last_day(self) -> None:
        # D+6 11시대 실행은 만료(D+8 0시)까지 약 37시간 — 종료 안내는 마지막 날에만 간다.
        self.store.add_active_reservation(email="a@example.com", expires_at=self.store.now + timedelta(hours=37))

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

    def test_last_day_schedule_d6_nothing_d7_one_final_mail_d8_cleanup(self) -> None:
        # PRD 4.3-E — 11-06 15:00 KST 등록 → 만료 11-14 00:00 KST. 발송 실행은 매일 KST 11시대.
        reservation_id = self.store.add_active_reservation(email="last@example.com")
        self.store.now = NOW + timedelta(days=6, hours=-3, minutes=30)  # 11-12(D+6) 11:30 KST

        self.assertEqual(self.run_pass()["expiry_notices_queued"], 0)
        self.store.advance(days=1)  # 11-13(D+7) 11:30 KST
        self.assertEqual(self.run_pass()["expiry_notices_queued"], 1)
        self.assertEqual([email.subject for email in self.sender.sent], [EXPIRY_SUBJECT])
        self.assertIn(FINAL_NOTICE, self.sender.sent[0].text)
        self.assertIn("/alerts/extend", self.sender.sent[0].text)  # 그날 자정 전까지 연장할 수 있다
        self.assertEqual(self.store.reservations[reservation_id]["status"], "active")

        self.store.advance(days=1)  # 11-14(D+8) 11:30 KST — 종료 다음 날 이메일을 지운다
        result = self.run_pass()
        self.assertEqual((result["reservations_expired"], result["messages_sent"]), (1, 0))
        self.assertIsNone(self.store.reservations[reservation_id]["email"])

    def test_last_day_results_and_final_notice_go_out_as_one_mail(self) -> None:
        # PRD 4.3-E 발송 단위(2026-11-15 결정) — 마지막 날 결과는 종료 안내 한 통에 합친다.
        self.store.now = NOW + timedelta(days=7, hours=-3, minutes=30)  # 11-13(D+7) 11:30 KST
        reservation_id, message_id = self.reservation_with_alert(
            "last@example.com", expires_at=datetime(2026, 11, 14, 0, 0, tzinfo=KOREA_TIMEZONE)
        )

        result = self.run_pass()

        self.assertEqual((result["expiry_notices_queued"], result["messages_sent"]), (1, 1))
        (email,) = self.sender.sent
        self.assertEqual(email.subject, EXPIRY_SUBJECT)
        self.assertIn(FINAL_NOTICE, email.text)
        self.assertIn("에어팟", email.text)
        self.assertEqual(self.store.messages[message_id]["kind"], "expiry_notice")
        self.assertEqual(self.store.messages[message_id]["status"], "sent")
        self.assertEqual(self.match_statuses(), [])
        self.assertEqual({note["message_id"] for note in self.store.notified.values()}, {message_id})
        self.assertEqual(self.store.reservations[reservation_id]["status"], "active")

    def test_expiry_day_morning_matches_go_out_before_cleanup(self) -> None:
        # 자정에 만료된 예약에 전날 다 못 보낸 알림 메일이 남았다 — 보낸 뒤 정리한다(설계 5.3).
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

    def test_db_error_while_reading_a_mail_puts_the_claimed_mails_back(self) -> None:
        # 리뷰 2026-11-15 — 발송 중 DB가 잠깐 끊겨도 집어 온 메일이 sending에 묶여 하루 늦게 가지 않는다.
        self.reservation_with_alert("a@example.com")
        self.reservation_with_alert("b@example.com")
        self.store.payload_failures_left = 1

        result = self.run_pass()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.sender.sent, [])
        self.assertEqual(self.match_statuses(), ["pending", "pending"])
        # 같은 날 다시 돌리면(DB가 돌아온 뒤) 둘 다 나간다.
        self.assertEqual(self.run_pass()["messages_sent"], 2)

    def test_db_error_after_a_send_puts_only_the_unsent_mails_back(self) -> None:
        self.reservation_with_alert("a@example.com")
        self.reservation_with_alert("b@example.com")
        self.store.finish_failures_left = 1  # 첫 메일을 보낸 뒤 "보냄" 기록이 실패한다

        result = self.run_pass()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.sender.sent), 1)
        # 보낸 메일은 다시 보내지 않게 sending에 둔다(10분 뒤 되살리기는 감수, 설계 6.3). 안 보낸 메일만 되돌린다.
        self.assertEqual(self.match_statuses(), ["pending", "sending"])

    def test_smtp_connection_failure_stops_the_pass_after_one_try(self) -> None:
        # 연결 시도마다 최대 20초를 기다리므로, 메일마다 다시 연결하면 300초 제한을 넘길 수 있다.
        self.reservation_with_alert("a@example.com")
        self.reservation_with_alert("b@example.com")
        self.sender.open_error = TemporarySendError("SMTP 연결 실패: TimeoutError")

        result = self.run_pass()

        self.assertEqual(self.sender.open_attempts, 1)
        self.assertEqual((result["status"], result["messages_deferred"]), ("partial", 2))
        self.assertEqual(self.match_statuses(), ["pending", "pending"])

    def test_confirmation_older_than_a_day_is_not_mailed(self) -> None:
        # 링크가 이미 만료된 확인 메일은 보내지 않는다(확인 링크는 24시간 유효).
        created = self.new_pending_reservation("late@example.com")
        self.store.advance(hours=24, minutes=1)

        result = self.run_pass()

        self.assertEqual(result["messages_sent"], 0)
        self.assertNotIn(created["message_id"], self.store.messages)
        self.assertEqual(self.store.reservations[created["reservation_id"]]["status"], "unconfirmed_expired")

    def test_confirmation_within_a_day_is_still_mailed(self) -> None:
        self.new_pending_reservation("fresh@example.com")
        self.store.advance(hours=23)

        self.assertEqual([email.subject for email in self.sender.sent], [])
        self.assertEqual(self.run_pass()["messages_sent"], 1)
        self.assertEqual([email.subject for email in self.sender.sent], [CONFIRM_SUBJECT])

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
