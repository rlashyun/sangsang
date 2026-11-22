"""PRD 3.2-2 · 5 — 원본을 디스크에 저장하지 않고 목록 사진만 Storage에 저장한다."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .resize import resize_image

BUCKET = "found-item-thumbnails"
MAX_ORIGINAL_BYTES = 20_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def thumbnail_key(item_id: int, source_url: str) -> str:
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
        raise ValueError("물품 ID가 올바르지 않습니다.")
    return f"{item_id}/{hashlib.sha256(source_url.encode('utf-8')).hexdigest()}.jpg"


def is_supported_source(source_url: str) -> bool:
    try:
        parts = urlsplit(source_url)
        return (parts.scheme == "https" and parts.hostname == "minwon24.police.go.kr"
                and parts.port in (None, 443) and not parts.username and not parts.password
                and not parts.fragment and not parts.path.lower().endswith("/img02_no_img.gif"))
    except ValueError:
        return False


class ThumbnailStorage:
    def __init__(self, supabase_url: str, secret_key: str, *, opener=None, timeout: float = 10):
        parsed = urlsplit(supabase_url)
        if (parsed.scheme != "https" or not parsed.hostname
                or not re.fullmatch(r"[a-z0-9-]+\.supabase\.co", parsed.hostname)
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment or not secret_key):
            raise ValueError("Storage 설정이 올바르지 않습니다.")
        self.url = f"https://{parsed.hostname}"
        self._secret = secret_key
        self.opener = opener or build_opener(_NoRedirect())
        self.timeout = timeout

    def create(self, item_id: int, source_url: str) -> str:
        if not is_supported_source(source_url):
            raise ValueError("지원하지 않는 원본 사진 주소입니다.")
        key = thumbnail_key(item_id, source_url)
        with self.opener.open(Request(source_url, headers={"User-Agent": "RetrieverLostFound/0.1"}), timeout=self.timeout) as response:
            original = response.read(MAX_ORIGINAL_BYTES + 1)
        if len(original) > MAX_ORIGINAL_BYTES:
            raise ValueError("원본 사진이 크기 제한을 초과했습니다.")
        image = resize_image(original)
        request = Request(f"{self.url}/storage/v1/object/{BUCKET}/{key}", data=image,
                          method="POST", headers={"Authorization": f"Bearer {self._secret}",
                          "apikey": self._secret, "Content-Type": "image/jpeg", "x-upsert": "true"})
        with self.opener.open(request, timeout=self.timeout) as response:
            response.read()
        return key

    def public_url(self, key: str) -> str:
        if not re.fullmatch(r"[1-9][0-9]*/[0-9a-f]{64}\.jpg", key):
            raise ValueError("축소 사진 경로가 올바르지 않습니다.")
        return f"{self.url}/storage/v1/object/public/{BUCKET}/{key}"

    def delete(self, key: str) -> None:
        self.public_url(key)  # 같은 버킷의 생성한 경로만 삭제한다.
        request = Request(f"{self.url}/storage/v1/object/{BUCKET}",
                          data=json.dumps({"prefixes": [key]}).encode(), method="DELETE",
                          headers={"Authorization": f"Bearer {self._secret}", "apikey": self._secret,
                                   "Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            response.read()
