from __future__ import annotations

import json
import unittest
from datetime import timedelta

from alert_fakes import NOW, TEST_SETTINGS, FakeAlertStore, FakeEmailSender
from retriever_lost_found.alerts.errors import AlertLimitError
from retriever_lost_found.alerts.links import link_token
from retriever_lost_found.alerts.reservations import AlertService
from retriever_lost_found.alerts.sender import TemporarySendError


SEOUL_STATION = {"name": "서울역", "latitude": 37.5547, "longitude": 126.9707}
CITY_HALL = {"name": "시청", "latitude": 37.5658, "longitude": 126.9772}
EULJIRO = {"name": "을지로입구", "latitude": 37.5660, "longitude": 126.9826}


def request_payload(**overrides):
    payload = {
        "email": "User@Example.com",
        "query": "에어팟",
        "places": [SEOUL_STATION],
        "radius_m": 1500,
        "consent": True,
    }
    payload.update(overrides)
    return payload


class AlertServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.sender = FakeEmailSender()
        self.service = AlertService(
            store=self.store, sender=self.sender, settings=TEST_SETTINGS, clock=lambda: self.store.now
        )

    def create(self, ip: str = "203.0.113.7", **overrides):
        return self.service.create(request_payload(**overrides), ip)

    def only_reservation(self) -> dict:
        (reservation,) = self.store.reservations.values()
        return reservation

    def token_for(self, reservation: dict) -> str:
        return link_token(TEST_SETTINGS.secret, reservation["id"], reservation["link_nonce"])


