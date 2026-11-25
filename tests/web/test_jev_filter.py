"""2026-11-23 사용자 결정 — 상위 100건으로 자른 뒤 Jev로 명백히 다른 물건만 뺀다(외부 호출 없음, 가짜 클라이언트).

같은 날 비용 최적화: 상위 30건만 판정, 자동 설명 문구 제외, 짧은 질문 키, 다음 쪽은 커서로 판정 결과 재사용.
"""
import json
import re

from retriever_lost_found.search import jev_filter
from retriever_lost_found.search.hybrid import search_found_items
from retriever_lost_found.search.jev_filter import MAX_REQUEST_BYTES, TOP_N, JevFilter, build_request


def row(i, name=None, description="", **extra):
    # 물품명에 id를 넣어 가짜 클라이언트가 짧은 질문 키(i1…)에서 물품을 알아보게 한다.
    return {"id": i, "item_name": name or f"검정 카드지갑 {i}호", "raw_category_name": "지갑 > 기타 지갑",
            "color_name": "블랙(검정)", "description": description, "storage_location_id": 7, **extra}


class FakeDecisions:
    """{item_id: 예 확률}. fail이면 호출 때 RuntimeError."""

    def __init__(self, probabilities, fail=False):
        self.probabilities = probabilities
        self.fail = fail
        self.calls = []

    def decide(self, model, state, questions, *, timeout):
        self.calls.append({"model": model, "state": state, "questions": questions, "timeout": timeout})
        if self.fail:
            raise RuntimeError("down")
        answers = {}
        for key in questions:
            found = re.search(r"(\d+)호", state["습득물"][key])
            answers[key] = self.probabilities.get(int(found[1])) if found else None
        return answers


def test_only_items_at_or_above_the_threshold_are_removed_and_order_is_kept():
    t = jev_filter.THRESHOLD
    client = FakeDecisions({1: 0.01, 2: t, 3: t - 0.01, 4: 0.99})
    kept, status, excluded = JevFilter(client).apply("검정 카드지갑", [row(i) for i in (1, 2, 3, 4)], budget=3.0)
    assert [r["id"] for r in kept] == [1, 3]
    assert status == "ok" and excluded == {2, 4}


def test_refusal_or_missing_answer_keeps_the_item():
    client = FakeDecisions({1: None})  # 2는 답 없음
    kept, _, excluded = JevFilter(client).apply("지갑", [row(1), row(2)], budget=3.0)
    assert [r["id"] for r in kept] == [1, 2] and excluded == set()


def test_jev_failure_keeps_everything_and_calls_no_other_model():
    # 2026-11-23 사용자 결정 — 폴백(Groundhog)은 끈다. 실패하면 아무것도 빼지 않는다.
    client = FakeDecisions({}, fail=True)
    rows = [row(1), row(2)]
    kept, status, excluded = JevFilter(client).apply("지갑", rows, budget=3.0)
    assert kept == rows and status == "unavailable" and excluded == set()
    assert {c["model"] for c in client.calls} == {jev_filter.MODEL}


def test_not_enough_time_skips_jev_without_calling_it():
    client = FakeDecisions({1: 0.99})
    rows = [row(1)]
    kept, status, _ = JevFilter(client).apply("지갑", rows, budget=jev_filter.MIN_BUDGET_SECONDS - 0.01)
    assert kept == rows and status == "skipped" and client.calls == []


def test_timeout_passed_to_the_client_never_exceeds_the_budget():
    client = FakeDecisions({})
    JevFilter(client).apply("지갑", [row(1)], budget=1.2)
    assert all(0 < call["timeout"] <= 1.2 for call in client.calls)


def test_item_text_is_masked_before_it_leaves_the_server():
    # AGENTS.md 4장 — LLM 입력은 mask_item_row를 거친다.
    client = FakeDecisions({})
    JevFilter(client).apply("지갑", [row(1, "지갑(홍길동 010-1234-5678)", "연락처 010-1234-5678")], budget=3.0)
    assert "010-1234-5678" not in json.dumps(client.calls[0], ensure_ascii=False)


