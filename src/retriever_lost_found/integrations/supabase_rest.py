from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .deadline import request_timeout


class SupabaseRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SupabaseRestClient:
    """서버 전용 secret key로 Supabase Data API를 부르는 공통 요청 코드(절대 규칙 4·6).

    수집 저장소와 알림 저장소가 함께 쓴다. 오류 문구에는 secret key를 남기지 않는다(PRD 5 보안).
    """

    error_label = "Supabase Data API"

    url: str
    secret_key: str
    timeout: float
    # 수집 크론은 단계 마감 시각을 넣는다(PRD 4.5). None이면 요청마다 timeout만 쓴다.
    deadline: float | None = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: list[tuple[str, str]] | dict[str, str] | None = None,
        payload: Any | None = None,
        prefer: str | None = None,
    ) -> Any:
        url = f"{self.url}/rest/v1/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
            "User-Agent": "RetrieverLostFound/0.1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        label = self.error_label
        try:
            with urllib.request.urlopen(request, timeout=request_timeout(self.deadline, self.timeout)) as response:
                response_body = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            detail = detail.replace(self.secret_key, "[REDACTED]")
            raise SupabaseRequestError(
                f"{label} HTTP {error.code}: {detail or error.reason}",
                status_code=error.code,
            ) from None
        except (urllib.error.URLError, TimeoutError) as error:
            reason = getattr(error, "reason", error)
            raise SupabaseRequestError(f"{label} 연결 실패: {reason}") from None
        if not response_body:
            return None
        try:
            return json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SupabaseRequestError(f"{label} 응답이 JSON이 아닙니다.") from error
