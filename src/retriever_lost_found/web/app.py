from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..alerts.links import LINK_PATHS
from ..alerts.reservations import AlertService
from ..alerts.sender import create_email_sender
from ..alerts.settings import load_alert_settings
from ..alerts.store import SupabaseAlertStore
from ..config import read_env_value, read_optional_env_value
from ..integrations.embeddings import GeminiQueryClient
from ..integrations.kakao import KakaoLocalClient, kakao_sdk_url
from ..integrations.supabase import (
    SupabaseFoundItemClient,
    SupabaseMapLocationClient,
    apply_item_counts,
    validate_location_ids,
)
from ..privacy.masking import IDENTIFIER_QUERY_ERROR, contains_identifier, mask_item_row
from ..search.hybrid import SEARCH_PAGE_SIZE, search_with_deadline, SearchDeadlineExceeded
from .alerts import create_alert_router


DEFAULT_STATIC_DIR = Path(__file__).with_name("static")
# PRD 3.3 · 5 — 검색어 없는 목록 한 페이지 크기. 화면의 "물품 더 보기"가 이만큼씩 늘린다.
FOUND_ITEM_PAGE_LIMIT = 50


def create_app(
    client: KakaoLocalClient,
    javascript_key: str,
    static_dir: Path = DEFAULT_STATIC_DIR,
    institutions: list[dict[str, Any]] | None = None,
    map_location_client: SupabaseMapLocationClient | None = None,
    found_item_client: SupabaseFoundItemClient | None = None,
    embedding_client: GeminiQueryClient | None = None,
    agent_router: APIRouter | None = None,
    alert_service: AlertService | None = None,
    jev_filter: Any | None = None,
) -> FastAPI:
    """로컬 Uvicorn과 Vercel이 함께 사용하는 FastAPI 애플리케이션을 만듭니다."""
    sdk_url = kakao_sdk_url(javascript_key)
    map_locations = institutions or []
    allowed_assets = {
        "app.js": "text/javascript; charset=utf-8",
        "styles.css": "text/css; charset=utf-8",
        "found-items.js": "text/javascript; charset=utf-8",
        "ui.js": "text/javascript; charset=utf-8",
        "institution-marker.svg": "image/svg+xml",
        "police-marker.svg": "image/svg+xml",
        "favicon.svg": "image/svg+xml",
        "favicon.ico": "image/x-icon",
        "apple-touch-icon.png": "image/png",
    }
    app = FastAPI(
        title="Retriever Lost & Found API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
    if agent_router is not None:
        app.include_router(agent_router)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = content_security_policy(
            getattr(found_item_client, "url", ""))
        return response

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(error)})

    @app.exception_handler(FileNotFoundError)
    async def handle_missing_file(
        request: Request, error: FileNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": "정적 웹 파일을 찾을 수 없습니다."},
        )

    @app.exception_handler(RuntimeError)
    async def handle_runtime_error(request: Request, error: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"error": str(error)})

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        if agent_router is not None:
            html = html.replace('<div id="root">', '<div id="root" data-agent-search="enabled">', 1)
        return HTMLResponse(html.replace("__KAKAO_SDK_URL__", sdk_url))

    def alert_link_page() -> HTMLResponse:
        """설계 5.4 — 메일 속 확인·연장·취소 링크가 여는 화면. React 앱이 같은 index.html로 그린다.

        여는 것만으로는 아무것도 바꾸지 않는다(화면이 요약을 보여 주고 버튼을 눌러야 POST한다).
        주소에 링크 토큰이 있으므로 지도 SDK 등 외부 요청에 주소가 실려 나가지 않게 한다.
        """
        response = index()
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    for link_path in LINK_PATHS.values():
        app.add_api_route(link_path, alert_link_page, methods=["GET"], response_class=HTMLResponse)

    def make_asset_response(asset_name: str, asset_media_type: str):
        def asset_response() -> FileResponse:
            return FileResponse(static_dir / asset_name, media_type=asset_media_type)

        return asset_response

    for filename, media_type in allowed_assets.items():
        app.add_api_route(
            f"/{filename}",
            make_asset_response(filename, media_type),
            methods=["GET"],
        )

    @app.get("/api/geocode")
    def geocode(address: str = "") -> dict[str, Any]:
        return client.geocode_address(address)

    @app.get("/api/institutions")
    def institution_list(with_counts: bool = True) -> JSONResponse:
        count_error = None
        if map_location_client is None:
            locations = map_locations
            counts_available = False
        else:
            try:
                database_locations = map_location_client.fetch_locations(with_counts=with_counts)
            except RuntimeError as error:
                if not with_counts:
                    raise
                # PRD 6 — 개수 집계가 실패해도 기관 마커는 보여야 한다.
                database_locations = map_location_client.fetch_locations(with_counts=False)
                count_error = str(error)
            if map_locations:
                counts = {
                    (
                        str(location["location_source_code"]),
                        str(location["source_key"]),
                    ): int(location["item_count"])
                    for location in database_locations
                }
                location_ids = {
                    (
                        str(location["location_source_code"]),
                        str(location["source_key"]),
                    ): int(location["storage_location_id"])
                    for location in database_locations
                }
                locations = apply_item_counts(map_locations, counts, location_ids)
            else:
                locations = database_locations
            counts_available = with_counts and count_error is None
        # 개수 없는 응답은 짧게만 캐시해 집계가 회복되면 곧 다시 반영되게 한다.
        cdn_cache = (
            "public, s-maxage=300, stale-while-revalidate=600"
            if count_error is None
            else "public, s-maxage=60, stale-while-revalidate=60"
        )
        return JSONResponse(
            content={
                "institutions": locations,
                "counts_available": counts_available,
                "count_error": count_error,
                # PRD 6 — 최종 갱신 시각은 화면에 표시하지 않기로 해(2026-11-05) 응답에도 넣지 않는다.
            },
            headers={
                "Cache-Control": "public, max-age=0, must-revalidate",
                "Vercel-CDN-Cache-Control": cdn_cache,
            },
        )

    @app.get("/api/found-items")
    def found_items(location_ids: str = "", q: str = "", cursor: str = "") -> JSONResponse:
        if found_item_client is None:
            return JSONResponse(
                status_code=503,
                content={"error": "Supabase 물품 조회가 설정되지 않았습니다."},
            )
        validated_ids = validate_location_ids(location_ids)
        search_query = q.strip()
        # PRD 3.3 · D3 — 습득물 검색어 상한 30자 (장소 검색칸 200자와는 별개)
        if len(search_query) > 30:
            raise ValueError("습득물 검색어는 30자 이하여야 합니다.")
        # PRD 3.3 · Q5 — 카드·주민·전화번호로 남의 물건을 역검색하지 못하게 한다.
        if contains_identifier(search_query):
            raise ValueError(IDENTIFIER_QUERY_ERROR)
        if not search_query:
            # PRD 3.3 · 5 — 검색어 없는 목록은 최신순 한 페이지만 보낸다.
            # 반경 안 후보 전체(3km 3.7만 건)를 넘기던 방식이 16~26초 지연의 원인이었다.
            page = found_item_client.fetch_item_page(
                validated_ids,
                limit=FOUND_ITEM_PAGE_LIMIT,
                before=parse_found_item_cursor(cursor),
            )
            # PRD 3.3 · Q5 — 저장 원문은 그대로 두고 응답 사본만 가린다(절대 규칙 3).
            rows = page["items"]
            if hasattr(found_item_client, "add_thumbnails"):
                rows = found_item_client.add_thumbnails(rows)
            items = [mask_item_row(item) for item in rows]
            return JSONResponse(
                content={
                    "items": items,
                    "query": search_query,
                    # 첫 페이지에서만 숫자, 다음 페이지부터는 null
                    "total_count": page["total_count"],
                    "next_cursor": (
                        found_item_cursor(items[-1]) if page["has_more"] and items else None
                    ),
                }
            )
        try:
            page = ranked_item_page(found_item_client, search_query, validated_ids, embedding_client, cursor,
                                    jev=jev_filter)
        except SearchDeadlineExceeded as error:
            return JSONResponse(status_code=504, content={"error": str(error)})
        return JSONResponse(content=page)

    @app.get("/api/places")
    def places(query: str = "", x: str = "", y: str = "") -> dict[str, Any]:
        longitude = optional_float(x, "x")
        latitude = optional_float(y, "y")
        return client.search_keyword(query, longitude=longitude, latitude=latitude)

    # 설계 5.4 — 메일 설정이 없으면 서비스가 None이고 /api/alerts*는 503이다.
    app.include_router(create_alert_router(alert_service, getattr(found_item_client, "add_thumbnails", None)))
    return app


