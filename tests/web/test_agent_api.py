import json
from io import BytesIO
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from retriever_lost_found.integrations.agent_model import OpenRouterAgentModel
from retriever_lost_found.web.agent_api import create_agent_router
from retriever_lost_found.web.agent_search import Institution, PlaceCandidate, SearchPage
from retriever_lost_found.web.app import create_app


def test_chat_api_confirms_place_retries_and_pages_without_llm():
    first = {"item_id": 1, "institution_id": 42}
    second = {"item_id": 2, "institution_id": 42}
    model = Mock(side_effect=[
        {"action": "search", "message": "확인할게요.", "place_query": "강남역", "item_query": "갈색 지갑"},
        {"query": "브라운 지갑"},
        {"question": None},
    ])
    search = Mock(side_effect=[SearchPage((), 0), SearchPage((first,), 2, "next"), SearchPage((second,), 2)])
    router = create_agent_router(
        model=model, find_places=lambda _: [PlaceCandidate("p1", "강남역", "", 37.5, 127.03)],
        get_institutions=lambda: [Institution(42, 37.5, 127.03)], search_items=search,
    )
    client = TestClient(create_app(Mock(), "dummy", agent_router=router))
    response = client.post("/api/search-chat/turns", json={"type": "message", "request_id": "start", "text": "갈색 지갑"})
    assert response.headers["content-type"].startswith("application/x-ndjson")
    place_events = [json.loads(line) for line in response.text.splitlines()]
    candidate = place_events[-1]["response"]
    assert [line["type"] for line in place_events] == ["run.started", "tool.started", "tool.completed", "run.finished"]
    assert candidate["status"] == "awaiting_place_selection"
    search.assert_not_called()

    select = {
        "type": "select_place", "request_id": "select", "session_id": candidate["session_id"],
        "revision": candidate["revision"], "candidate_id": "p1",
    }
    response = client.post("/api/search-chat/turns", json=select)
    result = json.loads(response.text.splitlines()[-1])["response"]
    assert result["recommended_item_ids"] == [1]
    assert result["matches"]["next_cursor"] == "next"
    assert client.post("/api/search-chat/turns", json=select).status_code == 409
    page_params = {"session_id": result["session_id"], "revision": result["revision"], "cursor": "next"}
    response = client.post("/api/search-chat/pages", json=page_params)
    assert response.json()["matches"]["items"] == [second]
    assert client.post("/api/search-chat/pages", json=page_params).status_code == 409
    assert model.call_count == 3
    assert [call.args for call in search.call_args_list] == [
        ((42,), "갈색 지갑", None), ((42,), "브라운 지갑", None), ((42,), "브라운 지갑", "next"),
    ]


def test_chat_api_confirms_map_point_before_and_after_item():
    # 지도 더블클릭으로 고른 지점: 물품 없이 먼저 고르면 물품을 묻고, 이어서 물품을 말하면 검색한다.
    model = Mock(side_effect=[
        {"action": "search", "message": "", "place_query": None, "item_query": "갈색 지갑"},
        {"question": None},
    ])
    item = {"item_id": 1, "institution_id": 42}
    search = Mock(return_value=SearchPage((item,), 1))
    router = create_agent_router(
        model=model, find_places=lambda _: [],
        get_institutions=lambda: [Institution(42, 37.5, 127.03)], search_items=search,
    )
    client = TestClient(create_app(Mock(), "dummy", agent_router=router))
    point = {"type": "select_point", "request_id": "pt", "latitude": 37.5, "longitude": 127.03}
    first = json.loads(client.post("/api/search-chat/turns", json=point).text.splitlines()[-1])["response"]
    assert first["status"] == "awaiting_clarification"
    assert first["conditions"]["place"]["latitude"] == 37.5
    search.assert_not_called()

    reply = {"type": "message", "request_id": "item", "text": "갈색 지갑",
             "session_id": first["session_id"], "revision": first["revision"]}
    done = json.loads(client.post("/api/search-chat/turns", json=reply).text.splitlines()[-1])["response"]
    assert done["matches"]["total_count"] == 1
    assert search.call_count == 1

    outside = {**point, "request_id": "far", "latitude": 10.0}
    assert client.post("/api/search-chat/turns", json=outside).status_code == 400


def test_luna_openrouter_request():
    model = OpenRouterAgentModel("dummy")
    with patch("retriever_lost_found.integrations.agent_model.build_opener") as opener:
        opener.return_value.open.return_value = BytesIO(json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": '{"question":null}'}}],
        }).encode())
        assert model("instructions", {}, {"type": "object"}) == {"question": None}
        request = opener.return_value.open.call_args.args[0]
        assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
        payload = json.loads(request.data)
        assert payload["model"] == "openai/gpt-6-luna"
        assert payload["response_format"]["json_schema"]["strict"] is True


def test_runtime_agent_reuses_places_institutions_and_ranked_search(monkeypatch):
    from retriever_lost_found.web import app as web_app

    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-luna")
    monkeypatch.setenv("KAKAO_REST_API_KEY", "dummy")
    monkeypatch.setenv("KAKAO_JAVASCRIPT_KEY", "dummy")
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "dummy")
    search = Mock(return_value=([
        {"id": 1, "storage_location_id": 42, "item_name": "갈색 지갑", "match_score": 0.8}
    ], 1, "ok"))
    monkeypatch.setattr(web_app, "search_with_deadline", search)
    with (
        patch.object(web_app.KakaoLocalClient, "search_keyword", return_value={"documents": []}) as keyword,
        patch.object(web_app.KakaoLocalClient, "geocode_address", return_value={"documents": [
            {"address_name": "서울 강남구 예시로 1", "y": "37.5", "x": "127.03"},
        ]}) as address,
        patch.object(web_app.SupabaseMapLocationClient, "fetch_locations", return_value=[
            {"storage_location_id": 42, "latitude": 37.5, "longitude": 127.03},
            {"storage_location_id": 99, "latitude": 38.0, "longitude": 127.03},
        ]) as institutions,
        patch.object(OpenRouterAgentModel, "__call__", side_effect=[
            {"action": "search", "message": "확인할게요.", "place_query": "서울 강남구 예시로 1", "item_query": "갈색 지갑"},
            {"question": None},
        ]),
    ):
        client = TestClient(web_app.create_runtime_app(agent_search=True))
        assert 'data-agent-search="enabled"' in client.get("/").text
        response = client.post("/api/search-chat/turns", json={
            "type": "message", "request_id": "start", "text": "갈색 지갑을 잃어버렸어요",
        })
        candidate = json.loads(response.text.splitlines()[-1])["response"]
        assert candidate["status"] == "awaiting_place_selection"
        assert candidate["place_candidates"][0]["latitude"] == 37.5
        search.assert_not_called()
        response = client.post("/api/search-chat/turns", json={
            "type": "select_place", "request_id": "select", "session_id": candidate["session_id"],
            "revision": candidate["revision"], "candidate_id": candidate["place_candidates"][0]["candidate_id"],
        })
        result = json.loads(response.text.splitlines()[-1])["response"]
        assert result["matches"]["items"] == [
            {"id": 1, "storage_location_id": 42, "item_id": 1, "institution_id": 42, "item_name": "갈색 지갑"}
        ]
        assert search.call_args.args[1:3] == ("갈색 지갑", [42])
        institutions.assert_called_once_with(with_counts=False)
        keyword.assert_called_once()
        address.assert_called_once()

    default_client = TestClient(web_app.create_runtime_app())
    assert 'data-agent-search="enabled"' not in default_client.get("/").text
    assert default_client.post("/api/search-chat/turns", json={}).status_code == 404
