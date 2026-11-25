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
   - 특징은 물품 글에 실제로 적히는 이어폰 방향(왼쪽·오른쪽)과 맥세이프만 조건으로 쓴다. 가죽·캐릭터·
     남성용 같은 특징, 사전에 없는 단어, 카드 상품은 조건으로 쓰지 않는다(거의 안 적혀 진짜 물건을 놓친다).
4. Jev 제외(2026-11-23 사용자 결정) — 검색 화면과 같은 search/jev_filter.py로 점수 상위 100건 중 명백히 다른
   물건만 뺀다(2026-11-25 — 25건 구간마다 요청 하나, 1~25위 0.92·26~100위 0.87). 크론(cron_judge)에서만 켜고, OpenRouter 키가 없거나 호출이 실패하면 아무것도 빼지 않는다.
"""

from __future__ import annotations

from typing import Any

from ..search.jev_filter import JevFilter

from ..search import color_filter, hybrid, synonyms


def candidate_terms(query: str) -> tuple[set[str], set[str]]:
    """`search_found_items`가 키워드 후보 RPC에 넘기는 것과 같은 (포함 단어, 독립 1글자 단어)."""
    return hybrid.candidate_terms(hybrid.query_text(query))


# 동의어 사전에서 조건으로 쓰는 분류. 카드 상품은 "티머니 카드"/"교통카드 티머니"처럼 표기에 따라
# 다른 개념이 되어 진짜 카드를 놓치므로 뺀다.
CONDITION_DOMAINS = ("color", "brand", "attribute")
# 특징(attribute)은 물품 글에 실제로 적히는 것만 조건으로 쓴다. 가죽·캐릭터·남성용·줄무늬 등은
# 물품 글에 거의 적히지 않아 "검정 가죽 지갑" 예약이 가죽이라고 안 적힌 검정 지갑을 놓쳤다(리뷰 2026-11-15).
CONDITION_ATTRIBUTES = frozenset({"왼쪽", "오른쪽", "왼쪽유닛", "오른쪽유닛", "맥세이프"})
# 같은 것으로 보는 다른 대표어. 모델 이름만 적힌 물품도 그 브랜드다(갤럭시 버즈에는 "삼성"이 없다).
# "왼쪽"과 "왼쪽유닛"은 사전에서 다른 개념이지만 같은 물건을 가리킨다.
ALSO_MATCHES = {
    "삼성": ("갤럭시", "버즈"),
    "왼쪽": ("왼쪽유닛",),
    "왼쪽유닛": ("왼쪽",),
    "오른쪽": ("오른쪽유닛",),
    "오른쪽유닛": ("오른쪽",),
}
_GROUPS = {group["canonical"]: group for group in synonyms._CONCEPTS}


def _is_condition(group: dict[str, Any]) -> bool:
    if group["domain"] not in CONDITION_DOMAINS:
        return False
    return group["domain"] != "attribute" or group["canonical"] in CONDITION_ATTRIBUTES


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
    for related in ALSO_MATCHES.get(canonical, ()):
        forms |= {related, *_GROUPS[related]["terms"]} if related in _GROUPS else {related}
    return any(form.casefold() in words for form in forms)


def meets_conditions(search_text: str, conditions: dict[str, list[str]]) -> bool:
    words = _item_words(search_text.casefold())
    for domain, canonicals in conditions.items():
        if domain == "color":
            # 색을 하나도 알 수 없는 물품은 넣는다(2026-11-15 사용자 결정). 2026-11-23 사용자 결정으로
            # 검색 화면과 같은 색 계열·팔레트 기준을 쓴다 — 확실히 다른 색일 때만 뺀다(search/color_filter.py).
            wanted = set(canonicals) - color_filter.UNKNOWN
            if color_filter.compatible(wanted, color_filter.item_colors({"search_text": search_text})):
                continue
            return False
        if any(_mentions(words, canonical) for canonical in canonicals):
            continue
        return False
    return True


# 2026-11-23 사용자 결정 — 예약 하나당 Jev에 줄 시간. 크론 매칭 단계 전체 마감(120초)은 run_match_pass가 지킨다.
JEV_ALERT_BUDGET_SECONDS = 3.0


def _with_palette_color(item: dict[str, Any]) -> dict[str, Any]:
    """알림 후보에는 색상 칸이 따로 없다. search_text의 경찰청 팔레트 값을 꺼내 보정 때와 같은 입력을 만든다."""
    found = color_filter._PALETTE_PATTERN.search(str(item.get("search_text") or ""))
    label = next((key for key in color_filter.PALETTE if found and key.casefold() == found[0]), "")
    return {**item, "color_name": label, "description": ""}


def search_judge(query: str, candidates: list[dict[str, Any]], *,
                 jev: JevFilter | None = None) -> list[tuple[dict[str, Any], float]]:
    """설계 3.2 — matching.SimilarityJudge. 후보는 반경·출처·커서로 이미 좁혀져 있다(공간 1차 필터)."""
    terms, words = candidate_terms(query)
    # keyword_found_item_candidates와 같은 포함 판정(PRD 4.5 — 검색 화면과 같은 후보).
    pool = [item for item in candidates
            if hybrid.candidate_matches(str(item.get("search_text") or ""), terms, words)]
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
    if jev is not None and matched:
        # 4. Jev 제외(2026-11-23, 2026-11-25 구간별 임계값) — 점수 상위 100건 중 명백히 다른 물건만 뺀다. 실패하면 아무것도 빼지 않는다.
        _, status, excluded = jev.apply(hybrid.query_text(query), [_with_palette_color(item) for item, _ in matched],
                                        budget=JEV_ALERT_BUDGET_SECONDS)
        if status in {"unavailable", "skipped"}:
            from ..agent_diagnostics import record
            record("jev.fallback", stage="alert_jev_filter", reason=status, final_status="fallback")
        matched = [pair for pair in matched if int(pair[0]["id"]) not in excluded]
    return matched