def ranked_item_page(
    found_item_client: SupabaseFoundItemClient,
    query: str,
    location_ids: list[int],
    embedding_client: GeminiQueryClient | None,
    cursor: str = "",
    jev=None,
    *,
    lost_on: str | None = None,
) -> dict[str, Any]:
    offset = 0
    jev_excluded = None
    if cursor.strip():
        # "search:50" 또는 "search:50:x=12,34" — x는 첫 쪽에서 Jev가 뺀 물품 id(2026-11-23 비용 최적화).
        match = SEARCH_CURSOR_PATTERN.fullmatch(cursor.strip())
        if not match:
            raise ValueError("검색 결과 페이지 위치가 올바르지 않습니다.")
        offset = int(match["offset"])
        if match["jev"] is not None:
            jev_excluded = {int(value) for value in match["jev"].split(",") if value}
    if contains_identifier(query):
        raise ValueError(IDENTIFIER_QUERY_ERROR)
    # PRD 4.1 · 4.5 — Both entry points use the same scoped search and paging.
    jev_out: dict[str, Any] = {}
    matched_items, candidate_count, semantic_status = search_with_deadline(
        found_item_client, query, location_ids, embedding_client, offset=offset, jev=jev,
        jev_excluded=jev_excluded, jev_out=jev_out, lost_on=lost_on,
    )
    if jev_excluded is None and "excluded" in jev_out:
        jev_excluded = jev_out["excluded"]
    match_count = len(matched_items)
    # PRD 4.5 — 검색 결과는 상위 100건을 한 번에 보낸다(2026-11-25). 검색어 없는 목록은 50건 그대로다.
    next_offset = offset + SEARCH_PAGE_SIZE
    page_items = matched_items[offset:next_offset]
    if hasattr(found_item_client, "add_thumbnails"):
        page_items = found_item_client.add_thumbnails(page_items)
    return {
        # PRD 3.3 · Q5 — 순위는 원문으로 매기고, 밖으로 나가는 페이지만 가린다.
        "items": [mask_item_row(item) for item in page_items],
        "candidate_count": candidate_count,
        "semantic_status": semantic_status,
        "match_count": match_count,
        "total_count": match_count,
        "query": query,
        "next_cursor": search_cursor(next_offset, jev_excluded) if next_offset < match_count else None,
    }


