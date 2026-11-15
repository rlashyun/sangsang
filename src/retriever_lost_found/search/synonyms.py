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
KIND_TERMS = {kind: frozenset(terms) for kind, terms in _DATA["kinds"].items()}
KIND_NAMES = ("지갑", "휴대폰", "이어폰", "모자", "가방", "카드")
_KINDS_BY_TERM: dict[str, set[str]] = {}
for _kind, _terms in KIND_TERMS.items():
    for _term in _terms:
        _KINDS_BY_TERM.setdefault(_term, set()).add(_kind)
_KIND_PATTERN = re.compile("|".join(re.escape(term) for term in sorted(_KINDS_BY_TERM, key=len, reverse=True)))
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
    color = query or bool(_KIND_PATTERN.search(text)) or all(
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
    compact = text.replace(" ", "")
    matches = [match[0] for match in _KIND_PATTERN.finditer(compact)]
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
_KIND_CANDIDATES["지갑"] |= {"돈을넣", "현금을넣", "돈을 넣", "현금을 넣"}
_KIND_CANDIDATES["이어폰"] |= {"귀에끼", "귀에넣", "귀에착용", "귀에 끼", "귀에 넣", "귀에 착용"}


def kind_candidate_terms(names: set[str]) -> set[str]:
    return set().union(*(_KIND_CANDIDATES[name] for name in names))
