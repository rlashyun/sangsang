"""2026-11-23 사용자 결정 — 알림 "유사" 판정에도 Jev 제외를 쓴다(검색과 같은 모델·임계값·입력 형식, 외부 호출 없음)."""

from __future__ import annotations

import re

from retriever_lost_found.alerts import matching
from retriever_lost_found.alerts.judge import JEV_ALERT_BUDGET_SECONDS, search_judge
from retriever_lost_found.search import jev_filter
from retriever_lost_found.search.jev_filter import JevFilter


def candidate(i: int, name: str, color: str = "블랙(검정)") -> dict:
    # alert_candidate_items 모양 — 색상·설명 칸이 따로 없고 search_text에만 들어 있다.
    text = f"{name} 지갑 > 기타 지갑 {color} {name}({color}색)을 습득하여 보관하고 있습니다.".lower()
    return {"id": i, "storage_location_id": 7, "search_text": text, "item_name": name, "raw_category_name": "지갑 > 기타 지갑"}


class FakeDecisions:
    def __init__(self, probabilities, fail=False):
        self.probabilities, self.fail, self.calls = probabilities, fail, []

    def decide(self, model, state, questions, *, timeout):
        self.calls.append({"state": state, "questions": questions, "timeout": timeout})
        if self.fail:
            raise RuntimeError("down")
        return {key: self.probabilities.get(int(re.search(r"(\d+)호", state["습득물"][key])[1])) for key in questions}


WALLETS = [candidate(i, f"검정 카드지갑 {i}호") for i in range(1, 6)]


def ids(pairs):
    return [int(item["id"]) for item, _ in pairs]


def test_jev_removes_clearly_different_matches_and_keeps_the_rest_in_order():
    base = ids(search_judge("검정 카드지갑", WALLETS))
    target = base[1]
    client = FakeDecisions({target: 0.99})
    judged = ids(search_judge("검정 카드지갑", WALLETS, jev=JevFilter(client)))
    assert judged == [i for i in base if i != target]
    assert len(client.calls) == 1


def test_jev_failure_keeps_every_match():
    base = ids(search_judge("검정 카드지갑", WALLETS))
    judged = ids(search_judge("검정 카드지갑", WALLETS, jev=JevFilter(FakeDecisions({}, fail=True))))
    assert judged == base


def test_no_match_means_no_jev_call():
    client = FakeDecisions({})
    assert search_judge("노트북", WALLETS, jev=JevFilter(client)) == []
    assert client.calls == []


def test_jev_sees_the_palette_color_recovered_from_search_text():
    # 보정 때와 같은 입력(물품명 / 분류 / 색)을 보낸다. 자동 설명 문구는 보내지 않는다.
    client = FakeDecisions({})
    search_judge("검정 카드지갑", [candidate(1, "검정 카드지갑 1호")], jev=JevFilter(client))
    sent = client.calls[0]["state"]["습득물"]["i1"]
    assert sent == "검정 카드지갑 1호 / 지갑 > 기타 지갑 / 검정"


def test_each_reservation_gets_a_bounded_jev_budget():
    client = FakeDecisions({})
    search_judge("검정 카드지갑", WALLETS, jev=JevFilter(client))
    assert 0 < client.calls[0]["timeout"] <= JEV_ALERT_BUDGET_SECONDS <= 3.0


def test_only_the_top_matches_by_score_are_judged():
    # 2026-11-25 사용자 결정 — 알림도 검색 화면과 같이 점수 상위 100건을 25건 구간마다 요청 하나로 판정한다.
    many = [candidate(i, f"검정 카드지갑 {i}호") for i in range(1, 121)]
    client = FakeDecisions({})
    search_judge("검정 카드지갑", many, jev=JevFilter(client))
    assert sorted(len(call["questions"]) for call in client.calls) == [25, 25, 25, 25] == [25] * (jev_filter.TOP_N // 25)


def test_lower_ranked_matches_use_the_looser_threshold():
    many = [candidate(i, f"검정 카드지갑 {i}호") for i in range(1, 61)]
    order = ids(search_judge("검정 카드지갑", many))
    first, second = order[0], order[30]  # 1위(0.92 구간)와 31위(0.87 구간)
    client = FakeDecisions({first: 0.9, second: 0.9})
    judged = ids(search_judge("검정 카드지갑", many, jev=JevFilter(client)))
    assert first in judged and second not in judged


def test_default_judge_stays_jev_free_and_the_cron_judge_uses_jev_when_a_key_exists(monkeypatch):
    assert matching.SIMILARITY_JUDGE is search_judge
    monkeypatch.setattr(matching, "read_optional_env_value", lambda name: "key" if name == "OPENROUTER_API_KEY" else None)
    judge = matching.cron_judge()
    assert judge is not search_judge and isinstance(judge.keywords["jev"], JevFilter)
    assert judge.keywords["jev"].thresholds == jev_filter.SEGMENT_THRESHOLDS == (0.92, 0.87, 0.87, 0.87)
    monkeypatch.setattr(matching, "read_optional_env_value", lambda name: None)
    assert matching.cron_judge() is search_judge


def test_cron_matching_uses_the_cron_judge(monkeypatch):
    from retriever_lost_found.web import cron

    sentinel = object()
    seen = []
    monkeypatch.setattr(matching, "cron_judge", lambda: sentinel)
    monkeypatch.setattr(cron, "run_match_pass", lambda store, judge, **kw: seen.append(judge) or {"status": "succeeded"})
    cron._run_alert_matching(object(), deadline=0.0)
    assert seen and all(judge is sentinel for judge in seen)
