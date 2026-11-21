import json
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from retriever_lost_found.integrations.embeddings import BgeM3Client
from retriever_lost_found.integrations.supabase import SupabaseFoundItemClient
from retriever_lost_found.search.hybrid import keyword_scores, search_found_items, search_with_deadline, SearchDeadlineExceeded, query_text, query_kinds
from retriever_lost_found.web.app import create_app


ITEMS = [
    {"id": 1, "storage_location_id": 7, "item_name": "검정 지갑"},
    {"id": 2, "storage_location_id": 7, "item_name": "머니클립"},
    {"id": 3, "storage_location_id": 7, "item_name": "무선이어폰"},
]


class FakeItems:
    def fetch_items(self, ids):
        assert ids == [7]
        return ITEMS

    def fetch_semantic_items(self, ids, vector, **kwargs):
        # PRD 4.5 — radius-wide 1-bit semantic RPC (cos ≥ 0.60, top 200). Every in-scope row is close.
        assert ids == [7] and len(vector) == 1024
        return [{"item_id": row["id"], "similarity": 0.9, "detail": row} for row in self.fetch_items(ids)]


class FakeEmbedding:
    def embed(self, query, **kwargs):
        return [1.0] * 1024


def test_bm25_korean_partial_words_and_unrelated_rejection():
    scores = keyword_scores([{"id": 1, "item_name": "검정색지갑"}, ITEMS[2]], "검정 지갑")
    assert scores[1] > 0
    assert 3 not in scores


def test_color_only_matches_are_rejected_but_wallet_survives():
    items = [
        {"id": 1, "item_name": "검정 가죽 지갑"},
        {"id": 2, "item_name": "빨강 장지갑"},
        {"id": 3, "item_name": "검정 삼성 휴대폰"},
        {"id": 4, "item_name": "검정 카드"},
    ]
    assert set(keyword_scores(items, "검정 가죽 지갑")) == {1, 2}


def test_reviewed_color_and_brand_aliases():
    assert 1 in keyword_scores([{"id": 1, "item_name": "검정 MLB 모자"}], "검은색 엠엘비 모자")


def test_keyword_candidates_include_wallet():
    assert keyword_scores(ITEMS,"지갑")


def test_semantic_scores_combine_with_keyword_candidates_without_mutating_items():
    results,count,status=search_found_items(FakeItems(),"지갑",[7],FakeEmbedding())
    assert count==3 and status=="ok" and {r["id"] for r in results}=={1,2}
    assert results==sorted(results,key=lambda r:(-r["match_score"],-r["id"]))
    assert "match_score" not in ITEMS[0]


def test_keyword_survives_embedding_failure():
    class Broken:
        def embed(self, query, **kwargs):
            raise RuntimeError("do not expose upstream body")

    results, _, status = search_found_items(FakeItems(), "지갑", [7], Broken())
    assert status == "unavailable" and {r["id"] for r in results} == {1, 2}
    assert search_found_items(FakeItems(), "지갑", [7])[2] == "disabled"


def test_semantic_budget_returns_without_waiting_for_blocked_worker():
    release = threading.Event()
    finished = threading.Event()

    class Slow(FakeEmbedding):
        def embed(self, query, **kwargs):
            release.wait(2)
            finished.set()
            return super().embed(query)

    try:
        rows, _, status = search_found_items(FakeItems(), "지갑", [7], Slow(), semantic_budget=0.01)
        assert status == "unavailable" and {r["id"] for r in rows} == {1, 2}
        assert not finished.is_set()  # executor 종료를 기다리지 않고 이미 응답했다.
    finally:
        release.set()
        assert finished.wait(2)


def test_semantic_rows_outside_the_scope_neither_add_nor_score():
    class Items(FakeItems):
        def fetch_semantic_items(self,ids,vector,**kwargs):
            return [{"item_id":1,"similarity":0.9,"detail":{**ITEMS[0],"description":"상세 보존"}},
                    {"item_id":2,"similarity":0.9,"detail":{**ITEMS[1],"storage_location_id":999}}]
    rows,count,_=search_found_items(Items(),"지갑",[7],FakeEmbedding())
    by_id={row["id"]:row for row in rows}
    assert count==3 and set(by_id)=={1,2} and by_id[1]["description"]=="상세 보존"
    assert by_id[2]["match_score"]==pytest.approx(0.75*keyword_scores(ITEMS,"지갑")[2],abs=1e-6)


