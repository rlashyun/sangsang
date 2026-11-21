"""PRD 4.2 P1 · 4.5 — 알림 "유사" 판정이 운영 검색 결과와 같은 물품을 고르는지 본다."""

from __future__ import annotations

import unittest

from alert_fakes import NOW, FakeAlertStore, FakeMonotonic
from retriever_lost_found.alerts.judge import search_judge
from retriever_lost_found.alerts.matching import SIMILARITY_JUDGE, run_match_pass
from retriever_lost_found.search import hybrid
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

    def fetch_candidate_bundle(self, location_ids, terms, *, timeout, word_terms=()):
        assert location_ids == [LOCATION_ID]
        # keyword_found_item_candidates — strpos(search_text, lower(term)) > 0, 1글자 단어는 독립 단어로만
        items = [
            candidate(item) for item in ITEMS
            if hybrid.candidate_matches(search_text(item), {term.lower() for term in terms if term}, set(word_terms))
        ]
        return {"items": items, "candidate_count": len(ITEMS)}


def search_ids(query: str) -> set[int]:
    rows, _, _ = search_found_items(SameAsSearchClient(), query, [LOCATION_ID])
    return {int(row["id"]) for row in rows}


def judged_ids(query: str) -> set[int]:
    return {int(item["id"]) for item, _ in search_judge(query, [candidate(item) for item in ITEMS])}


class SearchJudgeTests(unittest.TestCase):
    def test_judge_picks_exactly_what_the_search_screen_shows(self) -> None:
        # 2026-11-15 결정 — 색·브랜드·특징이 없는 검색어는 검색 화면 결과와 같은 물품을 고른다.
        for query in ("지갑", "에어팟", "아이폰", "핸드폰", "카드", "버즈", "노트북"):
            with self.subTest(query=query):
                self.assertEqual(judged_ids(query), search_ids(query))

    def test_condition_words_only_narrow_the_search_result(self) -> None:
        # 2026-11-15 사용자 결정 — 조건 단어가 있으면 검색 결과 중 조건에 맞는 것만 남긴다(넓히지 않는다).
        for query in ("검정 가방", "파란색 모자", "검정 지갑"):
            with self.subTest(query=query):
                self.assertLessEqual(judged_ids(query), search_ids(query))

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


WALLETS = [
    {"id": 21, "item_name": "반지갑", "raw_category_name": "지갑", "color_name": "블랙", "description": ""},
    {"id": 22, "item_name": "반지갑", "raw_category_name": "지갑", "color_name": "갈색", "description": ""},
    {"id": 23, "item_name": "반지갑", "raw_category_name": "지갑", "color_name": "", "description": ""},
    {"id": 24, "item_name": "루이비통 장지갑", "raw_category_name": "지갑", "color_name": "검정", "description": ""},
    {"id": 25, "item_name": "장지갑", "raw_category_name": "지갑", "color_name": "흰색", "description": "louis vuitton"},
    {"id": 26, "item_name": "블랙야크 지갑", "raw_category_name": "지갑", "color_name": "", "description": ""},
    {"id": 27, "item_name": "카드지갑", "raw_category_name": "지갑", "color_name": "검정", "description": "스누피 그림"},
]
EARPHONES = [
    {"id": 31, "item_name": "갤럭시 버즈", "raw_category_name": "무선이어폰", "color_name": "흰색", "description": ""},
    {"id": 32, "item_name": "에어팟 프로", "raw_category_name": "무선이어폰", "color_name": "흰색", "description": "왼쪽"},
    {"id": 33, "item_name": "에어팟 프로", "raw_category_name": "무선이어폰", "color_name": "흰색", "description": "오른쪽"},
]


def judged(query: str, items: list[dict]) -> set[int]:
    return {int(item["id"]) for item, _ in search_judge(query, [candidate(item) for item in items])}


