"""PRD 4.2 P1 · 4.5 — 알림 "유사" 판정이 운영 검색 결과와 같은 물품을 고르는지 본다."""

from __future__ import annotations

import unittest

from alert_fakes import NOW, FakeAlertStore, FakeMonotonic
from retriever_lost_found.alerts.judge import search_judge
from retriever_lost_found.alerts.matching import SIMILARITY_JUDGE, run_match_pass
from retriever_lost_found.search.hybrid import search_found_items

LOCATION_ID = 7
# 원문 필드 그대로의 물품(절대 규칙 3). search_text는 DB 투영과 같은 방식으로 만든다.
ITEMS = [
    {"id": 1, "item_name": "검정 가죽 지갑", "raw_category_name": "지갑", "color_name": "검정", "description": ""},
    {"id": 2, "item_name": "에어팟 프로", "raw_category_name": "무선이어폰", "color_name": "흰색", "description": ""},
    {"id": 3, "item_name": "삼성 갤럭시 S23", "raw_category_name": "휴대폰", "color_name": "검정", "description": ""},
    {"id": 4, "item_name": "아이폰 14", "raw_category_name": "휴대폰", "color_name": "파랑", "description": "케이스 있음"},
    {"id": 5, "item_name": "검정 백팩", "raw_category_name": "가방", "color_name": "검정", "description": "노트북 들어 있음"},
    {"id": 6, "item_name": "파란 모자", "raw_category_name": "모자", "color_name": "파랑", "description": ""},
    {"id": 7, "item_name": "체크카드", "raw_category_name": "카드", "color_name": "", "description": "국민은행"},
    {"id": 8, "item_name": "무선이어폰", "raw_category_name": "무선이어폰", "color_name": "검정", "description": "버즈"},
    {"id": 9, "item_name": "검정 물품", "raw_category_name": "기타", "color_name": "검정", "description": "가죽 지갑"},
]


def search_text(item: dict) -> str:
    # 20261111103818 sync_search_document — lower(concat_ws(' ', 물품명, 원분류, 색상, 설명))
    fields = (item["item_name"], item["raw_category_name"], item["color_name"], item["description"])
    return " ".join(value for value in fields if value).lower()


def candidate(item: dict) -> dict:
    """alert_candidate_items가 돌려주는 모양(키워드 후보 RPC와 같은 필드)."""
    return {
        "id": item["id"], "storage_location_id": LOCATION_ID, "search_text": search_text(item),
        "item_name": item["item_name"], "raw_category_name": item["raw_category_name"],
    }


class SameAsSearchClient:
    """운영 검색이 부르는 RPC를 SQL 조건대로 흉내 낸다(외부 호출 없음, AGENTS.md 7장)."""

    def fetch_candidate_bundle(self, location_ids, terms, *, timeout, kind_terms=None):
        assert location_ids == [LOCATION_ID] and kind_terms is None
        # keyword_found_item_candidates — term <> '' and strpos(search_text, lower(term)) > 0
        items = [
            candidate(item) for item in ITEMS
            if any(term and term.lower() in search_text(item) for term in terms)
        ]
        return {"items": items, "candidate_count": len(ITEMS)}

    def fetch_rerank_items(self, location_ids, item_ids, *, timeout):
        return [
            {"item_id": item["id"], "detail": {**item, "storage_location_id": LOCATION_ID}, "vector": None}
            for item in ITEMS if item["id"] in item_ids
        ]


def search_ids(query: str) -> set[int]:
    rows, _, _ = search_found_items(SameAsSearchClient(), query, [LOCATION_ID])
    return {int(row["id"]) for row in rows}


def judged_ids(query: str) -> set[int]:
    return {int(item["id"]) for item, _ in search_judge(query, [candidate(item) for item in ITEMS])}


class SearchJudgeTests(unittest.TestCase):
    def test_judge_picks_exactly_what_the_search_screen_shows(self) -> None:
        # 2026-11-15 결정 — "유사" = 같은 검색어로 같은 범위를 검색했을 때 결과에 나오는 물품.
        for query in ("지갑", "에어팟", "아이폰", "핸드폰", "검정 가방", "파란색 모자", "카드", "버즈", "노트북"):
            with self.subTest(query=query):
                self.assertEqual(judged_ids(query), search_ids(query))

    def test_similar_item_is_picked_and_an_unrelated_one_is_not(self) -> None:
        # AGENTS.md 7장 — negative는 positive와 쌍으로.
        self.assertIn(2, judged_ids("에어팟"))
        self.assertNotIn(1, judged_ids("에어팟"))
        self.assertIn(1, judged_ids("지갑"))
        self.assertNotIn(2, judged_ids("지갑"))

    def test_synonyms_reach_items_written_differently(self) -> None:
        # 동의어 사전 — "핸드폰"으로 예약해도 원분류가 "휴대폰"인 물품을 찾는다.
        self.assertTrue({3, 4} <= judged_ids("핸드폰"))
        self.assertIn(5, judged_ids("가방"))

    def test_kind_conflicts_are_rejected_like_the_search(self) -> None:
        # "아이폰" 예약에 삼성 갤럭시는 알리지 않는다. 아이폰은 알린다.
        self.assertIn(4, judged_ids("아이폰"))
        self.assertNotIn(3, judged_ids("아이폰"))

    def test_description_can_carry_the_kind(self) -> None:
        self.assertIn(9, judged_ids("지갑"))

    def test_no_candidates_means_no_matches(self) -> None:
        self.assertEqual(search_judge("에어팟", []), [])

    def test_scores_come_back_highest_first(self) -> None:
        scores = [score for _, score in search_judge("검정 지갑", [candidate(item) for item in ITEMS])]
        self.assertTrue(scores)
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(score > 0 for score in scores))


class MatchPassWithSearchJudgeTests(unittest.TestCase):
    def test_match_pass_uses_the_search_judge(self) -> None:
        self.assertIs(SIMILARITY_JUDGE, search_judge)
        store = FakeAlertStore(NOW)
        store.add_location(1, 37.5547 + 0.0089, 126.9707)
        reservation_id = store.add_active_reservation(query="에어팟", radius_m=1000)
        store.advance(hours=18)  # D+1
        store.add_found_item(10, storage_location_id=1, item_name="에어팟 프로", raw_category_name="무선이어폰")
        store.add_found_item(11, storage_location_id=1, item_name="검정 지갑", raw_category_name="지갑")

        result = run_match_pass(
            store, SIMILARITY_JUDGE, item_source_code="partner_api", deadline=100.0, clock=FakeMonotonic()
        )

        self.assertEqual((result["status"], result["items_queued"]), ("succeeded", 1))
        self.assertEqual(sorted(item_id for (rid, item_id) in store.notified if rid == reservation_id), [10])
        # 판정에 걸리지 않은 지갑도 커서가 지나가 다음 실행에서 다시 보지 않는다.
        store.advance(minutes=1)
        self.assertEqual(store.candidate_items(reservation_id, "partner_api")["items"], [])


if __name__ == "__main__":
    unittest.main()
