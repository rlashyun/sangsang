"""PRD 4.1/4.5: load fixed expressions once; preserve originals and specificity."""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
import re

from .text import normalize_base_text


# The packaged runtime subset has no source descriptions or sample item IDs.
_DATA = json.loads(files(__package__).joinpath("synonyms.json").read_text(encoding="utf-8"))
_CONCEPTS = _DATA["concepts"]
_LOOKUP = {term: group for group in _CONCEPTS for term in group["terms"]}
for _group in _CONCEPTS:
    for _term in _group["terms"]:
        # A reviewed multiword spelling also accepts omission of its spaces.
        _LOOKUP.setdefault(_term.replace(" ", ""), _group)
_CANONICAL = {term: group["canonical"] for term, group in _LOOKUP.items()}
ALIASES = tuple((term, group["canonical"]) for group in _CONCEPTS
                for term in group["terms"] if term != group["canonical"])
# PRD 4.5 (2026-11-21) — 물건 종류는 동의어가 아니므로 별도 파일에 둔다. Supabase 분류 21개·하위분류에서 뽑았다.
_KIND_DATA = json.loads(files(__package__).joinpath("item_kinds.json").read_text(encoding="utf-8"))
KIND_NAMES = tuple(kind["name"] for kind in _KIND_DATA["kinds"])
KIND_TERMS = {kind["name"]: frozenset(kind["terms"]) for kind in _KIND_DATA["kinds"]}
KIND_CATEGORIES = {kind["name"]: tuple(kind["categories"]) for kind in _KIND_DATA["kinds"]}
# 종류를 단정하지 않는 묘사(귀에 끼는 → 이어폰). 종류어가 없을 때만 쓴다.
KIND_HINTS = {name: tuple(phrases) for name, phrases in _KIND_DATA["hints"].items()}
_HINT_PATTERN = re.compile("|".join(re.escape(phrase) for phrases in KIND_HINTS.values() for phrase in phrases))
_KINDS_BY_TERM: dict[str, set[str]] = {}
for _kind, _terms in KIND_TERMS.items():
    for _term in _terms:
        _KINDS_BY_TERM.setdefault(_term, set()).add(_kind)
# 1글자 종류어(책·옷)는 책상·옷걸이에 걸리지 않게 독립 단어일 때만 종류로 본다.
_SHORT_KIND_TERMS = {term for term in _KINDS_BY_TERM if len(term) == 1}
_KIND_PATTERN = re.compile("|".join(re.escape(term) for term in sorted(
    set(_KINDS_BY_TERM) - _SHORT_KIND_TERMS, key=len, reverse=True)))
# PRD 4.5 (2026-11-21 전체 물품) — 6개 종류어가 없어도 사전에 등록한 물품 단어(우산·안경 등)가
# 있으면 색 단어를 대표어로 맞춘다. 미등록 문맥(브라운 면도기 등)은 그대로 둔다.
_ITEM_PATTERN = re.compile("|".join(re.escape(term.replace(" ", "")) for term in sorted(
    {term for group in _CONCEPTS if group["domain"] == "item" for term in group["terms"]},
    key=len, reverse=True)))
_EARPHONE_PATTERN = re.compile("이어폰|에어팟|버즈|유닛")
_COLOR_WORDS = {term for group in _CONCEPTS if group["domain"] == "color" for term in group["terms"]}
_EARPHONE_TERMS = {term for group in _CONCEPTS
                   if any("earphone_span" in scope for scope in group["scope"])
                   for term in group["terms"]}
_PHRASES = sorted((term for term in _LOOKUP if " " in term), key=len, reverse=True)
_PHRASE_PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, _PHRASES)) + r")(?!\w)")

# A whole unknown word stays intact. Split compounds only if every part is a
# known feature/type/model component, so 블랙야크 and 톰브라운 cannot be damaged.
_WORDS = set(_LOOKUP) | set(_KINDS_BY_TERM) | set(_DATA["protected"]) | {
    "케이스", "유닛", "프로", "플러스", "울트라", "미니", "맥스", "만", "없음", "있음",
}
_TRIE: dict = {}
for _word in _WORDS:
    if " " not in _word:
        _node = _TRIE
        for _char in _word:
            _node = _node.setdefault(_char, {})
        _node[None] = _word


def is_known_word(word: str) -> bool:
    """사전·종류 단어. 붙여 쓴 물품명을 나눌 때 독립 단어로 새로 생길 수 있다."""
    return word in _WORDS


