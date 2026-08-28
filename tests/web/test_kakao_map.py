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
    apply_item_counts,
)
from retriever_lost_found.search.fuzzy import fuzzy_item_score, rank_found_items
from retriever_lost_found.web.app import (
    content_security_policy as _content_security_policy,
    create_app,
    create_runtime_app,
)


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
    def fetch_locations(self) -> list[dict]:
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
            "item_count": 4,
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
