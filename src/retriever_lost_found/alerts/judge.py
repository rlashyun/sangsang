"""PRD 4.2 P1 · 4.5 — 알림 메일의 "유사" 판정(2026-11-06 결정: 1번 검색 방법을 따른다).

같은 검색어로 같은 반경을 검색했을 때 결과에 나오는 새 물품을 "유사한 물품"으로 본다(2026-11-15 결정).
운영 검색(`search/hybrid.py`의 `search_found_items`)과 같은 두 단계를 그대로 거친다.

1. 키워드 후보 — `keyword_found_item_candidates`와 같은 조건. 검색어(또는 종류 동의어)가
   `found_item_search_documents.search_text`에 들어 있는 물품만 남긴다.
2. BM25 — `keyword_scores`가 0보다 큰 점수를 준 물품. 종류 충돌(예: 이어폰 검색에 휴대폰)은 여기서 빠진다.

BGE-M3 재정렬은 판정에 쓰지 않는다. 운영 검색에서도 재정렬은 키워드 결과의 순서만 바꾸고
결과 집합을 넓히지 않는다(PRD 4.5 — 키워드 후보 밖 의미 단독 결과는 찾지 않는다). 새 물품의 임베딩은
수집 뒤에 따로 만들어져 판정 시점에 아직 없을 수도 있다. 점수 하한은 두지 않는다 — 검색 화면도
키워드 일치 결과를 모두 보여 주기 때문이다. 점수는 메일 속 물품 순서에만 쓴다.

3. 조건 단어(2026-11-15 사용자 결정 — 여기서부터 검색 화면과 기준이 다르다). 검색어에 동의어 사전의
   색·브랜드·특징 단어가 있으면 그 말이 맞는 물품만 남긴다. 검색 화면에서는 이 단어들이 순서만 바꾼다.
   - 같은 분류(색끼리 등) 안에서는 하나만 맞으면 되고, 분류끼리는 모두 맞아야 한다.
   - 색을 하나도 알 수 없는 물품은 색 조건을 통과시킨다(색을 안 적어 진짜 물건을 놓치지 않게).
   - 사전에 없는 단어, 카드 상품, 있음/없음 특징은 조건으로 쓰지 않는다(물품 글에 거의 안 적혀 놓친다).
"""

from __future__ import annotations

from typing import Any

from ..search import hybrid, synonyms


def candidate_terms(query: str) -> set[str]:
    """`search_found_items`가 키워드 후보 RPC에 넘기는 것과 같은 검색어 묶음."""
    query = hybrid.query_text(query)
    kinds = hybrid.query_kinds(query)
    if kinds:
        names = {name for name, group in zip(synonyms.KIND_NAMES, hybrid.KINDS) if group in kinds}
        return synonyms.kind_candidate_terms(names)
    return {term[2:] for term in hybrid.tokenize(query)} | synonyms.expanded_terms(query)


def _has_candidate_term(search_text: str, terms: set[str]) -> bool:
    # keyword_found_item_candidates — term <> '' and strpos(search_text, lower(term)) > 0
    return any(term and term.lower() in search_text for term in terms)


# 동의어 사전에서 조건으로 쓰는 분류. 카드 상품은 "티머니 카드"/"교통카드 티머니"처럼 표기에 따라
# 다른 개념이 되어 진짜 카드를 놓치므로 뺀다.
CONDITION_DOMAINS = ("color", "brand", "attribute")
# 모델 이름만 적힌 물품도 그 브랜드로 본다(갤럭시 버즈에는 "삼성"이 적혀 있지 않다).
IMPLIED_BRANDS = {"삼성": ("갤럭시", "버즈")}
_GROUPS = {group["canonical"]: group for group in synonyms._CONCEPTS}
_COLOR_GROUPS = [group for group in synonyms._CONCEPTS if group["domain"] == "color"]


def _is_condition(group: dict[str, Any]) -> bool:
    if group["domain"] not in CONDITION_DOMAINS:
        return False
    # 케이스없음·유닛있음 같은 상태 표현은 물품 글에 거의 적히지 않는다.
    return not group["canonical"].endswith(("없음", "있음"))


def condition_groups(query: str) -> dict[str, list[str]]:
    """검색어의 조건 단어를 분류별 대표어로 돌려준다. 예: "블랙 루이비통 지갑" → 색 검정, 브랜드 루이비통."""
    conditions: dict[str, list[str]] = {}
    for word in synonyms.normalize_text(query, query=True).split():
        group = synonyms._LOOKUP.get(word)
        if group and _is_condition(group) and group["canonical"] not in conditions.get(group["domain"], []):
            conditions.setdefault(group["domain"], []).append(group["canonical"])
    return conditions


def _item_words(search_text: str) -> set[str]:
    # 낱말 단위로 본다. 글자 포함으로 보면 "블랙야크"가 검정색 물품이 된다.
    words = set(synonyms.normalize_text(search_text).split())
    words |= set(synonyms.normalize_text(search_text, query=True).split())  # 색 단어도 대표어로 맞춘다
    return words


def _mentions(words: set[str], canonical: str) -> bool:
    group = _GROUPS[canonical]
    forms = {canonical, *group["terms"], *(term.replace(" ", "") for term in group["terms"])}
    forms |= set(IMPLIED_BRANDS.get(canonical, ()))
    return any(form.casefold() in words for form in forms)


def meets_conditions(search_text: str, conditions: dict[str, list[str]]) -> bool:
    words = _item_words(search_text.casefold())
    for domain, canonicals in conditions.items():
        if any(_mentions(words, canonical) for canonical in canonicals):
            continue
        # 색을 하나도 알 수 없는 물품은 넣는다(2026-11-15 사용자 결정). 다른 색이 적혀 있으면 뺀다.
        if domain == "color" and not any(_mentions(words, group["canonical"]) for group in _COLOR_GROUPS):
            continue
        return False
    return True


def search_judge(query: str, candidates: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    """설계 3.2 — matching.SimilarityJudge. 후보는 반경·출처·커서로 이미 좁혀져 있다(공간 1차 필터)."""
    terms = candidate_terms(query)
    pool = [item for item in candidates if _has_candidate_term(str(item.get("search_text") or ""), terms)]
    if not pool:
        return []
    scores = hybrid.keyword_scores(pool, hybrid.query_text(query))
    conditions = condition_groups(query)
    matched = [
        (item, scores[int(item["id"])])
        for item in pool
        if int(item["id"]) in scores and meets_conditions(str(item.get("search_text") or ""), conditions)
    ]
    matched.sort(key=lambda pair: (-pair[1], -int(pair[0]["id"])))
    return matched
