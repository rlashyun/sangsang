"""퍼지 순위 가속이 점수·순서를 하나도 바꾸지 않는지 확인한다 — PRD 4.5, 3.3.

아래 `_reference_*`는 2026-11-05 기준 `search/fuzzy.py` 구현을 그대로 옮긴 것이다.
속도를 위해 구현을 바꿔도 점수는 이 기준과 **정확히** 같아야 한다(임계값 0.7/0.5 경계에서
순위가 달라지면 사용자가 보는 결과가 바뀐다). 검색 방식 자체를 바꾸는 결정(⑥)이 나면
이 기준도 그 결정에 맞춰 함께 바꾼다.
"""
from __future__ import annotations

import random
import unicodedata
import unittest
from typing import Any

from retriever_lost_found.search.fuzzy import fuzzy_item_score, rank_found_items


def _reference_normalize(value: Any, *, keep_spaces: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    characters = [character if character.isalnum() else " " for character in normalized]
    spaced = " ".join("".join(characters).split())
    return spaced if keep_spaces else spaced.replace(" ", "")


def _reference_levenshtein_ratio(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


def _reference_ngrams(value: str, size: int = 2) -> set[str]:
    if not value:
        return set()
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _reference_jaccard(left: str, right: str) -> float:
    left_grams = _reference_ngrams(left)
    right_grams = _reference_ngrams(right)
    union = left_grams | right_grams
    return len(left_grams & right_grams) / len(union) if union else 0.0


def _reference_score(query: str, item: dict[str, Any]) -> float:
    query_compact = _reference_normalize(query)
    if not query_compact:
        return 0.0
    query_tokens = _reference_normalize(query, keep_spaces=True).split()
    weighted_fields = (
        (item.get("item_name"), 1.0),
        (item.get("raw_category_name"), 0.9),
        (item.get("color_name"), 0.82),
        (item.get("description"), 0.72),
        (item.get("normalized_search_text"), 0.72),
    )
    best = 0.0
    document_parts: list[str] = []
    for raw_value, weight in weighted_fields:
        field = _reference_normalize(raw_value)
        if not field:
            continue
        document_parts.append(field)
        field_tokens = _reference_normalize(raw_value, keep_spaces=True).split()
        variants = {field, *(token for token in field_tokens if len(token) >= 2)}
        if len(field) > len(query_compact):
            window_size = len(query_compact)
            variants.update(
                field[index : index + window_size]
                for index in range(len(field) - window_size + 1)
            )
        field_score = 0.0
        for variant in variants:
            if query_compact in variant:
                variant_score = 1.0
            elif variant in query_compact and len(variant) >= 2:
                variant_score = 0.9
            else:
                levenshtein = _reference_levenshtein_ratio(query_compact, variant)
                jaccard = _reference_jaccard(query_compact, variant)
                prefix_bonus = 0.08 if variant.startswith(query_compact[:2]) else 0.0
                variant_score = min(1.0, max(levenshtein, jaccard) + prefix_bonus)
            field_score = max(field_score, variant_score)
        best = max(best, field_score * weight)

    document = "".join(document_parts)
    if query_tokens:
        token_coverage = sum(
            _reference_normalize(token) in document for token in query_tokens
        ) / len(query_tokens)
        best = max(best, token_coverage * 0.88)
    best = max(best, _reference_jaccard(query_compact, document) * 0.8)
    return round(min(best, 1.0), 4)


def _reference_rank(items: list[dict[str, Any]], query: str) -> list[tuple[Any, float]]:
    query = query.strip()
    if not query:
        return [(item.get("id"), None) for item in items]
    threshold = 0.7 if len(_reference_normalize(query)) <= 2 else 0.5
    scored = []
    for item in items:
        score = _reference_score(query, item)
        if score >= threshold:
            scored.append((item.get("id"), score))
    scored.sort(key=lambda pair: -pair[1])
    return scored


# 운영 데이터에서 자주 보이는 형태를 섞는다 — 괄호·꺾쇠 분류, 영문 대소문자, 숫자, 전각 문자, 빈 값.
_WORDS = (
    "지갑", "남성용", "여성용", "반지갑", "장지갑", "가방", "백팩", "숄더백", "에코백", "크로스백",
    "휴대폰", "핸드폰", "스마트폰", "아이폰", "갤럭시", "삼성", "애플", "에어팟", "버즈", "무선이어폰",
    "이어폰", "카드", "신용카드", "체크카드", "교통카드", "신분증", "모자", "비니", "캡모자", "의류",
    "검정", "블랙", "흰색", "화이트", "갈색", "브라운", "회색", "네이비", "빨강", "분홍",
    "가죽", "케이스", "투명", "스트랩", "보관", "하고", "있습니다", "습득", "지하철", "서울역",
    "iPhone", "AirPods", "Galaxy", "PRO", "Max", "15", "S24", "2", "ＩＰＨＯＮＥ", "１５",
)
_PUNCTUATION = (" ", " ", " ", "(", ")", " > ", "/", ",", "-", ".", "·")


def _random_text(rng: random.Random, max_words: int) -> str:
    parts = []
    for _ in range(rng.randint(1, max_words)):
        parts.append(rng.choice(_WORDS))
        parts.append(rng.choice(_PUNCTUATION))
    return "".join(parts).strip()


def _corpus(seed: int, size: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    items = []
    for index in range(size):
        item = {
            "id": index + 1,
            "item_name": _random_text(rng, 4),
            "raw_category_name": _random_text(rng, 3),
            "color_name": rng.choice(("블랙(검정)", "브라운(갈색)", "화이트(흰색)", "", None, "기타")),
            "description": _random_text(rng, 14) if rng.random() < 0.8 else None,
            "normalized_search_text": _random_text(rng, 18) if rng.random() < 0.7 else "",
            "registered_on": "2026-11-0" + str(rng.randint(1, 4)),
        }
        items.append(item)
    return items


_QUERIES = (
    "지갑", "휴대폰", "검정 가방", "아이퐁", "에어팟 프로", "갈색 반지갑", "카드", "핸드폰",
    "백팩", "비니", "iphone 15", "ＡＩＲＰＯＤＳ", "폰", "가", "삼성 갤럭시 s24 케이스",
    "검정색 가죽 장지갑 남성용", "!!!", " 지갑 ", "교통 카드", "무선 이어폰 버즈",
    "아이폰15프로맥스", "흰색 에코백", "모자 캡", "신분증 지갑 카드", "가방가방가방",
)


class FuzzyEquivalenceTests(unittest.TestCase):
    def test_scores_match_reference_implementation_exactly(self) -> None:
        items = _corpus(seed=20261105, size=60)
        for query in _QUERIES:
            with self.subTest(query=query):
                self.assertEqual(
                    [fuzzy_item_score(query, item) for item in items],
                    [_reference_score(query, item) for item in items],
                )

    def test_ranking_order_and_scores_match_reference_implementation(self) -> None:
        items = _corpus(seed=7, size=60)
        for query in _QUERIES:
            with self.subTest(query=query):
                ranked = rank_found_items(items, query)
                self.assertEqual(
                    [(item["id"], item["match_score"]) for item in ranked],
                    _reference_rank(items, query),
                )

    def test_reference_corpus_exercises_both_sides_of_the_threshold(self) -> None:
        # positive와 negative를 함께 — 모든 항목이 걸리거나 하나도 안 걸리면 위 비교가 무의미하다.
        items = _corpus(seed=7, size=60)
        for query in ("지갑", "검정 가방", "아이퐁"):
            with self.subTest(query=query):
                matched = len(_reference_rank(items, query))
                self.assertGreater(matched, 0)
                self.assertLess(matched, len(items))


if __name__ == "__main__":
    unittest.main()
