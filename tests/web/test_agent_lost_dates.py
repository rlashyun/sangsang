import json
from datetime import date
from unittest.mock import Mock, call

import pytest
from fastapi.testclient import TestClient

from retriever_lost_found.integrations.supabase import SupabaseFoundItemClient
from retriever_lost_found.search.hybrid import search_found_items
from retriever_lost_found.web.agent_api import create_agent_router
from retriever_lost_found.web.agent_dates import FutureDateError, resolve_lost_on
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


@pytest.mark.parametrize("skip_date", [False, True])
def test_invalid_date_keeps_item_for_followup_and_stale_date_is_ignored(monkeypatch, skip_date):
    monkeypatch.setattr("retriever_lost_found.web.agent_conversation.korea_today", lambda: date(2026, 11, 26))
    base = {"action": "search", "message": "", "place_query": None, "item_query": None,
            "clear_item_query": False, "asks_for": None, "item_changes": None, "target": None}
    model = Mock(side_effect=[
        {**base, "item_query": "검은 가방", "lost_date_text": "어제"},
        {**base, "item_query": "검은 반지갑", "lost_date_text": "내일"},
        {**base, **({"clear_lost_on": True} if skip_date else {"lost_date_text": "오늘"})},
        {**base, "item_query": "검은 지갑", "lost_date_text": "내일"},
    ])
    item = {"item_id": 7, "institution_id": 42}
    search = Mock(return_value=SearchPage((item,), 1))
    client = TestClient(create_app(Mock(), "", agent_router=create_agent_router(
        model=model, find_places=Mock(),
        get_institutions=lambda: [Institution(42, 37.5, 127.03)], search_items=search,
    )))

    def turn(request_id, preceding=None, **body):
        if preceding:
            body.update(session_id=preceding["session_id"], revision=preceding["revision"])
        response = client.post("/api/search-chat/turns", json={**body, "request_id": request_id})
        assert response.status_code == 200
        return json.loads(response.text.splitlines()[-1])["response"]

    place = turn("place", type="select_point", latitude=37.5, longitude=127.03, name="강남역")
    first = turn("initial", place, type="message", text="어제 검은 가방을 잃어버렸어")
    assert first["status"] == "results"
    invalid = turn("invalid", first, type="message", text="검은 반지갑을 내일 잃어버렸어")
    assert invalid["conditions"]["item_query"] == "검은 반지갑"
    assert invalid["conditions"]["lost_on"] == "2026-11-25"
    assert "11월 27일은 아직 오지 않은 날짜예요." in invalid["message"]
    assert invalid["matches"] is None
    search.assert_called_once_with((42,), "검은 가방", None, lost_on="2026-11-25")

    corrected = turn("corrected", invalid, type="message",
                     text="날짜 없이 찾아줘" if skip_date else "오늘 잃어버렸어")
    assert model.call_args.args[1]["awaiting_answer"] == "date"
    assert model.call_args.args[1]["item_query"] == "검은 반지갑"
    assert corrected["status"] == "results"
    assert corrected["conditions"]["item_query"] == "검은 반지갑"
    expected_date = None if skip_date else "2026-11-26"
    assert corrected["conditions"]["lost_on"] == expected_date

    restated = turn("item-only", corrected, type="message", text="검은 지갑이라고 했잖아")
    assert restated["status"] == "results"
    assert restated["conditions"]["item_query"] == "검은 지갑"
    assert restated["conditions"]["lost_on"] == expected_date
    date_filter = {"lost_on": expected_date} if expected_date else {}
    assert search.call_args_list[1:] == [
        call((42,), "검은 반지갑", None, **date_filter),
        call((42,), "검은 지갑", None, **date_filter),
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


@pytest.mark.parametrize(("expression", "future"), [
    ("11월 29일", "2026-11-29"),
    ("11/29", "2026-11-29"),
    ("2026년 11월 29일", "2026-11-29"),
    ("27일", "2026-11-27"),
    ("내일", "2026-11-27"),
    ("모레", "2026-11-28"),
    ("글피", "2026-11-29"),
])
def test_future_loss_date_is_reported_instead_of_moving_to_last_year(expression, future):
    with pytest.raises(FutureDateError) as raised:
        resolve_lost_on(expression, date(2026, 11, 26))
    assert raised.value.lost_on.isoformat() == future


def test_year_is_rolled_back_only_inside_the_retention_window():
    # PRD 3.1-2 — 보존기간(6개월) 안으로 돌아갈 때만 작년 날짜로 읽는다.
    assert resolve_lost_on("7월 1일", date(2026, 1, 1)) == "2025-07-01"
    with pytest.raises(FutureDateError):
        resolve_lost_on("6월 30일", date(2026, 1, 1))


def _dated_chat(monkeypatch, lost_date_text, latest):
    monkeypatch.setattr("retriever_lost_found.web.agent_conversation.korea_today", lambda: date(2026, 11, 26))
    base = {"action": "search", "message": "", "place_query": None, "item_query": None, "clear_item_query": False,
            "asks_for": None, "item_changes": None, "target": None}
    model = Mock(side_effect=[
        {**base, "place_query": "강남역"},
        {**base, "message": "분실일을 반영해 검색할게요.", "item_query": "검은 반지갑", "lost_date_text": lost_date_text},
        {"query": None},
    ])
    search = Mock(return_value=SearchPage(({"item_id": 7, "institution_id": 42},), 1))
    router = create_agent_router(
        model=model, find_places=lambda _: [PlaceCandidate("p", "강남역", "", 37.5, 127.03)],
        get_institutions=lambda: [Institution(42, 37.5, 127.03), Institution(99, 37.511, 127.03)],
        search_items=search, latest_registered_on=latest,
    )
    client = TestClient(create_app(Mock(), "", agent_router=router))
    preceding = {}
    for request_id, body in (
        ("start", {"type": "message", "text": "강남역"}),
        ("select", {"type": "select_place", "candidate_id": "p"}),
        ("item", {"type": "message", "text": f"{lost_date_text}에 검은 반지갑을 잃어버렸어요."}),
    ):
        response = client.post("/api/search-chat/turns", json={**body, **preceding, "request_id": request_id})
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines()]
        result = events[-1]["response"]
        preceding = {"session_id": result["session_id"], "revision": result["revision"]}
    return result, search


