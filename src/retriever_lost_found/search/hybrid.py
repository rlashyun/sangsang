"""PRD 4.1 · 4.5 — 기관 범위 내 BM25 + BGE-M3 점수 합산 초안."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from difflib import SequenceMatcher
import logging
import math
from time import monotonic
from typing import Any

from .fuzzy import fuzzy_item_score, normalize_fuzzy_text

FUZZY_LIMIT = 200
SEMANTIC_BUDGET_SECONDS = 5.0  # 실험용 전체 예산. 성능 목표 달성 수치가 아니다.
_semantic_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="semantic")
_semantic_slots = BoundedSemaphore(4)
# PRD 4.5 — 검토한 소규모 표현만 검색 시 통일하며 원문은 바꾸지 않는다.
ALIASES = (("검은색", "검정"), ("검정색", "검정"), ("하얀색", "흰색"),
           ("하얀", "흰색"), ("핑크", "분홍"), ("브라운", "갈색"),
           ("엠엘비", "mlb"), ("큐씨와이", "qcy"), ("선캡", "썬캡"))
KINDS = (("카드지갑", "지갑", "장지갑", "반지갑", "머니클립"),
         ("휴대폰", "핸드폰", "스마트폰", "아이폰", "갤럭시"),
         ("이어폰", "에어팟", "버즈"), ("모자", "썬캡", "캡모자"),
         ("가방", "백팩", "배낭"), ("카드", "신용", "체크카드", "교통카드"))


def search_text(text: str) -> str:
    text = normalize_fuzzy_text(text, keep_spaces=True)
    for source, target in ALIASES:
        text = text.replace(source, target)
    return text


def query_kinds(query: str) -> list[tuple[str, ...]]:
    # 카드지갑의 '카드'처럼 겹치는 표현은 긴 표현을 먼저 소비한다.
    remaining = query.replace(" ", "")
    groups = []
    for group in KINDS:
        if any(word in remaining for word in group):
            groups.append(group)
            for word in sorted(group, key=len, reverse=True):
                remaining = remaining.replace(word, "")
    return groups


def tokenize(text: str) -> list[str]:
    words = search_text(text).split()
    # 한국어 띄어쓰기·조사에 덜 민감하도록 단어와 글자 bigram을 함께 쓴다.
    return [token for word in words for token in (
        ["w:" + word] + ["c:" + word[i:i + 2] for i in range(len(word) - 1)]
    )]


def keyword_scores(items: list[dict[str, Any]], query: str, *, timings: dict | None = None) -> dict[int, float]:
    started = monotonic()
    query = search_text(query)
    kinds = query_kinds(query)
    docs = [Counter(tokenize(" ".join(str(item.get(field) or "") for field in (
        "item_name", "item_name", "raw_category_name", "color_name", "description",
    )))) for item in items]
    lengths = [sum(doc.values()) for doc in docs]
    average = sum(lengths) / max(1, len(docs)) or 1
    terms = sorted(set(tokenize(query)))
    frequencies = {term: sum(term in doc for doc in docs) for term in terms}
    bm25 = {}
    eligible = set()
    short_items = {}
    typo_candidates = []
    threshold = 0.7 if len(normalize_fuzzy_text(query)) <= 2 else 0.5
    for item, doc, length in zip(items, docs, lengths):
        score = 0.0
        for term in terms:
            tf = doc[term]
            if tf:
                df = frequencies[term]
                idf = math.log(1 + (len(docs) - df + 0.5) / (df + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
        item_id = int(item["id"])
        name = search_text(str(item.get("item_name") or ""))[:128]
        category = search_text(str(item.get("raw_category_name") or ""))[:64]
        short_items[item_id] = {"item_name": name, "raw_category_name": category}
        evidence = (name + category).replace(" ", "")
        if kinds and not any(any(word in evidence for word in group) for group in kinds):
            continue
        eligible.add(item_id)
        bm25[item_id] = score / (score + 1)
        # BM25가 0인 오타도 이름 유사도로 별도 후보를 확보한다. 긴 설명은 비교하지 않는다.
        similarity = max((SequenceMatcher(None, query.replace(" ", ""), word).ratio()
                          for word in [name.replace(" ", ""), *name.split()] if word), default=0)
        if similarity >= 0.5:
            typo_candidates.append((similarity, item_id))
    lexical_ids = sorted(eligible, key=lambda i: (-bm25[i], -i))[:FUZZY_LIMIT // 2]
    typo_ids = [i for _, i in sorted(typo_candidates, reverse=True)[:FUZZY_LIMIT // 2]]
    fuzzy_ids = set(lexical_ids + typo_ids)
    fuzzy_started = monotonic()
    scores = {}
    for item_id in eligible:
        fuzzy = fuzzy_item_score(query, short_items[item_id]) if item_id in fuzzy_ids else 0
        # 종류가 명시되지 않은 긴 질의는 일부 글자만 겹치는 결과를 받지 않는다.
        words = query.split()
        evidence = " ".join(short_items[item_id].values()).replace(" ", "")
        coverage = sum(word in evidence for word in words) / max(1, len(words))
        if not kinds and len(words) > 1 and coverage < 0.6 and fuzzy < 0.8:
            continue
        score = bm25[item_id]
        lexical = max(score, 0.7 * fuzzy if fuzzy >= threshold else 0)
        if lexical:
            scores[item_id] = lexical
    if timings is not None:
        timings["bm25_candidates_ms"] = round((fuzzy_started - started) * 1000, 2)
        timings["fuzzy_ms"] = round((monotonic() - fuzzy_started) * 1000, 2)
        timings["fuzzy_candidates"] = len(fuzzy_ids)
    return scores


def search_found_items(client, query: str, location_ids: list[int], embedding_client=None,
                       *, semantic_budget: float = SEMANTIC_BUDGET_SECONDS):
    timings = {}
    deadline = monotonic() + semantic_budget
    def keyword_search():
        started = monotonic()
        fetch = getattr(client, "fetch_search_candidates", client.fetch_items)
        items = fetch(location_ids)
        timings["candidate_fetch_ms"] = round((monotonic() - started) * 1000, 2)
        started = monotonic()
        scores = keyword_scores(items, query, timings=timings)
        timings["keyword_ms"] = round((monotonic() - started) * 1000, 2)
        return items, scores

    def semantic_search():
        if embedding_client is None:
            return {}, "disabled", {}
        try:
            started = monotonic()
            remaining = deadline - started
            if remaining <= 0:
                return {}, "unavailable", {}
            vector = embedding_client.embed(query, timeout=remaining)
            embedded = monotonic()
            remaining = deadline - embedded
            if remaining <= 0:
                return {}, "unavailable", {}
            scores = client.semantic_scores(location_ids, vector, timeout=remaining)
            ended = monotonic()
            if ended > deadline:
                return {}, "unavailable", {}
            return scores, "ok", {"embedding_ms": round((embedded - started) * 1000, 2),
                                   "semantic_db_ms": round((ended - embedded) * 1000, 2)}
        except RuntimeError:
            # PRD 5 — 외부 장애 때 키워드 검색은 유지하고 비밀값/응답 본문을 노출하지 않는다.
            return {}, "unavailable", {}

    # 요청마다 executor를 닫으면 timeout 뒤에도 종료 대기로 막힌다.
    # 공용 작업 수를 제한해 지연된 요청이 무제한 쌓이지 않게 한다.
    semantics = None
    if embedding_client is not None and _semantic_slots.acquire(blocking=False):
        semantics = _semantic_pool.submit(semantic_search)
        semantics.add_done_callback(lambda _: _semantic_slots.release())
    items, lexical = keyword_search()
    semantic, status = {}, "disabled" if embedding_client is None else "unavailable"
    if semantics is not None:
        try:
            semantic, status, semantic_timings = semantics.result(timeout=max(0, deadline - monotonic()))
            timings.update(semantic_timings)
        except TimeoutError:
            semantics.cancel()
    ranked = []
    for item in items:
        item_id = int(item["id"])
        if item_id not in lexical and item_id not in semantic:
            continue
        score = 0.5 * lexical.get(item_id, 0) + 0.5 * semantic.get(item_id, 0)
        ranked.append({**item, "match_score": round(score, 6)})
    ranked.sort(key=lambda item: (-item["match_score"], -int(item["id"])))
    if hasattr(client, "fetch_item_details"):
        started = monotonic()
        details = {int(row["id"]): row for row in client.fetch_item_details(
            location_ids, [int(item["id"]) for item in ranked]
        ) if row.get("storage_location_id") in location_ids}
        ranked = [{**details[int(item["id"])], "match_score": item["match_score"]}
                  for item in ranked if int(item["id"]) in details]
        timings["detail_fetch_ms"] = round((monotonic() - started) * 1000, 2)
    # 질의·물품·비밀값은 기록하지 않고 구간별 시간/건수만 남긴다.
    logging.getLogger(__name__).info("search timings=%s candidates=%d matches=%d semantic=%s",
                                   timings, len(items), len(ranked), status)
    return ranked, len(items), status
