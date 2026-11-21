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


def test_invalid_data_is_rejected():
    with pytest.raises(ValueError):
        resize_image(b"not an image")
