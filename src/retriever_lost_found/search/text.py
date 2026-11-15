"""PRD 4.5 — shared text primitives; item ranking lives in hybrid.py."""
from __future__ import annotations

import unicodedata
from typing import Any


def normalize_base_text(value: Any, *, keep_spaces: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    characters = [character if character.isalnum() else " " for character in normalized]
    spaced = " ".join("".join(characters).split())
    return spaced if keep_spaces else spaced.replace(" ", "")


def levenshtein_ratio(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))
