"""PRD 4.1 · 4.5 — 기관 범위 내 BM25 + BGE-M3 점수 합산 초안."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore
import logging
import math
from time import monotonic
from typing import Any

from . import synonyms
from .synonyms import ALIASES

RERANK_LIMIT = 200
SEMANTIC_BUDGET_SECONDS = 4.0
SEARCH_BUDGET_SECONDS = 4.4  # HTTP/JSON 전송 여유를 포함해 5초 목표를 검증한다.
_semantic_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="semantic")
_semantic_slots = BoundedSemaphore(4)
_search_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="found-items")
_search_slots = BoundedSemaphore(4)


def search_text(text: str) -> str:
    return synonyms.normalize_text(text)


def query_text(text: str) -> str:
    """PRD 4.5 — 검색어 정리: 대소문자·특수문자 전처리와 동의어 대표어만 맞춘다.

    2026-11-21 김성현 결정으로 자동 오타 보정은 뺐다. 옛 6종 22단어에 한 글자 차이만 고쳐
    '스마트키'를 '스마트폰'으로 바꾸는 오보정이 생겼다. 자주 틀리는 표기는 동의어 사전에 직접 넣는다.
    """
    return synonyms.normalize_text(text, query=True)


def query_kinds(query: str) -> list[str]:
    """검색어(또는 물품 글)가 가리키는 물건 종류 이름. 종류 목록은 item_kinds.json(분류 21개 기준)이다."""
    names = synonyms.kind_names(query)
    kinds = [name for name in synonyms.KIND_NAMES if name in names]
    # 명시한 종류가 있으면 이를 우선한다. '블루투스'만으로 이어폰을 단정하지 않는다.
    if not kinds:
        compact = query.replace(" ", "")
        kinds = [name for name, phrases in synonyms.KIND_HINTS.items()
                 if any(phrase in compact for phrase in phrases)]
    return kinds


def _description_kinds(item: dict, normalized_document: str | None):
    if item.get("description"):
        return query_kinds(search_text(str(item["description"])))
    if normalized_document is not None:
        return query_kinds(normalized_document)
    return query_kinds(search_text(str(item.get("search_text") or "")))


def supports_kind(item: dict, wanted: list[str], query: str,
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
    if "휴대폰" in wanted and groups == ["이어폰"]:
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


def candidate_terms(query: str) -> tuple[set[str], set[str]]:
    """PRD 4.5 — 포함 검사(키워드 후보 조회)에 보낼 (포함 단어, 독립 1글자 단어).

    `query`는 query_text()를 거친 검색어다. 포함 검사는 BM25가 점수를 줄 물품을 먼저 버리지 않는다.
    """
    kinds = query_kinds(query)
    if kinds:
        # 종류어가 있으면 종류 표현만 쓴다. 색 같은 속성만 맞는 물품은 supports_kind가 어차피 0점을 준다.
        # 종류 표현에는 Supabase 분류명이 들어 있어 '귀금속 > 시계' 같은 분류 경로로도 통과한다.
        # 1글자 종류어(책·옷)는 정규화가 붙여 쓴 말을 나눠 새로 만들 수 있으므로 strpos로 넓게 보낸다.
        terms, words = synonyms.kind_candidate_terms(set(kinds)), set()
    else:
        terms = {term[2:] for term in tokenize(query)} | synonyms.expanded_terms(query)
        # BM25는 1글자 검색어를 조각 없이 독립 단어(w:키)로만 비교한다. 사전 단어나 대표어가 아니면
        # 정규화로 새로 생기지 않으므로, 원문에서 앞뒤가 글자·숫자가 아닐 때만 통과시켜도 결과가 같다.
        canonical = {name for _, name in synonyms.ALIASES}
        words = {word for word in search_text(query).split() if len(word) == 1
                 and not synonyms.is_known_word(word) and word not in canonical}
    terms = {term.lower() for term in terms if term.strip()} - words
    # 짧은 단어가 이미 들어 있는 긴 단어(지갑 ⊂ 장지갑)는 결과를 바꾸지 않으므로 빼서 비교 횟수를 줄인다.
    return {term for term in terms if not any(other != term and other in term for other in terms)}, words


def _standalone(text: str, word: str) -> bool:
    start = text.find(word)
    while start >= 0:
        end = start + len(word)
        if (start == 0 or not text[start - 1].isalnum()) and (end == len(text) or not text[end].isalnum()):
            return True
        start = text.find(word, start + 1)
    return False


def candidate_matches(text: str, terms: set[str], words: set[str]) -> bool:
    """keyword_found_item_candidates(20261121120000)와 같은 포함 판정. text는 DB search_text(소문자 원문)다."""
    return any(term in text for term in terms) or any(_standalone(text, word) for word in words)


class SearchDeadlineExceeded(RuntimeError):
    failure_reason = "timeout"


class SearchBusy(SearchDeadlineExceeded):
    failure_reason = "busy"


def search_with_deadline(client, query, location_ids, embedding_client=None, *, timeout=4.6, offset=0):
    """Bound the entire read pipeline, including slow body reads and ranking.

    Timed-out readers have no writes; bounded slots prevent an unbounded queue.
    """
    if not _search_slots.acquire(blocking=False):
        raise SearchBusy("검색 요청이 많습니다. 잠시 후 다시 검색해주세요.")
    future = _search_pool.submit(copy_context().run, search_found_items, client, query, location_ids, embedding_client, offset=offset)
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
    for name in kinds:
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
    # PRD 4.1/4.5: one scoped RPC without the DB kind_mask filter. Literal type
    # expressions from synonyms.json do the kind gate, including aliases the
    # mask never knew. Attribute-only hits cannot widen it.
    terms, words = candidate_terms(query)
    if hasattr(client,"fetch_candidate_bundle"):
        bundle = client.fetch_candidate_bundle(location_ids,sorted(terms),word_terms=sorted(words),timeout=1.8)
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
        except (RuntimeError,TimeoutError) as error:
            # PRD 5 — Report fallback without logging the query or provider payload.
            from ..agent_diagnostics import record
            record("embedding.fallback", error=error, stage="embedding", final_status="fallback")
            vector_future.cancel()
    if embedding_client is not None and vector_future is None:
        from ..agent_diagnostics import record
        record("embedding.fallback", stage="embedding", reason="busy", final_status="fallback")
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
