from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


_WHITESPACE_RE = re.compile(r"\s+")
_CATEGORY_SEPARATOR_RE = re.compile(r"\s*[>＞]\s*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ServiceCategory:
    code: str
    name: str
    parent: str
    children: tuple[str, ...]


SERVICE_CATEGORIES: tuple[ServiceCategory, ...] = (
    ServiceCategory("bag", "가방", "가방", ("여성용가방", "남성용가방", "기타가방")),
    ServiceCategory("clothing", "의류", "의류", ("모자",)),
    ServiceCategory("electronics", "전자기기", "전자기기", ("무선이어폰",)),
    ServiceCategory(
        "wallet",
        "지갑",
        "지갑",
        ("남성용지갑", "여성용지갑", "기타지갑"),
    ),
    ServiceCategory(
        "mobile_phone",
        "휴대폰",
        "휴대폰",
        ("삼성휴대폰", "LG휴대폰", "아이폰", "기타휴대폰", "기타통신기기"),
    ),
    ServiceCategory(
        "card",
        "카드",
        "카드",
        ("신용(체크)카드", "일반카드", "교통카드", "기타카드"),
    ),
)


def normalize_text(value: str | None) -> str:
    """NFKC 정규화 후 연속 공백을 하나로 줄입니다."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def normalize_category_component(value: str | None) -> str:
    """분류 비교용으로 모든 공백을 제거하고 대소문자를 통일합니다."""
    return normalize_text(value).replace(" ", "").casefold()


def parse_product_category(value: str | None) -> tuple[str, str] | None:
    """`상위분류 > 하위분류`를 비교 가능한 두 구성요소로 변환합니다."""
    normalized = normalize_text(value)
    parts = _CATEGORY_SEPARATOR_RE.split(normalized, maxsplit=1)
    if len(parts) != 2:
        return None
    parent = normalize_category_component(parts[0])
    child = normalize_category_component(parts[1])
    if not parent or not child:
        return None
    return parent, child


_CATEGORY_BY_PAIR = {
    (
        normalize_category_component(category.parent),
        normalize_category_component(child),
    ): category
    for category in SERVICE_CATEGORIES
    for child in category.children
}


def classify_product_category(value: str | None) -> ServiceCategory | None:
    """서비스 6개 카테고리에 해당하면 대표 카테고리를 반환합니다."""
    parsed = parse_product_category(value)
    if parsed is None:
        return None
    return _CATEGORY_BY_PAIR.get(parsed)


def select_supported_items(
    items: Iterable[dict[str, Any]],
) -> list[tuple[dict[str, Any], ServiceCategory]]:
    """`prdtClNm`이 허용 목록과 정확히 일치하는 항목만 선택합니다."""
    selected: list[tuple[dict[str, Any], ServiceCategory]] = []
    for item in items:
        category = classify_product_category(str(item.get("prdtClNm", "")))
        if category is not None:
            selected.append((item, category))
    return selected
