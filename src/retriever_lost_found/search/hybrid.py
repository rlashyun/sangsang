"""PRD 4.1 · 4.5 — 기관 범위 내 BM25 + BGE-M3 점수 합산 초안."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
import logging
import math
from time import monotonic
from typing import Any

from .text import levenshtein_ratio
from . import synonyms
from .synonyms import ALIASES

RERANK_LIMIT = 200
SEMANTIC_BUDGET_SECONDS = 4.0
SEARCH_BUDGET_SECONDS = 4.4  # HTTP/JSON 전송 여유를 포함해 5초 목표를 검증한다.
_semantic_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="semantic")
_semantic_slots = BoundedSemaphore(4)
_search_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="found-items")
_search_slots = BoundedSemaphore(4)
# PRD 4.5 — fixed, corpus-reviewed expressions; original records stay untouched.
KINDS = (("카드지갑", "지갑", "장지갑", "반지갑", "머니클립"),
         ("휴대폰", "핸드폰", "스마트폰", "아이폰"),
         ("이어폰", "에어팟", "버즈"), ("모자", "썬캡", "캡모자"),
         ("가방", "백팩", "배낭"), ("카드", "신용", "체크카드", "교통카드"))


def search_text(text: str) -> str:
    return synonyms.normalize_text(text)


def query_text(text: str) -> str:
    """Correct only an unambiguous one-edit query word against the small item vocabulary.

    The work depends on query length, never the number of found items.
    """
    vocabulary = {word for group in KINDS for word in group}
    words = synonyms.normalize_text(text, query=True).split()
    for index, word in enumerate(words):
        if len(word)<3 or any(term in word for term in vocabulary):
            continue
        candidates = [term for term in vocabulary if abs(len(term)-len(word))<=1
                      and levenshtein_ratio(word, term)>=1-1/max(len(word),len(term))-1e-9]
        if len(candidates)==1:
            words[index] = candidates[0]
    return " ".join(words)


def query_kinds(query: str) -> list[tuple[str, ...]]:
    names = synonyms.kind_names(query)
    groups = [group for name, group in zip(synonyms.KIND_NAMES, KINDS) if name in names]
    # Keep the reviewed descriptive hints used by the existing search path.
    remaining = query.replace(" ", "")
    # 명시한 종류가 있으면 이를 우선한다. '블루투스'만으로 이어폰을 단정하지 않는다.
    if not groups:
        if any(word in remaining for word in ("귀에끼", "귀에넣", "귀에착용")):
            groups.append(KINDS[2])
        if any(word in remaining for word in ("돈을넣", "현금을넣")):
            groups.append(KINDS[0])
    return groups


def _description_kinds(item: dict, normalized_document: str | None):
    if item.get("description"):
        return query_kinds(search_text(str(item["description"])))
    if normalized_document is not None:
        return query_kinds(normalized_document)
    return query_kinds(search_text(str(item.get("search_text") or "")))


def supports_kind(item: dict, wanted: list[tuple[str, ...]], query: str,
                  *, normalized_document: str | None = None) -> bool:
    # 자유 설명의 잘못된 '아이폰' 표기가 명확한 이어폰 이름을 뒤집지 않게 한다.
    primary = search_text(str(item.get("item_name") or ""))
    groups = query_kinds(primary)
    description_groups = None
    if not groups:
        groups = query_kinds(search_text(str(item.get("raw_category_name") or "")))
    if not groups:
        description_groups = _description_kinds(item, normalized_document)
        groups = description_groups
    if "아이폰" in query:
        identity = primary + search_text(str(item.get("raw_category_name") or ""))
        if any(word in identity for word in ("삼성", "갤럭시", "엘지", "샤오미")) and "아이폰" not in identity:
            return False
    if not wanted or any(group in groups for group in wanted):
        return True
    if KINDS[1] in wanted and groups == [KINDS[2]]:
        return False
    # 한 행에 함께 보관된 다른 물품이 설명에 명시된 경우는 보존한다.
    if description_groups is None:
        description_groups = _description_kinds(item, normalized_document)
    return any(group in description_groups for group in wanted)


def tokenize(text: str) -> list[str]:
    return _tokens(search_text(text))


def _tokens(normalized_text: str) -> list[str]:
    words = normalized_text.split()
    # 한국어 띄어쓰기·조사에 덜 민감하도록 단어와 글자 bigram을 함께 쓴다.
    return [token for word in words for token in (
        ["w:" + word] + ["c:" + word[i:i + 2] for i in range(len(word) - 1)]
    )]


class SearchDeadlineExceeded(RuntimeError):
    pass


def search_with_deadline(client, query, location_ids, embedding_client=None, *, timeout=4.6, offset=0):
    """Bound the entire read pipeline, including slow body reads and ranking.

    Timed-out readers have no writes; bounded slots prevent an unbounded queue.
    """
    if not _search_slots.acquire(blocking=False):
        raise SearchDeadlineExceeded("검색 요청이 많습니다. 잠시 후 다시 검색해주세요.")
    future = _search_pool.submit(search_found_items, client, query, location_ids, embedding_client, offset=offset)
    future.add_done_callback(lambda _: _search_slots.release())
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        raise SearchDeadlineExceeded("검색이 지연되고 있습니다. 범위를 좁혀 다시 검색해주세요.") from None


def keyword_scores(items: list[dict[str, Any]], query: str, *, timings: dict | None = None) -> dict[int, float]:
    started = monotonic()
    query = query_text(query)
    kinds = query_kinds(query)
    terms = set(tokenize(query))
    # Small, reviewed kind synonyms also retrieve descriptive phrases such as cash storage.
    for group in kinds:
        name = next(name for name, legacy in zip(synonyms.KIND_NAMES, KINDS) if legacy == group)
        terms.update(tokenize(" ".join(synonyms.KIND_TERMS[name])))
    texts = [search_text(item.get("search_text") or " ".join(
        str(item.get(field) or "") for field in ("item_name", "raw_category_name", "color_name", "description")
    )) for item in items]
    docs = [Counter(_tokens(text)) for text in texts]
    lengths = [sum(doc.values()) for doc in docs]
    average = sum(lengths)/max(1,len(docs)) or 1
    frequencies = {term:sum(term in doc for doc in docs) for term in terms}
    scores = {}
    for item,doc,length,text in zip(items,docs,lengths,texts):
        if "item_name" in item and not supports_kind(item,kinds,query,normalized_document=text):
            continue
        score = 0.0
        for term in terms:
            tf = doc[term]
            if tf:
                df = frequencies[term]
                score += math.log(1+(len(docs)-df+0.5)/(df+0.5))*tf*2.2/(tf+1.2*(0.25+0.75*length/average))
        if score:
            scores[int(item["id"])] = score/(score+1)
    if timings is not None:
        timings["keyword_rank_ms"] = round((monotonic()-started)*1000,2)
    return scores


def search_found_items(client, query: str, location_ids: list[int], embedding_client=None,
                       *, semantic_budget: float = SEMANTIC_BUDGET_SECONDS, offset: int = 0):
    started = monotonic()
    query = query_text(query)
    deadline = started+SEARCH_BUDGET_SECONDS
    vector_future = None
    if embedding_client is not None and _semantic_slots.acquire(blocking=False):
        vector_future = _semantic_pool.submit(embedding_client.embed,query,timeout=min(semantic_budget,4.0))
        vector_future.add_done_callback(lambda _: _semantic_slots.release())
    kinds = query_kinds(query)
    names = {name for name, group in zip(synonyms.KIND_NAMES, KINDS) if group in kinds}
    # PRD 4.1/4.5: one scoped RPC; literal type expressions also recover aliases
    # absent from the legacy DB kind_mask. Attribute-only hits cannot widen it.
    terms = synonyms.kind_candidate_terms(names) if kinds else (
        {term[2:] for term in tokenize(query)} | synonyms.expanded_terms(query)
    )
    if hasattr(client,"fetch_candidate_bundle"):
        bundle = client.fetch_candidate_bundle(location_ids,sorted(terms),kind_terms=None,
                                               timeout=1.8)
        items,count = bundle["items"],bundle.get("candidate_count")
    else:
        items = client.fetch_items(location_ids)
        count = len(items)
    timings = {"keyword_fetch_ms":round((monotonic()-started)*1000,2)}
    lexical = keyword_scores(items,query,timings=timings)
    # ponytail: rerank only the top 200; keep every keyword match below them.
    # Semantic-only matches outside the keyword pool cannot be recovered here.
    ids = sorted(lexical,key=lambda item_id:(-lexical[item_id],-item_id))[:RERANK_LIMIT]
    timings["keyword_total_ms"] = round((monotonic()-started)*1000,2)
    if not ids:
        if vector_future is not None:
            vector_future.cancel()
        return [],count,"disabled" if embedding_client is None else "skipped"
    rerank_started = monotonic()
    # Load the small item matrix while the query embedding is still being generated.
    rows = client.fetch_rerank_items(location_ids,ids,timeout=max(0,deadline-monotonic()))
    vector = None
    status = "disabled" if embedding_client is None else "unavailable"
    if vector_future is not None:
        try:
            vector = vector_future.result(timeout=max(0,min(started+semantic_budget,deadline-0.1)-monotonic()))
            status = "ok"
        except (RuntimeError,TimeoutError):
            vector_future.cancel()
    query_norm = math.sqrt(math.sumprod(vector,vector)) if vector is not None else 0
    by_id = {int(item["id"]):item for item in items if int(item["id"]) in lexical}
    scored = {}
    for item_id in ids:
        by_id.pop(item_id,None)  # deleted/moved rows must not survive the live scope lookup
    for row in rows:
        item,item_vector=row["detail"],row["vector"]
        item_id=int(item["id"])
        if item_id in ids and item.get("storage_location_id") in location_ids and supports_kind(item,kinds,query):
            by_id[item_id]=item
            if vector is not None and item_vector is not None:
                scored[item_id]=math.sumprod(vector,item_vector)/(query_norm*math.sqrt(math.sumprod(item_vector,item_vector)))
    ranked=[]
    for item_id,item in by_id.items():
        score=lexical[item_id] if vector is None else 0.5*lexical[item_id]+0.5*max(0,scored.get(item_id,0))
        ranked.append({**item,"match_score":round(score,6)})
    if vector is not None and not scored:
        status="unavailable"
    ranked.sort(key=lambda item:(-item["match_score"],-int(item["id"])))
    # Keep every keyword match. Only the visible page needs full display fields.
    page=ranked[offset:offset+50]
    missing=[int(item["id"]) for item in page if "image_url" not in item]
    if missing and hasattr(client,"fetch_item_details"):
        details={int(item["id"]):item for item in client.fetch_item_details(location_ids,missing,
                    timeout=max(0,deadline-monotonic())) if item.get("storage_location_id") in location_ids}
        for item in page:
            if int(item["id"]) in details:
                score=item["match_score"]
                item.update(details[int(item["id"])]);item["match_score"]=score
    timings["rerank_ms"]=round((monotonic()-rerank_started)*1000,2)
    logging.getLogger(__name__).info("search timings=%s pool=%d shortlist=%d semantic=%s",timings,len(items),len(ids),status)
    return ranked,count,status
