"""PRD 4.1 · 4.5 — 기관 범위 내 BM25 + BGE-M3 점수 합산 초안."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import math
from typing import Any

from .fuzzy import fuzzy_item_score, normalize_fuzzy_text


def tokenize(text: str) -> list[str]:
    words = normalize_fuzzy_text(text, keep_spaces=True).split()
    # 한국어 띄어쓰기·조사에 덜 민감하도록 단어와 글자 bigram을 함께 쓴다.
    return [token for word in words for token in (
        ["w:" + word] + ["c:" + word[i:i + 2] for i in range(len(word) - 1)]
    )]


def keyword_scores(items: list[dict[str, Any]], query: str) -> dict[int, float]:
    docs = [Counter(tokenize(" ".join(str(item.get(field) or "") for field in (
        "item_name", "item_name", "raw_category_name", "color_name", "description",
    )))) for item in items]
    lengths = [sum(doc.values()) for doc in docs]
    average = sum(lengths) / max(1, len(docs)) or 1
    terms = sorted(set(tokenize(query)))
    frequencies = {term: sum(term in doc for doc in docs) for term in terms}
    scores = {}
    threshold = 0.7 if len(normalize_fuzzy_text(query)) <= 2 else 0.5
    for item, doc, length in zip(items, docs, lengths):
        score = 0.0
        for term in terms:
            tf = doc[term]
            if tf:
                df = frequencies[term]
                idf = math.log(1 + (len(docs) - df + 0.5) / (df + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
        # 기존 오타 허용은 보조 신호로 유지한다. BM25는 0~1로 압축한다.
        fuzzy = fuzzy_item_score(query, item)
        lexical = max(score / (score + 1), 0.7 * fuzzy if fuzzy >= threshold else 0)
        if lexical:
            scores[int(item["id"])] = lexical
    return scores


def search_found_items(client, query: str, location_ids: list[int], embedding_client=None):
    def keyword_search():
        items = client.fetch_items(location_ids)
        return items, keyword_scores(items, query)

    def semantic_search():
        if embedding_client is None:
            return {}, "disabled"
        try:
            vector = embedding_client.embed(query)
            return client.semantic_scores(location_ids, vector), "ok"
        except RuntimeError:
            # PRD 5 — 외부 장애 때 키워드 검색은 유지하고 비밀값/응답 본문을 노출하지 않는다.
            return {}, "unavailable"

    with ThreadPoolExecutor(max_workers=2) as pool:
        keywords = pool.submit(keyword_search)
        semantics = pool.submit(semantic_search)
        items, lexical = keywords.result()
        semantic, status = semantics.result()
    ranked = []
    for item in items:
        item_id = int(item["id"])
        if item_id not in lexical and item_id not in semantic:
            continue
        score = 0.5 * lexical.get(item_id, 0) + 0.5 * semantic.get(item_id, 0)
        ranked.append({**item, "match_score": round(score, 6)})
    ranked.sort(key=lambda item: (-item["match_score"], -int(item["id"])))
    return ranked, len(items), status
