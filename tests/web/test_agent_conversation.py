import json
from io import BytesIO
from unittest.mock import Mock, patch

import pytest

from retriever_lost_found.integrations.agent_model import OpenRouterAgentModel
from retriever_lost_found.web.agent_conversation import Conversation, confirm_place, respond_to_message
from retriever_lost_found.web.agent_search import Institution, PlaceCandidate, SearchPage, search_confirmed_place


def decision(action, *, place=None, item=None, message="알겠습니다."):
    return {"action": action, "message": message, "place_query": place, "item_query": item}


def test_conversation_retains_place_and_applies_item_correction():
    place = PlaceCandidate("p1", "강남역", "서울 강남구", 37.5, 127.03)
    find_places = Mock(return_value=[place])
    model = Mock(side_effect=[
        decision("clarify", place="강남역", message="어떤 물품인가요?"),
        decision("search", item="검은 지갑", message=""),
        decision("search", item="갈색 카드지갑"),
    ])
    first = respond_to_message(Conversation(), "강남역에서 잃어버렸어요", model=model, find_places=find_places)
    # 장소만 말해도 후보를 바로 조회해 지도에 보여 주고, 물품은 이어서 묻는다.
    assert first.status == "awaiting_place_selection"
    assert first.message == "지도에 표시한 장소가 맞나요? 어떤 물품인가요?"
    assert first.state.candidates == (place,)
    find_places.assert_called_once_with("강남역")

    second = respond_to_message(first.state, "검은 지갑이요", model=model, find_places=find_places)
    assert second.status == "awaiting_place_selection"
    assert second.pending is None
    with pytest.raises(ValueError):
        confirm_place(second.state, "stale")
    confirmed, _ = confirm_place(second.state, "p1")

    edited = respond_to_message(confirmed, "갈색 카드지갑이었어요", model=model, find_places=find_places)
    assert edited.status == "ready_to_search"
    context = model.call_args.args[1]
    assert context["confirmed_place"] == "강남역"
    assert context["item_query"] == "검은 지갑"
    assert context["recent"][-1][0] == "검은 지갑이요"
    find_places.assert_called_once_with("강남역")

    search = Mock(return_value=SearchPage(({"item_id": 7, "institution_id": 42},), 1))
    events = list(search_confirmed_place(
        edited.pending, confirmed.confirmed.candidate_id,
        get_institutions=lambda: [Institution(42, 37.5, 127.03)], search_items=search, rewrite_query=Mock(),
    ))
    search.assert_called_once_with((42,), "갈색 카드지갑")
    assert events[-1]["recommended_item_ids"] == [7]


def test_place_can_be_confirmed_before_the_item_is_known():
    place = PlaceCandidate("p1", "부산역", "", 35.11, 129.04)
    model = Mock(side_effect=[decision("clarify", place="부산역", message="어떤 물품인가요?"), decision("search", item="검은 지갑")])
    first = respond_to_message(Conversation(), "부산역", model=model, find_places=Mock(return_value=[place]))
    confirmed, pending = confirm_place(first.state, "p1")
    assert pending is None and confirmed.confirmed == place
    # 물품을 말하면 이미 확정한 장소로 바로 검색할 수 있다.
    turn = respond_to_message(confirmed, "검은 지갑", model=model, find_places=Mock())
    assert turn.status == "ready_to_search" and turn.pending.query == "검은 지갑"


def test_changed_place_requires_confirmation_and_old_candidate_cannot_be_selected():
    old_place = PlaceCandidate("old", "강남역", "", 37.5, 127.03)
    new_place = PlaceCandidate("new", "역삼역", "", 37.5, 127.04)
    confirmed = Conversation("강남역", "검은 지갑", confirmed=old_place)
    turn = respond_to_message(
        confirmed, "역삼역이었고 갈색이야",
        model=Mock(return_value=decision("search", place="역삼역", item="갈색 지갑")),
        find_places=Mock(return_value=[new_place]),
    )
    assert turn.status == "awaiting_place_selection"
    assert turn.state.confirmed is None
    assert turn.state.item_query == "갈색 지갑"
    with pytest.raises(ValueError):
        confirm_place(turn.state, "old")