def photo_ranked_item_page(found_item_client, embedding_client, compare, location_ids, query, user_text, photos,
                           jev=None, *, lost_on: str | None = None):
    from ..search.photo_rerank import rerank_photos
    from .agent_adapters import adapt_search_item
    from .agent_search import SearchPage

    location_ids = list(location_ids)
    # 2026-11-23 — Jev 제외는 사진 비교(Luna) 전에 돈다.
    ranked, _, _ = search_with_deadline(found_item_client, query, location_ids, embedding_client, hydrate_page=False,
                                        jev=jev, lost_on=lost_on)
    ranked, status = rerank_photos(ranked, location_ids, user_text, photos,
                                    found_item_client.fetch_photo_candidates, compare)
    # PRD 4.5 — Hydrate the first page after reranking. Keep only the remaining IDs for the next page.
    page, remaining = ranked[:SEARCH_PAGE_SIZE], ranked[SEARCH_PAGE_SIZE:]
    missing = [row["id"] for row in page if "image_url" not in row]
    if missing:
        details = found_item_client.fetch_item_details(location_ids, missing)
        by_id = {row["id"]: row for row in details if row.get("storage_location_id") in location_ids}
        page = [{**row, **by_id.get(row["id"], {})} for row in page]
    items = tuple(adapt_search_item(row) for row in found_item_client.add_thumbnails(page))
    return SearchPage(items, len(ranked), f"search:{SEARCH_PAGE_SIZE}" if remaining else None,
                      tuple(row["id"] for row in remaining), status)


