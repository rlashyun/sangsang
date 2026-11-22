"""PRD 4.3-A · 4.3-E — Intent decisions preserve scope and draft isolation."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from retriever_lost_found.web.agent_api import create_agent_router
from retriever_lost_found.web.agent_conversation import Conversation, DialogueContext, respond_to_message
from retriever_lost_found.web.agent_search import Institution, PlaceCandidate, SearchPage
from retriever_lost_found.web.app import create_app


def decision(action, **updates):
    return {"action": action, "message": "어떤 물건으로 바꿀까요?", "place_query": None,
            "item_query": None, "clear_item_query": False, "asks_for": None,
            "item_changes": None, "target": None, **updates}


def context():
    return {"task": "alert", "alert": {"query": "검은 지갑", "radius": 1000,
            "places": [{"name": "서울역", "latitude": 37.55, "longitude": 126.97}]},
            "awaiting": "email", "last_message": "개인정보 이용에 동의하고 이메일을 입력해 주세요."}


def test_question_keeps_old_item_and_following_answer_can_replace_it():
    place = PlaceCandidate("p", "서울역", "서울", 37.55, 126.97)
    original = Conversation("서울역", "검은 지갑", confirmed=place)
    model = Mock(side_effect=[decision("clarify", asks_for="item"), decision("search", item_query="우산")])
    lookup = Mock()
    question = respond_to_message(original, "물건 바꿀게", model=model, find_places=lookup)
    assert question.status == "awaiting_clarification"
    assert question.pending is None
    assert question.state.item_query == "검은 지갑"
    assert question.state.awaiting_answer == "item"
    answer = respond_to_message(question.state, "우산", model=model, find_places=lookup)
    assert answer.pending.query == "우산"
    assert model.call_args.args[1]["awaiting_answer"] == "item"
    assert answer.state.confirmed == place
    lookup.assert_not_called()


def client_with_search():
    model = Mock(return_value=decision("search", item_query="검은 지갑"))
    search = Mock(return_value=SearchPage(({"item_id": 1, "institution_id": 42, "item_name": "검은 지갑"},), 1, "next"))
    institutions = Mock(return_value=[Institution(42, 37.55, 126.97)])
    places = Mock(return_value=[])
    client = TestClient(create_app(Mock(), "dummy", agent_router=create_agent_router(
        model=model, search_items=search, get_institutions=institutions, find_places=places)))
    def send(payload):
        response = client.post("/api/search-chat/turns", json=payload)
        assert response.status_code == 200
        return json.loads(response.text.splitlines()[-1])["response"]
    selected = send({"type": "select_point", "request_id": "point", "latitude": 37.55, "longitude": 126.97, "name": "서울역"})
    result = send({"type": "message", "request_id": "search", "text": "검은 지갑", "session_id": selected["session_id"], "revision": selected["revision"]})
    search.reset_mock(); institutions.reset_mock(); places.reset_mock(); model.reset_mock()
    return client, send, result, model, search, institutions, places


@pytest.mark.parametrize("action,updates", [
    ("clarify", {"awaiting": "item", "asks_for": "item"}),
    ("answer", {"message": "확인 메일의 링크를 눌러 주세요."}),
    ("update_alert", {"alert_changes": {"query": "우산", "radius": 1500, "remove_places": []}}),
    ("end", {}),
])
def test_alert_decisions_never_search_and_preserve_results(action, updates):
    _, send, original, model, search, institutions, places = client_with_search()
    model.return_value = decision(action, task="alert", **updates)
    payload = {"type": "message", "request_id": "alert", "text": "취소하지 말고 물건만 바꿀게",
               "session_id": original["session_id"], "revision": original["revision"], "dialogue": context()}
    result = send(payload)
    assert result["status"] == "answered"
    assert result["conditions"] == original["conditions"]
    assert model.call_args.args[1]["dialogue"]["awaiting"] == "email"
    if action == "update_alert":
        assert result["dialogue"]["alert"]["query"] == "우산"
        assert result["dialogue"]["changed"]
    elif action == "clarify":
        assert result["dialogue"]["alert"] == context()["alert"]
        assert result["dialogue"]["awaiting"] == "item"
    elif action == "end":
        assert result["dialogue"]["task"] == "search"
    # Replay must not call the model again, nor change the alert twice.
    assert send(payload) == result
    model.assert_called_once()
    search.assert_not_called(); institutions.assert_not_called(); places.assert_not_called()


def test_alert_to_search_transition_applies_new_query():
    _, send, original, model, search, _, _ = client_with_search()
    model.return_value = decision("search", task="search", item_query="우산")
    result = send({"type": "message", "request_id": "switch", "text": "메일은 나중에 하고 우산 찾아줘",
                   "session_id": original["session_id"], "revision": original["revision"], "dialogue": context()})
    assert result["status"] == "results"
    assert result["dialogue"]["task"] == "search"
    search.assert_called_once_with((42,), "우산", None)


def test_prepare_alert_can_start_from_search_with_alert_task():
    _, send, original, model, search, institutions, places = client_with_search()
    model.return_value = decision("prepare_alert", task="alert")
    result = send({"type": "message", "request_id": "prepare", "text": "알림 신청할래",
                   "session_id": original["session_id"], "revision": original["revision"],
                   "dialogue": {"task": "search", "alert": None, "awaiting": None, "last_message": "검색을 마쳤어요."}})
    assert result["prepare_alert"] is True
    assert result["conditions"] == original["conditions"]
    search.assert_not_called(); institutions.assert_not_called(); places.assert_not_called()


def test_question_and_following_alert_update_share_context_without_search():
    _, send, original, model, search, institutions, places = client_with_search()
    model.return_value = decision("clarify", task="alert", awaiting="item", asks_for="item")
    question = send({"type": "message", "request_id": "question", "text": "물건 바꿀게",
                     "session_id": original["session_id"], "revision": original["revision"], "dialogue": context()})
    followup = {key: value for key, value in question["dialogue"].items() if key != "changed"}
    followup["last_message"] = question["message"]
    model.return_value = decision("update_alert", task="alert", message="알림은 등록 다음날부터 7일간 유지돼요.",
                                  alert_changes={"query": "우산", "radius": 1500, "remove_places": []})
    result = send({"type": "message", "request_id": "answer", "text": "우산으로 하고 반경은 1.5km. 언제 끝나?",
                   "session_id": question["session_id"], "revision": question["revision"], "dialogue": followup})
    assert result["dialogue"]["alert"]["query"] == "우산"
    assert result["dialogue"]["alert"]["radius"] == 1500
    assert result["dialogue"]["awaiting"] == "email"
    assert "7일" in result["message"]
    assert model.call_args.args[1]["dialogue"]["awaiting"] == "item"
    assert result["conditions"] == original["conditions"]
    search.assert_not_called(); institutions.assert_not_called(); places.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"query": None, "radius": None, "remove_places": [1]},
    {"query": None, "radius": None, "remove_places": [2]},
    {"query": "user@example.com", "radius": None, "remove_places": []},
])
def test_invalid_alert_changes_preserve_draft_and_make_no_search(changes):
    _, send, original, model, search, institutions, places = client_with_search()
    model.return_value = decision("update_alert", task="alert", alert_changes=changes)
    result = send({"type": "message", "request_id": "invalid", "text": "알림 바꿔줘",
                   "session_id": original["session_id"], "revision": original["revision"], "dialogue": context()})
    assert result["status"] == "answered"
    assert "dialogue" not in result
    assert "validation error" not in result["message"].lower()
    if changes["remove_places"] == [1]:
        assert "검색으로 돌아가" in result["message"]
    assert result["conditions"] == original["conditions"]
    search.assert_not_called(); institutions.assert_not_called(); places.assert_not_called()


def test_private_context_is_rejected_before_model():
    client, _, original, model, _, _, _ = client_with_search()
    private = context()
    private["alert"]["query"] = "user@example.com"
    response = client.post("/api/search-chat/turns", json={"type": "message", "request_id": "private", "text": "바꿔줘",
                          "session_id": original["session_id"], "revision": original["revision"], "dialogue": private})
    assert response.status_code == 400
    model.assert_not_called()


def test_context_masks_phone_in_previous_assistant_message():
    data = context()
    data["last_message"] = "전화 010-1234-5678로 문의하세요."
    parsed = DialogueContext.model_validate(data)
    assert "010-1234-5678" not in parsed.last_message


def test_email_in_message_never_reaches_model_or_closes_alert():
    _, send, original, model, search, institutions, places = client_with_search()
    result = send({"type": "message", "request_id": "email", "text": "user@example.com",
                   "session_id": original["session_id"], "revision": original["revision"], "dialogue": context()})
    assert result["status"] == "answered"
    assert "dialogue" not in result
    assert "user@example.com" not in json.dumps(result)
    model.assert_not_called()
    search.assert_not_called(); institutions.assert_not_called(); places.assert_not_called()
