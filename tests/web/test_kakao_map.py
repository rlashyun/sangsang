import json
import os
import tempfile
import unittest
import urllib.parse
import urllib.request
from io import BytesIO
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
    apply_item_counts,
)
from retriever_lost_found.search.hybrid import keyword_scores
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
    def fetch_rerank_items(self, location_ids, item_ids, **kwargs):
        return [{"item_id": row["id"], "detail": row, "vector": None}
                for row in self.fetch_items(location_ids) if row["id"] in item_ids]

    def __init__(self, *, page_has_more: bool = False) -> None:
        self.page_has_more = page_has_more
        self.fetch_items_calls: list[list[int]] = []
        self.page_calls: list[dict] = []

    def fetch_item_page(
        self,
        location_ids: list[int],
        *,
        limit: int,
        before: tuple[str, int] | None = None,
    ) -> dict:
        self.page_calls.append(
            {"location_ids": location_ids, "limit": limit, "before": before}
        )
        return {
            "items": [
                {"id": 12, "storage_location_id": location_ids[0], "item_name": "검정 지갑", "registered_on": "2026-08-26"},
                {"id": 11, "storage_location_id": location_ids[0], "item_name": "파란 우산", "registered_on": "2026-08-25"},
            ],
            "has_more": self.page_has_more,
            "total_count": 120 if before is None else None,
        }

    def fetch_items(self, location_ids: list[int]) -> list[dict]:
        self.fetch_items_calls.append(location_ids)
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

    def test_http_response_upgrades_insecure_cdn_requests_without_opening_other_hosts(self) -> None:
        # PRD 5 — 외부 HTTP 요청을 HTTPS로 올리고 스크립트 출처 허용 범위를 넓히지 않는다.
        client = TestClient(create_app(KakaoLocalClient("test-rest-key"), "test-js-key"))
        response = client.get("/")
        directives = _csp_directives(response.headers["Content-Security-Policy"])

        self.assertIn("upgrade-insecure-requests", directives)
        for name in ("script-src", "style-src", "img-src", "connect-src"):
            self.assertNotIn("http://*.kakaocdn.net", directives[name], name)
            self.assertIn("https://*.kakaocdn.net", directives[name], name)
        for value in ("*", "http:", "https:", "'unsafe-inline'", "'unsafe-eval'"):
            self.assertNotIn(value, directives["script-src"])

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
        first = {
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
        rows = [
            {**first, "id": 101 + index, "source_key": f"partner:{index}"}
            for index in range(1_001)
        ]
        urlopen.return_value = FakeResponse(rows)
        client = SupabaseMapLocationClient(
            "https://example.supabase.co",
            "server-secret",
        )

        locations = client.fetch_locations()

        self.assertEqual(len(locations), 1_001)
        self.assertEqual(locations[-1]["storage_location_id"], 1101)
        self.assertEqual(locations[0]["storage_location_id"], 101)
        self.assertEqual(locations[0]["item_count"], 7)
        self.assertEqual(locations[0]["source"], "partner")
        self.assertEqual(locations[0]["longitude"], 127.1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            "https://example.supabase.co/rest/v1/rpc/map_locations_with_item_counts_json",
            request.full_url,
        )
        self.assertEqual(urlopen.call_count, 1)
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
    def test_supabase_map_locations_without_counts_are_paginated_without_process_cache(self, urlopen) -> None:
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

        first = client.fetch_locations(with_counts=False)
        second = client.fetch_locations(with_counts=False)

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

    # PRD 3.3 · 5 — 검색어 없는 목록은 후보 전체 대신 한 페이지만 받는다.
    @patch("urllib.request.urlopen")
    def test_found_item_first_page_asks_one_extra_row_and_total(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({
            "items": [
                {"id": 3, "registered_on": "2026-08-27"},
                {"id": 2, "registered_on": "2026-08-26"},
                {"id": 1, "registered_on": "2026-08-25"},
            ],
            "total_count": 120,
        })
        client = SupabaseFoundItemClient("https://example.supabase.co", "server-secret")

        page = client.fetch_item_page([88, 77, 77], limit=2)

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://example.supabase.co/rest/v1/rpc/found_items_page",
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer server-secret")
        self.assertEqual(json.loads(request.data), {
            "p_location_ids": [77, 88],
            "p_limit": 3,
            "p_before_registered_on": None,
            "p_before_id": None,
            "p_include_total": True,
        })
        self.assertEqual([item["id"] for item in page["items"]], [3, 2])
        self.assertTrue(page["has_more"])
        self.assertEqual(page["total_count"], 120)

    @patch("urllib.request.urlopen")
    def test_found_item_last_page_has_no_more_and_skips_total(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({
            "items": [{"id": 1, "registered_on": "2026-08-25"}],
            "total_count": None,
        })
        client = SupabaseFoundItemClient("https://example.supabase.co", "server-secret")

        page = client.fetch_item_page([77], limit=2, before=("2026-08-26", 2))

        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["p_before_registered_on"], "2026-08-26")
        self.assertEqual(body["p_before_id"], 2)
        self.assertFalse(body["p_include_total"])
        self.assertEqual([item["id"] for item in page["items"]], [1])
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["total_count"])

    def test_found_item_page_rejects_more_than_300_locations(self) -> None:
        client = SupabaseFoundItemClient("https://example.supabase.co", "server-secret")

        with self.assertRaises(ValueError):
            client.fetch_item_page(list(range(1, 302)), limit=50)

    def test_keyword_search_matches_query_correction_and_rejects_unrelated_item(self) -> None:
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

        scores = keyword_scores([wallet, phone], "아이퐁")

        self.assertEqual(set(scores), {1})
        self.assertGreater(scores[1], 0)

    def test_keyword_search_combines_color_and_item_tokens(self) -> None:
        item = {
            "id": 1,
            "item_name": "남성용 지갑",
            "raw_category_name": "지갑 > 남성용 지갑",
            "color_name": "블랙(검정)",
            "description": "가죽 제품",
            "registered_on": "2026-08-25",
        }

        scores = keyword_scores([item], "검정 지갑")

        self.assertEqual(set(scores), {1})
        self.assertGreater(scores[1], 0)

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

    # PRD 3.3 · 5 — 검색어 없는 목록은 50건씩 나눠 보내고, 다음 페이지 커서를 준다.
    def test_found_item_endpoint_without_query_returns_first_page_and_cursor(self) -> None:
        found_item_client = FakeFoundItemClient(page_has_more=True)
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=found_item_client,
        )

        response = TestClient(app).get("/api/found-items", params={"location_ids": "77,88"})
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in payload["items"]], [12, 11])
        self.assertEqual(payload["total_count"], 120)
        self.assertEqual(payload["next_cursor"], "2026-08-25:11")
        self.assertEqual(
            found_item_client.page_calls,
            [{"location_ids": [77, 88], "limit": 50, "before": None}],
        )
        # 후보 전체 조회(1,000건씩 반복)는 타지 않는다.
        self.assertEqual(found_item_client.fetch_items_calls, [])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_found_item_endpoint_last_page_has_no_cursor(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=FakeFoundItemClient(page_has_more=False),
        )

        response = TestClient(app).get("/api/found-items", params={"location_ids": "77"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 2)
        self.assertIsNone(response.json()["next_cursor"])

    def test_found_item_endpoint_passes_cursor_to_next_page(self) -> None:
        found_item_client = FakeFoundItemClient(page_has_more=True)
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=found_item_client,
        )

        response = TestClient(app).get(
            "/api/found-items",
            params={"location_ids": "77", "cursor": "2026-08-25:11"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(found_item_client.page_calls[0]["before"], ("2026-08-25", 11))
        self.assertIsNone(response.json()["total_count"])

    def test_found_item_endpoint_rejects_malformed_cursor(self) -> None:
        found_item_client = FakeFoundItemClient()
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=found_item_client,
        )

        for cursor in ("2026-08-25", "2026-13-01:11", "2026-08-25:abc", "2026-08-25:-1"):
            with self.subTest(cursor=cursor):
                response = TestClient(app).get(
                    "/api/found-items",
                    params={"location_ids": "77", "cursor": cursor},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "목록 페이지 위치가 올바르지 않습니다.")
        self.assertEqual(found_item_client.page_calls, [])

    def test_found_item_endpoint_with_query_still_ranks_all_candidates(self) -> None:
        # PRD 4.5 — 검색어가 있으면 퍼지 점수를 위해 후보 전체를 본다. 페이지로 자르지 않는다.
        found_item_client = FakeFoundItemClient(page_has_more=True)
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=found_item_client,
        )

        response = TestClient(app).get(
            "/api/found-items",
            params={"location_ids": "77", "q": "아이퐁"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(found_item_client.fetch_items_calls, [[77], [77]])
        self.assertEqual(found_item_client.page_calls, [])
        self.assertIsNone(response.json()["next_cursor"])

    def test_found_item_endpoint_rejects_cursor_with_query(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            institutions=[],
            found_item_client=FakeFoundItemClient(),
        )

        response = TestClient(app).get(
            "/api/found-items",
            params={"location_ids": "77", "q": "지갑", "cursor": "2026-08-25:11"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"],
            "검색 결과 페이지 위치가 올바르지 않습니다.",
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

    def test_institution_endpoint_can_load_locations_without_count_query(self) -> None:
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=FakeMapLocationClient(counts_error=True),
        )

        response = TestClient(app).get("/api/institutions?with_counts=false")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["institutions"][0]["storage_location_id"], 77)
        self.assertFalse(payload["counts_available"])
        self.assertIsNone(payload["count_error"])

    @patch("urllib.request.urlopen")
    def test_institution_endpoint_keeps_map_when_item_counts_fail(self, urlopen) -> None:
        # PRD 6 — 개수 집계가 실패해도 기관 마커는 보여야 한다.
        urlopen.side_effect = [
            urllib.error.HTTPError(
                "https://example.supabase.co/rest/v1/rpc/map_locations_with_item_counts_json",
                500, "statement timeout", {}, BytesIO(b"statement timeout"),
            ),
            FakeResponse([{
                "id": 77,
                "location_source_code": "partner_csv",
                "source_key": "partner:test",
                "display_group": "partner",
                "longitude": 127.1,
                "latitude": 37.5,
            }]),
        ]
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=SupabaseMapLocationClient(
                "https://example.supabase.co", "server-secret",
            ),
        )

        response = TestClient(app).get("/api/institutions")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["institutions"]), 1)
        self.assertEqual(payload["institutions"][0]["storage_location_id"], 77)
        self.assertFalse(payload["counts_available"])
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("/rpc/map_locations?", urlopen.call_args.args[0].full_url)
        self.assertIn("statement timeout", payload["count_error"])
        self.assertEqual(
            response.headers["vercel-cdn-cache-control"],
            "public, s-maxage=60, stale-while-revalidate=60",
        )

    def test_institution_endpoint_omits_data_updated_at(self) -> None:
        # PRD 6 — 최종 갱신 시각은 화면에 표시하지 않기로 해(2026-11-05) 응답에도 넣지 않는다.
        app = create_app(
            KakaoLocalClient("kakao-secret"),
            "javascript-key",
            map_location_client=FakeMapLocationClient(),
        )

        response = TestClient(app).get("/api/institutions")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("data_updated_at", payload)
        self.assertEqual(len(payload["institutions"]), 1)
        self.assertEqual(payload["institutions"][0]["storage_location_id"], 77)
        self.assertTrue(payload["counts_available"])

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