class CreateReservationTests(AlertServiceTestCase):
    def test_valid_request_saves_a_pending_reservation_and_sends_one_confirmation(self) -> None:
        result = self.create()

        self.assertEqual(result["status"], "pending")
        reservation = self.only_reservation()
        self.assertEqual(reservation["status"], "pending")
        self.assertEqual(reservation["email"], "user@example.com")
        self.assertEqual([email.to for email in self.sender.sent], ["user@example.com"])
        (message,) = self.store.messages.values()
        self.assertEqual((message["kind"], message["status"]), ("confirm", "sent"))
        self.assertTrue(self.sender.sent[0].idempotency_key.startswith(f"alert-{message['id']}-"))

    def test_without_consent_nothing_is_saved_or_sent(self) -> None:
        with self.assertRaisesRegex(ValueError, "동의"):
            self.create(consent=False)

        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(self.sender.sent, [])

    def test_query_must_be_one_to_thirty_characters(self) -> None:
        self.create(query="가" * 30)

        for query in ("가" * 31, "   ", None):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.create(query=query)

    def test_radius_must_be_one_of_the_screen_options(self) -> None:
        # PRD 4.3-E — 알림 반경은 1·1.5·2km(2026-11-09 결정). 옛 3·5km는 받지 않는다.
        for index, radius in enumerate((1000, 1500, 2000)):
            with self.subTest(radius=radius):
                self.create(ip=f"203.0.113.{index + 10}", email=f"radius{index}@example.com", radius_m=radius)

        for radius in (3000, 5000, 500, True, "1500", 1500.5):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                self.create(radius_m=radius)

    def test_coordinates_must_be_inside_korea(self) -> None:
        self.create()

        with self.assertRaisesRegex(ValueError, "대한민국"):
            self.create(places=[SEOUL_STATION, {"name": "뉴욕", "latitude": 40.7, "longitude": -74.0}])
        self.assertEqual(len(self.store.reservations), 1)

    def test_up_to_three_places_are_saved_in_screen_order(self) -> None:
        # 설계 16장 — 검색 화면처럼 장소를 최대 3곳까지 고르고 반경 하나를 함께 쓴다.
        self.create(places=[SEOUL_STATION, CITY_HALL, EULJIRO])

        places = self.only_reservation()["places"]
        self.assertEqual([place["name"] for place in places], ["서울역", "시청", "을지로입구"])
        self.assertEqual((places[1]["latitude"], places[1]["longitude"]), (37.5658, 126.9772))

    def test_zero_or_more_than_three_places_are_refused(self) -> None:
        fourth = {"name": "명동", "latitude": 37.5609, "longitude": 126.9862}
        for places, message in (([], "1곳 이상"), ([SEOUL_STATION, CITY_HALL, EULJIRO, fourth], "3곳까지")):
            with self.subTest(count=len(places)), self.assertRaisesRegex(ValueError, message):
                self.create(places=places)
        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(self.sender.sent, [])

    def test_the_same_coordinates_count_once(self) -> None:
        # 설계 16장 — 좌표가 같은 장소는 하나로 합친다(처음 나온 이름을 쓴다).
        self.create(places=[SEOUL_STATION, dict(SEOUL_STATION, name="서울역 2번 출구"), CITY_HALL])

        self.assertEqual([place["name"] for place in self.only_reservation()["places"]], ["서울역", "시청"])

    def test_each_place_needs_a_name_and_numeric_coordinates(self) -> None:
        broken = (
            "서울역",
            {"latitude": 37.5547, "longitude": 126.9707},
            dict(SEOUL_STATION, name="   "),
            dict(SEOUL_STATION, name="가" * 101),
            dict(SEOUL_STATION, latitude="37.5547"),
            dict(SEOUL_STATION, longitude=None),
        )
        for place in broken:
            with self.subTest(place=place), self.assertRaises(ValueError):
                self.create(places=[place])
        for places in (None, "서울역", {"0": SEOUL_STATION}):
            with self.subTest(places=places), self.assertRaisesRegex(ValueError, "장소"):
                self.create(places=places)

    def test_single_place_fields_are_not_accepted_any_more(self) -> None:
        # 설계 16장 — API는 places 하나만 받는다. 옛 place_name·latitude·longitude만 보내면 장소가 없는 요청이다.
        payload = request_payload(place_name="서울역", latitude=37.5547, longitude=126.9707)
        del payload["places"]

        with self.assertRaisesRegex(ValueError, "1곳 이상"):
            self.service.create(payload, "203.0.113.7")

    def test_email_is_validated(self) -> None:
        for email in ("no-at-sign", "two@@example.com", "a b@example.com", "user@localhost", "<x>@example.com", 7):
            with self.subTest(email=email), self.assertRaises(ValueError):
                self.create(email=email)

    def test_one_email_can_hold_three_open_reservations(self) -> None:
        for _ in range(3):
            self.create(email="User@Example.com")

        # 대소문자·공백만 바꿔도 같은 이메일로 센다.
        with self.assertRaises(AlertLimitError):
            self.create(email=" user@EXAMPLE.com ")
        self.assertEqual(len(self.store.reservations), 3)

    def test_one_ip_can_create_five_reservations_in_24_hours(self) -> None:
        for index in range(5):
            self.create(email=f"user{index}@example.com")

        with self.assertRaises(AlertLimitError):
            self.create(email="user5@example.com")
        self.create(email="user6@example.com", ip="198.51.100.1")  # 다른 IP는 된다
        self.store.advance(hours=25)
        self.create(email="user7@example.com")  # 24시간이 지나면 다시 된다

    def test_cancelling_does_not_give_the_ip_quota_back(self) -> None:
        # 설계 14장 — 예약과 취소를 되풀이해 확인 메일로 하루 450통을 다 쓰지 못하게 한다.
        for index in range(5):
            self.create(email=f"user{index}@example.com")
        for reservation in list(self.store.reservations.values()):
            self.service.cancel(self.token_for(reservation))

        with self.assertRaises(AlertLimitError):
            self.create(email="user5@example.com")
        self.assertEqual(len(self.sender.sent), 5)

    def test_ip_hash_is_not_kept_on_the_reservation(self) -> None:
        self.create()

        self.assertNotIn("ip_hash", self.only_reservation())
        self.assertEqual(len(self.store.request_log), 1)
        self.assertNotIn("203.0.113.7", self.store.request_log[0][0])

    def test_failure_to_record_the_confirmation_sends_nothing_and_keeps_nothing(self) -> None:
        # 설계 14장 — 보냄 기록을 먼저 남기고 보낸다. 기록에 실패하면 메일을 보내지 않고 예약도 남기지 않아,
        # 나중에 발송 크론이 같은 확인 메일을 다시 보내는 일이 없다.
        self.store.fail_finish_message = True

        with self.assertRaisesRegex(RuntimeError, "알림 예약을 저장하지 못했습니다"):
            self.create()

        self.assertEqual(self.sender.sent, [])
        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(len(self.store.messages), 0)

    def test_send_failure_leaves_no_reservation(self) -> None:
        self.sender.failures["user@example.com"] = [TemporarySendError("SMTP 421")]

        with self.assertRaisesRegex(RuntimeError, "확인 메일을 보내지 못했습니다"):
            self.create()

        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(len(self.store.messages), 0)

    def test_unexpected_send_error_also_leaves_no_reservation(self) -> None:
        # SendError가 아닌 예외도 예약을 지운다 — 남으면 "보냄"으로 기록된 채 한도만 줄어든다.
        self.sender.failures["user@example.com"] = [ValueError("unexpected")]

        with self.assertRaisesRegex(RuntimeError, "확인 메일을 보내지 못했습니다"):
            self.create()

        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(len(self.store.messages), 0)
        self.create()  # 바로 다시 시도할 수 있다
        self.assertEqual(len(self.sender.sent), 1)

    def test_daily_send_limit_blocks_new_reservations(self) -> None:
        self.store.add_sent_messages(450, sent_at=self.store.now - timedelta(hours=1))

        with self.assertRaises(AlertLimitError):
            self.create()

        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(self.sender.sent, [])

    def test_link_token_is_mailed_but_never_stored(self) -> None:
        self.create()
        token = self.token_for(self.only_reservation())

        self.assertIn(token, self.sender.sent[0].text)
        self.assertNotIn(token, json.dumps(self.store.reservations, default=str))
        self.assertNotIn(token, json.dumps(self.store.messages, default=str))


