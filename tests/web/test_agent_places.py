"""PRD 4.3-A — Multiple confirmed places share one scoped search and session."""
import json
from dataclasses import asdict, replace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from retriever_lost_found.web.agent_api import create_agent_router, _restore, _snapshot, Session
from retriever_lost_found.web.agent_conversation import Conversation, confirm_place, edit_places
from retriever_lost_found.web.agent_search import Institution, PlaceCandidate, SearchPage
from retriever_lost_found.web.agent_store import MemoryAgentStore
from retriever_lost_found.web.app import create_app

PLACES = (
    PlaceCandidate("a", "서울역", "서울 중구", 37.55, 126.97),
    PlaceCandidate("b", "서울숲", "서울 성동구", 37.54, 127.04),
    PlaceCandidate("c", "강남역", "서울 강남구", 37.50, 127.03),
)


def decision(action="search", place=None, item=None, target=None):
    return dict(action=action, message="", place_query=place, item_query=item, target=target,
                item_changes=None, clear_item_query=False, asks_for=None)


class Chat:
    def __init__(self):
        self.model = Mock()
        self.search = Mock(side_effect=lambda ids, query, cursor: SearchPage(
            tuple({"item_id": i, "institution_id": i, "item_name": query} for i in ids), len(ids)))
        self.store = MemoryAgentStore()
        self.last = None
        self.count = 0
        self.client = self.new_client()

    def new_client(self):
        router = create_agent_router(
            store=self.store, model=self.model,
            find_places=lambda query: [p for p in PLACES if p.name == query],
            get_institutions=lambda: [Institution(i, p.latitude, p.longitude) for i, p in enumerate(PLACES, 1)]
                                    + [Institution(9, 37.56, 126.97), Institution(99, 38., 128.)],
            search_items=self.search,
        )
        return TestClient(create_app(Mock(), "dummy", agent_router=router))

    def turn(self, type="message", proposal=None, **payload):
        if proposal is not None:
            self.model.return_value = proposal
        if self.last:
            payload.update(session_id=self.last["session_id"], revision=self.last["revision"])
        self.count += 1
        payload.update(type=type, request_id=str(self.count))
        response = self.client.post("/api/search-chat/turns", json=payload)
        assert response.status_code == 200
        self.last = json.loads(response.text.splitlines()[-1])["response"]
        return self.last

    def choose(self, place):
        return self.turn(type="select_place", candidate_id=place.candidate_id)

    def start(self):
        self.turn(text="서울역", proposal=decision(place="서울역"))
        return self.choose(PLACES[0])


def test_add_mixed_item_then_remove_and_replace_across_instances():
    chat = Chat()
    first = chat.start()
    assert first["conditions"]["institution_ids"] == [1]
    assert "들렀던 장소" in first["message"]
    chat.search.assert_not_called()
    pending = chat.turn(text="검은 지갑이고 서울숲도 갔어요", proposal=decision("add_place", "서울숲", "검은 지갑"))
    assert pending["conditions"]["places"] == [asdict(PLACES[0])]
    assert pending["conditions"]["item_query"] == "검은 지갑"
    chat.search.assert_not_called()
    chat.client = chat.new_client()
    done = chat.choose(PLACES[1])
    assert done["conditions"]["institution_ids"] == [1, 2]
    assert done["matches"]["total_count"] == 2
    chat.search.assert_called_once_with((1, 2), "검은 지갑", None)
    chat.turn(text="서울역 말고 강남역", proposal=decision("replace_place", "강남역", target=1))
    done = chat.choose(PLACES[2])
    assert [p["name"] for p in done["conditions"]["places"]] == ["강남역", "서울숲"]
    assert done["conditions"]["institution_ids"] == [2, 3]
    done = chat.turn(text="서울숲은 빼줘", proposal=decision("remove_place", target=2))
    assert done["conditions"]["institution_ids"] == [3]
    assert done["conditions"]["item_query"] == "검은 지갑"
    done = chat.turn(type="remove_place", place_target=1)
    assert done["conditions"]["places"] == []
    assert done["conditions"]["institution_ids"] == []
    assert done["conditions"]["place"] is None
    assert done["matches"] is None


