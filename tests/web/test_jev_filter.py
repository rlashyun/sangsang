"""2026-11-23 사용자 결정 — 상위 100건으로 자른 뒤 Jev로 명백히 다른 물건만 뺀다(외부 호출 없음, 가짜 클라이언트)."""
import json

from retriever_lost_found.search import jev_filter
from retriever_lost_found.search.hybrid import search_found_items
from retriever_lost_found.search.jev_filter import CHUNK_SIZE, MAX_REQUEST_BYTES, JevFilter


def row(i, name="검정 카드지갑", description="", **extra):
    return {"id": i, "item_name": name, "raw_category_name": "지갑 > 기타 지갑", "color_name": "블랙(검정)",
            "description": description, "storage_location_id": 7, **extra}


class FakeDecisions:
    """model → {item_id: 예 확률}. fail_models는 호출하면 RuntimeError."""

    def __init__(self, probabilities, fail_models=()):
        self.probabilities = probabilities
        self.fail_models = set(fail_models)
        self.calls = []

    def decide(self, model, state, questions, *, timeout):
        self.calls.append({"model": model, "state": state, "questions": questions, "timeout": timeout})
        if model in self.fail_models:
            raise RuntimeError("down")
        table = self.probabilities.get(model, {})
        return {key: table.get(int(key.split("_")[1])) for key in questions}


PRIMARY = jev_filter.MODEL


def test_only_items_at_or_above_the_threshold_are_removed_and_order_is_kept():
    t = jev_filter.THRESHOLD
    client = FakeDecisions({PRIMARY: {1: 0.01, 2: t, 3: t - 0.01, 4: 0.99}})
    kept, status = JevFilter(client).apply("검정 카드지갑", [row(i) for i in (1, 2, 3, 4)], budget=3.0)
    assert [r["id"] for r in kept] == [1, 3]
    assert status == "ok"


def test_refusal_or_missing_answer_keeps_the_item():
    client = FakeDecisions({PRIMARY: {1: None}})  # 2는 답 없음
    kept, _ = JevFilter(client).apply("지갑", [row(1), row(2)], budget=3.0)
    assert [r["id"] for r in kept] == [1, 2]


def test_jev_failure_keeps_everything_and_calls_no_other_model():
    # 2026-11-23 사용자 결정 — 폴백(Groundhog)은 끈다. 실패하면 아무것도 빼지 않는다.
    client = FakeDecisions({}, fail_models={PRIMARY})
    rows = [row(1), row(2)]
    kept, status = JevFilter(client).apply("지갑", rows, budget=3.0)
    assert kept == rows and status == "unavailable"
    assert {c["model"] for c in client.calls} == {PRIMARY}


def test_not_enough_time_skips_jev_without_calling_it():
    client = FakeDecisions({PRIMARY: {1: 0.99}})
    rows = [row(1)]
    kept, status = JevFilter(client).apply("지갑", rows, budget=jev_filter.MIN_BUDGET_SECONDS - 0.01)
    assert kept == rows and status == "skipped" and client.calls == []


def test_timeout_passed_to_the_client_never_exceeds_the_budget():
    client = FakeDecisions({PRIMARY: {}})
    JevFilter(client).apply("지갑", [row(1)], budget=1.2)
    assert all(0 < call["timeout"] <= 1.2 for call in client.calls)


def test_item_text_is_masked_before_it_leaves_the_server():
    # AGENTS.md 4장 — LLM 입력은 mask_item_row를 거친다.
    client = FakeDecisions({PRIMARY: {}})
    JevFilter(client).apply("지갑", [row(1, "지갑(홍길동 010-1234-5678)", "연락처 010-1234-5678")], budget=3.0)
    sent = json.dumps(client.calls[0], ensure_ascii=False)
    assert "010-1234-5678" not in sent


