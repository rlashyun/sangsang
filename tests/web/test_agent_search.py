from unittest.mock import Mock

import pytest

from retriever_lost_found.web.agent_search import (
    Institution,
    PlaceCandidate,
    SearchPage,
    prepare_search,
    search_confirmed_place,
)


@pytest.fixture
def pending_search():
    return prepare_search(
        "강남역에서 갈색 지갑을 잃어버렸어",
        lambda _: ("강남역", "갈색 지갑"),
        lambda _: [PlaceCandidate("p1", "강남역", "서울 강남구", 37.5, 127.03)],
    )


def test_confirmation_then_retry_in_same_scope(pending_search):
    institutions = Mock(return_value=[Institution(42, 37.5, 127.03), Institution(99, 37.6, 127.03)])
    items = tuple({"item_id": item_id, "institution_id": 42} for item_id in (5, 4, 3, 2))
    search = Mock(side_effect=[SearchPage((), 0), SearchPage(items, 5, "next")])
    rewrite = Mock(return_value="브라운 지갑")

    with pytest.raises(ValueError):
        list(search_confirmed_place(
            pending_search, "old", get_institutions=institutions, search_items=search, rewrite_query=rewrite,
        ))
    institutions.assert_not_called()
    search.assert_not_called()

    events = list(search_confirmed_place(
        pending_search, "p1", get_institutions=institutions, search_items=search, rewrite_query=rewrite,
    ))
    assert [call.args for call in search.call_args_list] == [((42,), "갈색 지갑"), ((42,), "브라운 지갑")]
    assert [row["type"] for row in events[:-1]] == [
        "tool.started", "tool.completed", "scope.updated",
        "tool.started", "tool.completed", "tool.started", "tool.completed",
    ]
    assert events[-1]["recommended_item_ids"] == [5, 4, 3]
    assert events[-1]["matches"] == {"items": list(items), "total_count": 5, "next_cursor": "next"}


def test_scope_expands_only_until_an_institution_exists(pending_search):
    search = Mock(return_value=SearchPage(({"item_id": 5, "institution_id": 42},), 1))
    rewrite = Mock()
    events = list(search_confirmed_place(
        pending_search, "p1", get_institutions=lambda: [Institution(42, 37.511, 127.03)],
        search_items=search, rewrite_query=rewrite,
    ))
    assert [row["radius_m"] for row in events if row.get("type") == "scope.updated"] == [1000, 1500]
    search.assert_called_once_with((42,), "갈색 지갑")
    rewrite.assert_not_called()


def test_no_institutions_never_searches(pending_search):
    search = Mock()
    events = list(search_confirmed_place(
        pending_search, "p1", get_institutions=lambda: [Institution(99, 37.6, 127.03)],
        search_items=search, rewrite_query=Mock(),
    ))
    assert events[-1]["status"] == "no_institutions"
    search.assert_not_called()


def test_failed_search_does_not_retry(pending_search):
    search = Mock(side_effect=RuntimeError("timeout"))
    rewrite = Mock()
    events = search_confirmed_place(
        pending_search, "p1", get_institutions=lambda: [Institution(42, 37.5, 127.03)],
        search_items=search, rewrite_query=rewrite,
    )
    for _ in range(4):
        next(events)
    assert next(events) == {"type": "tool.failed", "tool": "found_items", "attempt": 1}
    with pytest.raises(RuntimeError, match="timeout"):
        next(events)
    search.assert_called_once()
    rewrite.assert_not_called()