def test_ui_add_cancel_and_limit_preserve_places():
    chat = Chat()
    chat.start()
    for place in PLACES[1:]:
        chat.turn(type="add_place")
        chat.turn(text=place.name, proposal=decision(place=place.name))
        chat.choose(place)
    previous = chat.last
    rejected = chat.turn(type="add_place")
    assert rejected["status"] == "error"
    assert rejected["revision"] == previous["revision"]
    assert rejected["conditions"]["places"] == previous["conditions"]["places"]
    natural = chat.turn(text="한 곳 더", proposal=decision("add_place", "다른 곳"))
    assert natural["status"] == "answered"
    assert "최대 3곳" in natural["message"]
    chat.turn(type="replace_place", place_target=1)
    done = chat.turn(type="cancel_place_selection")
    assert done["conditions"]["places"] == previous["conditions"]["places"]
    chat.search.assert_not_called()


def test_duplicate_confirmation_does_not_commit_and_can_cancel():
    chat = Chat()
    chat.start()
    chat.turn(type="add_place")
    pending = chat.turn(text="서울역", proposal=decision(place="서울역"))
    rejected = chat.choose(PLACES[0])
    assert rejected["status"] == "error"
    assert rejected["revision"] == pending["revision"]
    assert len(rejected["conditions"]["places"]) == 1
    done = chat.turn(type="cancel_place_selection")
    assert done["conditions"]["place_operation"] is None
    assert done["conditions"]["institution_ids"] == [1]


def test_old_snapshot_restores_and_new_places_roundtrip():
    state = Conversation(place_query="서울역", confirmed=PLACES[0])
    old = _snapshot(Session(state=state))
    for key in ("places", "place_operation", "place_target", "pending_place_queries", "batch_places"):
        old["state"].pop(key)
    restored = _restore(old)
    assert restored.state.selected_places() == (PLACES[0],)
    pending = edit_places(restored.state, "add_place")
    state, _ = confirm_place(replace(pending, candidates=(PLACES[1],)), "b")
    assert _restore(_snapshot(Session(state=state))).state.selected_places() == PLACES[:2]


def test_union_distance_uses_nearest_center_and_keeps_one_search():
    from retriever_lost_found.web.agent_search import PendingSearch, search_confirmed_place
    search = Mock(return_value=SearchPage(({"item_id": 1, "institution_id": 7},), 1))
    events = list(search_confirmed_place(
        PendingSearch((PLACES[0],), "지갑"), "a", search_centers=PLACES[:2],
        get_institutions=lambda: [Institution(7, 37.54, 127.04), Institution(99, 38., 128.)],
        search_items=search, rewrite_query=lambda _: None,
    ))
    search.assert_called_once_with((7,), "지갑")
    assert events[-1]["matches"]["total_count"] == 1


def test_union_expands_all_centers_together_and_deduplicates_institutions():
    from retriever_lost_found.web.agent_search import PendingSearch, search_confirmed_place
    search = Mock(side_effect=[SearchPage((), 0), SearchPage(({"item_id": 4, "institution_id": 4},), 1)])
    events = list(search_confirmed_place(
        PendingSearch((PLACES[0],), "지갑"), "a", search_centers=PLACES[:2],
        get_institutions=lambda: [Institution(1, 37.55, 126.97), Institution(1, 37.55, 126.97),
                                  Institution(2, 37.54, 127.04), Institution(4, 37.551, 127.04)],
        search_items=search, rewrite_query=lambda _: None,
    ))
    scopes = [(event["radius_m"], event["institution_ids"]) for event in events if event.get("type") == "scope.updated"]
    assert scopes == [(1000, [1, 2]), (1500, [1, 2, 4])]
    assert [call.args[0] for call in search.call_args_list] == [(1, 2), (1, 2, 4)]