def test_search_projection_and_keyset_pagination_keep_scope():
    from urllib.parse import parse_qs, urlparse
    client = SupabaseFoundItemClient("https://test.supabase.co", "test-key")
    with patch("retriever_lost_found.integrations.supabase.FOUND_ITEM_PAGE_SIZE", 2), patch(
        "retriever_lost_found.integrations.supabase._request_json",
        side_effect=[[{"id": 1}, {"id": 2}], [{"id": 3}]],
    ) as request:
        assert len(client.fetch_search_candidates([7])) == 3
    queries = [parse_qs(urlparse(call.args[0].full_url).query) for call in request.call_args_list]
    assert queries[1]["id"] == ["gt.2"]
    assert all(q["storage_location_id"] == ["in.(7)"] and "offset" not in q for q in queries)
    assert "image_url" not in queries[0]["select"][0]
    assert "normalized_search_text" not in queries[0]["select"][0]


def test_detail_query_filters_both_ids_and_locations():
    from urllib.parse import parse_qs, urlparse
    client = SupabaseFoundItemClient("https://test.supabase.co", "test-key")
    with patch("retriever_lost_found.integrations.supabase._request_json", return_value=[]) as call:
        client.fetch_item_details([7], [1, 2])
    query = json.loads(call.call_args.args[0].data)
    assert query["p_item_ids"] == [1, 2] and query["p_location_ids"] == [7]


def test_keyword_candidate_rpc_sends_scope_and_terms_without_kind_mask_argument():
    # PRD 4.1, 4.5 — 20261120120000 마이그레이션의 2인자 함수와 맞아야 한다(p_kinds 없음).
    client = SupabaseFoundItemClient("https://test.supabase.co", "test-key")
    bundle = {"candidate_count": None, "items": [{"id": 1}]}
    with patch("retriever_lost_found.integrations.supabase._request_json", return_value=bundle) as call:
        assert client.fetch_candidate_bundle([7, 7], ["가방", "백팩"], timeout=1) == bundle
    request = call.call_args.args[0]
    assert request.full_url.endswith("/rest/v1/rpc/keyword_found_item_candidates")
    assert json.loads(request.data) == {"p_location_ids": [7], "p_terms": ["가방", "백팩"]}


def test_semantic_results_cannot_add_a_wrong_kind():
    class Items(FakeItems):
        def fetch_items(self,ids):
            return [{"id":1,"storage_location_id":7,"item_name":"모자"},
                    {"id":2,"storage_location_id":7,"item_name":"에어팟"}]
    rows,_,status=search_found_items(Items(),"귀에 끼는 블루투스 기기",[7],FakeEmbedding())
    assert status=="ok" and [r["id"] for r in rows]==[2]


def test_explicit_kind_takes_priority():
    assert len(query_kinds(query_text("블루투스 휴대폰"))) == 1


def test_description_can_support_kind_when_source_name_is_vague():
    item = {"id": 1, "item_name": "검정 물품", "raw_category_name": "기타", "description": "가죽 지갑"}
    assert 1 in keyword_scores([item], "지갑")


def test_search_pagination_exposes_total_without_dropping_remaining_matches():
    class Items(FakeItems):
        def fetch_items(self, ids):
            return [{"id": i, "storage_location_id": 7, "item_name": "지갑"} for i in range(1, 56)]

    client = TestClient(create_app(object(), "test-js", found_item_client=Items()))
    first = client.get("/api/found-items?location_ids=7&q=지갑").json()
    second = client.get("/api/found-items", params={"location_ids": 7, "q": "지갑", "cursor": first["next_cursor"]}).json()
    assert first["match_count"] == first["total_count"] == 55
    assert len(first["items"]) == 50 and len(second["items"]) == 5
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})


