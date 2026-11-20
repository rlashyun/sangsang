"""PRD 4.5 — 키워드 후보 포함 검사(keyword_found_item_candidates)와 서버 BM25의 관계.

원칙: 포함 검사는 BM25가 점수를 줄 물품을 먼저 버리지 않는다. 외부 호출 없이 Python 판정으로 확인한다.
"""
import json
from unittest.mock import patch

import pytest

from retriever_lost_found.integrations.supabase import SupabaseFoundItemClient
from retriever_lost_found.search import synonyms
from retriever_lost_found.search.hybrid import (
    candidate_matches, candidate_terms, keyword_scores, query_kinds, query_text, search_found_items, tokenize,
)

# DB search_text처럼 소문자 원문(물품명·분류·색·설명)을 흉내 낸다.
CORPUS = [
    "(키) 1개", "키 분실", "자동차 키 분실", "차키", "카드키", "키링 열쇠고리", "나이키 가방", "카키색 모자",
    "펜 1자루", "볼펜", "펜던트 목걸이", "책 1권", "책상 서랍", "공 1개", "공책", "컵", "종이컵",
    "검정 장지갑", "카드지갑 검정", "블랙 우산", "검정 긴 우산", "노트북 가방", "스프링노트",
    "삼성 노트북 컴퓨터 > 삼성노트북", "헤드폰", "무선 해드폰", "핸드폰", "보스 헤드셋", "보스턴백",
    "반지", "반지갑", "접이식 우산", "우산(접이식)", "보조 배터리", "아이패드 프로", "아이폰 14",
    "파란 가방", "만화책 도서용품 > 만화책",
]
ITEMS = [{"id": i, "search_text": text, "item_name": text, "raw_category_name": ""}
         for i, text in enumerate(CORPUS, 1)]


def _unreduced_terms(query: str) -> set[str]:
    """candidate_terms가 겹치는 긴 단어를 지우기 전의 strpos 단어 묶음(비교 기준)."""
    kinds = query_kinds(query)
    if kinds:
        return {term.lower() for term in synonyms.kind_candidate_terms(set(kinds))}
    _, words = candidate_terms(query)
    raw = {t[2:] for t in tokenize(query)} | synonyms.expanded_terms(query)
    return {term.lower() for term in raw if term.strip()} - words


def _passed(query: str) -> set[int]:
    terms, words = candidate_terms(query)
    return {item["id"] for item in ITEMS if candidate_matches(item["search_text"], terms, words)}


QUERIES = ["키", "펜", "책", "공", "컵", "검정색 긴 거", "노트북", "검정 지갑", "차키", "헤드폰",
           "보스", "반지", "접이식 우산", "보조배터리", "아이패드", "파란 우산", "잃어버린 노트북"]


@pytest.mark.parametrize("raw_query", QUERIES)
def test_containment_never_drops_an_item_that_bm25_scores(raw_query):
    query = query_text(raw_query)
    scored = set(keyword_scores(ITEMS, query))
    assert scored, "positive: 이 검색어로 점수를 받는 물품이 표본에 있어야 한다"
    assert scored <= _passed(query)


@pytest.mark.parametrize("raw_query", ["키", "펜", "공", "컵", "검정색 긴 거", "보스", "성심당 빵 봉투"])
def test_one_letter_split_is_never_wider_than_plain_containment(raw_query):
    query = query_text(raw_query)
    assert not query_kinds(query)
    old = {item["id"] for item in ITEMS
           if any(term in item["search_text"] for term in _unreduced_terms(query) | candidate_terms(query)[1])}
    assert _passed(query) <= old


@pytest.mark.parametrize(("query", "hits", "misses"), [
    ("키", ["(키) 1개", "키 분실"], ["카드키", "키링 열쇠고리", "나이키 가방", "카키색 모자"]),
    ("펜", ["펜 1자루"], ["볼펜", "펜던트 목걸이"]),
    ("공", ["공 1개"], ["공책"]),
    ("컵", ["컵"], ["종이컵"]),
])
def test_one_letter_words_match_only_as_standalone_words(query, hits, misses):
    terms, words = candidate_terms(query_text(query))
    assert words == {query} and query not in terms
    for text in hits:
        assert candidate_matches(text, terms, words), text
        assert keyword_scores([{"id": 1, "search_text": text, "item_name": text}], query), text
    for text in misses:
        assert not candidate_matches(text, terms, words), text
        assert not keyword_scores([{"id": 1, "search_text": text, "item_name": text}], query), text


