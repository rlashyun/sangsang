"""PRD 4.5, 6 — reviewed overseas custody; never infer from embassy keywords."""

from __future__ import annotations

import unicodedata


# Keep in sync with is_overseas_custody in the migration (covered by a parity test).
OVERSEAS_CUSTODY_NAMES = frozenset({
    "주타이베이대한민국대표부",
    "주오스트리아대사관",
    "주홍콩대한민국총영사관",
    "주이탈리아대한민국대사관",
    "주바르셀로나총영사관",
    "주덴마크대사관",
    "주체코대사관",
    "주헝가리대한민국대사관",
    "주스페인대사관",
    "주스위스대한민국대사관",
    "주고베총영사관",
    "주삿포로총영사관",
    "주독일대사관",
})


def is_overseas_custody(source_code: str, name: str | None) -> bool:
    normalized = unicodedata.normalize("NFKC", name or "").casefold()
    normalized = "".join(character for character in normalized if character.isalnum())
    return source_code == "partner_api" and normalized in OVERSEAS_CUSTODY_NAMES
