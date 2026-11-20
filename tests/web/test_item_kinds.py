"""PRD 4.5 (2026-11-21) — 물건 종류 목록(item_kinds.json)은 Supabase 분류 21개·하위분류를 따른다.

종류가 잡히면 포함 검사는 그 종류의 표현만 보내고(색·무늬는 보내지 않음), BM25는 종류가 다른 물품에 0점을 준다.
"""
import json
from importlib.resources import files

import pytest

from retriever_lost_found.ingestion.categories import SERVICE_CATEGORIES
from retriever_lost_found.search import synonyms
from retriever_lost_found.search.hybrid import (
    candidate_matches, candidate_terms, keyword_scores, query_kinds, query_text, search_text,
)

KINDS = json.loads(files("retriever_lost_found.search").joinpath("item_kinds.json").read_text(encoding="utf-8"))


def _item(item_id: int, name: str, category: str = "") -> dict:
    """DB search_text처럼 물품명·분류를 소문자로 이어 붙인 물품."""
    return {"id": item_id, "item_name": name, "raw_category_name": category,
            "search_text": " ".join(part for part in (name, category) if part).lower()}


def _scored(query: str, items: list[dict]) -> set[int]:
    return set(keyword_scores(items, query_text(query)))


def test_kinds_live_outside_the_synonym_dictionary():
    dictionary = json.loads(files("retriever_lost_found.search").joinpath("synonyms.json").read_text(encoding="utf-8"))
    assert "kinds" not in dictionary
    assert synonyms.KIND_NAMES == tuple(kind["name"] for kind in KINDS["kinds"])


@pytest.mark.parametrize("kind", KINDS["kinds"], ids=lambda kind: kind["name"])
def test_every_kind_is_detected_from_its_supabase_category_path(kind):
    parents = {category.name for category in SERVICE_CATEGORIES}
    for path in kind["categories"]:
        assert path.split(" > ")[0] in parents, path
        # 물품 글의 분류 경로만으로도 그 종류로 판단되어야 한다(물품명에 종류어가 없을 때).
        assert kind["name"] in query_kinds(search_text(path)), path
        terms, words = candidate_terms(query_text(kind["name"]))
        assert candidate_matches(path.lower(), terms, words), path


def test_watch_and_smartwatch_are_separate_kinds():
    watch, smartwatch = _item(1, "손목시계", "귀금속 > 시계"), _item(2, "애플워치", "전자기기 > 스마트워치")
    assert _scored("시계", [watch, smartwatch]) == {1}
    assert _scored("스마트워치", [watch, smartwatch]) == {2}


def test_glasses_and_sunglasses_are_one_kind():
    glasses, sunglasses = _item(1, "뿔테 안경", "기타물품 > 안경"), _item(2, "레이밴", "기타물품 > 선글라스")
    other = _item(3, "검정 우산", "기타물품 > 기타")
    assert _scored("안경", [glasses, sunglasses, other]) == {1, 2}
    assert _scored("선글라스", [glasses, sunglasses, other]) == {1, 2}


def test_id_cards_are_separate_from_payment_cards():
    id_card = _item(1, "주민등록증", "증명서 > 신분증")
    license_card = _item(2, "운전면허증", "증명서 > 면허증")
    credit = _item(3, "신한 체크카드", "카드 > 신용(체크)카드")
    assert _scored("신분증", [id_card, license_card, credit]) == {1, 2}
    assert _scored("카드", [id_card, license_card, credit]) == {3}


def test_attribute_words_are_not_sent_when_the_kind_is_known():
    terms, words = candidate_terms(query_text("파란 우산"))
    assert "우산" in terms and not {"파랑", "파란", "블루", "blue"} & terms and not words
    umbrella, blue_bag = _item(1, "검정 장우산", "기타물품 > 기타"), _item(2, "파란 가방", "가방 > 기타가방")
    assert candidate_matches(umbrella["search_text"], terms, words)
    assert not candidate_matches(blue_bag["search_text"], terms, words)
    assert _scored("파란 우산", [umbrella, blue_bag]) == {1}


def test_one_letter_kind_words_count_only_as_standalone_words():
    book = _item(1, "만화책", "도서용품 > 만화책")
    desk = _item(2, "책상 서랍", "기타물품 > 기타")
    loose = _item(3, "책 1권", "기타물품 > 기타")
    assert query_kinds(query_text("책")) == ["책"] and query_kinds(query_text("책상")) == []
    assert _scored("책", [book, desk, loose]) == {1, 3}


def test_smart_key_is_a_car_key_not_a_phone():
    # 자동 오타 보정이 '스마트키'를 '스마트폰'으로 바꿔 정답 20건이 모두 빠졌다(정답셋 v2 A1).
    assert query_text("현대차 스마트키") == "현대자동차 스마트키"
    assert query_kinds(query_text("현대차 스마트키")) == ["자동차열쇠"]
    key, phone = _item(1, "현대 스마트키", "자동차 > 자동차열쇠"), _item(2, "갤럭시 스마트폰", "휴대폰 > 삼성휴대폰")
    assert _scored("현대차 스마트키", [key, phone]) == {1}


def test_descriptive_hint_words_are_not_kinds_themselves():
    # '현금을 넣는 지갑'의 '현금'은 현금 종류가 아니다. 명시한 지갑만 남는다.
    assert query_kinds(query_text("현금을 넣는 지갑")) == ["지갑"]
    # positive 쌍: 현금이라고만 쓰면 현금 종류다.
    assert query_kinds(query_text("현금 5만원")) == ["현금"]


def _dictionary_items() -> list[dict]:
    words = {term for concept in synonyms._CONCEPTS for term in concept["terms"]}
    words |= {term for kind in KINDS["kinds"] for term in kind["terms"]}
    paths = {path for kind in KINDS["kinds"] for path in kind["categories"]}
    return [_item(i, text) for i, text in enumerate(sorted(words | paths), 1)]


@pytest.mark.parametrize("name", synonyms.KIND_NAMES)
def test_kind_containment_never_drops_a_dictionary_spelling_that_bm25_scores(name):
    # 사전 표현·종류 표현·분류 경로를 물품 글로 두고, 종류 검색어가 점수를 주는 물품은 모두 포함 검사를 통과해야 한다.
    items = _dictionary_items()
    query = query_text(name)
    terms, words = candidate_terms(query)
    scored = set(keyword_scores(items, query))
    assert scored
    missed = [items[i - 1]["search_text"] for i in scored if not candidate_matches(items[i - 1]["search_text"], terms, words)]
    assert missed == []
