from io import BytesIO

import pytest
from PIL import Image

from retriever_lost_found.thumbnails.resize import resize_image


def test_resize_preserves_aspect_ratio_and_removes_metadata():
    original = BytesIO()
    image = Image.new("RGB", (400, 200), "red")
    exif = Image.Exif()
    exif[270] = "private information"
    image.save(original, format="JPEG", exif=exif)
    result = Image.open(BytesIO(resize_image(original.getvalue())))
    assert result.size == (160, 80)
    assert result.format == "JPEG"
    assert 270 not in result.getexif()


def test_resize_respects_orientation():
    original = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (300, 100)).save(original, format="JPEG", exif=exif)
    result = Image.open(BytesIO(resize_image(original.getvalue())))
    assert result.size == (53, 160)


def _encoded(size, fmt):
    output = BytesIO()
    Image.new("RGB", size, "blue").save(output, format=fmt)
    return output.getvalue()


def test_jpeg_over_pixel_limit_is_read_reduced_and_resized(monkeypatch):
    # PRD 4.3-C — 운영 실패 사례(9248×6936 JPEG)를 작은 상한으로 재현한다.
    monkeypatch.setattr("retriever_lost_found.thumbnails.resize.MAX_DECODED_PIXELS", 100_000)
    result = Image.open(BytesIO(resize_image(_encoded((1280, 960), "JPEG"))))
    assert result.size == (160, 120)


def test_non_jpeg_over_pixel_limit_is_still_rejected(monkeypatch):
    monkeypatch.setattr("retriever_lost_found.thumbnails.resize.MAX_DECODED_PIXELS", 100_000)
    with pytest.raises(ValueError):
        resize_image(_encoded((1280, 960), "PNG"))
    # 상한 안의 PNG는 그대로 축소된다.
    assert Image.open(BytesIO(resize_image(_encoded((320, 240), "PNG")))).size == (160, 120)


def test_invalid_data_is_rejected():
    with pytest.raises(ValueError):
        resize_image(b"not an image")
