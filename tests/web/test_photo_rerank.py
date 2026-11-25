import base64
import json
from io import BytesIO
from unittest.mock import Mock

from fastapi.testclient import TestClient
from PIL import Image

from retriever_lost_found.search.photo_rerank import JPEG_PREFIX, PHOTO_BATCH_SIZE, rerank_photos
from retriever_lost_found.web.agent_api import create_agent_router
from retriever_lost_found.web.agent_search import Institution
from retriever_lost_found.web.agent_store import MemoryAgentStore
from retriever_lost_found.web.app import create_app, photo_ranked_item_page

THUMBNAIL_URL = "https://project.supabase.co/storage/v1/object/public/found-item-thumbnails/21/test.jpg"


def test_user_and_candidate_content_rerank_only_top_100_and_keep_page_order():
    class FoundItems:
        def fetch_items(self, locations):
            assert locations == [42]
            return [{"id": i, "storage_location_id": 42, "item_name": "지갑"} for i in range(1, 121)]

        def fetch_photo_candidates(self, locations, ids):
            assert locations == [42] and set(ids) == set(range(21, 121))
            return [
                {"id": 21, "storage_location_id": 42, "embedding_input": "보라 지갑",
                 "image_url": "https://minwon24.police.go.kr/photo.jpg", "thumbnail_url": THUMBNAIL_URL},
                {"id": 20, "storage_location_id": 42, "thumbnail_url": THUMBNAIL_URL},
                {"id": 22, "storage_location_id": 42, "image_url": "https://minwon24.police.go.kr/img02_no_img.gif"},
                {"id": 23, "storage_location_id": 99, "thumbnail_url": THUMBNAIL_URL},
                {"id": 24, "storage_location_id": 42, "image_url": "https://minwon24.police.go.kr/original.jpg"},
            ]

        def fetch_item_details(self, locations, ids):
            return [row for row in self.fetch_items(locations) if row["id"] in ids]

        def add_thumbnails(self, rows):
            return rows

    def compare(_, content, schema):
        assert json.loads(content[0]["text"]) == {"user_text": "보라색 지갑을 찾아줘"}
        assert content[1]["image_url"]["url"].startswith(JPEG_PREFIX)
        assert json.loads(content[2]["text"]) == {"id": "21", "embedding_input": "보라 지갑"}
        assert content[3]["image_url"]["url"] == THUMBNAIL_URL
        assert len(content) == 4
        return {"results": [{"id": "21", "label": "same"}]}

    verdict = Mock(side_effect=compare)
    model = Mock(return_value={"action": "search", "message": "", "place_query": None, "item_query": "지갑",
                              "clear_item_query": False, "asks_for": None, "item_changes": None, "target": None})
    photo_search = Mock(side_effect=lambda ids, query, text, photos: photo_ranked_item_page(
        FoundItems(), None, verdict, ids, query, text, photos,
    ))
    plain_search = Mock()
    store = MemoryAgentStore()
    router = create_agent_router(
        store=store, model=model, find_places=lambda _: [], get_institutions=lambda: [Institution(42, 37.5, 127.03)],
        search_items=plain_search, search_photo_items=photo_search,
        find_items_by_id=lambda _, ids: [{"item_id": i, "institution_id": 42} for i in sorted(ids)],
    )
    client = TestClient(create_app(Mock(), "dummy", agent_router=router))
    point = {"type": "select_point", "request_id": "photo-point", "latitude": 37.5, "longitude": 127.03}
    session = json.loads(client.post("/api/search-chat/turns", json=point).text.splitlines()[-1])["response"]
    image = BytesIO()
    Image.new("RGB", (16, 16)).save(image, "JPEG")
    photo = JPEG_PREFIX + base64.b64encode(image.getvalue()).decode("ascii")
    request = {"type": "message", "text": "보라색 지갑을 찾아줘", "photos": [photo], "request_id": "photo-search",
               "session_id": session["session_id"], "revision": session["revision"]}
    assert client.post("/api/search-chat/turns", json={**request, "photos": [JPEG_PREFIX + "broken"]}).status_code == 400
    photo_search.assert_not_called()

    result = json.loads(client.post("/api/search-chat/turns", json=request).text.splitlines()[-1])["response"]
    assert result["photo_status"] == "applied"
    assert result["matches"]["total_count"] == 100
    assert result["matches"]["next_cursor"] is None  # 2026-11-25 — 상위 100건을 한 번에 보낸다
    assert len(result["matches"]["items"]) == 100
    assert result["matches"]["items"][0]["item_id"] == 21
    assert "embedding_input" not in result["matches"]["items"][0]
    assert photo not in json.dumps(store.command(session["session_id"], "get")["data"])
    assert json.loads(client.post("/api/search-chat/turns", json=request).text.splitlines()[-1])["response"] == result

    assert [i["item_id"] for i in result["matches"]["items"]] == [21, *range(120, 21, -1)]
    plain_search.assert_not_called()
    verdict.assert_called_once()


