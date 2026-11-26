"""Recoverable HTTP turns backed by an atomic session store."""

import json
import hashlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field, replace
from threading import Event
from time import monotonic
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..integrations.agent_model import OpenRouterAgentModel
from ..search.photo_rerank import MAX_PHOTO_LENGTH, MAX_USER_PHOTOS, prepare_user_photos

from .agent_conversation import ITEM_QUESTION, PLACE_ITEM_QUESTION, Conversation, ItemDetails, Model, PlaceSelectionError, advance_place_selection, confirm_place, edit_places, respond_to_message, search_conversation
from .agent_search import Institution, PlaceCandidate, SearchPage, _validated_query, resolve_institution_scope
from .agent_conversation import AlertChanges, AlertDraft, DialogueContext, TurnDecision, apply_alert_decision
from .agent_store import AgentStore, MemoryAgentStore
from ..agent_diagnostics import trace_id, start_trace, record, failure_fields
from ..privacy.masking import contains_identifier


class RunCancelled(Exception):
    pass


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SavedPlace(Payload):
    # PRD 4.3-E — 알림에 저장된 장소. 지도에서 고른 지점과 같은 국내 좌표 범위만 받는다.
    name: str = Field(min_length=1, max_length=100)
    latitude: float = Field(ge=33.0, le=39.0)
    longitude: float = Field(ge=124.0, le=132.0)


class Turn(Payload):
    type: Literal["message", "select_place", "select_point", "change_radius", "end", "add_place", "replace_place", "remove_place", "cancel_place_selection", "skip_place", "saved_search"]
    request_id: str = Field(min_length=1, max_length=80)
    session_id: str | None = Field(default=None, max_length=80)
    revision: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, min_length=1, max_length=1000)
    changing_place: bool = False
    dialogue: DialogueContext | None = None
    place_target: int | None = Field(default=None, ge=1, le=3)
    visible_item_ids: list[int] = Field(default_factory=list, max_length=10)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=200)
    radius_m: Literal[1000, 1500, 2000] | None = None
    # 지도 더블클릭으로 고른 지점. 국내 좌표 범위만 받는다.
    latitude: float | None = Field(default=None, ge=33.0, le=39.0)
    longitude: float | None = Field(default=None, ge=124.0, le=132.0)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = Field(default=None, max_length=200)
    photos: list[Annotated[str, Field(max_length=MAX_PHOTO_LENGTH)]] = Field(default_factory=list, max_length=MAX_USER_PHOTOS)
    # saved_search 전용 — 결과 메일의 '전체 검색'이 보내는 저장된 장소 1~3곳.
    places: list[SavedPlace] = Field(default_factory=list, max_length=3)


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
    remaining_photo_ids: tuple[int, ...] = ()
    items: dict[int, dict[str, Any]] = field(default_factory=dict)
    run_id: str | None = None
    cancelled: Event = field(default_factory=Event)
    touched: float = field(default_factory=monotonic)


def _detail_cache(items) -> dict:
    fields = ("item_id", "institution_id", "item_name", "color_name", "raw_storage_name")
    return {str(item["item_id"]): {key: item[key] for key in fields if key in item} for item in list(items)[-100:]}


def _snapshot(session: Session) -> dict:
    return {
        "state": asdict(session.state), "revision": session.revision, "status": session.status,
        "scope": list(session.scope), "radius_m": session.radius_m, "manual_radius": session.manual_radius,
        "last_search_query": session.last_search_query, "cursor": session.cursor, "match_count": session.match_count,
        "remaining_photo_ids": list(session.remaining_photo_ids),
        "items": _detail_cache(session.items.values()),
    }


