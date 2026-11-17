"""Single-process HTTP integration for the conversational search preview."""

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field, replace
from threading import Event, Lock
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..integrations.agent_model import OpenRouterAgentModel

from .agent_conversation import ITEM_QUESTION, Conversation, Model, confirm_place, respond_to_message, search_conversation
from .agent_search import Institution, PlaceCandidate, SearchPage, resolve_institution_scope


class RunCancelled(Exception):
    pass


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Turn(Payload):
    type: Literal["message", "select_place", "select_point", "change_radius", "end"]
    request_id: str = Field(min_length=1, max_length=80)
    session_id: str | None = Field(default=None, max_length=80)
    revision: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, min_length=1, max_length=1000)
    changing_place: bool = False
    visible_item_ids: list[int] = Field(default_factory=list, max_length=10)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=200)
    radius_m: Literal[1000, 1500, 2000] | None = None
    # 지도 더블클릭으로 고른 지점. 국내 좌표 범위만 받는다.
    latitude: float | None = Field(default=None, ge=33.0, le=39.0)
    longitude: float | None = Field(default=None, ge=124.0, le=132.0)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = Field(default=None, max_length=200)


class NextPage(Payload):
    session_id: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=0)
    cursor: str = Field(min_length=1, max_length=512)


class Cancel(Payload):
    session_id: str = Field(min_length=1, max_length=80)
    run_id: str = Field(min_length=1, max_length=80)


@dataclass
class Session:
    state: Conversation = field(default_factory=Conversation)
    revision: int = 0
    status: str = ""
    scope: tuple[int, ...] = ()
    radius_m: int | None = None
    manual_radius: int | None = None
    last_search_query: str | None = None
    cursor: str | None = None
    match_count: int | None = None
    items: dict[int, dict[str, Any]] = field(default_factory=dict)
    run_id: str | None = None
    cancelled: Event = field(default_factory=Event)
    request_ids: set[str] = field(default_factory=set)
    touched: float = field(default_factory=monotonic)


def _error(code: str, message: str, retryable: bool = False) -> dict:
    return {"code": code, "message": message, "retryable": retryable}


def _json(content: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status, headers={"Cache-Control": "no-store"})


def _item_details_message(item: dict[str, Any], institutions: Sequence[Institution]) -> str:
    institution = next((row for row in institutions if row.institution_id == item["institution_id"]), None)
    name = item.get("item_name") or "선택한 습득물"
    storage = (institution.name if institution else "") or item.get("raw_storage_name")
    if not storage:
        return f"‘{name}’의 보관기관 정보가 표시되어 있지 않아요."
    lines = [f"‘{name}’의 보관기관은 {storage}입니다."]
    if institution and institution.address:
        lines.append(f"주소는 {institution.address}입니다.")
    if institution and institution.phone:
        lines.append(f"방문 전에 {institution.phone}으로 수령 가능 여부를 확인해 주세요.")
    return " ".join(lines)


def _requested_radius(action: str, target: int | None, current: int | None) -> int | None:
    radii = (1000, 1500, 2000)
    if action == "set_radius":
        return target if target in radii else None
    current = current or radii[0]
    if action == "expand_radius":
        return next((radius for radius in radii if radius > current), None)
    return next((radius for radius in reversed(radii) if radius < current), None)


