import json
import os
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from retriever_lost_found.integrations.kakao import (
    KakaoLocalClient,
    kakao_sdk_url as _kakao_sdk_url,
)
from retriever_lost_found.integrations.supabase import (
    SupabaseFoundItemClient,
    SupabaseMapLocationClient,
    SupabaseSyncStatusClient,
    apply_item_counts,
)
from retriever_lost_found.search.fuzzy import fuzzy_item_score, rank_found_items
from retriever_lost_found.web.app import (
    content_security_policy as _content_security_policy,
    create_app,
    create_runtime_app,
)


def _csp_directives(policy: str) -> dict[str, list[str]]:
    directives = {}
    for directive in policy.split(";"):
        name, *sources = directive.split()
        directives[name] = sources
    return directives


class FakeResponse:
    def __init__(self, payload) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class FakeFoundItemClient:
    def fetch_items(self, location_ids: list[int]) -> list[dict]:
        return [{
            "id": 1,
            "storage_location_id": location_ids[0],
            "item_name": "애플 아이폰",
            "raw_category_name": "휴대폰 > 아이폰",
            "color_name": "검정",
            "description": "검정색 아이폰",
            "registered_on": "2026-08-25",
        }]


class FakeMapLocationClient:
    def __init__(self, *, counts_error: bool = False, locations_error: bool = False) -> None:
        self.counts_error = counts_error
        self.locations_error = locations_error

    def fetch_locations(self, *, with_counts: bool = True) -> list[dict]:
        if with_counts and self.counts_error:
            raise RuntimeError("Supabase 지도 위치 API HTTP 500: statement timeout")
        if not with_counts and self.locations_error:
            raise RuntimeError("Supabase 지도 위치 API 연결 실패: timed out")
        return [{
            "id": 77,
            "storage_location_id": 77,
            "location_source_code": "partner_csv",
            "source_key": "partner:test",
            "name": "테스트 보관소",
            "address": "서울시 테스트로 1",
            "phone": "02-000-0000",
            "organization": "",
            "source": "partner",
            "longitude": 127.1,
            "latitude": 37.5,
            "item_count": 4 if with_counts else 0,
        }]


class FakeSyncStatusClient:
    def __init__(self, updated_at: str | None = None, *, error: bool = False) -> None:
        self.updated_at = updated_at
        self.error = error

    def fetch_data_updated_at(self) -> str | None:
        if self.error:
            raise RuntimeError("Supabase 동기화 기록 API 연결 실패: timed out")
        return self.updated_at


class SyncRunResponses:
    """출처별 ingestion_runs 조회 URL에 맞춰 가짜 응답을 돌려준다."""

    def __init__(self, rows_by_source: dict[str, list[dict]]) -> None:
        self.rows_by_source = rows_by_source

    def __call__(self, request, timeout=None):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        source = params["item_source_code"][0].removeprefix("eq.")
        return FakeResponse(self.rows_by_source.get(source, []))


