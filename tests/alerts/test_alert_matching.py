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
        # 11-06 15:00 KST 예약 → 11-07 09:00 KST(D+1). PRD 4.3-E — 비교는 등록 다음날부터 한다.
        # 이후 추가하는 물품은 예약 뒤에 들어온 것이다.
        self.store.advance(hours=18)

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

    def test_several_places_cover_every_radius_and_count_an_item_once(self) -> None:
        # 설계 16장 — 장소들 각각의 반경 안 기관의 합집합. 두 반경에 걸친 기관의 물품도 한 번만 알린다.
        store = FakeAlertStore(NOW)
        store.add_location(1, 37.5547 + 0.0089, 126.9707)  # 서울역 1km 안
        store.add_location(2, 37.5658 + 0.0045, 126.9772)  # 시청 북쪽 약 500m — 서울역 1km 밖
        store.add_location(3, 37.5603, 126.9740)  # 두 장소 사이 — 둘 다 1km 안
        store.add_location(4, 37.5547 - 0.0180, 126.9707)  # 서울역 남쪽 약 2km — 둘 다 밖
        reservation_id = store.add_active_reservation(
            radius_m=1000,
            places=[("서울역", 37.5547, 126.9707), ("시청", 37.5658, 126.9772)],
        )
        store.advance(hours=18)
        for item_id, location_id in ((10, 1), (11, 2), (12, 3), (13, 4)):
            store.add_found_item(item_id, storage_location_id=location_id)

        run_match_pass(store, keyword_judge, item_source_code="partner_api", deadline=100.0, clock=FakeMonotonic())

        self.assertEqual(sorted(item_id for (_, item_id) in store.notified), [10, 11, 12])
        self.assertEqual(len([m for m in store.messages.values() if m["reservation_id"] == reservation_id]), 1)

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

    def test_item_connected_to_a_storage_later_is_still_alerted(self) -> None:
        # 리뷰 2026-11-15 — 수집 때 기관 연결에 실패했다가 나중에 연결된 물품은 검색 화면에 보이기 시작하므로
        # 알림에도 나와야 한다(AGENTS.md 1장 "DB에 있는데 안 보이는" 실패 유형).
        self.store.add_found_item(10, storage_location_id=None)
        self.store.add_found_item(11, storage_location_id=1)
        self.run_pass()  # 커서가 11번을 지나간다
        self.assertEqual(self.queued_item_ids(), [11])

        self.store.advance(days=1)
        self.store.connect_location(10, 1)
        self.run_pass()

        self.assertEqual(self.queued_item_ids(), [10, 11])

    def test_item_seen_before_the_reservation_is_not_alerted_even_if_connected_later(self) -> None:
        # 위와 짝 — 예약 이후 새 물품만 알린다(설계 4.2). 예약 전에 들어온 물품은 나중에 연결돼도 빠진다.
        self.store.add_found_item(10, storage_location_id=None, first_seen_at=NOW - timedelta(minutes=5))
        self.store.advance(days=1)
        self.store.connect_location(10, 1)

        self.run_pass()

        self.assertEqual(self.queued_item_ids(), [])

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

    def test_registration_day_is_skipped_and_its_items_wait_for_the_next_day(self) -> None:
        # PRD 4.3-E — 등록일(D)에는 비교하지 않는다. 그날 들어온 물품은 D+1 첫 실행에서 본다.
        self.store.now = NOW + timedelta(hours=2)  # 11-06 17:00 KST, 아직 등록일
        self.store.add_found_item(10, storage_location_id=1)

        same_day = self.run_pass()

        self.assertEqual((same_day["reservations_checked"], self.queued_item_ids()), (0, []))
        self.store.advance(hours=16)  # 11-07 09:00 KST
        self.assertEqual(self.run_pass()["items_queued"], 1)
        self.assertEqual(self.queued_item_ids(), [10])

    def test_no_more_matching_once_the_final_notice_exists(self) -> None:
        # PRD 4.3-E — 종료 안내 뒤에는 "이후에는 메일이 발송되지 않아요". 연장하면 다시 본다.
        reservation = self.store.reservations[self.reservation_id]
        self.store._insert_message(self.reservation_id, "expiry_notice", expiry_for=reservation["expires_at"])
        self.store.add_found_item(10, storage_location_id=1)

        self.assertEqual(self.run_pass()["reservations_checked"], 0)
        self.store.extend_reservation(self.reservation_id)
        self.assertEqual(self.run_pass()["items_queued"], 1)

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