def create_agent_router(
    *,
    model: Model | None = None,
    openrouter_api_key: str | None = None,
    find_places: Callable[[str], Sequence[PlaceCandidate]],
    get_institutions: Callable[[], Sequence[Institution]],
    search_items: Callable[[tuple[int, ...], str, str | None], SearchPage],
    find_items_by_id: Callable[[tuple[int, ...], list[int]], Sequence[dict[str, Any]]] | None = None,
) -> APIRouter:
    if model is None:
        if not openrouter_api_key:
            raise ValueError("대화형 검색에 OpenRouter API 키가 필요합니다.")
        model = OpenRouterAgentModel(openrouter_api_key)
    router = APIRouter(prefix="/api/search-chat")
    sessions: dict[str, Session] = {}
    lock = Lock()

    def get_session(session_id: str | None) -> Session | None:
        now = monotonic()
        for key in [key for key, session in sessions.items() if session.run_id is None and now - session.touched >= 1800]:
            del sessions[key]
        return sessions.get(session_id) if session_id else None

    def conflict() -> JSONResponse:
        return _json({"error": _error("state_conflict", "검색 상태가 바뀌었어요. 현재 대화에서 다시 시도해 주세요.")}, 409)

    def expired() -> JSONResponse:
        return _json({"error": _error("session_expired", "대화가 만료됐어요. 새 대화를 시작해 주세요.")}, 410)

    async def parse(request: Request, schema: type[Payload]) -> Any:
        try:
            return schema.model_validate(await request.json())
        except ValueError:
            return _json({"error": _error("invalid_request", "요청 형식이 올바르지 않습니다.")}, 400)

    @router.post("/turns")
    async def turns(request: Request):
        turn = await parse(request, Turn)
        if isinstance(turn, JSONResponse):
            return turn
        if (turn.type == "message" and not (turn.text or "").strip()
                or turn.type == "change_radius" and turn.radius_m is None
                or turn.type == "select_place" and turn.candidate_id is None
                or turn.type == "select_point" and (turn.latitude is None or turn.longitude is None)
                or turn.session_id is None and (turn.type not in ("message", "select_point") or turn.revision is not None)):
            return _json({"error": _error("invalid_turn", "대화 요청에 필요한 값이 없습니다.")}, 400)
        with lock:
            session = get_session(turn.session_id)
            if turn.session_id is not None and session is None:
                return expired()
            if session is None:
                if any(turn.request_id in existing.request_ids for existing in sessions.values()):
                    return conflict()
                if len(sessions) >= 128:
                    return _json({"error": _error("session_capacity", "검색 요청이 많아요. 잠시 후 다시 시도해 주세요.", True)}, 503)
                session_id, session = uuid4().hex, Session()
                sessions[session_id] = session
            else:
                session_id = turn.session_id
                if (turn.revision != session.revision or session.run_id or session.status == "ended"
                        or turn.request_id in session.request_ids):
                    return conflict()
            if len(session.request_ids) >= 100:
                return expired()
            if turn.type == "select_place" and (
                session.status != "awaiting_place_selection"
                or not any(place.candidate_id == turn.candidate_id for place in session.state.candidates)
            ):
                return conflict()
            if turn.type == "change_radius" and (session.state.confirmed is None or session.state.item_query is None):
                return conflict()
            session.request_ids.add(turn.request_id)
            run_id = uuid4().hex
            if turn.type == "end":
                session.state = Conversation()
                session.status = "ended"
                session.scope = ()
                session.cursor = session.last_search_query = None
                session.items.clear()
                session.match_count = None
                session.touched = monotonic()
                session.revision += 1
                return _json({"session_id": session_id, "run_id": run_id, "revision": session.revision, "status": "ended"})
            session.run_id = run_id
            session.cancelled.clear()

        def stream() -> Iterator[str]:
            seq = 0
            state, manual_radius = session.state, session.manual_radius
            scope, radius = session.scope, session.radius_m
            result: dict[str, Any] = {}

            def line(event: dict) -> str:
                nonlocal seq
                seq += 1
                return json.dumps({**event, "run_id": run_id, "seq": seq}, ensure_ascii=False) + "\n"

            try:
                yield line({"type": "run.started", "session_id": session_id})
                if not session.cancelled.is_set():
                    if turn.type == "message":
                        def deferred_places(query: str) -> Sequence[PlaceCandidate]:
                            # Place lookup progress is streamed below, before doing real I/O.
                            nonlocal place_query
                            place_query = query
                            return ()

                        place_query = None
                        visible_by_id = {item_id: session.items[item_id] for item_id in turn.visible_item_ids if item_id in session.items}
                        missing_ids = [item_id for item_id in turn.visible_item_ids if item_id not in visible_by_id and item_id > 0]
                        if missing_ids and scope and find_items_by_id:
                            try:
                                visible_by_id.update(
                                    (item["item_id"], item) for item in find_items_by_id(scope, missing_ids)
                                    if item["item_id"] in missing_ids and item["institution_id"] in scope
                                )
                            except (RuntimeError, ValueError):
                                pass
                        # Preserve positions when an item is no longer available.
                        visible_items = [visible_by_id.get(item_id) for item_id in turn.visible_item_ids]
                        reply = respond_to_message(
                            state, turn.text, model=model, find_places=deferred_places,
                            changing_place=turn.changing_place, radius_m=radius,
                            match_count=session.match_count, last_search_query=session.last_search_query,
                            visible_items=visible_items,
                        )
                        if session.cancelled.is_set():
                            raise RunCancelled
                        prior_place = state
                        state = reply.state
                        # PRD 4.3-A — Reopening selection can invalidate a confirmed place with the same search text.
                        if state.place_query != prior_place.place_query or (prior_place.confirmed and not state.confirmed):
                            manual_radius = radius = None
                            scope = ()
                        result = {"status": reply.status, "message": reply.message}
                        if reply.decision is not None:
                            decision = reply.decision
                            target = decision.target or 0
                            # PRD 4.3 — Use only server-owned candidates, visible result IDs and allowed radii.
                            if decision.action == "preview_place":
                                if not turn.changing_place and 1 <= target <= len(state.candidates):
                                    candidate = state.candidates[target - 1]
                                    result = {
                                        "status": "answered", "highlight_candidate_id": candidate.candidate_id,
                                        "message": "지도에서 해당 장소를 확인해 주세요. 맞다면 ‘이 위치 선택하기’를 눌러 주세요.",
                                    }
                                else:
                                    result = {"status": "answered", "message": "목록에서 어떤 장소를 뜻하는지 알려주세요."}
                            elif decision.action == "item_details":
                                if 1 <= target <= len(visible_items) and (item := visible_items[target - 1]) is not None:
                                    try:
                                        institutions = get_institutions()
                                    except RuntimeError:
                                        institutions = ()
                                    answer = _item_details_message(item, institutions)
                                else:
                                    answer = "전체 검색 결과에서 확인할 물품의 번호나 이름을 알려주세요."
                                result = {"status": "answered", "message": answer}
                            elif state.confirmed is None or state.item_query is None or turn.changing_place:
                                result = {"status": "answered", "message": "장소와 물품을 먼저 정해 주세요."}
                            else:
                                requested = _requested_radius(decision.action, decision.target, radius)
                                if requested is None:
                                    answer = {
                                        "expand_radius": "이미 최대 반경인 2km까지 찾고 있어요.",
                                        "shrink_radius": "이미 최소 반경인 1km로 찾고 있어요.",
                                        "set_radius": "검색 반경은 1km, 1.5km, 2km 중에서 정할 수 있어요.",
                                    }[decision.action]
                                    result = {"status": "answered", "message": answer}
                                elif requested == radius:
                                    result = {"status": "answered", "message": "이미 그 반경으로 찾고 있어요."}
                                else:
                                    manual_radius = requested
                                    result = {"status": "ready_to_search"}
                            if result["status"] == "answered":
                                state = replace(state, recent=(*state.recent, (turn.text, result["message"]))[-4:])
                        if place_query is not None:
                            yield line({"type": "tool.started", "tool": "places"})
                            if session.cancelled.is_set():
                                raise RunCancelled
                            try:
                                candidates = tuple(find_places(place_query))
                            except RuntimeError:
                                yield line({"type": "tool.failed", "tool": "places"})
                                raise
                            yield line({"type": "tool.completed", "tool": "places"})
                            state = replace(state, candidates=candidates)
                            if candidates:
                                result = {"status": "awaiting_place_selection", "message": "지도에 표시한 장소가 맞나요?"}
                            state = replace(state, recent=(*state.recent[:-1], (turn.text, result["message"])))
                    elif turn.type == "select_place":
                        state, pending = confirm_place(state, turn.candidate_id)
                        result = {"status": "ready_to_search"} if pending else {
                            "status": "awaiting_clarification", "message": ITEM_QUESTION,
                        }
                    elif turn.type == "select_point":
                        # 지도에서 직접 고른 지점은 후보 조회 없이 바로 확정 장소가 된다.
                        point = PlaceCandidate(
                            f"point:{turn.latitude:.6f},{turn.longitude:.6f}", turn.name or "지도에서 고른 위치", turn.address or "",
                            turn.latitude, turn.longitude,
                        )
                        state, pending = confirm_place(
                            replace(state, candidates=(point,), place_query=point.name), point.candidate_id,
                        )
                        manual_radius = radius = None
                        scope = ()
                        if pending:
                            result = {"status": "ready_to_search"}
                        else:
                            result = {"status": "awaiting_clarification", "message": ITEM_QUESTION}
                            state = replace(state, recent=(*state.recent, ("지도에서 위치를 골랐어요.", result["message"]))[-4:])
                    else:
                        manual_radius = turn.radius_m
                        result = {"status": "ready_to_search"}

                    actions = None
                    if result["status"] == "ready_to_search":
                        actions = search_conversation(
                            state, model=model, get_institutions=get_institutions,
                            search_items=lambda ids, query: search_items(ids, query, None), radius_m=manual_radius,
                        )
                    elif turn.type in ("select_place", "select_point") and state.confirmed is not None:
                        # PRD 4.3-A — 장소 확정 직후 물품 입력 전에도 기관과 반경을 표시한다.
                        actions = resolve_institution_scope(state.confirmed, get_institutions=get_institutions)
                    if actions is not None:
                        while not session.cancelled.is_set():
                            event = next(actions, None)
                            if event is None:
                                break
                            if event.get("type") == "scope.updated":
                                previous_radius = radius
                                scope, radius = tuple(event["institution_ids"]), event["radius_m"]
                                if previous_radius is not None:
                                    event["previous_radius_m"] = previous_radius
                            if "type" in event:
                                yield line(event)
                            else:
                                state = event.pop("state")
                                result = event
            except RunCancelled:
                pass
            except (RuntimeError, ValueError):
                message = "검색 중 문제가 생겼어요. 다시 시도해 주세요."
                result = {"status": "error", "message": message, "error": _error("lookup_failed", message, True)}
            finally:
                with lock:
                    if session.cancelled.is_set() or not result:
                        result = {"status": "cancelled", "message": "검색을 취소했어요."}
                    elif result["status"] != "error":
                        session.state, session.scope, session.radius_m = state, scope, radius
                        session.manual_radius = manual_radius
                        if result["status"] != "answered":
                            session.status = result["status"]
                            matches = result.get("matches")
                            session.items = {item["item_id"]: item for item in matches["items"]} if matches else {}
                            session.match_count = matches["total_count"] if matches else None
                            session.cursor = matches["next_cursor"] if matches else None
                            attempts = result.get("search_attempts")
                            session.last_search_query = attempts[-1]["query"] if attempts else None
                        session.revision += 1
                    session.run_id = None
                    session.touched = monotonic()
                    response = {
                        "session_id": session_id, "run_id": run_id, "revision": session.revision,
                        "message": "", "search_attempts": [], "recommended_item_ids": [], "matches": None, "error": None,
                        "conditions": {
                            "place": asdict(session.state.confirmed) if session.state.confirmed else None,
                            "item_query": session.state.item_query, "radius_m": session.radius_m,
                            "institution_ids": list(session.scope),
                        },
                        "place_candidates": [asdict(place) for place in session.state.candidates],
                        **result,
                    }
            yield line({"type": "run.finished", "response": response})

        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "Content-Encoding": "identity"})

    @router.post("/cancel")
    async def cancel(request: Request):
        payload = await parse(request, Cancel)
        if isinstance(payload, JSONResponse):
            return payload
        with lock:
            session = get_session(payload.session_id)
            if session is None:
                return expired()
            accepted = session.run_id == payload.run_id
            if accepted:
                session.cancelled.set()
        return _json({"accepted": accepted})

    @router.post("/pages")
    async def pages(request: Request):
        payload = await parse(request, NextPage)
        if isinstance(payload, JSONResponse):
            return payload
        with lock:
            session = get_session(payload.session_id)
            if session is None:
                return expired()
            if session.run_id or session.revision != payload.revision or payload.cursor != session.cursor or session.last_search_query is None:
                return conflict()
            scope, query = session.scope, session.last_search_query
        try:
            page = await run_in_threadpool(search_items, scope, query, payload.cursor)
            if any(item["institution_id"] not in scope for item in page.items):
                raise RuntimeError
        except RuntimeError:
            return _json({"error": _error("lookup_failed", "다음 페이지를 불러오지 못했어요.", True)}, 502)
        with lock:
            if session.revision != payload.revision or session.run_id or session.cursor != payload.cursor:
                return conflict()
            session.items.update((item["item_id"], item) for item in page.items)
            session.cursor = page.next_cursor
            session.touched = monotonic()
        return _json({"matches": {"items": page.items, "total_count": page.total_count, "next_cursor": page.next_cursor}})

    return router
