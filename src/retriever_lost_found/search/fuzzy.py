from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from rapidfuzz import process as _process
from rapidfuzz.distance import Levenshtein as _Levenshtein

# PRD 4.5 — 가중치는 물품명 1.0 / 원분류 0.9 / 색상 0.82 / 설명·정규화 텍스트 0.72.
# 큰 가중치부터 본다. 이미 얻은 점수가 다음 필드의 가중치 이상이면 그 필드는 점수를 바꿀 수 없다.
_WEIGHTED_FIELDS = (
    ("item_name", 1.0),
    ("raw_category_name", 0.9),
    ("color_name", 0.82),
    ("description", 0.72),
    ("normalized_search_text", 0.72),
)
_TOKEN_COVERAGE_WEIGHT = 0.88
_DOCUMENT_JACCARD_WEIGHT = 0.8


# str.isalnum()이 아닌 문자의 연속. 파이썬 정규식의 \w는 isalnum() 문자와 밑줄이므로
# [\W_]는 정확히 "isalnum()이 아닌 문자"다(전 코드 포인트에서 같은 분류임을 확인함).
_NON_ALNUM_RUN = re.compile(r"[\W_]+")


def normalize_fuzzy_text(value: Any, *, keep_spaces: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    spaced = " ".join(_NON_ALNUM_RUN.sub(" ", normalized).split())
    return spaced if keep_spaces else spaced.replace(" ", "")


def levenshtein_ratio(left: str, right: str) -> float:
    """1 − 편집거리 / 긴 쪽 길이.

    PRD 3.3 · 5 — 같은 정의를 C 구현(rapidfuzz)으로 계산한다. 순수 Python 이중 루프가
    검색어 응답 시간의 대부분을 차지했다. 결과는 기존 구현과 비트 단위로 같다
    (tests/web/test_fuzzy_equivalence.py).
    """
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    return _Levenshtein.normalized_similarity(left, right)


def ngrams(value: str, size: int = 2) -> set[str]:
    if not value:
        return set()
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def jaccard_similarity(left: str, right: str) -> float:
    return _jaccard_with_grams(ngrams(left), right)


def _jaccard_with_grams(left_grams: set[str], right: str) -> float:
    right_grams = ngrams(right)
    shared = len(left_grams & right_grams)
    # |A ∪ B| = |A| + |B| − |A ∩ B| — 합집합을 만들지 않고 같은 정수를 얻는다.
    union = len(left_grams) + len(right_grams) - shared
    return shared / union if union else 0.0


@dataclass(frozen=True)
class _PreparedQuery:
    compact: str
    tokens: tuple[str, ...]
    grams: frozenset[str]
    prefix: str


def _prepare_query(query: str) -> _PreparedQuery | None:
    spaced = normalize_fuzzy_text(query, keep_spaces=True)
    compact = spaced.replace(" ", "")
    if not compact:
        return None
    return _PreparedQuery(
        compact=compact,
        tokens=tuple(normalize_fuzzy_text(token) for token in spaced.split()),
        grams=frozenset(ngrams(compact)),
        prefix=compact[:2],
    )


def _field_score(query: _PreparedQuery, field: str, field_tokens: list[str]) -> float:
    """필드 전체·토큰(2자 이상)·검색어 길이의 창 가운데 가장 높은 변형 점수.

    호출 전에 "필드가 검색어를 포함"하는 경우(1.0)는 걸러져 있다. 그러면 창은 검색어와 길이가
    같으므로 포함 규칙(1.0·0.9)에 걸리지 않고 편집거리·자카드로만 점수가 정해진다.
    """
    query_compact = query.compact
    field_score = 0.0
    scored_variants: list[str] = []
    for variant in {field, *(token for token in field_tokens if len(token) >= 2)}:
        if query_compact in variant:
            return 1.0
        if variant in query_compact and len(variant) >= 2:
            field_score = 0.9
        else:
            scored_variants.append(variant)
    if len(field) > len(query_compact):
        window_size = len(query_compact)
        scored_variants.extend(
            {field[index : index + window_size] for index in range(len(field) - window_size + 1)}
        )
    if not scored_variants:
        return field_score

    # 모든 변형은 필드의 부분 문자열이라 변형의 2-gram은 필드의 2-gram 안에 있다.
    # 검색어와 겹치는 2-gram이 필드에 하나도 없으면 자카드는 모두 0이고,
    # 검색어 앞 두 글자로 시작하는 변형도 없어 접두 보너스도 0이다.
    # 그때 변형 점수는 편집거리 비율 그 자체이므로 최댓값을 C 구현에서 한 번에 구한다.
    if not (query.grams & ngrams(field)):
        best_match = _process.extractOne(
            query_compact,
            scored_variants,
            scorer=_Levenshtein.normalized_similarity,
            processor=None,
        )
        return max(field_score, best_match[1])

    for variant in scored_variants:
        levenshtein = levenshtein_ratio(query_compact, variant)
        jaccard = _jaccard_with_grams(query.grams, variant)
        prefix_bonus = 0.08 if variant.startswith(query.prefix) else 0.0
        variant_score = min(1.0, max(levenshtein, jaccard) + prefix_bonus)
        if variant_score > field_score:
            field_score = variant_score
    return field_score


def _score_prepared(query: _PreparedQuery, item: dict[str, Any]) -> float:
    best = 0.0
    document_parts: list[str] = []
    for field_name, weight in _WEIGHTED_FIELDS:
        spaced = normalize_fuzzy_text(item.get(field_name), keep_spaces=True)
        field = spaced.replace(" ", "")
        if not field:
            continue
        document_parts.append(field)
        if best >= weight:
            continue  # field_score ≤ 1이므로 이 필드는 best를 넘지 못한다
        if query.compact in field:
            best = weight  # 필드 전체가 검색어를 포함하면 field_score는 1.0
            continue
        best = max(best, _field_score(query, field, spaced.split()) * weight)

    if best >= 1.0:
        return 1.0
    document = "".join(document_parts)
    if query.tokens and best < _TOKEN_COVERAGE_WEIGHT:
        token_coverage = sum(token in document for token in query.tokens) / len(query.tokens)
        best = max(best, token_coverage * _TOKEN_COVERAGE_WEIGHT)
    if best < _DOCUMENT_JACCARD_WEIGHT:
        best = max(best, _jaccard_with_grams(query.grams, document) * _DOCUMENT_JACCARD_WEIGHT)
    return round(min(best, 1.0), 4)


def fuzzy_item_score(query: str, item: dict[str, Any]) -> float:
    prepared = _prepare_query(query)
    if prepared is None:
        return 0.0
    return _score_prepared(prepared, item)


def rank_found_items(
    items: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return [dict(item) for item in items]
    threshold = 0.7 if len(normalize_fuzzy_text(query)) <= 2 else 0.5
    prepared = _prepare_query(query)
    if prepared is None:
        return []
    ranked: list[dict[str, Any]] = []
    for item in items:
        score = _score_prepared(prepared, item)
        if score < threshold:
            continue
        result = dict(item)
        result["match_score"] = score
        ranked.append(result)
    ranked.sort(key=lambda item: -float(item.get("match_score", 0)))
    return ranked
