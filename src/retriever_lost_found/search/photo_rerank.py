"""PRD 4.5 — Compare photos within the text-ranked search results."""

import base64
import binascii
import json
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from threading import BoundedSemaphore

from ..agent_diagnostics import record
from ..privacy.masking import is_high_risk_category, mask_text
from ..thumbnails.resize import resize_image

MAX_PHOTO_LENGTH = 400_000
MAX_USER_PHOTOS = 3
JPEG_PREFIX = "data:image/jpeg;base64,"
ADJUSTMENTS = {"same": 0.10, "similar": 0.05, "unsure": 0.0, "different": -0.05, "very_different": -0.10}
PHOTO_BATCH_SIZE = 5
_photo_search_slots = BoundedSemaphore(2)

PROMPT = """사용자 입력은 user_text와 그 뒤에 오는 사용자 사진이야.
각 후보의 글은 id와 함께 제공되는 embedding_input이고, 그 바로 뒤에 해당 후보의 사진이 와.
사용자 글+사진을 각 후보의 글+사진에 독립적으로 비교해. 비교 시 글+사진에서 종류, 브랜드·모델, 색상, 식별 가능한 특징을 확인해. 같은 종류라는 이유만으로 같은 물품으로 판단하지 마.
조명, 촬영 각도, 케이스 차이만으로 감점에 해당하는 판정을 하지 마.
사진이 흐리거나 가려져 있거나 글과 사진이 충돌해 확인하기 어려우면 unsure로 분류해.
- same (같아 보임): 모델이나 구체적인 식별 특징이 뚜렷하게 일치하고 확인된 불일치가 없어.
- similar (비슷함): 종류나 외형은 비슷한데 모델이나 식별 특징까지 확인하기 어려워.
- unsure (판단 불가): 비교할 증거가 부족하거나 확인한 단서가 충돌해.
- different (다름): 사용자가 명시한 색상이나 식별 특징에 뚜렷한 차이가 있어.
- very_different (매우 다름): 물품 종류가 다르거나 확인 가능한 브랜드·모델이 명백히 달라.
후보마다 입력 ID와 판정 하나만 JSON으로 반환해."""


def prepare_user_photos(photos: list[str]) -> tuple[str, ...]:
    prepared = []
    for photo in photos:
        if not photo.startswith(JPEG_PREFIX):
            raise ValueError("사진 형식이 올바르지 않습니다.")
        try:
            raw = base64.b64decode(photo[len(JPEG_PREFIX):], validate=True)
            jpeg = resize_image(raw, max_side=512)
        except (ValueError, binascii.Error) as error:
            raise ValueError("사진을 열 수 없습니다.") from error
        prepared.append(JPEG_PREFIX + base64.b64encode(jpeg).decode("ascii"))
    return tuple(prepared)


def _image(url):
    return {"type": "image_url", "image_url": {"url": url, "detail": "low"}}


def _compare_batch(compare, user_text, photos, batch):
    ids = {item_id for item_id, _, _ in batch}
    schema = {"type": "object", "properties": {"results": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "string", "enum": sorted(ids)},
        "label": {"type": "string", "enum": list(ADJUSTMENTS)}},
        "required": ["id", "label"], "additionalProperties": False,
    }}}, "required": ["results"], "additionalProperties": False}
    content = [{"type": "text", "text": json.dumps({"user_text": user_text}, ensure_ascii=False)}]
    content.extend(_image(photo) for photo in photos)
    for item_id, text, url in batch:
        content.append({"type": "text", "text": json.dumps({"id": item_id, "embedding_input": text}, ensure_ascii=False)})
        content.append(_image(url))
    try:
        response = compare(PROMPT, content, schema)
    except (RuntimeError, OSError):
        return {}
    verdicts = response.get("results") if isinstance(response, dict) else None
    if not isinstance(verdicts, list):
        return {}

    labels = {}
    duplicates = set()
    for verdict in verdicts:
        item_id = verdict.get("id") if isinstance(verdict, dict) else None
        if not isinstance(item_id, str) or item_id not in ids:
            continue
        if item_id in labels:
            duplicates.add(item_id)
        labels[item_id] = verdict.get("label")
    return {
        int(item_id): ADJUSTMENTS[label]
        for item_id, label in labels.items()
        if item_id not in duplicates and isinstance(label, str) and label in ADJUSTMENTS
    }


def rerank_photos(ranked, location_ids, user_text, photos, fetch_candidates, compare):
    if not ranked:
        return ranked, "no_photos"
    if not _photo_search_slots.acquire(blocking=False):
        return ranked, "unavailable"
    try:
        item_ids = {row["id"] for row in ranked}
        try:
            rows = fetch_candidates(location_ids, list(item_ids))
        except (RuntimeError, OSError, HTTPException) as error:
            record("photo_rerank.lookup_failed", error=error, stage="photo_rerank", final_status="fallback")
            return ranked, "unavailable"
        eligible = []
        for row in rows:
            url = row.get("thumbnail_url")
            if (row.get("id") in item_ids and row.get("storage_location_id") in location_ids
                    and isinstance(url, str) and url):
                text = mask_text(row.get("embedding_input"), high_risk=is_high_risk_category(row.get("raw_category_name")))
                eligible.append((str(row["id"]), (text or "")[:500], url))
        if not eligible:
            return ranked, "no_photos"
        adjustments = {}
        batches = [eligible[i:i + PHOTO_BATCH_SIZE] for i in range(0, len(eligible), PHOTO_BATCH_SIZE)]
        with ThreadPoolExecutor(max_workers=min(len(batches), 10)) as pool:
            for result in pool.map(lambda batch: _compare_batch(compare, mask_text(user_text), photos, batch), batches):
                adjustments.update(result)
        # PRD 4.5 — Apply valid verdicts even when a batch fails. Missing verdicts keep the text score.
        if not adjustments:
            record("photo_rerank.fallback", stage="photo_rerank", final_status="fallback")
            return ranked, "unavailable"
        if len(adjustments) < len(eligible):
            record("photo_rerank.partial", stage="photo_rerank", final_status="applied",
                   judged_count=len(adjustments), candidate_count=len(eligible))
        # Stable sorting retains the text rank for ties. The original scores and search scope stay intact.
        return sorted(ranked, key=lambda row: -round(row["match_score"] + adjustments.get(row["id"], 0.0), 6)), "applied"
    finally:
        _photo_search_slots.release()
