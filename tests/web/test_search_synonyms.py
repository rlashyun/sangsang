"""PRD 4.1/4.5: fixed synonym expressions reach scoped retrieval and ranking."""
from copy import deepcopy

import pytest

from retriever_lost_found.search.hybrid import keyword_scores, query_kinds, query_text, search_found_items, search_text


@pytest.mark.parametrize(("source", "expected"), [
    ("검은색 마틴킴 카드 지갑", "검정 마뗑킴 카드지갑"),
    ("해지스 배낭", "헤지스 백팩"),
    ("루이비똥 지갑", "루이비통 지갑"),
    ("벙거지 모자", "벙거지모자"),
    ("티머니 교통카드", "티머니카드"),
    ("무선 이어폰 유닛 X", "무선이어폰 유닛없음"),
    ("검정색지갑", "검정 지갑"),
    ("해지스백팩", "헤지스 백팩"),
    ("다크블루 지갑", "어두운파랑 지갑"),
    ("톰 브라운 지갑", "톰브라운 지갑"),
    ("matinkim 지갑", "마뗑킴 지갑"),
    ("louisvuitton 지갑", "루이비통 지갑"),
])
def test_fixed_synonyms_normalize_both_spaced_and_known_compound_terms(source, expected):
    assert search_text(source) == expected
    assert search_text(expected) == expected


@pytest.mark.parametrize("source", [
    "블랙야크 가방", "오프화이트 지갑", "톰브라운 지갑", "브라운슈가",
    "아이팟", "카카오카드", "토스카드", "기업카드", "삼성카드",
    "아이폰 15 프로", "갤럭시 s24 울트라", "파우치", "케이스 x",
])
def test_brand_boundaries_product_types_models_and_ambiguous_terms_survive(source):
    assert search_text(source) == source


def test_model_side_and_absence_are_preserved():
    normalized = search_text("에어팟 프로 2 왼쪽 유닛 없음")
    assert all(term in normalized for term in ("에어팟", "프로", "2", "왼쪽", "없음"))
    assert "유닛있음" not in normalized


def test_all_packaged_aliases_normalize_in_their_declared_context_and_are_idempotent():
    import json
    from importlib.resources import files
    data = json.loads(files("retriever_lost_found.search").joinpath("synonyms.json").read_text(encoding="utf-8"))
    for group in data["concepts"]:
        for term in group["terms"]:
            if any("earphone_span" in scope for scope in group["scope"]):
                source, expected = f"무선이어폰 {term}", f"무선이어폰 {group['canonical']}"
            elif group["domain"] == "color":
                source, expected = f"{term} 지갑", f"{group['canonical']} 지갑"
            else:
                source, expected = term, group["canonical"]
            assert search_text(source) == expected
            assert search_text(expected) == expected


def test_unrecognized_brand_context_is_not_treated_as_a_color():
    assert search_text("브라운 면도기") == "브라운 면도기"


def test_query_color_is_normalized_even_when_the_other_description_is_unknown():
    assert query_text("검은색 물품") == "검정 물품"
    assert keyword_scores([{"id": 1, "item_name": "검정 지갑"}], "검은색 물품")


def test_very_long_source_words_do_not_recurse_or_partially_rewrite():
    source = "검정색" * 1000
    assert search_text(source) == source


def test_descriptive_kind_hints_reach_the_literal_rpc():
    client = ScopedLiteralCandidates()
    client.items.append({"id": 6, "storage_location_id": 7, "item_name": "현금을 넣는 물품",
                         "search_text": "현금을 넣는 물품"})
    rows, _, _ = search_found_items(client, "현금을 넣는 물품", [7])
    assert 6 in {row["id"] for row in rows}


def test_kind_retrieval_does_not_expand_into_generic_phrase_components():
    from retriever_lost_found.search.synonyms import kind_candidate_terms
    wallet = kind_candidate_terms({"지갑"})
    earphones = kind_candidate_terms({"이어폰"})
    assert "카드 지갑" in wallet and "카드" not in wallet
    assert "블루투스 이어폰" in earphones and "블루투스" not in earphones


