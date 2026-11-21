"""목록용 축소본 생성. 원본은 영구 저장하지 않는다."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


def resize_image(data: bytes, *, max_side: int = 160, quality: int = 80) -> bytes:
    """PRD 4.3-C — 사진 비율과 회전을 보존해 JPEG 축소본을 만든다."""
    if not data or len(data) > 20_000_000:
        raise ValueError("사진이 없거나 크기 제한을 초과했습니다.")
    if not 1 <= max_side <= 1024 or not 1 <= quality <= 95:
        raise ValueError("축소 사진 설정이 올바르지 않습니다.")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("사진 해상도가 너무 큽니다.")
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("사진을 변환할 수 없습니다.") from error
