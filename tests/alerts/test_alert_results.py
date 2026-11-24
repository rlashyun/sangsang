"""PRD 4.3-E — 결과 메일의 "리트리버에서 보기"가 그 메일에서 알린 물품을 그대로 보여 준다(2026-11-24 결정)."""

from __future__ import annotations

import unittest

from alert_fakes import NOW, TEST_SETTINGS, FakeAlertStore, FakeEmailSender
from retriever_lost_found.alerts.links import link_token
from retriever_lost_found.alerts.reservations import BAD_LINK, AlertService


class AlertResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.service = AlertService(
            store=self.store, sender=FakeEmailSender(), settings=TEST_SETTINGS, clock=lambda: self.store.now
        )
        self.store.add_location(1, 37.5547, 126.9707, name="서울역 유실물센터")
        self.reservation_id = self.store.add_active_reservation(query="블랙 에어팟", radius_m=1000)
        self.token = link_token(TEST_SETTINGS.secret, self.reservation_id, "nonce-1")

    def queue(self, reservation_id: int, items: dict[int, float]) -> int:
        """물품을 알린 메일 한 통을 만든다. items는 {물품 id: 점수}."""
        for item_id in items:
            if item_id not in self.store.found_items:
                self.store.add_found_item(item_id, storage_location_id=1, item_name=f"검정 에어팟 {item_id}")
        message_id = None
        for item_id, score in items.items():
            message_id = self.store.queue_match(reservation_id, [item_id], score=score)
        return message_id

    def test_returns_every_item_of_that_mail_beyond_the_five_shown_in_it(self) -> None:
        message_id = self.queue(self.reservation_id, {index: index / 10 for index in range(1, 8)})

        result = self.service.results(self.token, str(message_id))

        # 메일 본문은 5건까지만 싣는다. 링크 화면은 그 메일이 알린 7건을 모두 보여 준다.
        self.assertEqual([item["id"] for item in result["items"]], [7, 6, 5, 4, 3, 2, 1])
        self.assertEqual(result["query"], "블랙 에어팟")
        # 지도가 저장된 장소로 옮겨 가도록 좌표도 준다(링크 토큰을 가진 본인만 읽는다).
        self.assertEqual(result["places"], [{"name": "서울역", "latitude": 37.5547, "longitude": 126.9707}])
        self.assertEqual(result["radius_m"], 1000)
        self.assertEqual(result["status"], "active")

    def test_only_that_mail_is_shown_not_earlier_mails(self) -> None:
        first = self.queue(self.reservation_id, {1: 0.9})
        self.store.messages[first].update(status="sent")
        second = self.queue(self.reservation_id, {2: 0.8})

        self.assertEqual([item["id"] for item in self.service.results(self.token, str(second))["items"]], [2])
        self.assertEqual([item["id"] for item in self.service.results(self.token, str(first))["items"]], [1])

    def test_a_mail_of_another_reservation_is_refused(self) -> None:
        other = self.store.add_active_reservation(link_nonce="nonce-other")
        others_mail = self.queue(other, {9: 1.0})
        own_mail = self.queue(self.reservation_id, {1: 1.0})

        # 정상: 자기 메일은 열린다.
        self.assertEqual(len(self.service.results(self.token, str(own_mail))["items"]), 1)
        # 거절: 같은 토큰으로 남의 메일 번호를 넣어도 물품을 주지 않는다.
        with self.assertRaisesRegex(ValueError, BAD_LINK):
            self.service.results(self.token, str(others_mail))

    def test_malformed_message_numbers_and_tokens_are_refused(self) -> None:
        message_id = self.queue(self.reservation_id, {1: 1.0})
        self.assertEqual(len(self.service.results(self.token, str(message_id))["items"]), 1)
        for bad in ("", "0", "-1", "1.5", "abc", "1e3", None, "999"):
            with self.subTest(message=bad), self.assertRaisesRegex(ValueError, BAD_LINK):
                self.service.results(self.token, bad)
        with self.assertRaisesRegex(ValueError, BAD_LINK):
            self.service.results(link_token(TEST_SETTINGS.secret, self.reservation_id, "wrong"), str(message_id))

    def test_unmatched_items_are_not_shown(self) -> None:
        # AGENTS.md 절대 규칙 5 — 기관이 끊긴 물품은 어디에도 보이지 않는다. 연결된 물품은 보인다.
        message_id = self.queue(self.reservation_id, {1: 1.0, 2: 0.5})
        self.store.found_items[2]["storage_location_id"] = None

        self.assertEqual([item["id"] for item in self.service.results(self.token, str(message_id))["items"]], [1])

    def test_results_stay_readable_after_the_reservation_ends(self) -> None:
        message_id = self.queue(self.reservation_id, {1: 1.0})
        self.store.messages[message_id].update(status="sent")
        self.store.cancel_reservation(self.reservation_id)

        result = self.service.results(self.token, str(message_id))

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual([item["id"] for item in result["items"]], [1])

    def test_reading_results_changes_nothing(self) -> None:
        message_id = self.queue(self.reservation_id, {1: 1.0})
        before = ({key: dict(value) for key, value in self.store.messages.items()}, dict(self.store.notified))

        self.service.results(self.token, str(message_id))

        self.assertEqual(({key: dict(value) for key, value in self.store.messages.items()}, dict(self.store.notified)), before)


if __name__ == "__main__":
    unittest.main()