def test_only_the_top_n_items_are_judged_in_one_small_request():
    client = FakeDecisions({i: 0.99 for i in range(1, 101)})
    rows = [row(i, f"검정 카드지갑 {i}호 " + "설명" * 40, "습득물 설명 " * 30) for i in range(1, 101)]
    kept, _, excluded = JevFilter(client).apply("검정 카드지갑", rows, budget=3.0)
    assert len(client.calls) == 1 and len(client.calls[0]["questions"]) == TOP_N == 30
    assert excluded == set(range(1, 31)) and [r["id"] for r in kept] == list(range(31, 101))
    size = len(json.dumps({"state": client.calls[0]["state"], "questions": client.calls[0]["questions"]},
                          ensure_ascii=False).encode())
    assert size < MAX_REQUEST_BYTES  # 32KB를 넘으면 느린 long-context 모드로 간다


def test_item_ids_are_not_sent_and_keys_are_short():
    state, questions, keys = build_request("지갑", [row(123456), row(987654)])
    assert list(questions) == ["i1", "i2"] and keys == {"i1": 123456, "i2": 987654}
    assert "123456" not in json.dumps(questions) and "987654" not in json.dumps(state, ensure_ascii=False).replace("987654호", "")


def test_each_question_names_its_own_item_key():
    # 2026-11-23 — 질문이 모두 같으면 Jev가 어느 물품을 묻는지 몰라 판별력이 떨어졌다(AUC 0.94→0.76).
    _, questions, _ = build_request("지갑", [row(1), row(2)])
    assert questions["i1"]["instructions"].startswith("i1") and questions["i2"]["instructions"].startswith("i2")


def test_auto_generated_description_is_not_sent_but_real_notes_are():
    state, _, _ = build_request("지갑", [
        row(1, "검정 반지갑", "검정 반지갑(블랙(검정)색)을 습득하여 보관하고 있습니다."),
        row(2, "카드지갑", "카드지갑 (레드(연한레드)색)을 습득하여 보관하고 있습니다."),
        row(3, "지갑", "지갑 안에 학생증과 버스카드(파랑(블루)색)을 습득하여 보관하고 있습니다."),
    ])
    items = state["습득물"]
    assert items["i1"] == "검정 반지갑 / 지갑 > 기타 지갑 / 검정"
    assert "습득하여" not in items["i2"] and "레드" not in items["i2"]
    assert "학생증과 버스카드" in items["i3"] and "습득하여" not in items["i3"]


def test_rule_tells_jev_to_trust_the_item_name_over_a_wrong_category():
    # 평가에서 '쇼핑백 > 쇼핑백'으로 잘못 분류된 선글라스 정답을 뺐다(docs/measurements.md).
    assert "물품명을 따른다" in jev_filter.RULE


def test_empty_list_does_nothing():
    client = FakeDecisions({})
    assert JevFilter(client).apply("지갑", [], budget=3.0) == ([], "empty", set())
    assert client.calls == []


class Candidates:
    def __init__(self, items):
        self.items = items

    def fetch_candidate_bundle(self, location_ids, terms, **kwargs):
        return {"items": self.items, "candidate_count": len(self.items)}


def baseline_order(rows, query="검정 카드지갑"):
    return [int(r["id"]) for r in search_found_items(Candidates(rows), query, [7], hydrate_page=False)[0]]


def test_search_applies_jev_after_the_top_100_cut_and_reports_exclusions():
    rows = [row(i) for i in range(1, 121)]
    order = baseline_order(rows)
    inside, outside = order[5], order[40]  # 6위는 판정 범위(상위 30) 안, 41위는 밖
    client = FakeDecisions({inside: 0.99, outside: 0.99})
    out = {}
    ranked, _, _ = search_found_items(Candidates(rows), "검정 카드지갑", [7], jev=JevFilter(client),
                                      hydrate_page=False, jev_out=out)
    ids = [int(r["id"]) for r in ranked]
    assert inside not in ids and outside in ids and len(ids) == 99
    assert out["excluded"] == {inside}
    assert sum(len(call["questions"]) for call in client.calls) == 30


