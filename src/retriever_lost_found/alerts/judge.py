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


def search_judge(query: str, candidates: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    """설계 3.2 — matching.SimilarityJudge. 후보는 반경·출처·커서로 이미 좁혀져 있다(공간 1차 필터)."""
    terms = candidate_terms(query)
    pool = [item for item in candidates if _has_candidate_term(str(item.get("search_text") or ""), terms)]
    if not pool:
        return []
    scores = hybrid.keyword_scores(pool, hybrid.query_text(query))
    matched = [(item, scores[int(item["id"])]) for item in pool if int(item["id"]) in scores]
    matched.sort(key=lambda pair: (-pair[1], -int(pair[0]["id"])))
    return matched
