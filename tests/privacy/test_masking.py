"""PRD 3.3 · Q5 — 습득물 개인정보 출력 마스킹 규칙.

모든 사례는 합성 픽스처다. 운영 데이터에서 뽑은 표본을 쓰지 않는다(AGENTS.md 7장 · 작업 금지 사항).
가려야 하는 사례(positive)는 가리면 안 되는 사례(negative)와 쌍으로 둔다(AGENTS.md 7장).
"""

import pytest

from retriever_lost_found.privacy.masking import (
    HIGH_RISK_CATEGORY_PARENTS,
    IDENTIFIER_QUERY_ERROR,
    contains_identifier,
    is_high_risk_category,
    mask_item_row,
    mask_numbers,
    mask_text,
)


# --- 카드번호: 13~19자리, 공백·하이픈 허용, 앞 4자리와 뒤 4자리만 남기고 가운데를 가린다 ---
# 2026-11-20 사용자 결정 — 앞 4자리는 발급사 구분용이라 본인 확인에 쓰이고, PCI DSS 표시 범위(앞 6·뒤 4) 안이다.

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("신한카드 1234-5678-9012-3456", "신한카드 1234-****-****-3456"),
        ("카드(1234 5678 9012 3456)", "카드(1234 **** **** 3456)"),
        ("1234567890123456", "1234********3456"),
        ("아멕스 3782-822463-10005", "아멕스 3782-******-*0005"),  # 15자리
        ("4000123412341234567", "4000***********4567"),  # 19자리
        ("1234-5678-90123", "1234-****-*0123"),  # 13자리
        # 일부만 가려진 형식도 앞 4·뒤 4를 뺀 가운데를 모두 가린다
        ("1234-56**-****-5678", "1234-****-****-5678"),
        # 앞 4자리가 이미 가려진 형식은 그대로 둔다(덜 가려진 값을 새로 드러내지 않는다)
        ("****-5678-9012-3456", "****-****-****-3456"),
    ],
)
def test_card_numbers_keep_first_four_and_last_four_digits(raw, expected):
    assert mask_text(raw) == expected


def test_card_masking_is_idempotent():
    once = mask_text("신한카드 1234-5678-9012-3456")
    assert mask_text(once) == once


@pytest.mark.parametrize(
    "raw",
    [
        "카드 끝자리 3456",  # 4자리
        "2026-11-20 습득",  # 날짜 8자리
        "123456789012",  # 12자리 — 카드 길이 미만
        "12345678901234567890",  # 20자리 — 카드 길이 초과
        "****-****-****-3456",  # 이미 가려진 형식
        "갤럭시 S24 256GB",
    ],
)
def test_non_card_numbers_are_left_unchanged(raw):
    assert mask_text(raw) == raw


# --- 주민·외국인등록번호: 전부 가린다(일부만 가려진 형태 포함) ---

@pytest.mark.parametrize(
    "raw",
    [
        "주민등록증 900101-1234567",
        "주민등록증 900101 1234567",
        "주민등록증 9001011234567",
        "외국인등록증 900101-5234567",
        "외국인등록증 900101-8234567",
        "주민등록증 900101-1******",  # 뒷자리 일부만 가려진 형태
        "주민등록증 900101-2xxxxxx",
    ],
)
def test_resident_registration_numbers_are_fully_masked(raw):
    masked = mask_text(raw)
    assert masked.endswith("******-*******")
    assert "900101" not in masked
    assert not any(character.isdigit() for character in masked)


@pytest.mark.parametrize(
    "raw",
    [
        "생년월일 900101",  # 6자리 생년월일만 있는 경우
        "사업자 123-45-67890",  # 10자리
        "******-*******",  # 이미 가려진 형식
    ],
)
def test_non_registration_numbers_are_not_masked_as_registration(raw):
    assert mask_text(raw) == raw


# --- 휴대폰(01X): 가운데를 가린다 ---

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("연락처 010-1234-5678", "연락처 010-****-5678"),
        ("01012345678", "010-****-5678"),
        ("011-123-4567", "011-****-4567"),
        ("010 1234 5678", "010-****-5678"),
        ("010.1234.5678", "010-****-5678"),
    ],
)
def test_mobile_numbers_hide_the_middle_digits(raw, expected):
    assert mask_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "보관 02-123-4567",  # 기관 유선 전화는 개인 번호가 아니다
        "012-1234-5678",  # 01X 형식 아님
        "010-****-5678",  # 이미 가려진 형식
    ],
)
def test_non_mobile_numbers_are_left_unchanged(raw):
    assert mask_text(raw) == raw