def test_future_loss_date_asks_again_without_searching(monkeypatch):
    result, search = _dated_chat(monkeypatch, "11월 29일", Mock(return_value=date(2026, 11, 26)))
    assert result["message"] == (
        "11월 29일은 아직 오지 않은 날짜예요. 오늘(11월 26일)이나 그 전 날짜로 다시 알려주세요. "
        "날짜 없이도 찾을 수 있어요."
    )
    assert result["status"] == "awaiting_clarification"
    assert result["conditions"]["item_query"] == "검은 반지갑"
    assert result["conditions"]["lost_on"] is None
    search.assert_not_called()


def test_loss_date_after_latest_registration_skips_search_and_radius_expansion(monkeypatch):
    result, search = _dated_chat(monkeypatch, "11월 26일", Mock(return_value=date(2026, 11, 25)))
    search.assert_not_called()
    assert result["status"] == "no_matches"
    assert result["message"] == (
        "지금은 11월 25일에 등록된 습득물까지만 확인할 수 있어요. 11월 26일 이후에 등록된 물건은 아직 없어요. "
        "비슷한 물건이 새로 등록되면 메일로 알려드릴까요?"
    )
    assert result["latest_registered_on"] == "2026-11-25"
    assert result["matches"] == {"items": [], "total_count": 0, "next_cursor": None}
    assert "explanation" not in result
    assert result["conditions"]["lost_on"] == "2026-11-26"
    assert result["conditions"]["radius_m"] == 1000
    assert result["conditions"]["institution_ids"] == [42]


@pytest.mark.parametrize("latest", [
    Mock(return_value=date(2026, 11, 25)),
    Mock(return_value=None),
    Mock(side_effect=RuntimeError("down")),
])
def test_search_runs_when_the_date_has_data_or_latest_is_unknown(monkeypatch, latest):
    result, search = _dated_chat(monkeypatch, "11월 25일", latest)
    assert result["status"] == "results"
    assert "latest_registered_on" not in result
    search.assert_called_once_with((42,), "검은 반지갑", None, lost_on="2026-11-25")


@pytest.mark.parametrize(("rows", "expected"), [
    ({"partner_api": "2026-11-24", "police_api": "2026-11-25"}, date(2026, 11, 25)),
    ({}, None),
])
def test_latest_registered_on_takes_the_newer_source(monkeypatch, rows, expected):
    def fake_request(request, timeout, label):
        source = next((code for code in ("partner_api", "police_api") if code in request.full_url))
        return [{"registered_on": rows[source]}] if source in rows else []

    monkeypatch.setattr("retriever_lost_found.integrations.supabase._request_json", fake_request)
    assert SupabaseFoundItemClient("http://localhost", "dummy").latest_registered_on() == expected
