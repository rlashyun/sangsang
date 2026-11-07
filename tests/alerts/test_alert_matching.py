from __future__ import annotations

import unittest
from datetime import timedelta

from alert_fakes import NOW, FakeAlertStore, FakeMonotonic
from retriever_lost_found.alerts.matching import find_new_similar_items, run_match_pass


def keyword_judge(query, candidates):
    """⑥ 결론 전 테스트용 판정기 — 물품명에 검색어가 들어 있으면 유사로 본다."""
    return [(item, 1.0) for item in candidates if query in (item.get("item_name") or "")]


class MatchPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.store.add_location(1, 37.5547 + 0.0089, 126.9707)  # 서울역 북쪽 약 990m — 1km 안
        self.store.add_location(2, 37.5547 + 0.0180, 126.9707)  # 약 2km — 1km 밖
        self.reservation_id = self.store.add_active_reservation(radius_m=1000)
        self.store.advance(hours=1)  # 이후 추가하는 물품은 예약 뒤에 들어온 것이다

    def run_pass(self, source: str = "partner_api", clock=None) -> dict:
        return run_match_pass(
            self.store,
            keyword_judge,
            item_source_code=source,
            deadline=100.0,
            clock=clock or FakeMonotonic(),
        )

    def queued_item_ids(self) -> list[int]:
        return sorted(item_id for (_, item_id) in self.store.notified)

    def test_new_item_inside_the_radius_is_queued_and_outside_is_not(self) -> None:
        self.store.add_found_item(10, storage_location_id=1)
        self.store.add_found_item(11, storage_location_id=2)

        result = self.run_pass()

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.queued_item_ids(), [10])

    def test_only_items_first_seen_after_the_reservation_count(self) -> None:
        self.store.add_found_item(10, storage_location_id=1, first_seen_at=NOW - timedelta(minutes=5))
        self.store.add_found_item(11, storage_location_id=1)

        self.run_pass()

        self.assertEqual(self.queued_item_ids(), [11])

    def test_unmatched_items_never_reach_the_mail(self) -> None:
        # 절대 규칙 5 — 기관 매칭에 실패한 물품(위치 NULL)은 지도처럼 알림에도 나오지 않는다.
        self.store.add_found_item(10, storage_location_id=None)
        self.store.add_found_item(11, storage_location_id=1)

        self.run_pass()

        self.assertEqual(self.queued_item_ids(), [11])

    def test_an_item_is_queued_only_once(self) -> None:
        self.store.add_found_item(10, storage_location_id=1)

        first = self.run_pass()
        second = self.run_pass()

        self.assertEqual((first["items_queued"], second["items_queued"]), (1, 0))
        self.assertEqual(len([m for m in self.store.messages.values() if m["kind"] == "match"]), 1)

    def test_partner_and_police_items_share_one_waiting_mail(self) -> None:
        self.store.add_found_item(10, storage_location_id=1, item_source_code="partner_api")
        self.store.add_found_item(11, storage_location_id=1, item_source_code="police_api")

        self.run_pass("partner_api")
        self.run_pass("police_api")

        matches = [m for m in self.store.messages.values() if m["kind"] == "match"]
        self.assertEqual(len(matches), 1)
        self.assertEqual({note["message_id"] for note in self.store.notified.values()}, {matches[0]["id"]})
        self.assertEqual(self.queued_item_ids(), [10, 11])

    def test_expired_reservation_gets_nothing(self) -> None:
        self.store.reservations[self.reservation_id]["expires_at"] = self.store.now - timedelta(minutes=1)
        self.store.add_found_item(10, storage_location_id=1)

        result = self.run_pass()

        self.assertEqual(result["reservations_checked"], 0)
        self.assertEqual(self.queued_item_ids(), [])

    def test_items_the_judge_rejects_are_not_looked_at_again(self) -> None:
        self.store.add_found_item(10, storage_location_id=1, item_name="지갑")

        self.run_pass()

        self.assertEqual(self.queued_item_ids(), [])
        self.assertEqual(self.store.candidate_items(self.reservation_id, "partner_api")["items"], [])

    def test_time_budget_stops_the_pass_and_the_next_run_catches_up(self) -> None:
        self.store.add_found_item(10, storage_location_id=1)

        late = self.run_pass(clock=FakeMonotonic(start=100.0))

        self.assertEqual(late["status"], "partial")
        self.assertEqual(self.queued_item_ids(), [])
        self.assertEqual(self.run_pass()["items_queued"], 1)

    def test_failures_are_recorded_without_the_secret(self) -> None:
        def broken_judge(query, candidates):
            raise RuntimeError(f"DB down {self.store.secret_key}")

        self.store.add_found_item(10, storage_location_id=1)

        result = run_match_pass(
            self.store, broken_judge, item_source_code="partner_api", deadline=100.0, clock=FakeMonotonic()
        )

        self.assertEqual(result["status"], "failed")
        (run,) = [run for run in self.store.runs.values() if run["kind"] == "match"]
        self.assertEqual(run["status"], "failed")
        self.assertNotIn(self.store.secret_key, run["error_message"])
        self.assertIn("[REDACTED]", run["error_message"])

    def test_judge_is_not_called_without_candidates(self) -> None:
        calls = []

        def judge(query, candidates):
            calls.append(candidates)
            return []

        result = find_new_similar_items(
            self.store, judge, {"id": self.reservation_id, "query": "에어팟"}, "partner_api"
        )

        self.assertEqual(calls, [])
        self.assertEqual(result.items, [])


if __name__ == "__main__":
    unittest.main()