def test_next_page_reuses_the_excluded_ids_without_calling_jev():
    rows = [row(i) for i in range(1, 121)]
    target = baseline_order(rows)[3]
    client = FakeDecisions({target: 0.99})
    ranked, _, _ = search_found_items(Candidates(rows), "검정 카드지갑", [7], jev=JevFilter(client),
                                      hydrate_page=False, jev_excluded={target})
    assert client.calls == [] and target not in [int(r["id"]) for r in ranked] and len(ranked) == 99


def test_search_without_jev_is_unchanged():
    rows = [row(i) for i in range(1, 121)]
    ranked, _, _ = search_found_items(Candidates(rows), "검정 카드지갑", [7], hydrate_page=False)
    assert len(ranked) == 100


def test_threshold_is_the_calibrated_value():
    assert jev_filter.MODEL == "typesafe/jev-1.14"
    assert 0 < jev_filter.THRESHOLD <= 1


class PagedItems(Candidates):
    pass


def test_found_items_sends_all_search_results_in_one_response():
    # 2026-11-25 — 검색 결과 상위 100건을 한 응답에 보낸다. 다음 쪽 요청이 없어 Jev도 한 번만 부른다.
    from fastapi.testclient import TestClient
    from retriever_lost_found.web import app as web_app

    rows = [row(i) for i in range(1, 121)]
    order = baseline_order(rows)
    a, b = sorted((order[2], order[70]))
    client = FakeDecisions({a: 0.99, b: 0.99})
    app = web_app.create_app(object(), "js", found_item_client=PagedItems(rows), jev_filter=JevFilter(client))
    http = TestClient(app)
    first = http.get("/api/found-items", params={"location_ids": "7", "q": "검정 카드지갑"}).json()
    ids = [r["id"] for r in first["items"]]
    assert first["next_cursor"] is None and first["match_count"] == len(ids)
    assert len(client.calls) == 1
    assert a not in ids and len(ids) == len(set(ids))


def test_found_items_still_accepts_an_old_50_item_cursor():
    # 배포 전 화면이 들고 있던 "search:50:x=…" 커서도 받아 그 뒤를 이어 준다. Jev는 다시 부르지 않는다.
    from fastapi.testclient import TestClient
    from retriever_lost_found.web import app as web_app

    rows = [row(i) for i in range(1, 121)]
    order = baseline_order(rows)
    a = order[60]
    client = FakeDecisions({})
    app = web_app.create_app(object(), "js", found_item_client=PagedItems(rows), jev_filter=JevFilter(client))
    second = TestClient(app).get("/api/found-items", params={"location_ids": "7", "q": "검정 카드지갑",
                                                             "cursor": f"search:50:x={a}"}).json()
    ids = [r["id"] for r in second["items"]]
    assert client.calls == [] and second["next_cursor"] is None
    assert ids == [i for i in order if i != a][50:100]


def test_cursor_without_exclusions_still_marks_that_jev_already_ran():
    from retriever_lost_found.web.app import SEARCH_CURSOR_PATTERN, search_cursor
    assert search_cursor(50, set()) == "search:50:x="
    assert search_cursor(50, None) == "search:50"
    for cursor in ("search:50", "search:50:x=", "search:50:x=1,22,333"):
        assert SEARCH_CURSOR_PATTERN.fullmatch(cursor)
    for cursor in ("search:50:x=a", "search:50:y=1", "search:50:x=" + ",".join(["1"] * 31)):
        assert not SEARCH_CURSOR_PATTERN.fullmatch(cursor)


def test_found_items_endpoint_passes_the_jev_filter_to_search(monkeypatch):
    from fastapi.testclient import TestClient
    from retriever_lost_found.web import app as web_app

    seen = {}

    def fake_search(client, query, location_ids, embedding_client=None, **kwargs):
        seen["jev"] = kwargs.get("jev")
        return [], 0, "disabled"

    monkeypatch.setattr(web_app, "search_with_deadline", fake_search)
    jev = JevFilter(FakeDecisions({}))
    app = web_app.create_app(object(), "js", found_item_client=object(), jev_filter=jev)
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
