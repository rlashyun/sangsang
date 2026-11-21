"""Place confirmation and scoped search for the conversational search agent."""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Any

from ..privacy.masking import IDENTIFIER_QUERY_ERROR, contains_identifier


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
    name: str = ""
    address: str = ""
    phone: str = ""


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
    # PRD 3.3 · Q5 — 카드·주민·전화번호 형태의 검색어는 대화 검색에서도 받지 않는다.
    if contains_identifier(query):
        raise ValueError(IDENTIFIER_QUERY_ERROR)
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


def resolve_institution_scope(
    place: PlaceCandidate,
    *,
    get_institutions: Callable[[], Sequence[Institution]],
    radius_m: int | None = None,
    search_centers: Sequence[PlaceCandidate] = (),
) -> Iterator[dict[str, Any]]:
    yield {"type": "tool.started", "tool": "institutions"}
    try:
        institutions = get_institutions()
    except RuntimeError:
        yield {"type": "tool.failed", "tool": "institutions"}
        raise
    yield {"type": "tool.completed", "tool": "institutions"}

    # PRD 4.1·4.5 — Establish a scope before searching. Only zero institutions
    # can trigger radius expansion.
    distances = [(row.institution_id, min(_distance_meters(center, row) for center in (search_centers or (place,)))) for row in institutions]
    radii = (radius_m,) if radius_m is not None else (1000, 1500, 2000)
    for radius in radii:
        scope = tuple(sorted({institution_id for institution_id, distance in distances if distance <= radius}))
        yield {"type": "scope.updated", "radius_m": radius, "institution_ids": list(scope)}
        if scope:
            return


def search_confirmed_place(
    pending: PendingSearch,
    candidate_id: str,
    *,
    get_institutions: Callable[[], Sequence[Institution]],
    search_items: Callable[[tuple[int, ...], str], SearchPage],
    rewrite_query: Callable[[str], str | None],
    radius_m: int | None = None,
    search_centers: Sequence[PlaceCandidate] = (),
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

    # PRD 4.1·4.5 — 2026-11-21 사용자 승인: 물품도 0건이면 다음 반경으로 확장한다.
    distances = [(row.institution_id, min(_distance_meters(center, row) for center in (search_centers or (selected,)))) for row in institutions]
    if radius_m is not None and radius_m not in (1000, 1500, 2000):
        raise ValueError("검색 반경은 1km, 1.5km, 2km 중에서 정해 주세요.")
    radii = tuple(radius for radius in (1000, 1500, 2000) if radius >= (radius_m or 1000))
    attempts = []
    query = pending.query
    rewrite_used = False
    page = None
    previous_radius = None
    expansion_reason = None
    for radius in radii:
        scope = tuple(sorted({institution_id for institution_id, distance in distances if distance <= radius}))
        event = {"type": "scope.updated", "radius_m": radius, "institution_ids": list(scope)}
        if previous_radius is not None:
            event.update(previous_radius_m=previous_radius, expansion_reason=expansion_reason)
        yield event
        previous_radius = radius
        if not scope:
            expansion_reason = "no_institutions"
            continue
        is_rewrite = False
        while True:
            attempt = len(attempts) + 1
            yield {"type": "tool.started", "tool": "found_items", "attempt": attempt, "is_rewrite": is_rewrite}
            try:
                page = search_items(scope, query)
            except RuntimeError:
                yield {"type": "tool.failed", "tool": "found_items", "attempt": attempt}
                raise
            if any(item["institution_id"] not in scope for item in page.items):
                raise RuntimeError("검색 결과의 보관기관 범위가 올바르지 않습니다.")
            yield {"type": "tool.completed", "tool": "found_items", "attempt": attempt}
            attempts.append({"attempt": attempt, "query": query, "radius_m": radius,
                             "status": "completed", "match_count": page.total_count})
            if page.total_count or rewrite_used:
                break
            # Keep the existing single rewrite budget across the entire expanded search.
            rewrite_used = True
            rewritten = rewrite_query(query)
            if not rewritten or rewritten.strip() == query:
                break
            query = _validated_query(rewritten)
            is_rewrite = True
        if page.total_count:
            break
        expansion_reason = "no_matches"

    if page is None:
        yield {"status": "no_institutions", "matches": None, "recommended_item_ids": [], "search_attempts": []}
        return

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