def test_every_keyword_match_is_preserved_without_semantic_scores():
    class Items(FakeItems):
        def fetch_items(self, ids):
            return [{"id": i,"storage_location_id":7,"item_name":"지갑"} for i in range(1,251)]
        def fetch_semantic_items(self, ids, vector, **kwargs):
            return super().fetch_semantic_items(ids,vector,**kwargs)[:200]
    rows,_,status=search_found_items(Items(),"지갑",[7],FakeEmbedding())
    assert status=="ok" and {row["id"] for row in rows}==set(range(1,251))


def test_supabase_compressed_json_is_decoded():
    import gzip
    from urllib.request import Request
    from retriever_lost_found.integrations.supabase import _request_json
    class Response:
        headers={"Content-Encoding":"gzip"}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return gzip.compress(b'{"items":[]}')
    with patch("urllib.request.urlopen",return_value=Response()):
        assert _request_json(Request("https://test.supabase.co"),1,"test")=={"items":[]}


def test_entire_candidate_pipeline_is_bounded_not_only_semantic_wait():
    release = threading.Event()
    finished = threading.Event()

    class SlowItems(FakeItems):
        def fetch_items(self, ids):
            release.wait(2)
            finished.set()
            return ITEMS

    try:
        with pytest.raises(SearchDeadlineExceeded):
            search_with_deadline(SlowItems(), "지갑", [7], timeout=0.01)
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(2)


def test_embedding_cache_reuses_only_successful_query_vectors():
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({"data": [{"index": 0, "embedding": [1.0]*1024}]}).encode()
    client = BgeM3Client("test-key")
    with patch("retriever_lost_found.integrations.embeddings.build_opener") as opener:
        opener.return_value.open.return_value = Response()
        first = client.embed("지갑")
        first[0] = 9
        assert client.embed("지갑")[0] == 1
        assert opener.return_value.open.call_count == 1


def test_searches_start_in_parallel():
    barrier = threading.Barrier(2, timeout=2)

    class Items(FakeItems):
        def fetch_items(self, ids):
            if not getattr(self,"started",False):
                self.started=True
                barrier.wait()
            return super().fetch_items(ids)

    class Embedding(FakeEmbedding):
        def embed(self, query, **kwargs):
            barrier.wait()
            return super().embed(query)

    assert search_found_items(Items(), "지갑", [7], Embedding())[2] == "ok"