@lru_cache(maxsize=8192)
def _word_parts(word: str) -> tuple[str, ...]:
    """Cache bounded lexical segmentation, never search responses or records."""
    if len(word) > 128 or word in _WORDS or not re.search("[가-힣]", word):
        return (word,)

    @lru_cache(maxsize=None)
    def split(start: int):
        if start == len(word):
            return ()
        node = _TRIE
        ends = []
        for end in range(start, len(word)):
            node = node.get(word[end])
            if node is None:
                break
            if None in node:
                ends.append(end + 1)
        # Model numbers are kept verbatim and never spell-corrected.
        digits = re.match(r"\d+", word[start:])
        if digits:
            ends.append(start + len(digits[0]))
        for end in sorted(ends, reverse=True):
            tail = split(end)
            if tail is not None:
                return (word[start:end],) + tail
        return None

    return split(0) or (word,)


def normalize_text(text: str, *, query: bool = False) -> str:
    """One pass per text, with prebuilt phrase/trie indexes, not 325 replacements."""
    text = normalize_base_text(text, keep_spaces=True)
    earphone = bool(_EARPHONE_PATTERN.search(text))
    # Color words in recognized item/brand descriptions or a color-only query.
    color = query or bool(_KIND_PATTERN.search(text)) or bool(_ITEM_PATTERN.search(text.replace(" ", ""))) or all(
        word in _LOOKUP for word in text.split()
    )

    def replacement(term: str) -> str:
        if (term in _COLOR_WORDS and not color) or (term in _EARPHONE_TERMS and not earphone):
            return term
        return _CANONICAL.get(term, term)

    text = _PHRASE_PATTERN.sub(lambda match: replacement(match[0]), text)
    return " ".join(replacement(part) for word in text.split()
                    for part in (_word_parts(word) if len(word) <= 128 else (word,)))


def kind_names(text: str) -> set[str]:
    """Longest types first: card wallets stay wallets, and pouches stay ambiguous."""
    # Callers normalize once before type detection; do not normalize each
    # item name/category/description a second time during supports_kind().
    # 묘사 표현 속 단어('현금을 넣는 지갑'의 현금)는 종류가 아니다.
    compact = _HINT_PATTERN.sub("|", text.replace(" ", ""))
    matches = [match[0] for match in _KIND_PATTERN.finditer(compact)]
    matches += [word for word in text.split() if word in _SHORT_KIND_TERMS]
    names = {kind for term in matches for kind in _KINDS_BY_TERM[term]}
    # Galaxy is shared by phones and Buds; the explicit earphone type wins.
    if "이어폰" in names and not any("휴대폰" in _KINDS_BY_TERM[term] and term != "갤럭시" for term in matches):
        names.discard("휴대폰")
    return names


def expanded_terms(text: str) -> set[str]:
    """Expand only concepts actually present in this query; preserve raw spellings."""
    normalized = normalize_text(text)
    terms = {text, normalized}
    for word in normalized.split():
        terms.add(word)
        group = _LOOKUP.get(word)
        if group:
            terms.update(group["terms"])
    # PostgreSQL stores lowercased original text, including spaces/punctuation.
    # Both phrases and their components can retrieve J.ESTINA / Matin Kim, etc.
    terms.update(term.replace(" ", "") for term in tuple(terms))
    terms.update(word for term in tuple(terms) for word in term.split() if len(word) >= 2)
    return {term.casefold() for term in terms if term.strip()}


# Precompute full kind expressions once. Do not use attributes such as "black"
# as an OR condition when the query already identifies a type.
def _kind_forms(words: frozenset[str]) -> frozenset[str]:
    forms = set(words)
    for word in words:
        canonical = normalize_text(word)
        forms.add(canonical)
        group = _LOOKUP.get(canonical)
        if group:
            forms.update(group["terms"])
    return frozenset(forms | {term.replace(" ", "") for term in forms})


_KIND_CANDIDATES = {kind: _kind_forms(words) for kind, words in KIND_TERMS.items()}
for _kind, _phrases in KIND_HINTS.items():
    # 원문에는 '돈을 넣'처럼 띄어 쓴 묘사도 있다. 조사 뒤 첫 공백만 넣은 형태를 함께 보낸다.
    _KIND_CANDIDATES[_kind] |= {form for phrase in _phrases
                                for form in (phrase, re.sub(r"(을|에)", r"\1 ", phrase, count=1))}


def kind_candidate_terms(names: set[str]) -> set[str]:
    return set().union(*(_KIND_CANDIDATES[name] for name in names))