class SupabaseSyncStatusClientTests(unittest.TestCase):
    # PRD 6 — "오늘 오전 9시 기준" 최종 갱신 시각. 두 출처 중 더 오래된 성공 시각을 쓴다.

    def _client(self) -> SupabaseSyncStatusClient:
        return SupabaseSyncStatusClient(
            "https://example.supabase.co",
            "server-secret",
            source_codes=("partner_api", "police_api"),
        )

    @patch("urllib.request.urlopen")
    def test_returns_older_success_time_of_two_sources(self, urlopen) -> None:
        urlopen.side_effect = SyncRunResponses({
            "partner_api": [{"finished_at": "2026-09-27T00:01:40+00:00"}],
            "police_api": [{"finished_at": "2026-09-27T00:03:05+00:00"}],
        })

        self.assertEqual(
            self._client().fetch_data_updated_at(),
            "2026-09-27T00:01:40+00:00",
        )

    @patch("urllib.request.urlopen")
    def test_failed_source_holds_time_back_to_its_last_success(self, urlopen) -> None:
        # 경찰관서가 오늘 실패했다면 조회 결과는 어제 성공 기록이다.
        urlopen.side_effect = SyncRunResponses({
            "partner_api": [{"finished_at": "2026-09-27T00:01:40+00:00"}],
            "police_api": [{"finished_at": "2026-09-26T00:02:10+00:00"}],
        })

        self.assertEqual(
            self._client().fetch_data_updated_at(),
            "2026-09-26T00:02:10+00:00",
        )

    @patch("urllib.request.urlopen")
    def test_queries_only_runs_whose_data_was_applied(self, urlopen) -> None:
        # partial은 upsert까지 끝난 실행이다 — 데이터는 반영되었다 (service.py).
        urlopen.side_effect = SyncRunResponses({
            "partner_api": [{"finished_at": "2026-09-27T00:01:40+00:00"}],
            "police_api": [{"finished_at": "2026-09-27T00:03:05+00:00"}],
        })

        self._client().fetch_data_updated_at()

        self.assertEqual(urlopen.call_count, 2)
        for call in urlopen.call_args_list:
            request = call.args[0]
            parsed = urllib.parse.urlparse(request.full_url)
            params = urllib.parse.parse_qs(parsed.query)
            self.assertEqual(parsed.path, "/rest/v1/ingestion_runs")
            self.assertEqual(params["status"], ["in.(succeeded,partial)"])
            self.assertEqual(params["order"], ["started_at.desc"])
            self.assertEqual(params["limit"], ["1"])
            self.assertEqual(request.headers["Authorization"], "Bearer server-secret")

    @patch("urllib.request.urlopen")
    def test_returns_none_when_any_source_never_succeeded(self, urlopen) -> None:
        urlopen.side_effect = SyncRunResponses({
            "partner_api": [{"finished_at": "2026-09-27T00:01:40+00:00"}],
            "police_api": [],
        })

        self.assertIsNone(self._client().fetch_data_updated_at())

    @patch("urllib.request.urlopen")
    def test_rejects_malformed_finished_at(self, urlopen) -> None:
        urlopen.side_effect = SyncRunResponses({
            "partner_api": [{"finished_at": "2026-09-27T00:01:40+00:00"}],
            "police_api": [{"finished_at": "어제"}],
        })

        with self.assertRaises(RuntimeError):
            self._client().fetch_data_updated_at()


class KakaoLocalClientTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_geocode_address_uses_address_endpoint(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({"documents": [{"x": "127", "y": "37"}]})
        client = KakaoLocalClient("secret")

        payload = client.geocode_address("서울 중구 세종대로 110")

        self.assertEqual(payload["documents"][0]["x"], "127")
        request = urlopen.call_args.args[0]
        parsed = urllib.parse.urlparse(request.full_url)
        self.assertEqual(parsed.path, "/v2/local/search/address.json")
        self.assertEqual(
            urllib.parse.parse_qs(parsed.query)["query"],
            ["서울 중구 세종대로 110"],
        )
        self.assertEqual(request.headers["Authorization"], "KakaoAK secret")

    @patch("urllib.request.urlopen")
    def test_keyword_search_uses_center_without_overriding_accuracy_sort(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({"documents": []})
        client = KakaoLocalClient("secret")

        client.search_keyword("태릉유실물센터", longitude=126.978, latitude=37.5665)

        request = urlopen.call_args.args[0]
        params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertNotIn("sort", params)
        self.assertEqual(params["x"], ["126.97800000"])
        self.assertEqual(params["y"], ["37.56650000"])

    def test_empty_query_is_rejected_before_api_call(self) -> None:
        client = KakaoLocalClient("secret")

        with self.assertRaisesRegex(ValueError, "검색어를 입력"):
            client.search_keyword("   ")

    def test_invalid_coordinate_is_rejected(self) -> None:
        client = KakaoLocalClient("secret")

        with self.assertRaisesRegex(ValueError, "좌표 범위"):
            client.search_keyword("서울역", longitude=200, latitude=37)

    def test_csp_allows_kakao_tiles_on_local_http_server(self) -> None:
        policy = _content_security_policy()

        self.assertIn("http://t1.daumcdn.net", policy)
        self.assertIn("http://*.daumcdn.net", policy)

    def test_csp_allows_new_kakao_cdn_for_sdk_and_resources(self) -> None:
        directives = _csp_directives(_content_security_policy())

        for name in ("script-src", "style-src", "img-src", "connect-src"):
            self.assertIn("https://*.kakaocdn.net", directives[name], name)
        self.assertNotIn("*", directives["script-src"])
        self.assertNotIn("https:", directives["script-src"])
        self.assertNotIn("'unsafe-inline'", directives["script-src"])
        self.assertNotIn("'unsafe-eval'", directives["script-src"])

    def test_http_response_applies_updated_csp(self) -> None:
        client = TestClient(create_app(KakaoLocalClient("test-rest-key"), "test-js-key"))

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Security-Policy"], _content_security_policy())

    # PRD 3.2-2 — 습득물 사진(image_url)은 전부 minwon24.police.go.kr에서 온다 (운영 DB 2026-09-27 확인)
    def test_csp_allows_police_item_photos_as_images(self) -> None:
        directives = _csp_directives(_content_security_policy())

        self.assertIn("https://minwon24.police.go.kr", directives["img-src"])

    def test_csp_limits_police_photo_host_to_img_src(self) -> None:
        directives = _csp_directives(_content_security_policy())

        for name, sources in directives.items():
            if name != "img-src":
                self.assertNotIn("https://minwon24.police.go.kr", sources, name)
        self.assertNotIn("http://minwon24.police.go.kr", directives["img-src"])
        self.assertNotIn("https://*.police.go.kr", directives["img-src"])
        self.assertNotIn("https:", directives["img-src"])
        self.assertNotIn("*", directives["img-src"])

    def test_handler_requests_clusterer_library(self) -> None:
        url = _kakao_sdk_url("javascript-key")

        self.assertIn("libraries=clusterer", url)
        self.assertIn("appkey=javascript-key", url)

    @patch("urllib.request.urlopen")
    def test_supabase_map_locations_use_server_side_rpc(self, urlopen) -> None:
        urlopen.return_value = FakeResponse([
            {
                "id": 101,
                "location_source_code": "partner_csv",
                "source_key": "partner:test",
                "name": "테스트 보관소",
                "address": "서울시 테스트로 1",
                "phone": "02-000-0000",
                "display_group": "partner",
                "longitude": 127.1,
                "latitude": 37.5,
                "item_count": 7,
            }
        ])
        client = SupabaseMapLocationClient(
            "https://example.supabase.co",
            "server-secret",
        )

        locations = client.fetch_locations()

        self.assertEqual(locations[0]["storage_location_id"], 101)
        self.assertEqual(locations[0]["item_count"], 7)
        self.assertEqual(locations[0]["source"], "partner")
        self.assertEqual(locations[0]["longitude"], 127.1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertIn(
            "/rest/v1/rpc/map_locations_with_item_counts?",
            request.full_url,
        )
        self.assertIn("offset=0", request.full_url)
        self.assertEqual(request.headers["Authorization"], "Bearer server-secret")

    @patch("urllib.request.urlopen")
    def test_supabase_map_locations_without_counts_use_lightweight_rpc(self, urlopen) -> None:
        urlopen.return_value = FakeResponse([
            {
                "id": 101,
                "location_source_code": "partner_csv",
                "source_key": "partner:test",
                "name": "테스트 보관소",
                "address": "서울시 테스트로 1",
                "phone": "",
                "display_group": "partner",
                "longitude": 127.1,
                "latitude": 37.5,
            }
        ])
        client = SupabaseMapLocationClient("https://example.supabase.co", "server-secret")

        locations = client.fetch_locations(with_counts=False)

        self.assertEqual(locations[0]["storage_location_id"], 101)
        self.assertEqual(locations[0]["item_count"], 0)
        request = urlopen.call_args.args[0]
        self.assertIn("/rest/v1/rpc/map_locations?", request.full_url)
        self.assertNotIn("map_locations_with_item_counts", request.full_url)

    @patch("urllib.request.urlopen")
    def test_supabase_map_locations_are_paginated_without_process_cache(self, urlopen) -> None:
        def row(identifier: int, display_group: str = "partner") -> dict:
            return {
                "id": identifier,
                "location_source_code": "partner_csv",
                "source_key": f"partner:{identifier}",
                "name": f"기관 {identifier}",
                "address": "서울",
                "phone": "",
                "display_group": display_group,
                "longitude": 127.0,
                "latitude": 37.5,
                "item_count": identifier,
            }

        urlopen.side_effect = [
            FakeResponse([row(1), row(2)]),
            FakeResponse([row(3, "police")]),
            FakeResponse([row(1)]),
        ]
        client = SupabaseMapLocationClient(
            "https://example.supabase.co",
            "server-secret",
            page_size=2,
        )

        first = client.fetch_locations()
        second = client.fetch_locations()

        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 1)
        self.assertEqual(urlopen.call_count, 3)
        self.assertIn("offset=2", urlopen.call_args_list[1].args[0].full_url)
        self.assertIn("offset=0", urlopen.call_args.args[0].full_url)

    def test_item_counts_are_joined_by_location_source_and_source_key(self) -> None:
        locations = [{
            "name": "테스트기관",
            "location_source_code": "partner_csv",
            "source_key": "partner:test",
        }]

        enriched = apply_item_counts(
            locations,
            {("partner_csv", "partner:test"): 4},
            {("partner_csv", "partner:test"): 77},
        )

        self.assertEqual(enriched[0]["item_count"], 4)
        self.assertEqual(enriched[0]["storage_location_id"], 77)
        self.assertNotIn("item_count", locations[0])

    @patch("urllib.request.urlopen")
    def test_found_items_are_filtered_by_selected_location_ids(self, urlopen) -> None:
        urlopen.return_value = FakeResponse([{
            "id": 1,
            "storage_location_id": 77,
            "item_name": "검정 지갑",
        }])
        client = SupabaseFoundItemClient("https://example.supabase.co", "server-secret")

        items = client.fetch_items([88, 77, 77])

        self.assertEqual(items[0]["storage_location_id"], 77)
        request = urlopen.call_args.args[0]
        params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertEqual(params["storage_location_id"], ["in.(77,88)"])
        self.assertEqual(request.headers["Authorization"], "Bearer server-secret")

    def test_fuzzy_search_matches_korean_typo_and_rejects_unrelated_item(self) -> None:
        phone = {
            "id": 1,
            "item_name": "애플 아이폰 15 프로",
            "raw_category_name": "휴대폰 > 아이폰",
            "color_name": "블랙(검정)",
            "description": "검정색 아이폰을 보관하고 있습니다.",
            "registered_on": "2026-08-25",
        }
        wallet = {
            "id": 2,
            "item_name": "갈색 남성용 지갑",
            "raw_category_name": "지갑 > 남성용 지갑",
            "color_name": "브라운",
            "description": "가죽 지갑",
            "registered_on": "2026-08-25",
        }

        ranked = rank_found_items([wallet, phone], "아이퐁")

        self.assertEqual([item["id"] for item in ranked], [1])
        self.assertGreater(fuzzy_item_score("아이퐁", phone), 0.5)

    def test_fuzzy_search_combines_color_and_item_tokens(self) -> None:
        item = {
            "id": 1,
            "item_name": "남성용 지갑",
            "raw_category_name": "지갑 > 남성용 지갑",
            "color_name": "블랙(검정)",
            "description": "가죽 제품",
            "registered_on": "2026-08-25",
        }

        ranked = rank_found_items([item], "검정 지갑")

        self.assertEqual(len(ranked), 1)
        self.assertGreaterEqual(ranked[0]["match_score"], 0.8)

    def test_found_item_http_endpoint_returns_ranked_items(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=FakeFoundItemClient(),
        )
        response = TestClient(app).get(
            "/api/found-items",
            params={"location_ids": "77", "q": "아이퐁"},
        )
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(payload["match_count"], 1)
        self.assertEqual(payload["items"][0]["item_name"], "애플 아이폰")
        self.assertEqual(response.headers["cache-control"], "no-store")

    # PRD 3.3 · D3 — 습득물 검색어 상한 30자. 경계값 양쪽을 쌍으로 확인한다.
    def test_found_item_endpoint_accepts_query_of_30_characters(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=FakeFoundItemClient(),
        )
        query = "가" * 30

        response = TestClient(app).get(
            "/api/found-items",
            params={"location_ids": "77", "q": query},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["query"], query)

    def test_found_item_endpoint_rejects_query_of_31_characters(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=FakeFoundItemClient(),
        )

        response = TestClient(app).get(
            "/api/found-items",
            params={"location_ids": "77", "q": "가" * 31},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"],
            "습득물 검색어는 30자 이하여야 합니다.",
        )

    def test_institution_endpoint_uses_database_locations_and_vercel_cdn_cache(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=FakeMapLocationClient(),
        )

        response = TestClient(app).get("/api/institutions")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["institutions"][0]["storage_location_id"], 77)
        self.assertEqual(payload["institutions"][0]["item_count"], 4)
        self.assertTrue(payload["counts_available"])
        self.assertEqual(
            response.headers["cache-control"],
            "public, max-age=0, must-revalidate",
        )
        self.assertEqual(
            response.headers["vercel-cdn-cache-control"],
            "public, s-maxage=300, stale-while-revalidate=600",
        )

    def test_institution_endpoint_keeps_map_when_item_counts_fail(self) -> None:
        # PRD 6 — 개수 집계가 실패해도 기관 마커는 보여야 한다.
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=FakeMapLocationClient(counts_error=True),
        )

        response = TestClient(app).get("/api/institutions")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["institutions"]), 1)
        self.assertEqual(payload["institutions"][0]["storage_location_id"], 77)
        self.assertFalse(payload["counts_available"])
        self.assertIn("statement timeout", payload["count_error"])
        self.assertEqual(
            response.headers["vercel-cdn-cache-control"],
            "public, s-maxage=60, stale-while-revalidate=60",
        )

    def test_institution_endpoint_reports_data_updated_at(self) -> None:
        # PRD 6 — 최종 갱신 시각. 기관 목록과 함께 내려준다.
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=FakeMapLocationClient(),
            sync_status_client=FakeSyncStatusClient("2026-09-27T00:01:40+00:00"),
        )

        response = TestClient(app).get("/api/institutions")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data_updated_at"], "2026-09-27T00:01:40+00:00")
        self.assertEqual(len(payload["institutions"]), 1)

    def test_institution_endpoint_keeps_map_when_sync_status_fails(self) -> None:
        # PRD 6 — 갱신 시각을 못 읽어도 기관 마커는 보여야 한다. 문구만 숨긴다(null).
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=FakeMapLocationClient(),
            sync_status_client=FakeSyncStatusClient(error=True),
        )

        response = TestClient(app).get("/api/institutions")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(payload["data_updated_at"])
        self.assertEqual(len(payload["institutions"]), 1)
        self.assertEqual(payload["institutions"][0]["storage_location_id"], 77)
        self.assertTrue(payload["counts_available"])

    def test_institution_endpoint_without_sync_status_client_returns_null(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=FakeMapLocationClient(),
        )

        payload = TestClient(app).get("/api/institutions").json()

        self.assertIn("data_updated_at", payload)
        self.assertIsNone(payload["data_updated_at"])
        self.assertEqual(len(payload["institutions"]), 1)

    def test_institution_endpoint_fails_when_locations_are_unavailable(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=FakeMapLocationClient(
                counts_error=True, locations_error=True
            ),
        )

        response = TestClient(app).get("/api/institutions")

        self.assertEqual(response.status_code, 502)
        self.assertIn("연결 실패", response.json()["error"])

    def test_runtime_app_uses_supabase(self) -> None:
        environment = {
            "KAKAO_REST_API_KEY": "kakao-rest",
            "KAKAO_JAVASCRIPT_KEY": "kakao-js",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SECRET_KEY": "server-secret",
        }

        with patch.dict(os.environ, environment, clear=True):
            app = create_runtime_app()

        self.assertEqual(app.state.location_source, "supabase")

    def test_runtime_app_requires_supabase_url(self) -> None:
        environment = {
            "KAKAO_REST_API_KEY": "kakao-rest",
            "KAKAO_JAVASCRIPT_KEY": "kakao-js",
            "SUPABASE_SECRET_KEY": "server-secret",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, environment, clear=True):
                current = Path.cwd()
                try:
                    os.chdir(directory)
                    with self.assertRaisesRegex(ValueError, "SUPABASE_URL"):
                        create_runtime_app()
                finally:
                    os.chdir(current)

    def test_fastapi_app_serves_existing_frontend_and_security_headers(self) -> None:
        app = create_app(KakaoLocalClient("kakao-secret"), "javascript-key")

        response = TestClient(app).get("/")

        self.assertIsInstance(app, FastAPI)
        self.assertEqual(response.status_code, 200)
        self.assertIn("appkey=javascript-key", response.text)
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