def create_runtime_app(*, agent_search: bool = False) -> FastAPI:
    """필수 환경 변수와 Supabase DB를 사용해 실제 실행 앱을 구성합니다."""
    rest_api_key = read_env_value("KAKAO_REST_API_KEY")
    javascript_key = read_env_value("KAKAO_JAVASCRIPT_KEY")
    supabase_url = read_env_value("SUPABASE_URL")
    supabase_secret_key = read_optional_env_value(
        "SUPABASE_SECRET_KEY"
    ) or read_optional_env_value("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_secret_key:
        raise ValueError(
            "SUPABASE_SECRET_KEY 또는 SUPABASE_SERVICE_ROLE_KEY가 설정되지 않았습니다."
        )

    map_location_client = SupabaseMapLocationClient(supabase_url, supabase_secret_key)
    found_item_client = SupabaseFoundItemClient(supabase_url, supabase_secret_key)
    embedding_key = read_optional_env_value("OPENROUTER_API_KEY")
    embedding_client = GeminiQueryClient(embedding_key) if embedding_key else None
    # 2026-11-23 사용자 결정 — 일반·대화형 검색 모두 Jev 제외를 쓴다. 끄는 스위치는 없다(키가 없으면 건너뜀).
    jev_filter = None
    if embedding_key:
        from ..integrations.jev import OpenRouterDecisions
        from ..search.jev_filter import JevFilter
        jev_filter = JevFilter(OpenRouterDecisions(embedding_key))
    kakao_client = KakaoLocalClient(rest_api_key)
    agent_router = None
    if agent_search:
        from ..integrations.agent_model import OpenRouterAgentModel
        from .agent_adapters import adapt_institutions, adapt_places, adapt_search_item, adapt_search_page
        from .agent_api import create_agent_router
        from .agent_store import SupabaseAgentStore

        photo_model = OpenRouterAgentModel(embedding_key, timeout=4.5)

        def search_items(location_ids: tuple[int, ...], query: str, cursor: str | None, *, lost_on: str | None = None):
            ids = validate_location_ids(",".join(map(str, location_ids)))
            page = ranked_item_page(found_item_client, query, ids, embedding_client, cursor or "",
                                    jev=jev_filter, lost_on=lost_on)
            return adapt_search_page(page)

        def find_items_by_id(scope, item_ids):
            rows = found_item_client.fetch_item_details(list(scope), item_ids)
            return [adapt_search_item(row) for row in found_item_client.add_thumbnails(rows)]

        agent_router = create_agent_router(
            store=SupabaseAgentStore(supabase_url, supabase_secret_key),
            openrouter_api_key=embedding_key,
            find_places=lambda query: adapt_places(kakao_client, query),
            get_institutions=lambda: adapt_institutions(map_location_client.fetch_locations(with_counts=False)),
            search_items=search_items,
            find_items_by_id=find_items_by_id,
            search_photo_items=lambda ids, query, text, photos, *, lost_on=None: photo_ranked_item_page(
                found_item_client, embedding_client, photo_model.complete, ids, query, text, photos,
                jev=jev_filter, lost_on=lost_on,
            ),
        )
    try:
        alert_settings = load_alert_settings()
    except ValueError as error:
        # 메일 설정이 잘못돼도 지도 서비스는 멈추지 않게 알림 예약만 끈다(설계 14장).
        print(f"[alerts] 알림 예약을 끕니다: {error}", file=sys.stderr)
        alert_settings = None
    alert_service = None
    if alert_settings is not None:
        alert_service = AlertService(
            store=SupabaseAlertStore(supabase_url, supabase_secret_key),
            sender=create_email_sender(alert_settings.mail),
            settings=alert_settings,
        )
    app = create_app(
        kakao_client,
        javascript_key,
        map_location_client=map_location_client,
        found_item_client=found_item_client,
        embedding_client=embedding_client,
        agent_router=agent_router,
        alert_service=alert_service,
        jev_filter=jev_filter,
    )
    app.state.location_source = "supabase"
    app.state.item_counts_available = True
    return app


SEARCH_CURSOR_PATTERN = re.compile(r"search:(?P<offset>[0-9]{1,6})(?::x=(?P<jev>(?:[0-9]{1,18}(?:,[0-9]{1,18}){0,99})?))?")


def search_cursor(offset: int, jev_excluded: set[int] | None) -> str:
    """다음 쪽 커서. Jev 판정을 했으면(뺀 것이 없어도) x=를 붙여 다음 쪽이 다시 판정하지 않게 한다."""
    if jev_excluded is None:
        return f"search:{offset}"
    return f"search:{offset}:x=" + ",".join(map(str, sorted(jev_excluded)))


FOUND_ITEM_CURSOR_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2}):([1-9]\d{0,17})")  # bigint 범위 안


