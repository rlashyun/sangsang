"""2026-11-23 사용자 결정 — 색이 확실히 다른 물품만 뺀다(PRD 4.5 검색 2차 필터 보강).

- 검색어 색: synonyms.json 색 대표어.
- 물품 색: 경찰청 팔레트 값(color_name)을 실제로 적힌 색으로 옮긴 것 + 물품 글에 적힌 색. 둘을 합친다.
  팔레트 이름과 등록자가 적은 색이 자주 다르다(코발트(짙은청록) → 남색·파란색 등, color_families.json).
- 검색어 색과 물품 색이 한 계열(families)에도 함께 없을 때만 뺀다. 색을 모르는 물품, 투명, 색 없는 검색어는 거르지 않는다.
- 일반 검색·대화형 검색(search_found_items)과 알림 판정(alerts/judge.py)이 같은 함수를 쓴다. 끄는 스위치는 없다.
"""
from __future__ import annotations

from importlib.resources import files
import json
import re
from typing import Any

from . import synonyms

_DATA = json.loads(files(__package__).joinpath("color_families.json").read_text(encoding="utf-8"))
_COLOR_CANONICALS = frozenset(group["canonical"] for group in synonyms._CONCEPTS if group["domain"] == "color")
PALETTE: dict[str, frozenset[str]] = {label: frozenset(colors) for label, colors in _DATA["palette"].items()}
_PALETTE_FOLDED = {label.casefold(): colors for label, colors in PALETTE.items()}
# 팔레트 값은 search_text·설명 안에도 그대로 들어 있다("지갑(코발트(짙은청록)색)을 습득…"). 긴 값부터 찾는다.
_PALETTE_PATTERN = re.compile("|".join(re.escape(label.casefold()) for label in sorted(PALETTE, key=len, reverse=True)))
# 줄(family)은 그 이름 색이 받아 주는 색 목록이다. 줄을 거쳐 이어 붙이지 않는다 — 회색 줄에 검정과 흰색이
# 함께 있어도 검정과 흰색은 다른 색이다. 자기 줄이 없는 색(하늘색·노랑 등)은 자기가 들어 있는 줄들을 따른다.
_ROWS = {family["name"]: set(family["colors"]) | {family["name"]} for family in _DATA["families"]}
_ACCEPTS: dict[str, set[str]] = {}
for _color in _COLOR_CANONICALS:
    _ACCEPTS[_color] = set(_ROWS.get(_color) or set().union({_color}, *(row for row in _ROWS.values() if _color in row)))
# 받아 주는 관계는 양쪽으로 본다(검정 줄의 남색 ↔ 남색 줄의 검정).
_COMPATIBLE: dict[str, set[str]] = {color: {other for other in _COLOR_CANONICALS
                                            if other in _ACCEPTS[color] or color in _ACCEPTS[other]}
                                    for color in _COLOR_CANONICALS}
# 투명은 색을 알려 주지 않는다(투명 케이스 안의 물건 색이 따로 있다).
UNKNOWN = frozenset({"투명"})


def _colors_in(text: str) -> set[str]:
    # 낱말 단위로 본다. 글자 포함으로 보면 "블랙야크"가 검정색 물품이 된다(alerts/judge.py와 같은 기준).
    words = synonyms.normalize_text(text, query=True).split()
    return {synonyms._CANONICAL[word] for word in words
            if word in synonyms._CANONICAL and synonyms._CANONICAL[word] in _COLOR_CANONICALS}


def query_colors(query: str) -> set[str]:
    return _colors_in(query) - UNKNOWN


def _item_text(item: dict[str, Any]) -> str:
    return str(item.get("search_text") or " ".join(
        str(item.get(field) or "") for field in ("item_name", "raw_category_name", "color_name", "description")))


def item_colors(item: dict[str, Any]) -> set[str]:
    text = _item_text(item)
    colors = set()
    for label in _PALETTE_PATTERN.findall(text.casefold()):
        colors |= _PALETTE_FOLDED[label]
    # 팔레트 값을 지운 뒤 글을 읽는다. "레드(연한레드)"의 '레드'가 빨강 하나로 굳지 않게 매핑 결과만 쓴다.
    colors |= _colors_in(_PALETTE_PATTERN.sub(" ", text.casefold()))
    return colors


def compatible(query_color_set: set[str], item_color_set: set[str]) -> bool:
    if not query_color_set or not item_color_set or item_color_set & UNKNOWN:
        return True
    return any(_COMPATIBLE[color] & item_color_set for color in query_color_set)


def keeps(query: str, item: dict[str, Any], *, wanted: set[str] | None = None) -> bool:
    """True면 남긴다. 검색어 색을 이미 구했으면 wanted로 넘겨 다시 계산하지 않는다."""
    wanted = query_colors(query) if wanted is None else wanted
    return compatible(wanted, item_colors(item))