def test_invalid_model_decision_runs_no_lookup():
    find_places = Mock()
    with pytest.raises(RuntimeError, match="해석하지"):
        respond_to_message(
            Conversation(), "지갑을 잃어버렸어요",
            model=Mock(return_value={**decision("search", place="강남역", item="지갑"), "latitude": 1}),
            find_places=find_places,
        )
    find_places.assert_not_called()


def test_provider_requests_structured_output_and_rejects_truncated_response():
    model = OpenRouterAgentModel("dummy", "test/model", timeout=2, max_tokens=120)
    reply = decision("clarify", message="장소를 알려주세요.")
    with patch("retriever_lost_found.integrations.agent_model.build_opener") as build:
        opener = build.return_value
        opener.open.side_effect = [
            BytesIO(json.dumps({"choices": [
                {"finish_reason": reason, "message": {"content": json.dumps(reply)}}
            ]}).encode())
            for reason in ("stop", "length")
        ]
        assert model("instructions", {"message": "갈색 지갑"}, {"type": "object"}) == reply
        payload = json.loads(opener.open.call_args.args[0].data)
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["max_tokens"] == 120
        assert opener.open.call_args.kwargs["timeout"] == 2
        with pytest.raises(RuntimeError, match="생성하지"):
            model("instructions", {}, {})


def test_model_can_rewrite_after_zero_and_ask_about_actual_results():
    from retriever_lost_found.web.agent_conversation import search_conversation

    place = PlaceCandidate("p1", "강남역", "", 37.5, 127.03)
    state = Conversation("강남역", "갈색 지갑", confirmed=place)
    rows = tuple(
        {"item_id": item_id, "institution_id": 42, "item_name": name}
        for item_id, name in enumerate(("갈색 반지갑", "갈색 장지갑", "갈색 카드지갑", "갈색 동전지갑"), 1)
    )
    search = Mock(side_effect=[SearchPage((), 0), SearchPage(rows, 4)])
    model = Mock(side_effect=[{"query": "브라운 지갑"}, {"question": "카드지갑처럼 얇았나요?"}])
    result = list(search_conversation(
        state, model=model, get_institutions=lambda: [Institution(42, 37.5, 127.03)], search_items=search,
    ))[-1]
    assert [call.args for call in search.call_args_list] == [((42,), "갈색 지갑"), ((42,), "브라운 지갑")]
    assert model.call_args_list[0].args[1]["match_count"] == 0
    assert model.call_args.args[1]["top_items"] == [{"item_name": row["item_name"]} for row in rows[:3]]
    assert result["recommended_item_ids"] == [1, 2, 3]
    assert result["message"].endswith("카드지갑처럼 얇았나요?")
    assert result["state"].last_search_message == result["message"]


def test_successful_search_survives_failed_follow_up_without_retry():
    from retriever_lost_found.web.agent_conversation import search_conversation

    place = PlaceCandidate("p1", "강남역", "", 37.5, 127.03)
    state = Conversation("강남역", "갈색 지갑", confirmed=place)
    search = Mock(return_value=SearchPage(({"item_id": 7, "institution_id": 42},), 1))
    model = Mock(side_effect=RuntimeError("model unavailable"))
    result = list(search_conversation(
        state, model=model, get_institutions=lambda: [Institution(42, 37.5, 127.03)], search_items=search,
    ))[-1]
    search.assert_called_once_with((42,), "갈색 지갑")
    assert model.call_count == 1
    assert result["status"] == "results"
    assert result["recommended_item_ids"] == [7]


def test_invalid_rewrite_keeps_zero_match_response():
    from retriever_lost_found.web.agent_conversation import search_conversation

    place = PlaceCandidate("p1", "강남역", "", 37.5, 127.03)
    state = Conversation("강남역", "갈색 지갑", confirmed=place)
    search = Mock(return_value=SearchPage((), 0))
    model = Mock(side_effect=[{"query": "가" * 31}, {"question": None}])
    result = list(search_conversation(
        state, model=model, get_institutions=lambda: [Institution(42, 37.5, 127.03)], search_items=search,
    ))[-1]
    search.assert_called_once_with((42,), "갈색 지갑")
    assert result["status"] == "no_matches"
    assert result["matches"]["total_count"] == 0