def test_photo_rerank_applies_penalties_and_preserves_order_when_photo_comparison_fails():
    ranked = [{"id": i, "match_score": score} for i, score in [(3, .57), (2, .50), (1, .485)]]
    candidates = [{"id": i, "storage_location_id": 42, "thumbnail_url": THUMBNAIL_URL} for i in (3, 2)]
    fetch = Mock(return_value=candidates)
    compare = Mock(return_value={"results": [{"id": "3", "label": "very_different"}, {"id": "2", "label": "different"}]})
    items, status = rerank_photos(ranked, [42], "지갑", ("photo",), fetch, compare)
    assert [row["id"] for row in items] == [1, 3, 2] and status == "applied"

    compare.side_effect = RuntimeError
    assert rerank_photos(ranked, [42], "지갑", ("photo",), fetch, compare) == (ranked, "unavailable")
    fetch.return_value = []
    compare.reset_mock()
    assert rerank_photos(ranked, [42], "지갑", ("photo",), fetch, compare) == (ranked, "no_photos")
    compare.assert_not_called()

    fetch.side_effect = RuntimeError("thumbnail lookup failed")
    assert rerank_photos(ranked, [42], "지갑", ("photo",), fetch, compare) == (ranked, "unavailable")
    compare.assert_not_called()


def test_photo_rerank_keeps_successful_batch_when_another_batch_fails():
    last_id = PHOTO_BATCH_SIZE + 1
    ids = range(1, last_id + 1)
    scores = [.50, .528, .545, .55, .56, .58]
    ranked = [{"id": i, "match_score": scores[i - 1]} for i in reversed(ids)]
    candidates = [{"id": i, "storage_location_id": 42, "thumbnail_url": THUMBNAIL_URL} for i in ids]

    def compare(_, content, schema):
        batch_ids = [json.loads(part["text"])["id"] for part in content[2::2]]
        if str(last_id) in batch_ids:
            raise RuntimeError("provider timeout")
        labels = {"1": "same", "2": "similar"}
        return {"results": [{"id": item_id, "label": labels.get(item_id, "unsure")} for item_id in batch_ids]}

    items, status = rerank_photos(ranked, [42], "지갑", ("photo",), Mock(return_value=candidates), compare)

    assert status == "applied"
    assert [row["id"] for row in items] == [1, last_id, 2, 5, 4, 3]


def test_photo_rerank_keeps_valid_unique_verdicts_from_an_incomplete_batch():
    ranked = [{"id": i, "match_score": .5 + i * .001} for i in (5, 4, 3, 2, 1)]
    candidates = [{"id": row["id"], "storage_location_id": 42, "thumbnail_url": THUMBNAIL_URL} for row in ranked]
    compare = Mock(return_value={"results": [
        {"id": "1", "label": "same"},
        {"id": "2", "label": "same"},
        {"id": "2", "label": "very_different"},
        {"id": "4", "label": "unrecognized"},
        {"id": "100", "label": "same"},
    ]})

    items, status = rerank_photos(ranked, [42], "지갑", ("photo",), Mock(return_value=candidates), compare)

    assert status == "applied"
    assert [row["id"] for row in items] == [1, 5, 4, 3, 2]