def test_dictionary_one_letter_words_keep_plain_containment():
    # 사전 단어(예: '만')는 붙여 쓴 물품명을 나눌 때 독립 단어로 새로 생길 수 있어 strpos를 그대로 쓴다.
    assert synonyms.is_known_word("만")
    terms, words = candidate_terms(query_text("검정 만"))
    assert "만" in terms and "만" not in words
    # positive 쌍: 사전 밖 1글자는 독립 단어 비교로 간다.
    assert candidate_terms(query_text("검정 키"))[1] == {"키"}


@pytest.mark.parametrize(("query", "kept", "dropped"), [
    ("검정 지갑", "지갑", "장지갑"),
    ("차키", "차키", "자동차키"),
    ("접이식 우산", "우산", "접이식우산"),
])
def test_longer_terms_that_contain_a_shorter_term_are_not_sent(query, kept, dropped):
    query = query_text(query)
    terms, words = candidate_terms(query)
    assert kept in terms and dropped not in terms
    assert dropped in _unreduced_terms(query)
    old = {item["id"] for item in ITEMS if any(term in item["search_text"] for term in _unreduced_terms(query))}
    assert _passed(query) == old


def test_headphone_is_not_corrected_to_phone():
    # PRD 4.5 — '헤드폰'이 '핸드폰'으로 고쳐져 휴대폰 종류로 좁혀졌다(평가셋 E3 정답 20건 누락).
    assert query_text("블루투스 헤드폰") == "블루투스 헤드폰"
    assert query_kinds(query_text("블루투스 헤드폰")) == ["헤드폰"]
    assert query_text("무선 해드폰") == "무선 헤드폰"
    scored = keyword_scores(ITEMS, query_text("헤드폰"))
    assert {CORPUS.index("헤드폰") + 1, CORPUS.index("무선 해드폰") + 1} <= set(scored)
    assert CORPUS.index("핸드폰") + 1 not in scored
    # positive 쌍: 핸드폰은 여전히 휴대폰 종류다.
    assert query_kinds(query_text("핸드폰")) == ["휴대폰"]


def test_rpc_sends_word_terms_only_when_present():
    client = SupabaseFoundItemClient("https://test.supabase.co", "test-key")
    bundle = {"candidate_count": None, "items": []}
    with patch("retriever_lost_found.integrations.supabase._request_json", return_value=bundle) as call:
        client.fetch_candidate_bundle([7], [], word_terms=["키"], timeout=1)
        client.fetch_candidate_bundle([7], ["가방"], word_terms=[], timeout=1)
    first, second = (json.loads(c.args[0].data) for c in call.call_args_list)
    assert first == {"p_location_ids": [7], "p_terms": [], "p_word_terms": ["키"]}
    # 1글자 단어가 없으면 이전 2인자 함수와 같은 요청이다(마이그레이션 전에도 동작).
    assert second == {"p_location_ids": [7], "p_terms": ["가방"]}


def test_search_sends_one_letter_words_separately_and_drops_nike():
    class Client:
        def __init__(self):
            self.calls = []

        def fetch_candidate_bundle(self, ids, terms, *, timeout, word_terms=()):
            self.calls.append((terms, list(word_terms)))
            items = [{**item, "storage_location_id": 7} for item in ITEMS
                     if candidate_matches(item["search_text"], set(terms), set(word_terms))]
            return {"items": items, "candidate_count": len(ITEMS)}

        def fetch_rerank_items(self, ids, item_ids, **kwargs):
            return [{"item_id": item["id"], "detail": {**item, "storage_location_id": 7}, "vector": None}
                    for item in ITEMS if item["id"] in item_ids]

    client = Client()
    rows, _, _ = search_found_items(client, "키", [7])
    assert client.calls == [([], ["키"])]
    names = {row["item_name"] for row in rows}
    assert "(키) 1개" in names and "키 분실" in names
    assert not names & {"카드키", "나이키 가방", "카키색 모자"}
