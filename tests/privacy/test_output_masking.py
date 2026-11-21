"""PRD 3.3 · Q5 — 습득물 원문이 시스템 밖으로 나가는 지점마다 마스킹이 적용되는지 확인한다.

나가는 지점: /api/found-items(두 분기) · 대화 검색(브라우저·LLM) · 알림 메일 · 임베딩 API 입력.
저장 원문(item_name·description·raw_payload)은 바꾸지 않는다(절대 규칙 3).
모든 데이터는 합성 픽스처이며 외부 API를 호출하지 않는다(AGENTS.md 7장).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from retriever_lost_found.alerts.reservations import _query as alert_query
from retriever_lost_found.alerts.templates import _item_line
from retriever_lost_found.embedding.service import run_slice
from retriever_lost_found.ingestion.categories import FALLBACK_CATEGORY, classify_product_category
from retriever_lost_found.ingestion.collector import build_search_text, to_csv_row
from retriever_lost_found.integrations.kakao import KakaoLocalClient
from retriever_lost_found.privacy.masking import IDENTIFIER_QUERY_ERROR, mask_text
from retriever_lost_found.privacy.remask import plan_updates, run_remask
from retriever_lost_found.web.agent_adapters import adapt_search_item
from retriever_lost_found.web.agent_conversation import IDENTIFIER_MESSAGE, Conversation, respond_to_message
from retriever_lost_found.web.agent_search import _validated_query
from retriever_lost_found.web.app import create_app

CARD_ROW = {
    "id": 21,
    "storage_location_id": 77,
    "item_name": "신한카드(홍길동)",
    "raw_category_name": "카드 > 신용(체크)카드",
    "color_name": "검정",
    "description": "카드번호 1234-5678-9012-3456 연락처 010-1234-5678",
    "registered_on": "2026-11-19",
    "image_url": "",
}
PLAIN_ROW = {
    "id": 20,
    "storage_location_id": 77,
    "item_name": "검정 지갑",
    "raw_category_name": "지갑 > 남성용 지갑",
    "color_name": "검정",
    "description": "가죽 반지갑",
    "registered_on": "2026-11-18",
    "image_url": "",
}


class FakeFoundItemClient:
    """두 분기에 같은 원문 행을 돌려준다. 원문 dict가 응답 처리 중 바뀌지 않았는지도 본다."""

    def __init__(self) -> None:
        self.rows = [dict(CARD_ROW), dict(PLAIN_ROW)]

    def fetch_item_page(self, location_ids, *, limit, before=None):
        return {"items": self.rows, "has_more": False, "total_count": len(self.rows)}

    def fetch_items(self, location_ids):
        return self.rows


def _client(found_item_client: FakeFoundItemClient) -> TestClient:
    return TestClient(create_app(
        KakaoLocalClient("kakao-secret"), "javascript-key", institutions=[], found_item_client=found_item_client,
    ))


def _assert_card_row_masked(item: dict) -> None:
    assert item["item_name"] == "신한카드(홍*동)"
    assert item["description"] == "카드번호 1234-****-****-3456 연락처 010-****-5678"


# --- /api/found-items ---

def test_found_items_list_without_query_masks_items_and_keeps_stored_rows():
    fake = FakeFoundItemClient()
    response = _client(fake).get("/api/found-items", params={"location_ids": "77"})
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["items"]}
    _assert_card_row_masked(items[21])
    assert items[20]["item_name"] == "검정 지갑"  # 개인정보가 없는 행은 그대로
    assert fake.rows[0] == CARD_ROW  # 원문 dict는 바뀌지 않는다


def test_found_items_search_masks_ranked_items():
    fake = FakeFoundItemClient()
    response = _client(fake).get("/api/found-items", params={"location_ids": "77", "q": "신한카드"})
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["items"]}
    assert 21 in items
    _assert_card_row_masked(items[21])
    assert fake.rows[0] == CARD_ROW


@pytest.mark.parametrize("query", ["1234-5678-9012-3456", "900101-1234567", "010-1234-5678"])
def test_found_items_rejects_identifier_queries_with_guidance(query):
    response = _client(FakeFoundItemClient()).get("/api/found-items", params={"location_ids": "77", "q": query})
    assert response.status_code == 400
    assert response.json()["error"] == IDENTIFIER_QUERY_ERROR


def test_found_items_accepts_card_company_and_last_four_digits():
    response = _client(FakeFoundItemClient()).get("/api/found-items", params={"location_ids": "77", "q": "신한카드 3456"})
    assert response.status_code == 200


# --- 대화 검색 ---

def test_agent_search_item_is_masked_for_browser_and_llm():
    item = adapt_search_item({**CARD_ROW, "match_score": 0.9})
    _assert_card_row_masked(item)
    assert item["item_id"] == 21 and item["institution_id"] == 77
    assert "match_score" not in item


def test_agent_query_validation_rejects_identifiers_and_accepts_descriptions():
    with pytest.raises(ValueError, match="카드사"):
        _validated_query("010-1234-5678")
    assert _validated_query("검정 신한카드") == "검정 신한카드"


def test_conversation_with_identifier_is_answered_without_calling_the_llm():
    model = Mock()
    turn = respond_to_message(
        Conversation(), "신한카드 1234-5678-9012-3456 잃어버렸어요", model=model, find_places=Mock(),
    )
    model.assert_not_called()
    assert turn.status == "answered"
    assert turn.message == IDENTIFIER_MESSAGE
    # 다음 LLM 호출에 실릴 대화 기록에도 번호는 가려져 있다.
    assert turn.state.recent[-1][0] == "신한카드 1234-****-****-3456 잃어버렸어요"


def test_conversation_without_identifier_still_reaches_the_llm_with_masked_visible_items():
    model = Mock(return_value={
        "action": "answer", "message": "1번 물품이에요.", "place_query": None, "item_query": None,
        "clear_item_query": False, "asks_for": None, "item_changes": None, "target": None,
    })
    visible = [adapt_search_item(CARD_ROW)]
    turn = respond_to_message(
        Conversation(), "1번은 뭐예요?", model=model, find_places=Mock(), visible_items=visible,
    )
    model.assert_called_once()
    payload = model.call_args.args[1]
    assert payload["message"] == "1번은 뭐예요?"
    assert payload["visible_items"] == [{"number": 1, "name": "신한카드(홍*동)", "color": "검정"}]
    assert turn.status == "answered"


# --- 알림 메일 ---

def test_alert_mail_item_line_masks_names_and_numbers():
    line = _item_line({**CARD_ROW, "item_name": "체크카드 김철수 명의 1234-5678-9012-3456"}, show_place=False)
    assert "김*수" in line and "1234-****-****-3456" in line
    assert "김철수" not in line and "1234-5678" not in line


def test_alert_mail_item_line_keeps_ordinary_names():
    assert _item_line(PLAIN_ROW, show_place=False).startswith("검정 지갑")


def test_alert_reservation_rejects_identifier_queries():
    with pytest.raises(ValueError, match="카드사"):
        alert_query("01012345678")
    assert alert_query("검정 지갑") == "검정 지갑"


# --- 수집: 정규화 칸만 가리고 원문 칸은 그대로 ---

API_ITEM = {
    "atcId": "F2026111900001",
    "fdSn": "1",
    "fdPrdtNm": "신한카드(홍길동)",
    "fdSbjt": "카드번호 1234-5678-9012-3456",
    "clrNm": "검정",
    "prdtClNm": "카드 > 신용(체크)카드",
    "depPlace": "서울역",
    "fdFilePathImg": "",
}


def test_ingestion_masks_normalized_search_text_only():
    category = classify_product_category(API_ITEM["prdtClNm"]) or FALLBACK_CATEGORY
    row = to_csv_row(API_ITEM, category, "police_api", date(2026, 11, 19))
    assert row["normalized_search_text"] == "신한카드(홍*동) 카드번호 1234-****-****-3456 검정 카드 > 신용(체크)카드"
    # 절대 규칙 3 — 원문 칸과 raw_payload는 API 원문 그대로
    assert row["item_name"] == "신한카드(홍길동)"
    assert row["description"] == "카드번호 1234-5678-9012-3456"
    assert "1234-5678-9012-3456" in row["raw_payload"] and "홍길동" in row["raw_payload"]


def test_ingestion_does_not_apply_bracket_rule_outside_high_risk_categories():
    item = {**API_ITEM, "fdPrdtNm": "우산(장우산)", "fdSbjt": "", "prdtClNm": "기타물품 > 기타"}
    assert build_search_text(item) == "우산(장우산) 검정 기타물품 > 기타"


# --- 기존 행 재마스킹(운영에서 실행하지 않음 — 가짜 저장소로만 검증) ---

class FakeRemaskStore:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[tuple[int, str]] = []

    def fetch_page(self, after_id, limit):
        return [row for row in self.rows if row["id"] > after_id][:limit]

    def update_search_text(self, item_id, text):
        self.updates.append((item_id, text))


def _stored_rows():
    raw_items = [
        API_ITEM,
        {**API_ITEM, "fdPrdtNm": "검정 지갑", "fdSbjt": "", "prdtClNm": "지갑 > 남성용 지갑"},
        {**API_ITEM, "fdPrdtNm": "주민등록증(김영수)", "fdSbjt": "", "prdtClNm": "증명서 > 신분증"},
    ]
    # 마스킹 도입 전 저장값을 재현한다: 정규화만 한 원문 결합.
    from retriever_lost_found.ingestion.categories import normalize_text

    return [
        {
            "id": index,
            "raw_category_name": normalize_text(item["prdtClNm"]),
            "normalized_search_text": normalize_text(
                " ".join(item.get(key, "") for key in ("fdPrdtNm", "fdSbjt", "clrNm", "prdtClNm"))
            ),
            "expected": build_search_text(item),
        }
        for index, item in enumerate(raw_items, start=1)
    ]


def test_remask_plan_matches_new_ingestion_output_and_skips_unchanged_rows():
    rows = _stored_rows()
    updates = plan_updates(rows)
    assert [update["id"] for update in updates] == [1, 3]  # 2번(검정 지갑)은 바뀌지 않아 쓰지 않는다
    for update in updates:
        assert update["normalized_search_text"] == rows[update["id"] - 1]["expected"]


def test_remask_dry_run_writes_nothing_and_apply_writes_only_changed_rows():
    rows = _stored_rows()
    store = FakeRemaskStore(rows)
    summary = run_remask(store, apply=False, page_size=2)
    assert store.updates == []
    assert summary == {"mode": "dry_run", "scanned": 3, "changed": 2, "updated": 0, "last_id": 3, "complete": True}

    summary = run_remask(store, apply=True, page_size=2)
    assert [item_id for item_id, _ in store.updates] == [1, 3]
    assert summary["updated"] == 2


def test_remask_resumes_after_last_id():
    store = FakeRemaskStore(_stored_rows())
    summary = run_remask(store, apply=True, page_size=1, start_after_id=1)
    assert [item_id for item_id, _ in store.updates] == [3]
    assert summary["scanned"] == 2


def test_remask_is_a_no_op_after_new_ingestion():
    rows = [{**row, "normalized_search_text": row["expected"]} for row in _stored_rows()]
    assert plan_updates(rows) == []


# --- 임베딩 API 입력 ---

class EmbeddingStore:
    def __init__(self, inputs, categories=None, categories_error=False):
        self.inputs = inputs
        self.category_by_id = categories if categories is not None else {i: "기타물품 > 기타" for i in inputs}
        self.categories_error = categories_error
        self.saved: dict[int, str] = {}
        self.finished: tuple | None = None

    def categories(self, item_ids):
        if self.categories_error:
            raise RuntimeError("임베딩 DB 작업 실패")
        return {i: self.category_by_id[i] for i in item_ids if i in self.category_by_id}

    def batch(self, run, after):
        return [{"item_id": i, "embedded_input": text, "content_hash": "h"}
                for i, text in self.inputs.items() if i > after and i not in self.saved]

    def save(self, run, records):
        for record in records:
            self.saved[record["item_id"]] = record["embedded_input"]
        return len(records)

    def checkpoint(self, run, after, saved, failed):
        pass

    def finish(self, run, exhausted, error=None):
        self.finished = (exhausted, error)
        return {"status": "succeeded" if error is None else "failed"}


def test_embedding_api_receives_masked_text_while_snapshot_keeps_the_original():
    sent: list[str] = []

    class Provider:
        def embed_many(self, texts, **kwargs):
            sent.extend(texts)
            return [[0.5] * 1024 for _ in texts]

    original = "검정 신한카드 1234-5678-9012-3456 김철수 명의"
    plain = "검정 지갑"
    store = EmbeddingStore({1: original, 2: plain})
    run_slice(store, Provider(), {"id": 1, "after_id": 0}, budget=60)
    assert sent == ["검정 신한카드 1234-****-****-3456 김*수 명의", plain]
    # DB 해시 검증용 스냅숏은 원문 — 기존 저장·재사용 규칙과 맞물린다.
    assert store.saved == {1: original, 2: plain}
    assert mask_text(original) == sent[0]


# --- 임베딩 입력의 괄호 이름 규칙: 배치 행의 분류로 고위험 여부를 판정한다(PRD 3.3 · Q5) ---

class _Provider:
    def __init__(self):
        self.sent: list[str] = []

    def embed_many(self, texts, **kwargs):
        self.sent.extend(texts)
        return [[0.5] * 1024 for _ in texts]


def test_embedding_masks_bracket_names_only_for_high_risk_categories():
    provider = _Provider()
    store = EmbeddingStore(
        {1: "검정 신용카드(홍길동)", 2: "검정 지갑(김철수)", 3: "검정 가방(홍길동)"},
        categories={1: "카드 > 신용(체크)카드", 2: "지갑 > 남성용 지갑", 3: "가방 > 쌕"},
    )
    run_slice(store, provider, {"id": 1, "after_id": 0}, budget=60)
    # positive: 고위험 분류는 괄호 속 이름을 가린다. negative: 가방은 그대로(괄호는 대개 브랜드·특징).
    assert provider.sent == ["검정 신용카드(홍*동)", "검정 지갑(김*수)", "검정 가방(홍길동)"]
    # 해시 검증용 스냅숏은 원문 그대로다.
    assert store.saved == {1: "검정 신용카드(홍길동)", 2: "검정 지갑(김철수)", 3: "검정 가방(홍길동)"}


def test_embedding_masks_strictly_when_the_category_is_unknown():
    provider = _Provider()
    store = EmbeddingStore({1: "검정 카드(홍길동)"}, categories={})
    run_slice(store, provider, {"id": 1, "after_id": 0}, budget=60)
    assert provider.sent == ["검정 카드(홍*동)"]


def test_embedding_sends_nothing_when_the_category_lookup_fails():
    provider = _Provider()
    store = EmbeddingStore({1: "검정 카드(홍길동)"}, categories_error=True)
    result = run_slice(store, provider, {"id": 1, "after_id": 0}, budget=60)
    assert provider.sent == []
    assert store.saved == {}
    assert result == {"status": "failed"}
    assert store.finished == (False, "database_or_ingestion")


def test_embedding_store_reads_categories_by_item_id_in_one_request():
    from retriever_lost_found.embedding.store import EmbeddingStore as RealStore

    calls = []
    store = RealStore.__new__(RealStore)

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("query")))
        return [{"id": 2, "raw_category_name": "카드 > 신용(체크)카드"}, {"id": 1, "raw_category_name": None}]

    store._request = fake_request
    assert store.categories([2, 1, 2]) == {1: None, 2: "카드 > 신용(체크)카드"}
    assert calls == [("GET", "found_items", [("select", "id,raw_category_name"), ("id", "in.(1,2)")])]
    calls.clear()
    assert store.categories([]) == {}
    assert calls == []
