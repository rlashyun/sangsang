import json
from datetime import date
from unittest.mock import Mock, call

import pytest
from fastapi.testclient import TestClient

from retriever_lost_found.integrations.supabase import SupabaseFoundItemClient
from retriever_lost_found.search.hybrid import search_found_items
from retriever_lost_found.web.agent_api import create_agent_router
from retriever_lost_found.web.agent_dates import resolve_lost_on
from retriever_lost_found.web.agent_search import Institution, PlaceCandidate, SearchPage
from retriever_lost_found.web.app import create_app


@pytest.mark.parametrize(("expression", "expected"), [
    ("오늘", "2026-11-26"),
    ("어제", "2026-11-25"),
    ("저번주", "2026-11-16"),
    ("지난 주", "2026-11-16"),
    ("저번 주 목요일", "2026-11-19"),
    ("11월 25일", "2026-11-25"),
    ("2026-11-25", "2026-11-25"),
])
def test_resolve_lost_on(expression, expected):
    assert resolve_lost_on(expression, date(2026, 11, 26)) == expected


def test_resolve_dates_across_years_and_reject_future_or_invalid_dates():
    assert resolve_lost_on("저번주", date(2026, 1, 1)) == "2025-12-22"
    assert resolve_lost_on("12월 31일", date(2026, 1, 1)) == "2025-12-31"
    assert resolve_lost_on("24일", date(2026, 11, 26), "2026-10-25") == "2026-10-24"
    for expression in ("2026-11-27", "2026년 2월 30일", "언젠가"):
        with pytest.raises(ValueError):
            resolve_lost_on(expression, date(2026, 11, 26))


def test_date_survives_scope_expansion_paging_and_correction(monkeypatch):
    monkeypatch.setattr("retriever_lost_found.web.agent_conversation.korea_today", lambda: date(2026, 11, 26))
    base = {"action": "search", "message": "", "place_query": None, "item_query": None, "clear_item_query": False,
            "asks_for": None, "item_changes": None, "target": None}
    model = Mock(side_effect=[
        {**base, "place_query": "강남역"},
        {**base, "item_query": "검은 반지갑", "lost_date_text": "저번 주"},
        {"query": None},
        {**base, "lost_date_text": "저번 주 목요일"},
        {**base, "clear_lost_on": True},
    ])
    item = {"item_id": 7, "institution_id": 99}
    second = {"item_id": 8, "institution_id": 99}
    search = Mock(side_effect=[SearchPage((), 0), SearchPage((item,), 2, "next"),
                               SearchPage((second,), 2), SearchPage((item,), 1), SearchPage((item,), 1)])
    router = create_agent_router(
        model=model, find_places=lambda _: [PlaceCandidate("p", "강남역", "", 37.5, 127.03)],
        get_institutions=lambda: [Institution(42, 37.5, 127.03), Institution(99, 37.511, 127.03)],
        search_items=search,
    )
    client = TestClient(create_app(Mock(), "", agent_router=router))

    def turn(request_id, preceding=None, **body):
        if preceding:
            body.update(session_id=preceding["session_id"], revision=preceding["revision"])
        response = client.post("/api/search-chat/turns", json={**body, "request_id": request_id})
        assert response.status_code == 200
        return json.loads(response.text.splitlines()[-1])["response"]

    start = turn("start", type="message", text="강남역")
    place = turn("select", start, type="select_place", candidate_id="p")
    assert place["conditions"]["lost_on"] is None
    search.assert_not_called()

    result = turn("item", place, type="message", text="저번 주에 검은 반지갑을 잃어버렸어요.")
    assert result["conditions"]["lost_on"] == "2026-11-16"
    assert result["conditions"]["radius_m"] == 1500
    page = client.post("/api/search-chat/pages", json={
        "session_id": result["session_id"], "revision": result["revision"], "cursor": "next",
    })
    assert page.json()["matches"]["items"] == [second]
    corrected = turn("correct", result, type="message", text="저번 주 목요일이었어요.")
    assert corrected["conditions"]["lost_on"] == "2026-11-19"
    cleared = turn("clear", corrected, type="message", text="날짜 조건은 빼주세요.")
    assert cleared["conditions"]["lost_on"] is None
    assert search.call_args_list == [
        call((42,), "검은 반지갑", None, lost_on="2026-11-16"),
        call((42, 99), "검은 반지갑", None, lost_on="2026-11-16"),
        call((42, 99), "검은 반지갑", "next", lost_on="2026-11-16"),
        call((42, 99), "검은 반지갑", None, lost_on="2026-11-19"),
        call((42, 99), "검은 반지갑", None),
    ]


def test_hybrid_passes_the_same_date_to_both_rpc_calls():
    client = SupabaseFoundItemClient("http://localhost", "dummy")
    client._search_rpc = Mock(side_effect=lambda name, *_: [] if name.startswith("semantic") else
                              {"items": [], "candidate_count": None})
    embedding = Mock()
    embedding.embed.return_value = [0.5] * 1024
    search_found_items(client, "지갑", [42], embedding, lost_on="2026-11-25")
    calls = {entry.args[0]: entry.args[1] for entry in client._search_rpc.call_args_list}
    assert set(calls) == {"keyword_found_item_candidates", "semantic_found_item_candidates_gemini2"}
    assert all(params["p_lost_on"] == "2026-11-25" and params["p_location_ids"] == [42]
               for params in calls.values())
    assert calls["keyword_found_item_candidates"]["p_word_terms"] == []