def test_endpoint_maintains_response_and_does_not_expose_vectors():
    app = create_app(object(), "test-js", found_item_client=FakeItems(), embedding_client=FakeEmbedding())
    response = TestClient(app).get("/api/found-items", params={"location_ids": "7", "q": "지갑"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["semantic_status"] == "ok"
    assert response.json()["candidate_count"] == 3
    assert {r["id"] for r in response.json()["items"]} == {1, 2}
    assert "embedding" not in response.text


def test_database_semantic_failure_falls_back_to_keywords():
    class Items(FakeItems):
        def fetch_semantic_items(self,ids,vector,**kwargs):
            return SupabaseFoundItemClient("https://test.supabase.co","test-key").fetch_semantic_items(ids,vector,timeout=1)
    with patch("retriever_lost_found.integrations.supabase._request_json",side_effect=TimeoutError):
        rows,_,status=search_found_items(Items(),"지갑",[7],FakeEmbedding())
    assert status=="unavailable" and {r["id"] for r in rows}=={1,2}


def test_unrelated_query_has_no_keyword_results():
    rows, count, status = search_found_items(FakeItems(), "청록색 우산", [7])
    assert rows == [] and count == 3 and status == "disabled"


@pytest.mark.parametrize("bad_vector", [[1.0], [float("nan")] * 1024, [0.0] * 1024])
def test_embedding_rejects_invalid_vectors(bad_vector):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({"data": [{"index": 0, "embedding": bad_vector}]}).encode()
    with patch("retriever_lost_found.integrations.embeddings.build_opener") as opener:
        opener.return_value.open.return_value = Response()
        with pytest.raises(RuntimeError, match="의미 검색용"):
            BgeM3Client("test-key").embed("지갑")


def test_embedding_uses_stored_model_and_provider():
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({"data": [{"index": 0, "embedding": [1.0] * 1024}]}).encode()
    with patch("retriever_lost_found.integrations.embeddings.build_opener") as opener:
        opener.return_value.open.return_value = Response()
        assert len(BgeM3Client("test-key").embed("지갑")) == 1024
        body = json.loads(opener.return_value.open.call_args.args[0].data)
        assert body["model"] == "baai/bge-m3"
        assert body["provider"] == {"only": ["deepinfra"], "allow_fallbacks": False}


# PRD 4.5 — 2026-11-21 확정안: 1비트 후보 → 원본 재채점 의미 검색 + 0.75·BM25 + 0.25·정규화 코사인.
class SemanticItems(FakeItems):
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.calls = list(rows), error, []

    def fetch_semantic_items(self, ids, vector, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.rows


def test_semantic_search_adds_items_the_keyword_step_missed():
    extra = {"id": 9, "storage_location_id": 7, "item_name": "반지갑", "image_url": None}
    client = SemanticItems([{"item_id": 9, "similarity": 0.8, "detail": extra}])
    rows, _, status = search_found_items(client, "지갑", [7], FakeEmbedding())
    assert status == "ok" and {r["id"] for r in rows} == {1, 2, 9}
    assert client.calls[0]["min_similarity"] == 0.6 and client.calls[0]["rescore"] == 500
    assert client.calls[0]["limit"] == 200
    semantic_only = next(r for r in rows if r["id"] == 9)
    assert semantic_only["match_score"] == pytest.approx(0.25 * (0.8 - 0.6) / 0.4)


def test_semantic_only_items_need_the_threshold_and_the_kind_gate():
    rows = [{"item_id": 8, "similarity": 0.59, "detail": {"id": 8, "storage_location_id": 7, "item_name": "카드지갑"}},
            {"item_id": 9, "similarity": 0.9, "detail": {"id": 9, "storage_location_id": 7, "item_name": "검정 우산"}},
            {"item_id": 10, "similarity": 0.9, "detail": {"id": 10, "storage_location_id": 999, "item_name": "지갑"}}]
    results, _, _ = search_found_items(SemanticItems(rows), "지갑", [7], FakeEmbedding())
    assert {r["id"] for r in results} == {1, 2}


def test_semantic_search_runs_even_without_keyword_matches():
    extra = {"id": 9, "storage_location_id": 7, "item_name": "머니클립"}
    rows, _, status = search_found_items(SemanticItems([{"item_id": 9, "similarity": 0.7, "detail": extra}]),
                                         "청록색 돈집게", [7], FakeEmbedding())
    assert status == "ok" and [r["id"] for r in rows] == [9]


def test_semantic_rpc_failure_keeps_keyword_scores_only():
    rows, _, status = search_found_items(SemanticItems(error=RuntimeError("db")), "지갑", [7], FakeEmbedding())
    lexical = keyword_scores(ITEMS, "지갑")
    assert status == "unavailable" and {r["id"]: r["match_score"] for r in rows} == {
        i: pytest.approx(score, abs=1e-6) for i, score in lexical.items()}


def test_semantic_rpc_sends_scope_vector_and_rescore_count():
    client = SupabaseFoundItemClient("https://test.supabase.co", "test-key")
    detail = {"id": 1, "storage_location_id": 7}
    with patch("retriever_lost_found.integrations.supabase._request_json",
               return_value=[{"item_id": 1, "similarity": 0.7, "detail": detail}]) as call:
        rows = client.fetch_semantic_items([7, 7], [0.5] * 1024, timeout=1)
    request = call.call_args.args[0]
    body = json.loads(request.data)
    assert request.full_url.endswith("/rest/v1/rpc/semantic_found_item_candidates")
    assert body["p_location_ids"] == [7] and body["p_rescore"] == 500 and body["p_limit"] == 200
    assert body["p_min"] == 0.6 and body["p_query"].startswith("[0.5,") and rows[0]["similarity"] == 0.7
    with patch("retriever_lost_found.integrations.supabase._request_json",
               return_value=[{"item_id": 1, "similarity": 0.7, "detail": {**detail, "storage_location_id": 9}}]):
        with pytest.raises(RuntimeError):
            client.fetch_semantic_items([7], [0.5] * 1024, timeout=1)
