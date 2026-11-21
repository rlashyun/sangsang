"""습득물 개인정보 출력 마스킹 — PRD 3.3 · Q5 (2026-11-20 결정 변경).

경찰청이 마스킹해 제공한다는 가정이 틀린 것으로 확인돼(docs/measurements.md) 서비스가 직접 가린다.

절대 규칙 3 — DB의 `item_name`·`description`·`raw_payload` 원문은 바꾸지 않는다.
이 모듈은 **시스템 밖으로 나가는 값**(API 응답·LLM 입력·알림 메일)과
정규화 전용 칸 `normalized_search_text`(임베딩 입력)에만 쓴다.

규칙
- 번호(모든 분류): 주민·외국인등록번호는 전부, 카드번호(13~19자리)는 앞 4자리와 뒤 4자리만 남기고,
  휴대폰(01X)은 가운데를 가린다.
- 이름(모든 분류): `○○○ 명의`, `성명:`·`이름:`·`소유자:` 뒤의 2~4자 한글.
- 이름(고위험 분류만): 괄호 속 3자 한글(두 글자 성이면 4자) 중 흔한 성씨로 시작하고 제외 사전에 없는 것.
  고위험 분류에서는 덜 가리기보다 더 가린다.
- 이미 가려진 `홍*동` 형식은 다시 건드리지 않는다. 모든 함수는 두 번 적용해도 결과가 같다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any



# PRD 3.3 — 상위분류 정확 일치로만 고위험을 판정한다(PRD 4.5 부분일치 금지와 같은 원칙).
# 휴대폰은 넣지 않는다: 이름은 대개 카드·신분증·통장류에 적히고, 휴대폰 물품명의 괄호는
# 기종·색상(예: "아이폰(블랙)")이라 괄호 규칙의 오탐만 늘어난다. 휴대폰 물품의 번호(전화·카드·
# 주민번호)와 명의 문맥은 분류와 상관없이 모든 물품에 적용하는 규칙으로 가린다.
HIGH_RISK_CATEGORY_PARENTS = frozenset({"카드", "지갑", "증명서", "서류", "유가증권", "현금"})

# 마스킹 대상 텍스트 칸. 원문 칸은 출력 직전 사본에서만 가린다.
MASKED_ROW_FIELDS = ("item_name", "description", "normalized_search_text", "search_text")

IDENTIFIER_QUERY_ERROR = (
    "카드번호·주민등록번호·전화번호로는 검색할 수 없어요. "
    "개인정보 보호를 위해 카드사·색상·물품 종류로 찾아 주세요."
)

# --- 번호 패턴 -------------------------------------------------------------
# 앞뒤에 숫자·별표가 바로 붙어 있으면 더 긴 번호의 일부로 보고 건드리지 않는다.
_START = r"(?<![\d*xX])"
_END = r"(?![\d*xX])"
# 카드번호는 하이픈 하나를 사이에 두고 이어지는 숫자도 같은 번호로 본다. 휴대폰을 먼저 가려
# "010-****-5678" 형태로 만든 뒤 카드 규칙을 돌리므로, 붙어 있는 두 전화번호를 카드로 오인하지 않고
# "010-1234-5678 1234-5678-9012-3456"의 카드번호도 놓치지 않는다.
_CARD_START = _START + r"(?<![\d*]-)"
_CARD_END = _END + r"(?!-[\d*])"

# 주민·외국인등록번호: 앞 6자리 + 뒷자리 첫 숫자 1~8 + 6자리(일부가 *·x로 가려진 형태 포함)
_REGISTRATION_RE = re.compile(_START + r"\d{6}[ -]?[1-8][\d*xX]{6}" + _END)
# 카드번호: 숫자·별표 13~19개, 사이에 공백·하이픈 하나씩 허용
_CARD_RE = re.compile(_CARD_START + r"[\d*](?:[ -]?[\d*]){12,18}" + _CARD_END)
# 휴대폰: 01X + 3~4자리 + 4자리
_MOBILE_RE = re.compile(_START + r"(01[016789])[ .-]?\d{3,4}[ .-]?(\d{4})" + _END)

# --- 이름 패턴 -------------------------------------------------------------
_HANGUL_NAME = r"[가-힣]{2,4}"
# "홍길동 명의", "김철수명의"
_OWNER_RE = re.compile(r"(?<![가-힣])(" + _HANGUL_NAME + r")(?=\s*명의)")
# "성명: 홍길동", "이름 김민", "소유자 : 박지성"
_LABEL_RE = re.compile(
    r"(?<![가-힣])((?:성명|이름|소유자|명의자)(?:\s*[:：]\s*|\s+))(" + _HANGUL_NAME + r")(?![가-힣])"
)
# 고위험 분류의 괄호 속 3~4자 한글
# PRD 3.3 · Q5 — 괄호 규칙은 3~4자만 본다. 2026-11-20 운영 dry-run에서 2자 괄호 단어(현금·서류·선녹·라임…)가
# 매치의 큰 부분을 차지했고 대부분 이름이 아니었다(docs/measurements.md). 4자는 두 글자 성만 이름으로 본다.
_BRACKET_NAME = r"[가-힣]{3,4}"
_BRACKET_RE = re.compile(r"([(\[（［]\s*)(" + _BRACKET_NAME + r")(\s*[)\]）］])")

# 흔한 성씨(두 글자 성 포함). 고위험 분류의 괄호 규칙에서만 쓴다.
_TWO_CHAR_SURNAMES = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")
_SURNAMES = frozenset(
    "김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민류나진지엄채원천방공"
    "현함변염여추도소석선설마길연위표명기반라왕금옥육인맹제모탁국어은편용예경봉사부가복태목형피두감음빈"
    "동온호범좌팽승간상시갈단견당"
)

# 사람 이름이 아닌 "명의"·라벨 뒤 단어
_NON_NAME_WORDS = frozenset(
    {
        "본인", "법인", "회사", "타인", "가족", "부모", "부모님", "개인", "공동", "자녀", "남편", "아내",
        "배우자", "단체", "기관", "학교", "없음", "미상", "불명", "모름", "확인", "불가", "기재", "미기재",
        "남성", "여성", "남자", "여자", "성인", "아동", "학생",
        # 2026-11-20 운영 dry-run에서 괄호 안에 반복된 비이름 3자 단어(상태·노선·카드명·물품)
        "연락됨", "경인선", "동백전", "지퍼형", "어린이",
    }
)
# 카드사·은행 이름 앞부분 — "신한", "우리카드", "기업은행"처럼 성씨로 시작하는 기관명을 거른다.
_INSTITUTION_PREFIXES = (
    "국민", "신한", "농협", "우리", "하나", "삼성", "현대", "롯데", "기업", "비씨", "씨티", "카카오", "토스",
    "케이", "새마을", "우체국", "수협", "신협", "부산", "대구", "경남", "광주", "전북", "제주", "산업",
    "제일", "외환", "한국", "케이비", "엔에이치", "아이비케이",
    # 카드·증명서에 흔한 가맹점·발급 용도 — 성씨로 시작하지만 이름이 아니다.
    "신세계", "이마트", "주민", "운전", "공무원", "국가", "장애", "보훈", "건강", "여권", "사원", "회원",
)
# 물품·증명서·용도를 뜻하는 끝말 — 학생증, 검정색, 공기폰, 상품권, 남성용, 교통카드, 장지갑, 기업은행
_NON_NAME_SUFFIXES = ("색", "증", "폰", "권", "용", "카드", "은행", "지갑", "가방", "통장", "됨")

_SYNONYMS_PATH = Path(__file__).resolve().parents[1] / "search" / "synonyms.json"
_KINDS_PATH = Path(__file__).resolve().parents[1] / "search" / "item_kinds.json"


@lru_cache(maxsize=1)
def _dictionary_words() -> frozenset[str]:
    """동의어 사전의 색상·카드상품·브랜드·물품 표현. 사전 파일이 없으면 기본 제외어만 쓴다."""
    try:
        data = json.loads(_SYNONYMS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    words: set[str] = set()
    for concept in data.get("concepts", []):
        if concept.get("domain") in {"color", "card_product", "brand", "item"}:
            words.update(str(term).replace(" ", "") for term in concept.get("terms", []))
    words.update(str(term).replace(" ", "") for term in data.get("protected") or [])
    # PRD 4.5 (2026-11-21) — 물건 종류 표현은 item_kinds.json으로 옮겼다. 이름으로 오인하지 않게 함께 쓴다.
    try:
        kinds = json.loads(_KINDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kinds = {}
    for kind in kinds.get("kinds", []):
        words.update(str(term).replace(" ", "") for term in kind.get("terms", []))
    return frozenset(words)


@lru_cache(maxsize=1)
def _high_risk_keys() -> frozenset[str]:
    from ..ingestion.categories import normalize_category_component

    return frozenset(normalize_category_component(name) for name in HIGH_RISK_CATEGORY_PARENTS)


def is_high_risk_category(raw_category_name: str | None) -> bool:
    """`category_parent(raw_category_name)`가 고위험 상위분류와 정확히 일치하는지."""
    # 수집 패키지(ingestion/__init__ → collector)가 이 모듈을 쓰므로 순환 import를 피해 늦게 불러온다.
    from ..ingestion.categories import category_parent, normalize_category_component

    return normalize_category_component(category_parent(raw_category_name)) in _high_risk_keys()


def _is_non_name(word: str) -> bool:
    return (
        word in _NON_NAME_WORDS
        or word in _dictionary_words()
        or word.endswith(_NON_NAME_SUFFIXES)
        or word.startswith(_INSTITUTION_PREFIXES)
    )


def _starts_with_surname(word: str) -> bool:
    return word.startswith(_TWO_CHAR_SURNAMES) or word[0] in _SURNAMES


def _mask_name(name: str) -> str:
    # 경찰청 원문의 기존 가림 형식(홍*동)과 맞춘다. 두 글자 이름은 첫 글자만 남긴다.
    if len(name) <= 2:
        return name[0] + "*" * (len(name) - 1)
    return name[0] + "*" * (len(name) - 2) + name[-1]


def _mask_card(match: re.Match[str]) -> str:
    # PRD 3.3 · Q5 (2026-11-20 사용자 결정) — 앞 4자리와 뒤 4자리만 남기고 가운데를 가린다.
    # 앞 4자리는 발급사 구분용이라 본인 확인에 쓰이고, 카드 결제에는 CVC·유효기간이 더 필요하다.
    # 숫자와 별표를 같은 칸으로 세므로 이미 가려진 앞자리(****-…)를 다시 드러내지 않고, 두 번 적용해도 같다.
    value = match.group(0)
    cells = [index for index, character in enumerate(value) if character.isdigit() or character == "*"]
    hidden = set(cells[4:-4])
    return "".join("*" if index in hidden else character for index, character in enumerate(value))


def mask_numbers(text: str) -> str:
    """번호 패턴만 가립니다. 사용자 대화처럼 이름 규칙을 쓰지 않을 곳에 씁니다."""
    if not text:
        return text
    # 순서가 중요하다 — 13자리 주민번호가 카드 규칙에 먼저 걸리지 않게 하고, 공백으로 이어진
    # 전화번호 두 개가 카드 길이로 묶이지 않도록 휴대폰을 카드보다 먼저 가린다.
    text = _REGISTRATION_RE.sub("******-*******", text)
    text = _MOBILE_RE.sub(lambda match: f"{match.group(1)}-****-{match.group(2)}", text)
    return _CARD_RE.sub(_mask_card, text)


def mask_text(text: str | None, *, high_risk: bool = False) -> str | None:
    """번호와 이름 규칙을 적용한 사본을 돌려줍니다. 입력이 비어 있으면 그대로 돌려줍니다."""
    if not text:
        return text
    text = mask_numbers(text)

    def owner(match: re.Match[str]) -> str:
        word = match.group(1)
        return word if _is_non_name(word) else _mask_name(word)

    def labelled(match: re.Match[str]) -> str:
        label, word = match.group(1), match.group(2)
        return match.group(0) if _is_non_name(word) else label + _mask_name(word)

    text = _OWNER_RE.sub(owner, text)
    text = _LABEL_RE.sub(labelled, text)
    if high_risk:

        def bracketed(match: re.Match[str]) -> str:
            word = match.group(2)
            if _is_non_name(word) or not _starts_with_surname(word):
                return match.group(0)
            if len(word) == 4 and not word.startswith(_TWO_CHAR_SURNAMES):
                return match.group(0)  # 4자 이름은 두 글자 성(남궁·황보…)만 — 한 글자 성 + 3자는 단어로 본다
            return match.group(1) + _mask_name(word) + match.group(3)

        text = _BRACKET_RE.sub(bracketed, text)
    return text


def contains_identifier(text: str | None) -> bool:
    """카드·주민·외국인등록·휴대폰 번호 형태가 들어 있으면 True. 검색어 거절에 씁니다."""
    return bool(text) and mask_numbers(text) != text


def mask_item_row(row: dict[str, Any]) -> dict[str, Any]:
    """습득물 행의 출력용 사본을 만듭니다. 입력 dict(원문)는 바꾸지 않습니다 — 절대 규칙 3."""
    high_risk = is_high_risk_category(row.get("raw_category_name"))
    masked = dict(row)
    for field in MASKED_ROW_FIELDS:
        value = masked.get(field)
        if isinstance(value, str):
            masked[field] = mask_text(value, high_risk=high_risk)
    return masked


def row_needs_masking(row: dict[str, Any]) -> bool:
    """출력할 때 `mask_item_row`가 무언가를 가리는 행인지. 수집 감사 로그의 `masked_count`에 씁니다."""
    masked = mask_item_row(row)
    return any(masked.get(field) != row.get(field) for field in MASKED_ROW_FIELDS)
