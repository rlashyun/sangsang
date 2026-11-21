"""PRD 3.3 · Q5 — 마스킹 전 원문 기반 벡터 재생성 도구. 가짜 저장소·가짜 임베딩 클라이언트만 쓴다(AGENTS.md 7장)."""

from __future__ import annotations

from pathlib import Path

import pytest

from retriever_lost_found.privacy.reembed import plan_candidates, run_reembed

CUTOFF = "2026-11-20T18:50:00+09:00"
MIGRATION = (
    Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261121151000_replace_masked_embeddings.sql"
)

ROWS = [
    # 번호 — 분류와 상관없이 다시 만든다
    {"item_id": 1, "embedded_input": "검정 카드 1111-5678-9012-2222", "content_hash": "a" * 64},
    # 고위험 분류의 괄호 이름
    {"item_id": 2, "embedded_input": "신한카드(홍길동)", "content_hash": "b" * 64},
    # 일반 분류의 괄호 단어 — 괄호 규칙을 쓰지 않으므로 그대로
    {"item_id": 3, "embedded_input": "아이폰(김민수)", "content_hash": "c" * 64},
    # 가릴 것이 없는 물품
    {"item_id": 4, "embedded_input": "검정 지갑", "content_hash": "d" * 64},
    # 분류를 읽지 못한 물품 — 고위험으로 본다
    {"item_id": 5, "embedded_input": "학생증(박지성)", "content_hash": "e" * 64},
]
CATEGORIES = {1: "기타물품 > 기타", 2: "카드 > 신용(체크)카드", 3: "휴대폰 > 스마트폰", 4: "지갑 > 남성용 지갑"}


class FakeStore:
    def __init__(self) -> None:
        self.replaced: list[dict] = []
        self.cutoffs: set[str] = set()

    def fetch_page(self, after_id, limit, embedded_before):
        self.cutoffs.add(embedded_before)
        return [row for row in ROWS if row["item_id"] > after_id][:limit]

    def categories(self, item_ids):
        return {item_id: CATEGORIES[item_id] for item_id in item_ids if item_id in CATEGORIES}

    def replace(self, records, embedded_before):
        self.cutoffs.add(embedded_before)
        self.replaced.extend(records)
        return len(records)


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def embed_many(self, texts, *, timeout=30):
        self.sent.extend(texts)
        return [[0.1] * 1024 for _ in texts]


def test_plan_picks_only_rows_whose_input_changes_when_masked() -> None:
    picked = {row["item_id"]: row["masked_input"] for row in plan_candidates(ROWS, CATEGORIES)}
    assert set(picked) == {1, 2, 5}
    assert picked[1] == "검정 카드 1111-****-****-2222"
    assert picked[2] == "신한카드(홍*동)"
    assert picked[5] == "학생증(박*성)"


def test_dry_run_calls_no_api_and_writes_nothing() -> None:
    store = FakeStore()
    summary = run_reembed(store, None, embedded_before=CUTOFF, apply=False, page_size=2)
    assert summary == {"mode": "dry_run", "scanned": 5, "candidates": 3, "replaced": 0,
                       "skipped_changed": 0, "last_id": 5, "complete": True}
    assert store.replaced == []


def test_apply_sends_only_masked_text_and_keeps_raw_snapshot_for_hash_check() -> None:
    store, client = FakeStore(), FakeClient()
    summary = run_reembed(store, client, embedded_before=CUTOFF, apply=True, page_size=2)
    assert summary["replaced"] == 3 and summary["complete"]
    assert client.sent == ["검정 카드 1111-****-****-2222", "신한카드(홍*동)", "학생증(박*성)"]
    # 외부 API로 원문 번호·이름이 0건 나간다
    assert not any("5678" in text or "홍길동" in text or "박지성" in text for text in client.sent)
    # DB 해시 검증용 스냅숏은 원문 그대로 넘긴다(DB가 현재 물품 입력과 대조한다)
    assert [row["embedded_input"] for row in store.replaced] == [ROWS[0]["embedded_input"],
                                                               ROWS[1]["embedded_input"],
                                                               ROWS[4]["embedded_input"]]
    assert store.cutoffs == {CUTOFF}


def test_apply_without_key_and_bad_cutoff_fail_before_db() -> None:
    with pytest.raises(ValueError):
        run_reembed(FakeStore(), None, embedded_before=CUTOFF, apply=True)
    with pytest.raises(ValueError):
        run_reembed(FakeStore(), FakeClient(), embedded_before="어제", apply=False)


def test_replace_rpc_only_updates_unchanged_old_vectors_and_is_service_role_only() -> None:
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").split())
    assert "set embedding=s.embedding::extensions.halfvec(1024), embedded_at=now()" in sql
    for guard in ("e.embedded_at<p_embedded_before", "e.embedded_input=s.embedded_input",
                  "e.content_hash=s.content_hash", "f.embedding_input=s.embedded_input", "e.is_current"):
        assert guard in sql
    assert "update public.found_items" not in sql  # 절대 규칙 3
    assert ("revoke all on function public.replace_masked_bge_m3_embeddings(jsonb,timestamptz) "
            "from public,anon,authenticated") in sql
    assert "grant execute on function public.replace_masked_bge_m3_embeddings(jsonb,timestamptz) to service_role" in sql
