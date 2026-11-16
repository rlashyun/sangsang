import type { Institution, KakaoPlaceDocument, Place } from "../types";

export function normalizePlace(document: KakaoPlaceDocument): Place {
  const address = document.road_address_name
    || document.road_address?.address_name
    || document.address_name
    || document.address?.address_name
    || "주소 정보 없음";
  return {
    name: document.place_name || address,
    address,
    phone: document.phone || "",
    category: document.category_name || "",
    latitude: Number(document.y),
    longitude: Number(document.x),
    url: document.place_url || "",
  };
}

export function institutionLocationIds(scope: Institution[]) {
  return [...new Set(scope
    .map((institution) => Number(institution.storage_location_id))
    .filter((value) => Number.isInteger(value) && value > 0))];
}

export type CategoryKind = "bag" | "cap" | "buds" | "wallet" | "phone" | "card" | "box";

// PRD 4.5 — 6개 분류는 상위 분류명이 정확히 일치할 때만 구분하고, 그 밖은 기타(box)로 둔다.
export function categoryKind(rawCategory?: string): CategoryKind {
  const parent = String(rawCategory || "").split(">")[0].trim();
  return ({
    "가방": "bag",
    "의류": "cap",
    "전자기기": "buds",
    "지갑": "wallet",
    "휴대폰": "phone",
    "카드": "card",
  } as Record<string, CategoryKind>)[parent] || "box";
}

export function foundItemImageUrl(value?: string | null) {
  // PRD 3.2-2 — 목록과 상세 모두 원천의 기본 그림을 실제 사진으로 취급하지 않는다.
  const url = value?.trim();
  return url && !url.split(/[?#]/)[0].endsWith("/img02_no_img.gif") ? url : null;
}

export function sourceLabel(source?: string) {
  return source === "police_api" ? "경찰청" : "연계기관";
}

export function formatRegisteredDate(value?: string) {
  const parts = String(value || "").split("-");
  return parts.length === 3 ? `${parts[0]}.${parts[1]}.${parts[2]}` : String(value || "");
}

export function escapeHtml(value?: string) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
