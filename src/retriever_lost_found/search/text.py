"""PRD 4.5 — shared text primitives; item ranking lives in hybrid.py."""
from __future__ import annotations

import unicodedata
from typing import Any


def normalize_base_text(value: Any, *, keep_spaces: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    characters = [character if character.isalnum() else " " for character in normalized]
    spaced = " ".join("".join(characters).split())
    return spaced if keep_spaces else spaced.replace(" ", "")