def test_hundred_items_are_split_into_small_parallel_requests():
    client = FakeDecisions({PRIMARY: {}})
    rows = [row(i, "검정 카드지갑 " + "설명" * 40, "습득물 설명 " * 30) for i in range(1, 101)]
    JevFilter(client).apply("검정 카드지갑", rows, budget=3.0)
    assert len(client.calls) == -(-100 // CHUNK_SIZE)
    for call in client.calls:
        size = len(json.dumps({"state": call["state"], "questions": call["questions"]}, ensure_ascii=False).encode())
        assert size < MAX_REQUEST_BYTES  # 32KB를 넘으면 느린 long-context 모드로 간다


def test_empty_list_does_nothing():
    client = FakeDecisions({})
    assert JevFilter(client).apply("지갑", [], budget=3.0) == ([], "empty")
    assert client.calls == []


class Candidates:
    def __init__(self, items):
        self.items = items

    def fetch_candidate_bundle(self, location_ids, terms, **kwargs):
        return {"items": self.items, "candidate_count": len(self.items)}


def test_search_applies_jev_after_the_top_100_cut():
    rows = [row(i) for i in range(1, 121)]
    client = FakeDecisions({PRIMARY: {50: 0.99}})  # 동점은 id 내림차순 — 상위 100건은 120~21
    ranked, _, _ = search_found_items(Candidates(rows), "검정 카드지갑", [7], jev=JevFilter(client),
                                      hydrate_page=False)
    ids = [int(r["id"]) for r in ranked]
    assert 50 not in ids and 51 in ids and len(ids) == 99  # 100건으로 자른 뒤 뺐으므로 채우지 않는다
    assert sum(len(call["questions"]) for call in client.calls) == 100


def test_search_without_jev_is_unchanged():
    rows = [row(i) for i in range(1, 121)]
    ranked, _, _ = search_found_items(Candidates(rows), "검정 카드지갑", [7], hydrate_page=False)
    assert len(ranked) == 100


def test_threshold_is_the_calibrated_value():
    assert jev_filter.MODEL == "typesafe/jev-1.14"
    assert jev_filter.THRESHOLD == 0.92  # 보정 50케이스 정답 0건 최저 0.87 + 여유 0.05


def test_found_items_endpoint_passes_the_jev_filter_to_search(monkeypatch):
    from fastapi.testclient import TestClient
    from retriever_lost_found.web import app as web_app

    seen = {}

    def fake_search(client, query, location_ids, embedding_client=None, **kwargs):
        seen["jev"] = kwargs.get("jev")
        return [], 0, "disabled"

    monkeypatch.setattr(web_app, "search_with_deadline", fake_search)

    class Items:
        pass

    jev = JevFilter(FakeDecisions({}))
    app = web_app.create_app(object(), "js", found_item_client=Items(), jev_filter=jev)
    response = TestClient(app).get("/api/found-items", params={"location_ids": "7", "q": "지갑"})
    assert response.status_code == 200
    assert seen["jev"] is jev


def test_runtime_app_builds_a_jev_filter_when_the_openrouter_key_exists(monkeypatch):
    from retriever_lost_found.web import app as web_app

    env = {"KAKAO_REST_API_KEY": "k", "KAKAO_JAVASCRIPT_KEY": "j", "SUPABASE_URL": "https://example.supabase.co",
           "SUPABASE_SECRET_KEY": "s", "OPENROUTER_API_KEY": "o"}
    monkeypatch.setattr(web_app, "read_env_value", lambda name: env[name])
    monkeypatch.setattr(web_app, "read_optional_env_value", lambda name: env.get(name))
    monkeypatch.setattr(web_app, "load_alert_settings", lambda: None)
    app = web_app.create_runtime_app()
    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/found-items")
    closure = {name: cell.cell_contents for name, cell in zip(route.endpoint.__code__.co_freevars,
                                                                route.endpoint.__closure__ or ())}
    assert isinstance(closure["jev_filter"], JevFilter)
