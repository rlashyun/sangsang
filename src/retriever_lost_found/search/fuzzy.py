from __future__ import annotations

import unicodedata
from typing import Any


def normalize_fuzzy_text(value: Any, *, keep_spaces: bool = False) -> str:
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


def ngrams(value: str, size: int = 2) -> set[str]:
    if not value:
        return set()
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def jaccard_similarity(left: str, right: str) -> float:
    left_grams = ngrams(left)
    right_grams = ngrams(right)
    union = left_grams | right_grams
    return len(left_grams & right_grams) / len(union) if union else 0.0


def fuzzy_item_score(query: str, item: dict[str, Any]) -> float:
    query_compact = normalize_fuzzy_text(query)
    if not query_compact:
        return 0.0
    query_tokens = normalize_fuzzy_text(query, keep_spaces=True).split()
    weighted_fields = (
        (item.get("item_name"), 1.0),
        (item.get("raw_category_name"), 0.9),
        (item.get("color_name"), 0.82),
        (item.get("description"), 0.72),
        (item.get("normalized_search_text"), 0.72),
    )
    best = 0.0
    document_parts: list[str] = []
    for raw_value, weight in weighted_fields:
        field = normalize_fuzzy_text(raw_value)
        if not field:
            continue
        document_parts.append(field)
        field_tokens = normalize_fuzzy_text(raw_value, keep_spaces=True).split()
        variants = {field, *(token for token in field_tokens if len(token) >= 2)}
        if len(field) > len(query_compact):
            window_size = len(query_compact)
            variants.update(
                field[index : index + window_size]
                for index in range(len(field) - window_size + 1)
            )
        field_score = 0.0
        for variant in variants:
            if query_compact in variant:
                variant_score = 1.0
            elif variant in query_compact and len(variant) >= 2:
                variant_score = 0.9
            else:
                levenshtein = levenshtein_ratio(query_compact, variant)
                jaccard = jaccard_similarity(query_compact, variant)
                prefix_bonus = 0.08 if variant.startswith(query_compact[:2]) else 0.0
                variant_score = min(1.0, max(levenshtein, jaccard) + prefix_bonus)
            field_score = max(field_score, variant_score)
        best = max(best, field_score * weight)

    document = "".join(document_parts)
    if query_tokens:
        token_coverage = sum(
            normalize_fuzzy_text(token) in document for token in query_tokens
        ) / len(query_tokens)
        best = max(best, token_coverage * 0.88)
    best = max(best, jaccard_similarity(query_compact, document) * 0.8)
    return round(min(best, 1.0), 4)


def rank_found_items(
    items: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return [dict(item) for item in items]
    threshold = 0.7 if len(normalize_fuzzy_text(query)) <= 2 else 0.5
    ranked: list[dict[str, Any]] = []
    for item in items:
        score = fuzzy_item_score(query, item)
        if score < threshold:
            continue
        result = dict(item)
        result["match_score"] = score
        ranked.append(result)
    ranked.sort(key=lambda item: -float(item.get("match_score", 0)))
    return ranked