def test_failed_confirmation_can_retry_same_request_and_invalidates_old_cursor():
    chat = Chat()
    chat.start()
    chat.turn(text="지갑", proposal=decision(item="지갑"))
    original = chat.last
    chat.turn(type="add_place")
    stale = chat.client.post('/api/search-chat/pages', json={
        "session_id": original["session_id"], "revision": original["revision"], "cursor": "old",
    })
    assert stale.status_code == 409
    chat.turn(text="서울숲", proposal=decision(place="서울숲"))
    pending = chat.last
    request = dict(type="select_place", candidate_id="b", request_id="retry-commit",
                   session_id=pending["session_id"], revision=pending["revision"])
    chat.search.side_effect = TimeoutError("test timeout")
    failed = json.loads(chat.client.post('/api/search-chat/turns', json=request).text.splitlines()[-1])["response"]
    assert failed["status"] == "error"
    assert failed["conditions"]["places"] == pending["conditions"]["places"]
    chat.search.side_effect = lambda ids, query, cursor: SearchPage(({"item_id": 2, "institution_id": 2},), 1)
    recovered = json.loads(chat.new_client().post('/api/search-chat/turns', json=request).text.splitlines()[-1])["response"]
    assert recovered["status"] == "results"
    assert len(recovered["conditions"]["places"]) == 2
    assert recovered["revision"] == pending["revision"] + 1
    replay = json.loads(chat.new_client().post('/api/search-chat/turns', json=request).text.splitlines()[-1])["response"]
    assert replay == recovered


def test_place_sequence_confirms_every_location_before_searching_without_extra_llm_calls():
    chat = Chat()
    proposal = {**decision('set_places', item='지갑'), 'place_queries': [p.name for p in PLACES]}
    first = chat.turn(text='세 곳에서 지갑을 찾아줘', proposal=proposal)
    assert [p['candidate_id'] for p in first['place_candidates']] == ['a']
    assert first['conditions']['pending_place_queries'] == ['서울숲', '강남역']
    assert first['conditions']['places'] == []
    chat.client = chat.new_client()
    second = chat.choose(PLACES[0])
    assert second['status'] == 'awaiting_place_selection'
    assert second['conditions']['places'] == [asdict(PLACES[0])]
    assert [p['candidate_id'] for p in second['place_candidates']] == ['b']
    chat.search.assert_not_called()
    chat.client = chat.new_client()
    third = chat.choose(PLACES[1])
    assert [p['candidate_id'] for p in third['place_candidates']] == ['c']
    assert third['conditions']['pending_place_queries'] == []
    chat.search.assert_not_called()
    done = chat.choose(PLACES[2])
    assert done['status'] == 'results'
    assert done['conditions']['places'] == [asdict(p) for p in PLACES]
    assert done['conditions']['place_operation'] is None
    chat.search.assert_called_once_with((1, 2, 3), '지갑', None)
    chat.model.assert_called_once()


def test_add_sequence_keeps_original_and_failed_lookup_can_be_skipped():
    chat = Chat()
    chat.start()
    proposal = {**decision('add_place'), 'place_queries': ['없는 상호', '서울숲']}
    first = chat.turn(text='두 곳도 추가해', proposal=proposal)
    assert first['status'] == 'no_place_candidates'
    assert first['conditions']['places'] == [asdict(PLACES[0])]
    assert first['conditions']['pending_place_queries'] == ['서울숲']
    next_place = chat.turn(type='skip_place')
    assert [p['candidate_id'] for p in next_place['place_candidates']] == ['b']
    done = chat.choose(PLACES[1])
    assert done['conditions']['places'] == [asdict(p) for p in PLACES[:2]]
    assert done['status'] == 'awaiting_clarification'
    chat.search.assert_not_called()


