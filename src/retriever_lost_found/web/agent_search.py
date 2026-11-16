"""Place confirmation and scoped search for the conversational search agent."""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Any


@dataclass(frozen=True)
class PlaceCandidate:
    candidate_id: str
    name: str
    address: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Institution:
    institution_id: int
    latitude: float
    longitude: float


@dataclass(frozen=True)
class PendingSearch:
    candidates: tuple[PlaceCandidate, ...]
    query: str


@dataclass(frozen=True)
class SearchPage:
    items: tuple[dict[str, Any], ...]
    total_count: int
    next_cursor: str | None = None


def _validated_query(query: str) -> str:
    # PRD 3.3 — The existing item query limit also applies to rewritten text.
    query = query.strip()
    if not 1 <= len(query) <= 30:
        raise ValueError("습득물 검색어는 1자 이상 30자 이하여야 합니다.")
    return query


def _distance_meters(place: PlaceCandidate, institution: Institution) -> float:
    latitude = radians(institution.latitude - place.latitude)
    longitude = radians(institution.longitude - place.longitude)
    a = sin(latitude / 2) ** 2 + (
        cos(radians(place.latitude)) * cos(radians(institution.latitude)) * sin(longitude / 2) ** 2
    )
    return 2 * 6371008.8 * asin(min(1, sqrt(a)))


def prepare_search(
    message: str,
    extract: Callable[[str], tuple[str, str]],
    find_places: Callable[[str], Sequence[PlaceCandidate]],
) -> PendingSearch:
    """Prepare a selection from the tool's candidates, even when only one exists."""
    place_text, item_query = extract(message)
    return PendingSearch(tuple(find_places(place_text)), _validated_query(item_query))


def search_confirmed_place(
    pending: PendingSearch,
    candidate_id: str,
    *,
    get_institutions: Callable[[], Sequence[Institution]],
    search_items: Callable[[tuple[int, ...], str], SearchPage],
    rewrite_query: Callable[[str], str | None],
    radius_m: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield tool events and a result for a candidate from the pending selection."""
    selected = next((place for place in pending.candidates if place.candidate_id == candidate_id), None)
    if selected is None:
        raise ValueError("현재 장소 후보에서 선택해 주세요.")

    yield {"type": "tool.started", "tool": "institutions"}
    try:
        institutions = get_institutions()
    except RuntimeError:
        yield {"type": "tool.failed", "tool": "institutions"}
        raise
    yield {"type": "tool.completed", "tool": "institutions"}

    # PRD 4.1·4.5 — Establish a scope before searching. Only zero institutions
    # can trigger radius expansion.
    distances = [(row.institution_id, _distance_meters(selected, row)) for row in institutions]
    radii = (radius_m,) if radius_m is not None else (1000, 1500, 2000)
    for radius in radii:
        scope = tuple(sorted({institution_id for institution_id, distance in distances if distance <= radius}))
        yield {"type": "scope.updated", "radius_m": radius, "institution_ids": list(scope)}
        if scope:
            break
    if not scope:
        yield {"status": "no_institutions", "matches": None, "recommended_item_ids": [], "search_attempts": []}
        return

    attempts = []
    query = pending.query
    for attempt in (1, 2):
        yield {"type": "tool.started", "tool": "found_items", "attempt": attempt}
        try:
            page = search_items(scope, query)
        except RuntimeError:
            yield {"type": "tool.failed", "tool": "found_items", "attempt": attempt}
            raise
        yield {"type": "tool.completed", "tool": "found_items", "attempt": attempt}
        attempts.append({"attempt": attempt, "query": query, "status": "completed", "match_count": page.total_count})
        if page.total_count or attempt == 2:
            break
        rewritten = rewrite_query(query)
        if not rewritten or rewritten.strip() == query:
            break
        query = _validated_query(rewritten)

    # Only the final attempt supplies the results and cursor. Earlier attempts
    # are recorded for progress and never contribute items to the list.
    if any(item["institution_id"] not in scope for item in page.items):
        raise RuntimeError("검색 결과의 보관기관 범위가 올바르지 않습니다.")
    items = list({item["item_id"]: item for item in page.items}.values())
    yield {
        "status": "results" if page.total_count else "no_matches",
        "search_attempts": attempts,
        "recommended_item_ids": [item["item_id"] for item in items[:3]],
        "matches": {"items": items, "total_count": page.total_count, "next_cursor": page.next_cursor},
    }