def found_item_cursor(item: dict[str, Any]) -> str:
    """목록 마지막 행의 (등록일, id)를 다음 페이지 커서로 만듭니다."""
    return f"{item['registered_on']}:{item['id']}"


def parse_found_item_cursor(value: str) -> tuple[str, int] | None:
    value = value.strip()
    if not value:
        return None
    match = FOUND_ITEM_CURSOR_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("목록 페이지 위치가 올바르지 않습니다.")
    registered_on, item_id = match.groups()
    try:
        date.fromisoformat(registered_on)
    except ValueError:
        raise ValueError("목록 페이지 위치가 올바르지 않습니다.") from None
    return registered_on, int(item_id)


def optional_float(value: str, name: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name} 좌표가 숫자가 아닙니다.") from None


def content_security_policy(supabase_url: str = "") -> str:
    from urllib.parse import urlsplit
    parsed = urlsplit(supabase_url)
    image_host = ""
    if (parsed.scheme == "https" and parsed.hostname
            and re.fullmatch(r"[a-z0-9-]+\.supabase\.co", parsed.hostname)
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            and parsed.path in ("", "/") and parsed.port in (None, 443)):
        image_host = " https://" + parsed.hostname
    return "; ".join(
        [
            "default-src 'self'",
            # PRD 5 — 로컬 HTTP에서 SDK가 만드는 외부 HTTP 요청도 HTTPS로 올려 기존 CDN 허용 범위를 유지한다.
            "upgrade-insecure-requests",
            # PRD 5 — 카카오맵의 새 CDN 하위 호스트도 허용한다 (공식 CSP 공지 2026-07-07).
            "script-src 'self' https://dapi.kakao.com https://t1.daumcdn.net http://t1.daumcdn.net https://*.kakaocdn.net",
            "style-src 'self' 'unsafe-inline' https://t1.daumcdn.net http://t1.daumcdn.net https://*.kakaocdn.net",
            # PRD 3.2-2 — 습득물 사진(image_url) 원본 호스트. 이미지로만 허용한다
            "img-src 'self' data: blob: https://*.daumcdn.net http://*.daumcdn.net https://*.kakao.com http://*.kakao.com https://*.kakaocdn.net https://minwon24.police.go.kr" + image_host,
            "connect-src 'self' https://dapi.kakao.com https://*.daumcdn.net http://*.daumcdn.net https://*.kakao.com http://*.kakao.com https://*.kakaocdn.net",
            "font-src 'self' data:",
            "frame-src https://map.kakao.com",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="카카오맵 검색 및 좌표 변환 데모")
    parser.add_argument("--host", default="127.0.0.1", help="서버 주소")
    parser.add_argument("--port", type=int, default=8000, help="서버 포트")
    parser.add_argument("--agent-search", action="store_true", help="로컬 대화형 검색 API 활성화")
    args = parser.parse_args()

    app = create_runtime_app(agent_search=args.agent_search)
    print(f"카카오맵 데모: http://{args.host}:{args.port}")
    print("기관 좌표 및 물품 수: Supabase 연결됨 (응답 CDN 5분 캐시)")
    print("종료하려면 Ctrl+C를 누르세요.")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