def test_new_subtypes_and_ambiguous_pouches_have_the_right_kinds():
    from retriever_lost_found.search.hybrid import KINDS
    assert query_kinds("비니") == [KINDS[3]]
    assert query_kinds("버킷햇") == [KINDS[3]]
    assert query_kinds("카드홀더") == [KINDS[0]]
    assert query_kinds("카드지갑") == [KINDS[0]]
    assert query_kinds("갤럭시 버즈") == [KINDS[2]]
    assert set(query_kinds("파우치")) == {KINDS[0], KINDS[4]}


def test_bm25_uses_the_same_brand_dictionary_without_mutating_source():
    items = [{"id": 1, "item_name": "마땡킴 카드지갑"},
             {"id": 2, "item_name": "다른 지갑"}]
    original = deepcopy(items)
    scores = keyword_scores(items, "마틴킴 카드 지갑")
    assert scores[1] > scores[2]
    assert items == original


class ScopedLiteralCandidates:
    """Mirror the existing RPC: kinds gate ignores terms; None uses literal OR."""
    def __init__(self):
        self.items = [
            {"id": 1, "storage_location_id": 7, "item_name": "루이비똥", "search_text": "루이비똥"},
            {"id": 2, "storage_location_id": 7, "item_name": "비니", "search_text": "비니"},
            {"id": 3, "storage_location_id": 7, "item_name": "검정 휴대폰", "search_text": "검정 휴대폰"},
            {"id": 4, "storage_location_id": 999, "item_name": "비니", "search_text": "비니"},
            {"id": 5, "storage_location_id": 7, "item_name": "휴대전화", "search_text": "휴대전화"},
        ]
        self.requests = []

    def fetch_candidate_bundle(self, locations, terms, *, kind_terms, timeout):
        assert locations == [7]
        self.requests.append((terms, kind_terms))
        # These fixtures deliberately have no legacy kind_mask. New synonyms
        # must not disappear before Python can normalize or score them.
        items = [] if kind_terms else [
            item for item in self.items if item["storage_location_id"] in locations
            and any(term.lower() in item["search_text"].lower() for term in terms)
        ]
        return {"items": items, "candidate_count": None}

    def fetch_rerank_items(self, locations, item_ids, **kwargs):
        return [{"detail": item, "vector": None} for item in self.items
                if item["storage_location_id"] in locations and item["id"] in item_ids]


@pytest.mark.parametrize(("query", "expected"), [
    ("루이비통", [1]), ("검정 비니", [2]), ("모자", [2]), ("휴대폰", [3, 5]),
])
def test_synonyms_survive_the_real_candidate_gate_and_keep_location_scope(query, expected):
    client = ScopedLiteralCandidates()
    results, _, _ = search_found_items(client, query, [7])
    assert {row["id"] for row in results} == set(expected)
    assert len(client.requests) == 1
    terms, kinds = client.requests[0]
    if "비니" in query:
        assert "검정" not in terms  # Color alone must not broaden kind retrieval.


def test_embedding_receives_the_canonical_query_not_the_expanded_dictionary():
    class Embedding:
        def embed(self, query, **kwargs):
            assert query == "루이비통"
            return [1.0] * 1024

    rows, _, _ = search_found_items(ScopedLiteralCandidates(), "루이비똥", [7], Embedding())
    assert [row["id"] for row in rows] == [1]


def test_public_api_uses_synonyms_and_preserves_original_item_names():
    from fastapi.testclient import TestClient
    from retriever_lost_found.web.app import create_app
    client = TestClient(create_app(object(), "test-js", found_item_client=ScopedLiteralCandidates()))
    response = client.get("/api/found-items", params={"location_ids": "7", "q": "루이비통"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    rows = response.json()["items"]
    assert [row["id"] for row in rows] == [1]
    assert rows[0]["item_name"] == "루이비똥"
    assert "vector" not in rows[0]
