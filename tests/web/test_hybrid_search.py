import json
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from retriever_lost_found.integrations.embeddings import BgeM3Client
from retriever_lost_found.integrations.supabase import SupabaseFoundItemClient
from retriever_lost_found.search.hybrid import keyword_scores, search_found_items
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

    def semantic_scores(self, ids, vector, **kwargs):
        assert ids == [7] and vector == [1.0] * 1024
        return {1: 0.9, 2: 0.85, 99: 0.99}  # 범위 밖 ID는 합산에서도 제외


class FakeEmbedding:
    def embed(self, query, **kwargs):
        return [1.0] * 1024


def test_bm25_korean_partial_words_and_unrelated_rejection():
    scores = keyword_scores([{"id": 1, "item_name": "검정색지갑"}, ITEMS[2]], "검정 지갑")
    assert scores[1] > 0
    assert 3 not in scores


def test_color_only_matches_are_rejected_but_wallet_and_typo_survive():
    items = [
        {"id": 1, "item_name": "검정 가죽 지갑"},
        {"id": 2, "item_name": "빨강 장지갑"},
        {"id": 3, "item_name": "검정 삼성 휴대폰"},
        {"id": 4, "item_name": "검정 카드"},
    ]
    assert set(keyword_scores(items, "검정 가죽 지갑")) == {1, 2}
    assert 3 in keyword_scores(items, "휴대푠")


def test_reviewed_color_and_brand_aliases():
    assert 1 in keyword_scores([{"id": 1, "item_name": "검정 MLB 모자"}], "검은색 엠엘비 모자")


def test_expensive_fuzzy_is_bounded_and_does_not_read_description():
    items = [{"id": i, "item_name": "지갑", "description": "긴 설명" * 100} for i in range(1000)]
    with patch("retriever_lost_found.search.hybrid.fuzzy_item_score", return_value=1) as fuzzy:
        keyword_scores(items, "지갑")
    assert fuzzy.call_count <= 200
    assert all("description" not in call.args[1] for call in fuzzy.call_args_list)


def test_fusion_includes_semantic_only_and_boosts_shared_match():
    results, count, status = search_found_items(FakeItems(), "지갑", [7], FakeEmbedding())
    assert count == 3 and status == "ok"
    assert [r["id"] for r in results] == [1, 2]
    assert results[0]["match_score"] > results[1]["match_score"]
    assert "match_score" not in ITEMS[0]


def test_keyword_survives_embedding_failure():
    class Broken:
        def embed(self, query, **kwargs):
            raise RuntimeError("do not expose upstream body")

    results, _, status = search_found_items(FakeItems(), "지갑", [7], Broken())
    assert status == "unavailable" and [r["id"] for r in results] == [1]
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
        assert status == "unavailable" and rows[0]["id"] == 1
        assert not finished.is_set()  # executor 종료를 기다리지 않고 이미 응답했다.
    finally:
        release.set()
        assert finished.wait(2)


def test_details_are_fetched_only_for_matches_and_scope_is_checked_again():
    class Items(FakeItems):
        def fetch_search_candidates(self, ids):
            return ITEMS

        def fetch_item_details(self, ids, item_ids):
            assert ids == [7] and set(item_ids) == {1, 2}
            return [{**ITEMS[0], "description": "상세 보존"},
                    {**ITEMS[1], "storage_location_id": 999}]

    rows, count, _ = search_found_items(Items(), "지갑", [7], FakeEmbedding())
    assert count == 3
    assert len(rows) == 1 and rows[0]["description"] == "상세 보존"


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
    query = parse_qs(urlparse(call.call_args.args[0].full_url).query)
    assert query["id"] == ["in.(1,2)"] and query["storage_location_id"] == ["in.(7)"]


def test_searches_start_in_parallel():
    barrier = threading.Barrier(2, timeout=2)

    class Items(FakeItems):
        def fetch_items(self, ids):
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
    assert [r["id"] for r in response.json()["items"]] == [1, 2]
    assert "embedding" not in response.text


def test_semantic_rpc_contains_scope_and_only_returns_scores():
    with patch("retriever_lost_found.integrations.supabase._request_json", return_value=[{"item_id": 1, "similarity": 0.8}]) as call:
        result = SupabaseFoundItemClient("https://test.supabase.co", "test-key").semantic_scores([7], [1.0] * 1024)
    assert result == {1: 0.8}
    assert json.loads(call.call_args.args[0].data)["p_location_ids"] == [7]


def test_keyword_survives_semantic_rpc_timeout():
    class Items(FakeItems):
        def semantic_scores(self, ids, vector, **kwargs):
            return SupabaseFoundItemClient("https://test.supabase.co", "test-key").semantic_scores(ids, vector)
    with patch("retriever_lost_found.integrations.supabase._request_json", side_effect=TimeoutError):
        rows, _, status = search_found_items(Items(), "지갑", [7], FakeEmbedding())
    assert status == "unavailable" and [r["id"] for r in rows] == [1]


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
