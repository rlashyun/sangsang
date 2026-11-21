"""PRD 3.3 · Q5 (2026-11-21 결정 ⓑ) — 검색 문서의 번호 마스킹.

화면에서 가린 번호를 검색어로 맞혀 보지 못하게 search_text의 번호를 가린다. 이름은 검색되게 둔다.
SQL(20261121210000)은 Python `mask_numbers`와 같은 정규식을 써야 하므로 패턴 문자열을 직접 대조한다.
모든 데이터는 합성 픽스처이며 DB·외부 API를 호출하지 않는다(AGENTS.md 7장).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from retriever_lost_found.privacy import masking
from retriever_lost_found.search.hybrid import keyword_scores

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20261121210000_mask_search_document_numbers.sql"
)

CARD_ITEM = {
    "id": 1,
    "item_name": "신한카드(홍길동)",
    "raw_category_name": "카드 > 신용(체크)카드",
    "color_name": "검정",
    "description": "카드번호 1111-5678-9012-2222",
}
PHONE_ITEM = {
    "id": 2,
    "item_name": "검정 지갑",
    "raw_category_name": "지갑 > 남성용 지갑",
    "color_name": "검정",
    "description": "연락처 010-2468-1357",
}
REGISTRATION_ITEM = {
    "id": 3,
    "item_name": "주민등록증",
    "raw_category_name": "증명서 > 신분증",
    "color_name": "",
    "description": "900101-1234567",
}
ITEMS = [CARD_ITEM, PHONE_ITEM, REGISTRATION_ITEM]
# 보이는 자리(앞 4·뒤 4)는 가운데 숫자와 두 글자 조각이 겹치지 않게 골랐다. BM25는 글자 bigram도 비교하므로
# 겹치면 보이는 자리 덕분에 점수가 생길 뿐 가운데 숫자가 드러난 것은 아니다.


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _sql_pattern(name: str) -> str:
    match = re.search(rf"{name} constant text := '([^']*)';", _sql())
    assert match, f"{name} 정규식을 마이그레이션에서 찾지 못했다"
    return match.group(1)


@pytest.mark.parametrize(
    ("sql_name", "python_regex"),
    [
        ("v_registration", masking._REGISTRATION_RE),
        ("v_mobile", masking._MOBILE_RE),
        ("v_card", masking._CARD_RE),
    ],
)
def test_sql_number_patterns_match_python_rules(sql_name, python_regex) -> None:
    # 규칙 정본은 Python이다. 한쪽만 고치면 화면과 검색이 서로 다르게 가린다.
    assert _sql_pattern(sql_name) == python_regex.pattern


def test_sql_applies_rules_in_python_order_with_same_replacements() -> None:
    sql = _sql()
    registration = sql.index("regexp_replace(p_text, v_registration, '******-*******', 'g')")
    mobile = sql.index("regexp_replace(v_text, v_mobile, '\\1-****-\\2', 'g')")
    card = sql.index("regexp_instr(v_source, v_card, 1, v_nth)")
    assert registration < mobile < card
    # 카드는 앞 4·뒤 4 칸을 남긴다 — _mask_card와 같은 경계
    assert "v_cell > 4 and v_cell <= v_cells - 4" in sql


def test_trigger_masks_search_text_only_and_function_is_service_role_only() -> None:
    sql = " ".join(_sql().split())
    assert (
        "lower(public.mask_search_numbers(concat_ws(' ',new.item_name,new.raw_category_name,"
        "new.color_name,new.description)))" in sql
    )
    # detail(응답용)·content_hash(임베딩 기준)는 그대로다. 응답은 앱의 mask_item_row가 가린다.
    assert "public.search_document(new))" in sql
    # seed 함수도 같은 규칙으로 search_text를 만든다 — 원문 번호가 남는 다른 쓰기 경로가 없어야 한다
    assert (
        "lower(public.mask_search_numbers(concat_ws(' ',f.item_name,f.raw_category_name,"
        "f.color_name,f.description)))" in sql
    )
    assert "lower(concat_ws(" not in sql
    assert "bge_m3_input(new.normalized_search_text,new.item_name)" in sql
    # 절대 규칙 6
    assert "revoke all on function public.mask_search_numbers(text) from public,anon,authenticated" in sql
    assert "grant execute on function public.mask_search_numbers(text) to service_role" in sql
    # 기존 문서는 결과가 달라지는 행만 고치고 found_items 원문은 건드리지 않는다(절대 규칙 3)
    assert "update public.found_item_search_documents d" in sql
    assert "is distinct from d.search_text" in sql
    assert "update public.found_items" not in sql


@pytest.mark.parametrize(
    ("query", "hidden_item_id"),
    [
        ("5678", 1),  # 카드 가운데 자리
        ("9012", 1),
        ("2468", 2),  # 휴대폰 가운데 자리
        ("900101", 3),  # 주민번호 앞자리(생년월일)
        ("1234567", 3),
    ],
)
def test_hidden_digits_are_not_searchable(query, hidden_item_id) -> None:
    assert hidden_item_id not in keyword_scores([dict(item) for item in ITEMS], query)


@pytest.mark.parametrize(
    ("query", "item_id"),
    [
        ("신한카드", 1),  # 일반 검색어는 그대로 찾는다 — 위 0건 단언이 항상 빈 결과라서 통과하는 것이 아님
        ("2222", 1),  # 화면에도 보이는 카드 뒤 4자리
        ("1357", 2),  # 화면에도 보이는 휴대폰 뒤 4자리
        ("홍길동", 1),  # ⓑ — 이름은 가리지 않아 본인 이름으로 찾을 수 있다
        ("주민등록증", 3),
    ],
)
def test_visible_text_and_names_stay_searchable(query, item_id) -> None:
    assert item_id in keyword_scores([dict(item) for item in ITEMS], query)


def test_db_search_text_is_used_as_is() -> None:
    # 운영 경로: DB가 돌려준 search_text(이미 번호를 가림)를 그대로 쓴다.
    stored = {**CARD_ITEM, "search_text": masking.mask_numbers("신한카드 카드번호 1111-5678-9012-2222").lower()}
    assert 1 not in keyword_scores([stored], "5678")
    assert 1 in keyword_scores([stored], "신한카드")


def test_full_width_digits_left_by_sql_are_masked_before_bm25() -> None:
    # PostgreSQL `\d`는 ASCII 숫자만 가리므로 전각 카드번호는 DB search_text에 그대로 남을 수 있다.
    # NFKC가 이를 ASCII로 바꿔도 가운데 자리가 BM25 조각이 되지 않아야 한다.
    stored = {**CARD_ITEM, "search_text": "신한카드 １１１１５６７８９０１２２２２２"}
    assert 1 not in keyword_scores([stored], "5678")
    assert 1 in keyword_scores([stored], "신한카드")