def _restore(data: dict) -> Session:
    data = dict(data)
    state = dict(data.pop("state"))
    state["candidates"] = tuple(PlaceCandidate(**place) for place in state["candidates"])
    state["confirmed"] = PlaceCandidate(**state["confirmed"]) if state["confirmed"] else None
    state["places"] = tuple(PlaceCandidate(**place) for place in state.get("places", ()))
    state["batch_places"] = tuple(PlaceCandidate(**place) for place in state.get("batch_places", ()))
    state["pending_place_queries"] = tuple(state.get("pending_place_queries", ()))
    state["recent"] = tuple(tuple(pair) for pair in state["recent"])
    state["item_details"] = ItemDetails(**state["item_details"])
    data["scope"] = tuple(data["scope"])
    data["remaining_photo_ids"] = tuple(data.get("remaining_photo_ids", ()))
    data["items"] = {int(key): item for key, item in data.get("items", {}).items()}
    return Session(state=Conversation(**state), **data)


def _conditions(session: Session) -> dict:
    return {"conditions": {
        "place": asdict(session.state.confirmed) if session.state.confirmed else None,
        "places": [asdict(place) for place in session.state.selected_places()],
        "place_operation": session.state.place_operation,
        "pending_place_queries": list(session.state.pending_place_queries),
        "selecting_place_query": session.state.place_query if session.state.place_operation else None,
        "item_query": session.state.item_query, "radius_m": session.radius_m,
        "lost_on": session.state.lost_on,
        "institution_ids": list(session.scope),
    }, "place_candidates": [asdict(place) for place in session.state.candidates]}


class StoredCancellation:
    def __init__(self, store, session_id, run_id):
        self.store, self.session_id, self.run_id = store, session_id, run_id

    def is_set(self):
        result = self.store.command(self.session_id, "check", {"run_id": self.run_id})
        return result.get("cancelled", True)


def _error(code: str, message: str, retryable: bool = False) -> dict:
    return {"code": code, "message": message, "retryable": retryable}


