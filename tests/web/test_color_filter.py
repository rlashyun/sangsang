"""2026-11-23 사용자 결정 — 색이 확실히 다른 물품만 뺀다(색 계열 묶음, 경찰청 색 팔레트 매핑)."""
import json
from importlib.resources import files

import pytest

from retriever_lost_found.search import color_filter
from retriever_lost_found.search.color_filter import item_colors, keeps, query_colors
from retriever_lost_found.search.hybrid import search_found_items


def item(name, color_name="", description="", **extra):
    return {"id": extra.pop("id", 1), "item_name": name, "raw_category_name": "지갑",
            "color_name": color_name, "description": description, **extra}


def test_query_colors_use_dictionary_canonicals():
    assert query_colors("검은색 지갑") == {"검정"}
    assert query_colors("하늘색 캡모자") == {"하늘색"}
    assert query_colors("지갑") == set()
    assert query_colors("블랙야크 지갑") == set()


def test_palette_label_maps_to_colors_people_actually_write():
    # 스냅숏 27,747건: 코발트(짙은청록)로 등록된 물품은 이름에 남색·파란색이 적혀 있다.
    assert {"남색", "파랑"} <= item_colors(item("지갑", "코발트(짙은청록)"))
    assert "하늘색" in item_colors(item("파우치", "터키오이스(옥)"))
    assert "베이지" in item_colors(item("가방", "피치오렌지(오렌지)"))
    assert "분홍" in item_colors(item("지갑", "레드(연한레드)"))


def test_item_text_colors_are_added_to_the_palette_colors():
    colors = item_colors(item("남색 모자", "블랙(검정)"))
    assert {"검정", "남색"} <= colors


@pytest.mark.parametrize(("query", "row"), [
    ("검정 지갑", item("반지갑", "블랙(검정)")),
    ("남색 파우치", item("파우치", "코발트(짙은청록)")),          # 팔레트 이름과 적은 색이 다르다
    ("파랑 지갑", item("지갑", "블루(파랑)")),
    ("검정 지갑", item("지갑", "로열블루(밝은남)")),               # 검정 ↔ 남색은 같은 계열
    ("흰색 지갑", item("지갑", "", "아이보리색 지갑")),
    ("베이지 가방", item("가방", "피치오렌지(오렌지)")),
    ("하늘색 캡모자", item("캡모자", "딥스카이블루(새파란하늘)")),
    ("분홍 물병", item("물병", "레드(연한레드)")),
    ("검정 지갑", item("지갑")),                                   # 색을 모르면 남긴다
    ("검정 지갑", item("지갑", "기타")),
    ("검정 지갑", item("지갑", "처음보는색(새색)")),               # 모르는 팔레트 값
    ("검정 지갑", item("투명 지갑", "")),                         # 투명은 색을 모르는 것으로 본다
    ("투명 케이스", item("케이스", "레드(빨강)")),
    ("검정 흰색 지갑", item("지갑", "화이트(흰)")),                # 여러 색 중 하나만 맞으면 된다
    ("지갑", item("지갑", "레드(빨강)")),                          # 검색어에 색이 없으면 거르지 않는다
    ("검정 지갑", item("빨간 카드가 든 지갑", "블랙(검정)")),
])
def test_kept(query, row):
    assert keeps(query, row)


@pytest.mark.parametrize(("query", "row"), [
    ("검정 지갑", item("반지갑", "브라운(갈)")),
    ("흰색 지갑", item("반지갑", "블랙(검정)")),
    ("빨간색 텀블러", item("텀블러", "블루(파랑)")),
    ("초록색 백팩", item("백팩", "", "보라색 백팩")),
    ("하늘색 캡모자", item("캡모자", "레드(빨강)")),
])
def test_clearly_different_color_is_dropped(query, row):
    assert not keeps(query, row)


def test_every_palette_value_in_the_snapshot_is_mapped():
    data = json.loads(files("retriever_lost_found.search").joinpath("color_families.json").read_text(encoding="utf-8"))
    canonicals = {group["canonical"] for group in json.loads(
        files("retriever_lost_found.search").joinpath("synonyms.json").read_text(encoding="utf-8"))["concepts"]
        if group["domain"] == "color"}
    for colors in data["palette"].values():
        assert set(colors) <= canonicals
    for family in data["families"]:
        assert set(family["colors"]) <= canonicals
    assert len(data["palette"]) == 47  # 스냅숏 48개 값 중 '기타'만 뺐다


def test_search_text_column_alone_is_enough():
    # 알림 후보는 search_text만 갖는다(물품명·분류·색·설명을 이어 붙인 소문자 문자열).
    row = {"id": 1, "search_text": "지갑 지갑 코발트(짙은청록) 지갑(코발트(짙은청록)색)을 습득"}
    assert keeps("남색 지갑", row)
    assert not keeps("빨간 지갑", row)


class Candidates:
    def __init__(self, items):
        self.items = items

    def fetch_candidate_bundle(self, location_ids, terms, **kwargs):
        return {"items": self.items, "candidate_count": len(self.items)}


def test_search_drops_clearly_different_colors_before_the_top_100_cut():
    rows = [item("검정 지갑", "블랙(검정)", id=i, storage_location_id=7) for i in range(1, 101)]
    rows += [item("빨간 지갑", "레드(빨강)", id=i, storage_location_id=7) for i in range(101, 111)]
    rows += [item("지갑", "", id=111, storage_location_id=7)]
    ranked, _, _ = search_found_items(Candidates(rows), "검정 지갑", [7], hydrate_page=False)
    ids = {int(row["id"]) for row in ranked}
    assert not ids & set(range(101, 111))
    assert len(ranked) == 100


def test_semantic_only_rows_are_filtered_too():
    class Semantic(Candidates):
        def fetch_semantic_items(self, location_ids, vector, **kwargs):
            return [{"similarity": 0.9, "detail": item("반지갑", "레드(빨강)", id=50, storage_location_id=7)},
                    {"similarity": 0.9, "detail": item("반지갑", "블랙(검정)", id=51, storage_location_id=7)}]

    class Embedding:
        def embed(self, query, **kwargs):
            return [1.0] * 1024

    ranked, _, status = search_found_items(Semantic([]), "검정 지갑", [7], Embedding(), hydrate_page=False)
    assert status == "ok"
    assert [int(row["id"]) for row in ranked] == [51]


def test_module_exposes_no_switch():
    # 2026-11-23 사용자 결정 — 항상 켜져 있다.
    assert not any(name.isupper() and "ENABLE" in name for name in dir(color_filter))


def test_alerts_use_the_same_color_families():
    from retriever_lost_found.alerts.judge import condition_groups, meets_conditions
    conditions = condition_groups("검정 지갑")
    assert meets_conditions("반지갑 지갑 로열블루(밝은남)", conditions)      # 남색은 검정 계열
    assert meets_conditions("반지갑 지갑", conditions)                        # 색을 모르면 남긴다
    assert not meets_conditions("반지갑 지갑 브라운(갈)", conditions)
    assert not meets_conditions("반지갑 지갑 화이트(흰)", condition_groups("검정 루이비통 지갑"))