def test_contiguous_thirteen_digits_in_registration_shape_are_fully_masked():
    # 이어 쓴 13자리의 7번째 숫자가 1~8이면 주민번호로 보고 전부 가린다(카드 뒤 4자리도 남기지 않는다).
    assert mask_text("1234567890123") == "******-*******"


def test_six_seven_split_outside_registration_range_is_still_masked_as_a_number():
    # 뒷자리 첫 숫자가 9·0이면 등록번호 형식은 아니지만 13자리 번호이므로 카드 규칙으로 가린다(덜 가리기보다 더 가린다).
    assert mask_text("900101-9234567") == "9001**-***4567"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("010-1234-5678 010-9876-5432", "010-****-5678 010-****-5432"),
        ("010 1234 5678 010 9876 5432", "010-****-5678 010-****-5432"),
        ("01012345678,01098765432", "010-****-5678,010-****-5432"),
    ],
)
def test_adjacent_mobile_numbers_are_masked_separately_not_as_card(raw, expected):
    assert mask_text(raw) == expected


def test_mobile_number_followed_by_card_number_masks_both():
    assert mask_text("010-1234-5678 1234-5678-9012-3456") == "010-****-5678 1234-****-****-3456"


# --- 이름: 문맥 규칙(모든 분류) ---

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("국민카드(홍길동 명의)", "국민카드(홍*동 명의)"),
        ("신용카드 김철수명의", "신용카드 김*수명의"),
        ("체크카드 남궁민수 명의", "체크카드 남**수 명의"),
        ("성명: 홍길동", "성명: 홍*동"),
        ("이름:이영희", "이름:이*희"),
        ("소유자 : 박지성", "소유자 : 박*성"),
        ("학생증 이름 김민", "학생증 이름 김*"),
    ],
)
def test_names_in_context_are_masked(raw, expected):
    assert mask_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "본인 명의 휴대폰",
        "법인 명의 카드",
        "회사명의 법인카드",
        "타인 명의",
        "법인카드(회사 명의)",
        "이름표 달린 가방",
        "이름 없음",
        "성명: 미상",
        "홍*동 명의",  # 이미 가려진 이름
        "성명: 홍*동",
        "명의 확인 불가",
    ],
)
def test_context_words_without_a_personal_name_are_left_unchanged(raw):
    assert mask_text(raw) == raw


# --- 이름: 고위험 분류의 괄호 속 2~4자 한글 ---

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("신용카드(홍길동)", "신용카드(홍*동)"),
        ("주민등록증 (김영수)", "주민등록증 (김*수)"),
        ("학생증[이민]", "학생증[이*]"),
        ("운전면허증(황보영철)", "운전면허증(황**철)"),
    ],
)
def test_bracketed_names_are_masked_in_high_risk_categories(raw, expected):
    assert mask_text(raw, high_risk=True) == expected


def test_bracketed_names_are_not_masked_outside_high_risk_categories():
    # 가방·의류 등에서 괄호 속 한글은 대개 브랜드·특징이다 — 문맥 규칙만 적용한다.
    assert mask_text("가방(홍길동)", high_risk=False) == "가방(홍길동)"


@pytest.mark.parametrize(
    "raw",
    [
        "지갑(검정)",  # 색상
        "지갑(검정색)",
        "카드(신한)",  # 카드사 — 성씨 '신'으로 시작하지만 제외 사전
        "카드(우리카드)",
        "카드(국민)",
        "카드(하나)",
        "카드(현대)",
        "카드(삼성)",
        "체크카드(농협)",
        "카드(기업은행)",
        "카드(신분증)",  # 접미사 '증'
        "증명서(학생증)",
        "카드(교통카드)",
        "지갑(남성용)",  # 접미사 '용'
        "유가증권(상품권)",  # 접미사 '권'
        "지갑(장지갑)",
        "카드(티머니)",
        "지갑(루이비통)",  # 브랜드
        "카드(홍*동)",  # 이미 가려진 이름
        "카드(가나다라마)",  # 5자 — 대상 길이 밖
        "카드(아이)",  # 흔한 성씨로 시작하지 않음
        "카드(신세계)",  # 가맹점
        "카드(이마트)",
        "카드(주민등록)",  # 발급 용도
        "증명서(공무원)",
        "카드(장애인)",
    ],
)
def test_bracketed_non_names_are_left_unchanged_in_high_risk_categories(raw):
    assert mask_text(raw, high_risk=True) == raw