def _json(content: dict, status: int = 200) -> JSONResponse:
    return JSONResponse({**content, **({"trace_id": trace_id.get()} if status >= 400 else {})}, status_code=status, headers={"Cache-Control": "no-store"})


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
    store: AgentStore | None = None,
    model: Model | None = None,
    openrouter_api_key: str | None = None,
    find_places: Callable[[str], Sequence[PlaceCandidate]],
    get_institutions: Callable[[], Sequence[Institution]],
    search_items: Callable[..., SearchPage],
    find_items_by_id: Callable[[tuple[int, ...], list[int]], Sequence[dict[str, Any]]] | None = None,
    search_photo_items: Callable[..., SearchPage] | None = None,
) -> APIRouter:
    if model is None:
        if not openrouter_api_key:
            raise ValueError("대화형 검색에 OpenRouter API 키가 필요합니다.")
        model = OpenRouterAgentModel(openrouter_api_key)
    router = APIRouter(prefix="/api/search-chat")
    store = store if store is not None else MemoryAgentStore()

    def store_error(error: Exception):
        record("store.failed", error=error, stage="session_store", final_status="error")
        return _json({"error": _error("state_unavailable", "대화 상태를 확인하지 못했어요. 같은 요청을 다시 시도해 주세요.", True)}, 503)

    def get_session(session_id):
        row = store.command(session_id, "get")
        return (_restore(row["data"]), row) if row["outcome"] == "ok" else (None, row)

    def replay(response):
        events = [
            {"type": "run.started", "session_id": response["session_id"], "run_id": response["run_id"], "seq": 1},
            {"type": "run.finished", "response": response, "run_id": response["run_id"], "seq": 2},
        ]
        return StreamingResponse(iter(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
                                 media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    def conflict() -> JSONResponse:
        record("turn.rejected", stage="admission", reason="state_conflict", final_status="rejected")
        return _json({"error": _error("state_conflict", "검색 상태가 바뀌었어요. 현재 대화에서 다시 시도해 주세요.")}, 409)

    def expired() -> JSONResponse:
        record("turn.rejected", stage="admission", reason="session_expired", final_status="rejected")
        return _json({"error": _error("session_expired", "대화가 만료됐어요. 새 대화를 시작해 주세요.")}, 410)

    async def parse(request: Request, schema: type[Payload]) -> Any:
        try:
            if schema is Turn:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_PHOTO_LENGTH * MAX_USER_PHOTOS + 8_192:
                        return _json({"error": _error("invalid_request", "첨부 사진의 용량이 너무 큽니다.")}, 413)
                return schema.model_validate_json(body)
            return schema.model_validate(await request.json())
        except ValueError:
            return _json({"error": _error("invalid_request", "요청 형식이 올바르지 않습니다.")}, 400)

    @router.post("/turns")
    async def turns(request: Request):
        start_trace()
        turn = await parse(request, Turn)
        if isinstance(turn, JSONResponse):
            return turn
        if (turn.type == "message" and not (turn.text or "").strip()
                or turn.photos and (turn.type != "message" or not turn.session_id or turn.changing_place or turn.dialogue and turn.dialogue.task == "alert")
                or turn.dialogue and turn.type != "message" and (
                    turn.dialogue.task != "alert" or turn.type not in ("change_radius", "remove_place")
                    or turn.type == "remove_place" and turn.place_target is None
                )
                or turn.type == "change_radius" and turn.radius_m is None
                or turn.type == "select_place" and turn.candidate_id is None
                or turn.type == "select_point" and (turn.latitude is None or turn.longitude is None)
                or bool(turn.places) != (turn.type == "saved_search")
                or turn.type == "saved_search" and (
                    turn.session_id is not None or turn.radius_m is None or turn.text is None
                    or len({(place.latitude, place.longitude) for place in turn.places}) != len(turn.places))
                or turn.session_id is None and (turn.type not in ("message", "select_point", "saved_search") or turn.revision is not None)):
            return _json({"error": _error("invalid_turn", "대화 요청에 필요한 값이 없습니다.")}, 400)
        saved_query = None
        if turn.type == "saved_search":
            try:
                saved_query = _validated_query(turn.text or "")
            except ValueError as error:
                return _json({"error": _error("invalid_turn", str(error))}, 400)
        try:
            photos = await run_in_threadpool(prepare_user_photos, turn.photos) if turn.photos else ()
        except ValueError:
            return _json({"error": _error("invalid_photo", "첨부한 사진을 열 수 없어요. 다른 사진을 선택해 주세요.")}, 400)
        session_id = turn.session_id or store.session_id(turn.request_id)
        run_id = uuid4().hex
        fingerprint = hashlib.sha256(turn.model_dump_json(exclude=None if photos else {"photos"}).encode()).hexdigest()
        try:
            claim = await run_in_threadpool(store.command, session_id, "claim", {
                "create": turn.session_id is None, "initial": _snapshot(Session()),
                "request_id": turn.request_id, "fingerprint": fingerprint,
                "revision": turn.revision or 0, "run_id": run_id,
            })
        except Exception as error:
            return store_error(error)
        outcome = claim["outcome"]
        if outcome == "replay":
            record("turn.replayed", final_status=claim["response"]["status"])
            return replay(claim["response"])
        if outcome != "claimed":
            if outcome == "session_expired":
                return expired()
            if outcome == "session_capacity":
                record("turn.rejected", stage="admission", reason=outcome, final_status="rejected")
                return _json({"error": _error("session_capacity", "검색 요청이 많아요. 잠시 후 다시 시도해 주세요.", True)}, 503)
            if outcome == "busy":
                record("turn.rejected", stage="admission", reason=outcome, final_status="rejected")
                return _json({"error": _error("busy", "이전 검색을 처리하고 있어요. 잠시 후 같은 요청을 다시 시도해 주세요.", True)}, 409)
            return conflict()
        session = _restore(claim["data"])
        previous = _conditions(session)
        session.cancelled = StoredCancellation(store, session_id, run_id)
        original_revision = session.revision
        server_trace_id = trace_id.get()
        if (turn.type == "select_place" and (
            session.status != "awaiting_place_selection"
            or not any(place.candidate_id == turn.candidate_id for place in session.state.candidates)
        ) or turn.type == "change_radius" and (session.state.confirmed is None or session.state.item_query is None)):
            try:
                await run_in_threadpool(store.command, session_id, "finish", {
                    "run_id": run_id, "commit": False, "data": _snapshot(session),
                    "response": {"status": "error", "revision": session.revision}, "previous": previous,
                })
            except Exception as error:
                return store_error(error)
            return conflict()

        def stream() -> Iterator[str]:
            trace_id.set(server_trace_id)
            started = monotonic()
            stage = "admission"
            completed_work = False
            seq = 0
            state, manual_radius = session.state, session.manual_radius
            scope, radius = session.scope, session.radius_m
            result: dict[str, Any] = {}
            photo_page: SearchPage | None = None

            def search_current(ids, query):
                nonlocal photo_page
                date_filter = {"lost_on": state.lost_on} if state.lost_on else {}
                if photos and session.state.confirmed and search_photo_items is not None:
                    photo_page = search_photo_items(ids, query, turn.text, photos, **date_filter)
                    return photo_page
                return search_items(ids, query, None, **date_filter)

            def line(event: dict) -> str:
                nonlocal seq, stage
                if event.get("type") == "tool.started":
                    stage = event["tool"]
                seq += 1
                return json.dumps({**event, "run_id": run_id, "seq": seq}, ensure_ascii=False) + "\n"

            def call_model(instructions, context, schema):
                nonlocal stage
                if session.cancelled.is_set():
                    raise RunCancelled
                stage = {"QueryRewrite": "query_rewrite", "ResultFollowUp": "follow_up"}.get(schema.get("title"), "model")
                return model(instructions, context, schema)

            try:
                yield line({"type": "run.started", "session_id": session_id})
                if turn.type == "end":
                    state = Conversation()
                    scope, radius, manual_radius = (), None, None
                    result = {"status": "ended", "message": "대화를 마쳤어요."}
                elif not session.cancelled.is_set():
                    place_query = None
                    places_changed = False
                    if turn.dialogue and turn.type in ("change_radius", "remove_place"):
                        changes = AlertChanges(query=None, radius=turn.radius_m if turn.type == "change_radius" else None,
                                               remove_places=[turn.place_target] if turn.type == "remove_place" else [])
                        decision = TurnDecision(action="update_alert", alert_changes=changes, message="", place_query=None,
                                                item_query=None, clear_item_query=False, asks_for=None, item_changes=None, target=None)
                        try:
                            dialogue = apply_alert_decision(turn.dialogue, decision)
                            result = {"status": "answered", "message": dialogue.pop("message"), "dialogue": dialogue}
                        except ValueError:
                            result = {"status": "answered", "message": "알림 조건을 바꾸지 못했어요. 장소는 한 곳 이상 남겨 주세요."}
                    elif turn.type == "skip_place":
                        state = advance_place_selection(state)
                        if state.place_operation is not None:
                            place_query = state.place_query
                            result = {"status": "awaiting_place_selection", "message": ""}
                        else:
                            result = {"status": "ready_to_search"} if state.confirmed and state.item_query else {
                                "status": "awaiting_clarification", "message": ITEM_QUESTION if state.confirmed else "어디서 잃어버렸어요?",
                            }
                    elif turn.type in ("add_place", "replace_place", "remove_place", "cancel_place_selection"):
                        # PRD 4.3-A — UI와 LLM 명령은 같은 서버 검증을 거친다.
                        state = edit_places(state, turn.type, turn.place_target)
                        if turn.type in ("add_place", "replace_place"):
                            result = {"status": "awaiting_place_clarification", "message":
                                      "추가할 장소를 알려주세요." if turn.type == "add_place" else "변경할 장소를 알려주세요."}
                        elif state.place_operation is not None:
                            places_changed = True
                            result = {"status": "awaiting_place_selection" if state.candidates else "no_place_candidates",
                                      "message": f"이어서 ‘{state.place_query}’ 위치를 확인해 주세요."}
                        else:
                            if turn.type == "remove_place":
                                scope = ()
                            result = {"status": "ready_to_search"} if state.confirmed and state.item_query else {
                                "status": "awaiting_clarification", "message": ITEM_QUESTION if state.confirmed else "어디서 잃어버렸어요?",
                            }
                    elif turn.type == "saved_search":
                        # PRD 4.3-E — 알림에 저장된 장소·물품·반경은 이미 사용자가 확정한 조건이라
                        # 장소 후보 확인과 모델 해석 없이 바로 검색한다.
                        points = tuple(
                            PlaceCandidate(f"point:{place.latitude:.6f},{place.longitude:.6f}", place.name, "",
                                           place.latitude, place.longitude)
                            for place in turn.places
                        )
                        state = Conversation(place_query=points[0].name, item_query=saved_query, confirmed=points[0],
                                             places=points, awaiting_answer=None)
                        manual_radius, scope, radius = turn.radius_m, (), None
                        result = {"status": "ready_to_search"}
                    elif turn.type == "message":
                        def deferred_places(query: str) -> Sequence[PlaceCandidate]:
                            # Place lookup progress is streamed below, before doing real I/O.
                            nonlocal place_query
                            place_query = query
                            return ()

                        visible_by_id = {item_id: session.items[item_id] for item_id in turn.visible_item_ids if item_id in session.items}
                        missing_ids = [item_id for item_id in turn.visible_item_ids if item_id not in visible_by_id and item_id > 0]
                        if missing_ids and scope and find_items_by_id:
                            try:
                                visible_by_id.update(
                                    (item["item_id"], item) for item in find_items_by_id(scope, missing_ids)
                                    if item["item_id"] in missing_ids and item["institution_id"] in scope
                                )
                            except (RuntimeError, TimeoutError, ValueError) as error:
                                record("item_details.fallback", error=error, stage="item_details", final_status="fallback")
                        # Preserve positions when an item is no longer available.
                        visible_items = [visible_by_id.get(item_id) for item_id in turn.visible_item_ids]
                        stage = "model"
                        reply = respond_to_message(
                            state, turn.text, model=call_model, find_places=deferred_places,
                            changing_place=turn.changing_place, radius_m=radius,
                            match_count=session.match_count, last_search_query=session.last_search_query,
                            visible_items=visible_items, dialogue=turn.dialogue,
                        )
                        if session.cancelled.is_set():
                            raise RunCancelled
                        prior_place = state
                        state = reply.state
                        # PRD 4.3-A — Reopening selection can invalidate a confirmed place with the same search text.
                        legacy_place_change = turn.changing_place and prior_place.confirmed and not state.confirmed
                        query_changed = state.place_query != prior_place.place_query
                        places_changed = state.selected_places() != prior_place.selected_places()
                        if legacy_place_change or (state.place_operation is None and query_changed and not places_changed):
                            manual_radius = radius = None
                            scope = ()
                        if places_changed:
                            scope = ()
                        result = {"status": reply.status, "message": reply.message}
                        if (turn.dialogue and turn.dialogue.task == "alert" and reply.decision is None
                                and "@" not in turn.text and not contains_identifier(turn.text)):
                            result["dialogue"] = {"task": "search", "alert": None, "awaiting": None, "changed": False}
                        if reply.decision is not None:
                            decision = reply.decision
                            target = decision.target or 0
                            # PRD 4.3 — Use only server-owned candidates, visible result IDs and allowed radii.
                            task = decision.task or (turn.dialogue.task if turn.dialogue else "search")
                            starting_alert = decision.action == "prepare_alert" and (turn.dialogue is None or turn.dialogue.alert is None)
                            if (task == "alert" or decision.action == "update_alert") and not starting_alert:
                                try:
                                    dialogue = apply_alert_decision(turn.dialogue, decision)
                                    result = {"status": "answered", "message": dialogue.pop("message"), "dialogue": dialogue}
                                except ValidationError:
                                    result = {"status": "answered", "message": "알림 조건을 반영하지 못했어요. 기존 조건을 유지했으니 변경할 내용을 다시 알려주세요."}
                                except ValueError as error:
                                    result = {"status": "answered", "message": str(error)}
                            elif decision.action == "prepare_alert":
                                # PRD 4.3-E — 실제 신청은 브라우저에서 동의 후 기존 알림 API로 보낸다.
                                ready = bool(state.selected_places() and state.item_query and radius and not state.place_operation)
                                result = {"status": "answered", "prepare_alert": ready, "message": "알림 조건을 확인하고 개인정보 이용에 동의한 뒤 이메일을 입력해 주세요." if ready else "먼저 장소를 확정하고 물품을 검색해 주세요."}
                            elif decision.action == "preview_place":
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
                                    except (RuntimeError, TimeoutError) as error:
                                        record("item_details.fallback", error=error, stage="institutions", final_status="fallback")
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
                            if turn.dialogue and turn.dialogue.task == "alert" and task == "search":
                                result["dialogue"] = {"task": "search", "alert": None, "awaiting": None, "changed": False}
                    elif turn.type == "select_place":
                        state, pending = confirm_place(state, turn.candidate_id)
                        result = {"status": "ready_to_search"} if pending else {
                            "status": "awaiting_clarification", "message": PLACE_ITEM_QUESTION if len(state.selected_places()) < 3 else ITEM_QUESTION,
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
                            result = {"status": "awaiting_clarification", "message": PLACE_ITEM_QUESTION if len(state.selected_places()) < 3 else ITEM_QUESTION}
                            state = replace(state, recent=(*state.recent, ("지도에서 위치를 골랐어요.", result["message"]))[-4:])
                    else:
                        manual_radius = turn.radius_m
                        result = {"status": "ready_to_search"}

                    # PRD 4.3-A — 확정 뒤에도 남은 장소 후보를 차례로 조회하고 마지막에만 검색한다.
                    if turn.type in ("select_place", "select_point") and state.place_operation is not None:
                        place_query = state.place_query
                        result = {"status": "awaiting_place_selection", "message": ""}
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
                        if state.place_operation is not None:
                            prefix = "이어서" if turn.type in ("select_place", "select_point", "skip_place") else "먼저"
                            message = f"{prefix} ‘{place_query}’ 위치를 확인해 주세요." if candidates else (
                                f"‘{place_query}’ 장소를 찾지 못했어요. 다시 알려주시거나 건너뛰어 주세요."
                            )
                        else:
                            message = "지도에 표시한 장소가 맞나요?" if candidates else result["message"]
                        result = {"status": "awaiting_place_selection" if candidates else "no_place_candidates", "message": message}
                        state = replace(state, recent=(*state.recent, (turn.text or "장소 확인", message))[-4:])

                    # PRD 4.3-E — 검증한 알림 조건으로 기존 검색을 갱신한다.
                    alert_update = result.get("dialogue")
                    if alert_update and alert_update["changed"]:
                        updated = AlertDraft.model_validate(alert_update["alert"])
                        selected = {(p.name, p.latitude, p.longitude): p for p in state.selected_places()}
                        keys = [(p.name, p.latitude, p.longitude) for p in updated.places]
                        if state.place_operation or len(set(keys)) != len(keys) or any(key not in selected for key in keys):
                            result = {"status": "answered", "message": "현재 검색에서 확정한 장소로 알림 조건을 다시 설정해 주세요."}
                            alert_update = None
                        else:
                            centres = tuple(selected[key] for key in keys)
                            state = replace(state, confirmed=centres[-1], places=centres, place_query=centres[-1].name,
                                            candidates=(), item_query=updated.query,
                                            item_details=state.item_details if updated.query == state.item_query else ItemDetails())
                            manual_radius = updated.radius
                            result["status"] = "ready_to_search"

                    actions = None
                    decision_message = result.get("message") if result["status"] == "ready_to_search" else None
                    if result["status"] == "ready_to_search":
                        actions = search_conversation(
                            state, model=call_model, get_institutions=get_institutions,
                            search_items=search_current, radius_m=manual_radius,
                        )
                    elif state.confirmed is not None and (result["status"] == "awaiting_clarification" and not scope or places_changed or turn.type in ("select_place", "select_point", "skip_place")):
                        # PRD 4.3-A — 장소 확정 직후 물품 입력 전에도 기관과 반경을 표시한다.
                        actions = resolve_institution_scope(state.confirmed, get_institutions=get_institutions,
                                                            search_centers=state.selected_places(), radius_m=radius)
                    if actions is not None:
                        dialogue_result = result.get("dialogue")
                        while not session.cancelled.is_set():
                            event = next(actions, None)
                            if event is None:
                                break
                            if event.get("type") == "scope.updated":
                                previous_radius = radius
                                scope, radius = tuple(event["institution_ids"]), event["radius_m"]
                                if event.get("expansion_reason") == "no_matches":
                                    manual_radius = radius
                                if previous_radius is not None:
                                    event["previous_radius_m"] = previous_radius
                            if "type" in event:
                                yield line(event)
                            else:
                                state = event.pop("state")
                                result = event
                        if dialogue_result is not None:
                            result["dialogue"] = dialogue_result
                        if dialogue_result and dialogue_result["changed"]:
                            dialogue_result["alert"]["radius"] = radius
                            result["message"] = decision_message
                        elif decision_message and result.get("status") in {"results", "no_matches"}:
                            result["explanation"] = decision_message
                    if photos and result["status"] == "results":
                        result["photo_status"] = photo_page.photo_status if photo_page else "unavailable"
                    if not state.selected_places() and state.place_operation is None:
                        scope, radius, manual_radius = (), None, None
                completed_work = True
            except RunCancelled:
                result = {"status": "cancelled", "message": "검색을 취소했어요."}
            except PlaceSelectionError as error:
                result = {"status": "error", "message": str(error), "error": _error("invalid_place", str(error))}
            except Exception as error:
                fields = failure_fields(error)
                record("turn.failed", started=started, error=error, stage=stage, final_status="error")
                reason = fields["reason"]
                message = {
                    "timeout": "검색 응답이 늦어 중단했어요. 같은 요청을 다시 시도해 주세요.",
                    "busy": "검색 서비스의 요청이 많아요. 잠시 후 다시 시도해 주세요.",
                }.get(reason, "검색 정보를 불러오지 못했어요. 같은 요청을 다시 시도해 주세요.")
                result = {"status": "error", "message": message, "error": _error(reason, message, True)}
            finally:
                if not completed_work and result.get("status") not in ("error", "cancelled"):
                    message = "검색 연결이 끊겼어요. 같은 요청을 다시 시도해 주세요."
                    result = {"status": "error", "message": message, "error": _error("connection_lost", message, True)}
                elif not result:
                    result = {"status": "cancelled", "message": "검색을 취소했어요."}
                elif result["status"] != "error":
                    if result["status"] == "ended":
                        state, scope, radius, manual_radius = Conversation(), (), None, None
                    session.state, session.scope, session.radius_m = state, scope, radius
                    session.manual_radius = manual_radius
                    if result["status"] != "answered":
                        session.status = result["status"]
                        matches = result.get("matches")
                        session.items = {item["item_id"]: item for item in matches["items"]} if matches else {}
                        session.match_count = matches["total_count"] if matches else None
                        session.cursor = matches["next_cursor"] if matches else None
                        session.remaining_photo_ids = photo_page.remaining_photo_ids if matches and photo_page else ()
                        attempts = result.get("search_attempts")
                        session.last_search_query = attempts[-1]["query"] if attempts else None
                    session.revision += 1
                session.run_id = None
                session.touched = monotonic()
                response = {
                    "session_id": session_id, "run_id": run_id, "revision": session.revision,
                    "trace_id": server_trace_id, "message": "", "search_attempts": [], "recommended_item_ids": [], "matches": None, "error": None,
                    **_conditions(session),
                    **result,
                }
                try:
                    completed = store.command(session_id, "finish", {
                        "run_id": run_id, "commit": result["status"] not in ("error", "cancelled"),
                        "data": _snapshot(session), "response": response, "previous": previous,
                    })
                    if completed["outcome"] != "ok":
                        response.update(revision=original_revision, **previous, status="error", error=_error("state_conflict", "검색 상태가 바뀌었어요. 같은 요청을 다시 시도해 주세요.", True))
                    else:
                        response = completed["response"]
                except Exception as error:
                    record("store.failed", error=error, stage="session_store", final_status="error")
                    response.update(revision=original_revision, **previous, status="error", error=_error("state_unavailable", "결과 저장을 확인하지 못했어요. 같은 요청을 다시 시도해 주세요.", True))
                response["trace_id"] = server_trace_id
                record("turn.finished", started=started, stage=stage, final_status=response["status"],
                       reason=(response.get("error") or {}).get("code", response["status"]))
            yield line({"type": "run.finished", "response": response})

        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "Content-Encoding": "identity"})

    @router.post("/cancel")
    async def cancel(request: Request):
        start_trace()
        payload = await parse(request, Cancel)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            result = await run_in_threadpool(store.command, payload.session_id, "cancel", {"run_id": payload.run_id})
        except Exception as error:
            return store_error(error)
        if result["outcome"] == "session_expired":
            return expired()
        record("turn.cancel_requested", final_status="cancelled" if result["accepted"] else "completed")
        return _json({"accepted": result["accepted"]})

    @router.post("/pages")
    async def pages(request: Request):
        start_trace()
        payload = await parse(request, NextPage)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            session, row = await run_in_threadpool(get_session, payload.session_id)
            if session is None:
                return expired()
            if row["run_id"] or session.revision != payload.revision or payload.cursor != session.cursor or session.last_search_query is None:
                return conflict()
            scope, query = session.scope, session.last_search_query
            if session.remaining_photo_ids and find_items_by_id is not None:
                rows = await run_in_threadpool(find_items_by_id, scope, list(session.remaining_photo_ids))
                by_id = {row["item_id"]: row for row in rows}
                items = tuple(by_id[item_id] for item_id in session.remaining_photo_ids if item_id in by_id)
                page = SearchPage(items, session.match_count or 0)
            else:
                date_filter = {"lost_on": session.state.lost_on} if session.state.lost_on else {}
                page = await run_in_threadpool(search_items, scope, query, payload.cursor, **date_filter)
            if any(item["institution_id"] not in scope for item in page.items):
                raise RuntimeError("Invalid scope")
            result = await run_in_threadpool(store.command, payload.session_id, "page", {
                "revision": payload.revision, "cursor": payload.cursor, "next_cursor": page.next_cursor, "items": _detail_cache(page.items),
            })
            if result["outcome"] != "ok":
                return conflict()
        except Exception as error:
            record("page.failed", error=error, stage="found_items", final_status="error")
            return _json({"error": _error("lookup_failed", "다음 페이지를 불러오지 못했어요. 다시 시도해 주세요.", True)}, 502)
        return _json({"matches": {"items": page.items, "total_count": page.total_count, "next_cursor": page.next_cursor}})

    return router
