from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..config import read_env_value, read_optional_env_value
from ..integrations.kakao import KakaoLocalClient, kakao_sdk_url
from ..integrations.supabase import (
    SupabaseFoundItemClient,
    SupabaseMapLocationClient,
    apply_item_counts,
    validate_location_ids,
)
from ..search.fuzzy import rank_found_items


DEFAULT_STATIC_DIR = Path(__file__).with_name("static")


def create_app(
    client: KakaoLocalClient,
    javascript_key: str,
    static_dir: Path = DEFAULT_STATIC_DIR,
    institutions: list[dict[str, Any]] | None = None,
    map_location_client: SupabaseMapLocationClient | None = None,
    found_item_client: SupabaseFoundItemClient | None = None,
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
    }
    app = FastAPI(
        title="Retriever Lost & Found API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = content_security_policy()
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
        return HTMLResponse(html.replace("__KAKAO_SDK_URL__", sdk_url))

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
    def institution_list() -> JSONResponse:
        if map_location_client is None:
            locations = map_locations
            counts_available = False
        else:
            database_locations = map_location_client.fetch_locations()
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
            counts_available = True
        return JSONResponse(
            content={
                "institutions": locations,
                "counts_available": counts_available,
                "count_error": None,
            },
            headers={
                "Cache-Control": "public, max-age=0, must-revalidate",
                "Vercel-CDN-Cache-Control": (
                    "public, s-maxage=300, stale-while-revalidate=600"
                ),
            },
        )

    @app.get("/api/found-items")
    def found_items(location_ids: str = "", q: str = "") -> JSONResponse:
        if found_item_client is None:
            return JSONResponse(
                status_code=503,
                content={"error": "Supabase 물품 조회가 설정되지 않았습니다."},
            )
        validated_ids = validate_location_ids(location_ids)
        search_query = q.strip()
        if len(search_query) > 100:
            raise ValueError("습득물 검색어는 100자 이하여야 합니다.")
        candidates = found_item_client.fetch_items(validated_ids)
        matched_items = rank_found_items(candidates, search_query)
        return JSONResponse(
            content={
                "items": matched_items,
                "candidate_count": len(candidates),
                "match_count": len(matched_items),
                "query": search_query,
            }
        )

    @app.get("/api/places")
    def places(query: str = "", x: str = "", y: str = "") -> dict[str, Any]:
        longitude = optional_float(x, "x")
        latitude = optional_float(y, "y")
        return client.search_keyword(query, longitude=longitude, latitude=latitude)

    return app


def create_runtime_app() -> FastAPI:
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
    app = create_app(
        KakaoLocalClient(rest_api_key),
        javascript_key,
        map_location_client=map_location_client,
        found_item_client=found_item_client,
    )
    app.state.location_source = "supabase"
    app.state.item_counts_available = True
    return app


def optional_float(value: str, name: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name} 좌표가 숫자가 아닙니다.") from None


def content_security_policy() -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' https://dapi.kakao.com https://t1.daumcdn.net http://t1.daumcdn.net",
            "style-src 'self' 'unsafe-inline' https://t1.daumcdn.net http://t1.daumcdn.net",
            "img-src 'self' data: blob: https://*.daumcdn.net http://*.daumcdn.net https://*.kakao.com http://*.kakao.com https://*.kakaocdn.net",
            "connect-src 'self' https://dapi.kakao.com https://*.daumcdn.net http://*.daumcdn.net https://*.kakao.com http://*.kakao.com",
            "font-src 'self' data:",
            "frame-src https://map.kakao.com",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="카카오맵 검색 및 좌표 변환 데모")
    parser.add_argument("--host", default="127.0.0.1", help="서버 주소")
    parser.add_argument("--port", type=int, default=8000, help="서버 포트")
    args = parser.parse_args()

    app = create_runtime_app()
    print(f"카카오맵 데모: http://{args.host}:{args.port}")
    print("기관 좌표 및 물품 수: Supabase 연결됨 (응답 CDN 5분 캐시)")
    print("종료하려면 Ctrl+C를 누르세요.")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