# --- 고위험 분류 판정: category_parent 정확 일치 ---

@pytest.mark.parametrize(
    "category",
    [
        "카드 > 신용(체크)카드",
        "지갑 > 남성용 지갑",
        "증명서 > 신분증",
        "서류 > 기타서류",
        "유가증권 > 상품권",
        "현금 > 현금",
        "카드",
    ],
)
def test_high_risk_categories_match_parent_exactly(category):
    assert is_high_risk_category(category)


@pytest.mark.parametrize(
    "category",
    [
        "휴대폰 > 스마트폰",
        "가방 > 백팩",
        "기타물품 > 카드케이스",  # 하위분류에 '카드'가 있어도 부분일치로 고위험이 되지 않는다(PRD 4.5)
        "전자기기 > 카드리더기",
        "카드지갑",  # 상위분류 이름이 다르면 정확 일치가 아니다
        "",
        None,
    ],
)
def test_other_categories_are_not_high_risk(category):
    assert not is_high_risk_category(category)


def test_high_risk_set_is_the_agreed_six_parents():
    assert HIGH_RISK_CATEGORY_PARENTS == frozenset({"카드", "지갑", "증명서", "서류", "유가증권", "현금"})


# --- 행 단위 출력 마스킹 ---

def test_mask_item_row_masks_text_fields_and_keeps_the_original_row():
    row = {
        "id": 7,
        "storage_location_id": 3,
        "raw_category_name": "카드 > 신용(체크)카드",
        "color_name": "검정",
        "item_name": "신한카드(홍길동)",
        "description": "카드번호 1234-5678-9012-3456, 연락처 010-1234-5678",
        "normalized_search_text": "신한카드(홍길동) 카드번호 1234-5678-9012-3456",
        "raw_storage_name": "서울역 유실물센터",
        "image_url": "https://example.test/a.jpg",
    }
    original = dict(row)
    masked = mask_item_row(row)
    assert row == original  # 절대 규칙 3 — 입력(원문)은 바꾸지 않는다
    assert masked["item_name"] == "신한카드(홍*동)"
    assert masked["description"] == "카드번호 1234-****-****-3456, 연락처 010-****-5678"
    assert masked["normalized_search_text"] == "신한카드(홍*동) 카드번호 1234-****-****-3456"
    for key in ("id", "storage_location_id", "raw_category_name", "color_name", "raw_storage_name", "image_url"):
        assert masked[key] == row[key]


def test_mask_item_row_leaves_ordinary_rows_unchanged():
    row = {"raw_category_name": "가방 > 백팩", "item_name": "검정 백팩(나이키)", "description": "노트북 들어 있음"}
    assert mask_item_row(row) == row


def test_mask_item_row_tolerates_missing_and_null_fields():
    assert mask_item_row({"id": 1, "item_name": None}) == {"id": 1, "item_name": None}


@pytest.mark.parametrize(
    "raw",
    [
        "신한카드(홍길동) 1234-5678-9012-3456",
        "주민등록증 900101-1234567 김철수 명의",
        "연락처 010-1234-5678",
        "가방",
    ],
)
def test_masking_is_idempotent(raw):
    once = mask_text(raw, high_risk=True)
    assert mask_text(once, high_risk=True) == once


# --- 사용자 대화: 번호 패턴만 ---

def test_mask_numbers_masks_only_number_patterns():
    text = "홍길동 명의 카드 1234-5678-9012-3456 잃어버렸어요 010-1234-5678"
    assert mask_numbers(text) == "홍길동 명의 카드 1234-****-****-3456 잃어버렸어요 010-****-5678"


# --- 검색어 거절 ---

@pytest.mark.parametrize(
    "query",
    ["1234-5678-9012-3456", "1234567890123456", "900101-1234567", "010-1234-5678", "01012345678", "카드 010 1234 5678"],
)
def test_identifier_queries_are_detected(query):
    assert contains_identifier(query)


@pytest.mark.parametrize("query", ["검정 지갑", "신한카드", "카드 3456", "아이폰 15", "02-123-4567", "홍길동"])
def test_ordinary_queries_are_not_detected(query):
    assert not contains_identifier(query)


def test_identifier_error_guides_users_to_safe_search_terms():
    assert "카드사" in IDENTIFIER_QUERY_ERROR and "색상" in IDENTIFIER_QUERY_ERROR