class LinkActionTests(AlertServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.create()
        self.reservation = self.only_reservation()
        self.token = self.token_for(self.reservation)

    def test_confirm_within_24_hours_turns_the_reservation_on(self) -> None:
        self.store.advance(hours=23)

        self.assertEqual(self.service.confirm(self.token)["result"], "confirmed")
        self.assertEqual(self.reservation["status"], "active")
        self.assertEqual(self.service.confirm(self.token)["result"], "already_active")

    def test_confirm_after_24_hours_is_refused(self) -> None:
        self.store.advance(hours=24, minutes=1)

        self.assertEqual(self.service.confirm(self.token)["result"], "link_expired")
        self.assertEqual(self.reservation["status"], "pending")

    def test_extend_adds_seven_days_three_times_only(self) -> None:
        self.service.confirm(self.token)
        first_expiry = self.reservation["expires_at"]

        for left in (2, 1, 0):
            outcome = self.service.extend(self.token)
            self.assertEqual(outcome["result"], "extended")
            self.assertEqual(outcome["extensions_left"], left)

        self.assertEqual(self.reservation["expires_at"], first_expiry + timedelta(days=21))
        self.assertEqual(self.service.extend(self.token)["result"], "limit_reached")

    def test_extend_after_expiry_or_before_confirmation_is_refused(self) -> None:
        self.assertEqual(self.service.extend(self.token)["result"], "ended")

        self.service.confirm(self.token)
        self.store.now = self.reservation["expires_at"] + timedelta(minutes=1)
        self.assertEqual(self.service.extend(self.token)["result"], "ended")

    def test_cancel_deletes_the_email_and_the_waiting_mail(self) -> None:
        self.service.confirm(self.token)
        self.store.add_pending_match(self.reservation["id"])

        self.assertEqual(self.service.cancel(self.token)["result"], "cancelled")

        self.assertEqual(self.reservation["status"], "cancelled")
        self.assertIsNone(self.reservation["email"])
        self.assertEqual([m for m in self.store.messages.values() if m["status"] == "pending"], [])
        self.assertEqual(self.service.cancel(self.token)["result"], "already_ended")

    def test_wrong_token_is_a_bad_link(self) -> None:
        wrong_secret = link_token("other-secret", self.reservation["id"], self.reservation["link_nonce"])
        unknown = f"{self.reservation['id'] + 100}.{'0' * 32}"

        for token in (wrong_secret, unknown, "garbage", None):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "링크가 올바르지 않습니다"):
                self.service.confirm(token)

    def test_summary_reads_without_changing_anything(self) -> None:
        before = dict(self.reservation)

        summary = self.service.summary(self.token)

        self.assertEqual(summary["status"], "pending")
        self.assertEqual(summary["places"], [{"name": "서울역"}])
        self.assertNotIn("place_name", summary)
        self.assertEqual(summary["radius_m"], 1500)
        self.assertEqual(summary["extensions_left"], 3)
        self.assertEqual(summary["expires_at"], "2026-11-14T00:00:00+09:00")
        self.assertEqual(summary["last_day"], "2026-11-13")
        self.assertEqual(self.reservation, before)


if __name__ == "__main__":
    unittest.main()
