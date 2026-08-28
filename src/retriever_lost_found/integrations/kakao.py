from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


KAKAO_LOCAL_BASE_URL = "https://dapi.kakao.com/v2/local"


class KakaoLocalClient:
    """카카오맵 REST API의 주소 변환과 키워드 검색 클라이언트입니다."""

    def __init__(self, rest_api_key: str, timeout: float = 10.0) -> None:
        self.rest_api_key = rest_api_key.strip()
        self.timeout = timeout
        if not self.rest_api_key:
            raise ValueError("KAKAO_REST_API_KEY가 비어 있습니다.")

    def _get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        url = f"{KAKAO_LOCAL_BASE_URL}/{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"KakaoAK {self.rest_api_key}",
                "Accept": "application/json",
                "User-Agent": "RetrieverLostFound/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(body).get("msg") or error.reason
            except json.JSONDecodeError:
                message = error.reason
            raise RuntimeError(f"카카오 API HTTP {error.code}: {message}") from None
        except urllib.error.URLError as error:
            raise RuntimeError(f"카카오 API 연결 실패: {error.reason}") from None
        except json.JSONDecodeError:
            raise RuntimeError("카카오 API가 올바르지 않은 JSON을 반환했습니다.") from None

        if not isinstance(payload, dict):
            raise RuntimeError("카카오 API 응답 형식이 올바르지 않습니다.")
        return payload

    def geocode_address(self, address: str, size: int = 10) -> dict[str, Any]:
        query = validate_query(address, "주소")
        return self._get(
            "search/address.json",
            {"query": query, "analyze_type": "similar", "size": clamp_size(size)},
        )

    def search_keyword(
        self,
        query: str,
        *,
        longitude: float | None = None,
        latitude: float | None = None,
        size: int = 15,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "query": validate_query(query, "검색어"),
            "size": clamp_size(size),
        }
        if longitude is not None and latitude is not None:
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValueError("중심 좌표 범위가 올바르지 않습니다.")
            # 중심 좌표는 동명이인 장소의 정확도 보정에만 사용합니다.
            # 거리순을 강제하면 "명동역" 검색에 가까운 다른 역이 먼저 나올 수 있습니다.
            params.update({"x": f"{longitude:.8f}", "y": f"{latitude:.8f}"})
        return self._get("search/keyword.json", params)


def validate_query(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label}를 입력하세요.")
    if len(value) > 200:
        raise ValueError(f"{label}는 200자 이하여야 합니다.")
    return value


def clamp_size(size: int) -> int:
    return max(1, min(int(size), 15))


def kakao_sdk_url(javascript_key: str) -> str:
    return (
        "https://dapi.kakao.com/v2/maps/sdk.js?autoload=false&appkey="
        + urllib.parse.quote(javascript_key, safe="")
        + "&libraries=clusterer"
    )
