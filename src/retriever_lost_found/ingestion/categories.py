from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_WHITESPACE_RE = re.compile(r"\s+")
_CATEGORY_SEPARATOR_RE = re.compile(r"\s*[>＞]\s*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ServiceCategory:
    code: str
    name: str


# PRD 2.2, 4.5 — 2026-11-17 전체 물품 확대. 분류 단위는 API `prdtClNm`의 상위분류다.
# 2026-11-12~18 두 API 원본에서 본 상위분류 21개이며, 코드와 이름은
# DB `item_categories`(20260826133434 기존 6개 + 20261120090000 추가 15개)와 같아야 한다.
SERVICE_CATEGORIES: tuple[ServiceCategory, ...] = (
    ServiceCategory("bag", "가방"),
    ServiceCategory("clothing", "의류"),
    ServiceCategory("electronics", "전자기기"),
    ServiceCategory("wallet", "지갑"),
    ServiceCategory("mobile_phone", "휴대폰"),
    ServiceCategory("card", "카드"),
    ServiceCategory("jewelry", "귀금속"),
    ServiceCategory("etc", "기타물품"),
    ServiceCategory("books", "도서용품"),
    ServiceCategory("ownerless", "무주물"),
    ServiceCategory("industrial", "산업용품"),
    ServiceCategory("documents", "서류"),
    ServiceCategory("shopping_bag", "쇼핑백"),
    ServiceCategory("sports", "스포츠용품"),
    ServiceCategory("instrument", "악기"),
    ServiceCategory("securities", "유가증권"),
    ServiceCategory("abandoned", "유류품"),
    ServiceCategory("car", "자동차"),
    ServiceCategory("certificate", "증명서"),
    ServiceCategory("computer", "컴퓨터"),
    ServiceCategory("cash", "현금"),
)

# 상위분류가 목록에 없으면 버리지 않고 이 분류로 저장한다. 원문 분류명은 그대로 남는다.
FALLBACK_CATEGORY = next(category for category in SERVICE_CATEGORIES if category.code == "etc")


def normalize_text(value: str | None) -> str:
    """NFKC 정규화 후 연속 공백을 하나로 줄입니다."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def normalize_category_component(value: str | None) -> str:
    """분류 비교용으로 모든 공백을 제거하고 대소문자를 통일합니다."""
    return normalize_text(value).replace(" ", "").casefold()


def category_key(value: str | None) -> str:
    """DB 매핑 키 형식(`category_mappings.normalized_raw_category`)으로 바꿉니다.

    NFKC·casefold 후 글자와 숫자만 남깁니다. 예: `카드 > 신용(체크)카드` → `카드신용체크카드`.
    """
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def category_parent(value: str | None) -> str:
    """`상위분류 > 하위분류`에서 상위분류 원문을 돌려줍니다. 구분자가 없으면 전체가 상위분류입니다."""
    return _CATEGORY_SEPARATOR_RE.split(normalize_text(value), maxsplit=1)[0]


_CATEGORY_BY_PARENT = {
    normalize_category_component(category.name): category for category in SERVICE_CATEGORIES
}


def classify_product_category(value: str | None) -> ServiceCategory | None:
    """상위분류가 서비스 분류와 정확히 일치하면 그 분류를 반환합니다. PRD 4.5 — 부분일치 금지."""
    parent = normalize_category_component(category_parent(value))
    if not parent:
        return None
    return _CATEGORY_BY_PARENT.get(parent)