class ConditionWordTests(unittest.TestCase):
    """2026-11-15 사용자 결정 — 검색어의 색·브랜드·특징이 맞는 물품만 알린다(검색 화면과 기준이 다르다)."""

    def test_color_must_match_and_synonyms_count_as_the_same_color(self) -> None:
        ids = judged("검정 지갑", WALLETS)
        self.assertIn(21, ids)  # 블랙 = 검정
        self.assertNotIn(22, ids)  # 갈색은 뺀다
        self.assertEqual(judged("블랙 지갑", WALLETS), ids)

    def test_item_without_any_color_is_kept(self) -> None:
        # 색을 알 수 없는 물품은 넣는다(2026-11-15 사용자 결정). 색이 다른 물품은 빠진다.
        self.assertIn(23, judged("검정 지갑", WALLETS))
        self.assertNotIn(22, judged("검정 지갑", WALLETS))

    def test_any_of_several_colors_is_enough(self) -> None:
        ids = judged("검정 흰색 지갑", WALLETS)
        self.assertTrue({21, 25} <= ids)
        self.assertNotIn(22, ids)

    def test_every_kind_of_condition_must_match(self) -> None:
        # 색과 브랜드를 같이 쓰면 둘 다 맞아야 한다. 영문 브랜드 표기도 같은 브랜드다.
        self.assertEqual(judged("루이비통 지갑", WALLETS), {24, 25})
        self.assertEqual(judged("검정 루이비통 지갑", WALLETS), {24})

    def test_brand_inside_another_word_is_not_a_color(self) -> None:
        # 블랙야크는 브랜드 이름이다. 검정색 물품으로 보지 않으므로 색을 모르는 물품으로 넣는다.
        self.assertIn(26, judged("검정 지갑", WALLETS))
        self.assertIn(26, judged("흰색 지갑", WALLETS))
        self.assertNotIn(21, judged("흰색 지갑", WALLETS))

    def test_unknown_words_do_not_become_conditions(self) -> None:
        # 사전에 없는 단어(스누피)는 조건이 아니다 — 조사·오타로 진짜 물건을 놓치지 않게 한다.
        self.assertEqual(judged("스누피 지갑", WALLETS), judged("지갑", WALLETS))

    def test_kind_only_query_is_unchanged(self) -> None:
        self.assertEqual(judged("지갑", WALLETS), {21, 22, 23, 24, 25, 26, 27})

    def test_side_of_an_earphone_must_match(self) -> None:
        self.assertEqual(judged("에어팟 왼쪽", EARPHONES), {32})
        # "왼쪽 유닛"으로 적힌 물품도 왼쪽이다.
        unit = [{"id": 34, "item_name": "에어팟 왼쪽유닛", "raw_category_name": "무선이어폰", "color_name": "", "description": ""}]
        self.assertEqual(judged("에어팟 왼쪽", EARPHONES + unit), {32, 34})

    def test_rarely_written_features_are_not_conditions(self) -> None:
        # 가죽·캐릭터·남성용 같은 특징은 물품 글에 거의 안 적혀 조건으로 쓰면 진짜 물건을 놓친다.
        self.assertEqual(judged("검정 가죽 지갑", WALLETS), judged("검정 지갑", WALLETS))
        self.assertIn(21, judged("검정 가죽 지갑", WALLETS))
        self.assertNotIn(22, judged("검정 가죽 지갑", WALLETS))
        self.assertEqual(judged("남성용 지갑", WALLETS), judged("지갑", WALLETS))

    def test_samsung_is_implied_by_galaxy_models(self) -> None:
        # 갤럭시 버즈에는 "삼성"이 적혀 있지 않아도 삼성 제품이다. 에어팟은 아니다.
        self.assertEqual(judged("삼성 이어폰", EARPHONES), {31})

    def test_scores_still_come_back_highest_first(self) -> None:
        scores = [score for _, score in search_judge("검정 지갑", [candidate(item) for item in WALLETS])]
        self.assertTrue(scores)
        self.assertEqual(scores, sorted(scores, reverse=True))


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