def test_setting_entire_list_then_cancelling_restores_original_locations():
    chat = Chat()
    original = chat.start()
    proposal = {**decision('set_places'), 'place_queries': ['서울숲', '강남역']}
    chat.turn(text='장소를 이 두 곳으로 설정', proposal=proposal)
    interim = chat.choose(PLACES[1])
    assert interim['conditions']['places'] == [asdict(PLACES[1])]
    cancelled = chat.turn(type='cancel_place_selection')
    assert cancelled['conditions']['places'] == original['conditions']['places']
    assert cancelled['conditions']['pending_place_queries'] == []
    assert cancelled['conditions']['place_operation'] is None


def test_over_capacity_sequence_keeps_all_previous_state():
    chat = Chat()
    original = chat.start()
    too_many = {**decision('add_place'), 'place_queries': [p.name for p in PLACES]}
    result = chat.turn(text='세 곳도 추가해', proposal=too_many)
    assert result['status'] == 'answered'
    assert '최대 3곳' in result['message']
    assert result['conditions']['places'] == original['conditions']['places']
    assert result['conditions']['pending_place_queries'] == []
    chat.search.assert_not_called()


def test_skipping_whole_initial_sequence_returns_to_place_question():
    chat = Chat()
    chat.turn(text='두 곳에서 찾아줘', proposal={**decision('set_places'), 'place_queries': ['서울역', '서울숲']})
    chat.turn(type='skip_place')
    done = chat.turn(type='skip_place')
    assert done['status'] == 'awaiting_clarification'
    assert done['conditions']['places'] == []
    assert done['conditions']['place_operation'] is None
    assert done['conditions']['pending_place_queries'] == []
    chat.search.assert_not_called()


def test_correcting_current_query_keeps_rest_of_sequence_and_rejects_duplicate_coordinates():
    chat = Chat()
    chat.turn(text='두 장소', proposal={**decision('set_places'), 'place_queries': ['없는 곳', '서울숲']})
    corrected = chat.turn(text='첫 번째는 서울역', proposal={**decision(), 'place_queries': ['서울역']})
    assert corrected['conditions']['pending_place_queries'] == ['서울숲']
    chat.choose(PLACES[0])
    duplicate = chat.turn(text='여기도 서울역', proposal=decision(place='서울역'))
    assert duplicate['conditions']['pending_place_queries'] == []
    rejected = chat.choose(PLACES[0])
    assert rejected['status'] == 'error'
    assert rejected['revision'] == duplicate['revision']
    assert len(rejected['conditions']['places']) == 1
    done = chat.turn(type='skip_place')
    assert done['conditions']['places'] == [asdict(PLACES[0])]
    chat.search.assert_not_called()


def test_lost_intermediate_confirmation_response_replays_without_skipping_a_place():
    chat = Chat()
    chat.turn(text='세 곳', proposal={**decision('set_places'), 'place_queries': [p.name for p in PLACES]})
    pending = chat.last
    request = dict(type='select_place', candidate_id='a', request_id='same-confirm',
                   session_id=pending['session_id'], revision=pending['revision'])
    response = chat.client.post('/api/search-chat/turns', json=request)
    first = json.loads(response.text.splitlines()[-1])['response']
    repeated = chat.new_client().post('/api/search-chat/turns', json=request)
    second = json.loads(repeated.text.splitlines()[-1])['response']
    assert first == second
    assert first['conditions']['places'] == [asdict(PLACES[0])]
    assert first['conditions']['pending_place_queries'] == ['강남역']
    assert first['place_candidates'][0]['candidate_id'] == 'b'


def test_removing_confirmed_place_during_sequence_keeps_remaining_candidates():
    chat = Chat()
    chat.turn(text='세 곳', proposal={**decision('set_places'), 'place_queries': [p.name for p in PLACES]})
    chat.choose(PLACES[0])
    removed = chat.turn(type='remove_place', place_target=1)
    assert removed['conditions']['places'] == []
    assert removed['conditions']['pending_place_queries'] == ['강남역']
    assert removed['place_candidates'][0]['candidate_id'] == 'b'
    chat.choose(PLACES[1])
    done = chat.choose(PLACES[2])
    assert [p['name'] for p in done['conditions']['places']] == ['서울숲', '강남역']
    chat.search.assert_not_called()
