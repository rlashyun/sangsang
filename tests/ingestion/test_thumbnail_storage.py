from io import BytesIO
from urllib.error import URLError

import pytest
from PIL import Image

from retriever_lost_found.thumbnails.storage import ThumbnailStorage


class Response:
    def __init__(self, data=b""):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.data[:size] if size >= 0 else self.data


class FakeOpener:
    def __init__(self, source):
        self.source = source
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return Response(self.source if len(self.requests) == 1 else b"{}")


def test_uploads_only_resized_jpeg_and_produces_public_url():
    data = BytesIO()
    Image.new("RGB", (800, 400), "red").save(data, format="PNG")
    opener = FakeOpener(data.getvalue())
    storage = ThumbnailStorage("https://test-project.supabase.co", "secret", opener=opener)
    key = storage.create(42, "https://minwon24.police.go.kr/photo?id=42")
    assert len(opener.requests) == 2
    assert opener.requests[0].get_header("Authorization") is None
    assert opener.requests[1].get_header("Authorization") == "Bearer secret"
    assert Image.open(BytesIO(opener.requests[1].data)).size == (160, 80)
    assert storage.public_url(key).startswith("https://test-project.supabase.co/storage/v1/object/public/")


@pytest.mark.parametrize("url", ["http://minwon24.police.go.kr/a", "https://minwon24.police.go.kr.evil.example/a", "https://evil.example/a", "https://minwon24.police.go.kr/img02_no_img.gif"])
def test_rejects_untrusted_and_missing_photo_urls(url):
    opener = FakeOpener(b"")
    with pytest.raises(ValueError):
        ThumbnailStorage("https://test.supabase.co", "secret", opener=opener).create(1, url)
    assert opener.requests == []


def test_upload_failure_does_not_claim_success():
    class FailingOpener(FakeOpener):
        def open(self, request, timeout):
            if self.requests:
                raise URLError("failed")
            return super().open(request, timeout)

    image = BytesIO()
    Image.new("RGB", (20, 20)).save(image, format="JPEG")
    with pytest.raises(URLError):
        ThumbnailStorage("https://test.supabase.co", "secret", opener=FailingOpener(image.getvalue())).create(1, "https://minwon24.police.go.kr/a")
