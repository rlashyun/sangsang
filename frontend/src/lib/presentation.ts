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

export function sameInstitutionScope(left: Institution[], right: Institution[]) {
  const leftIds = institutionLocationIds(left).sort((a, b) => a - b);
  const rightIds = institutionLocationIds(right).sort((a, b) => a - b);
  return leftIds.length === rightIds.length
    && leftIds.every((value, index) => value === rightIds[index]);
}

export function categoryIcon(rawCategory?: string) {
  const parent = String(rawCategory || "").split(">")[0].trim();
  return ({
    "가방": "👜",
    "의류": "🧢",
    "전자기기": "🎧",
    "지갑": "👛",
    "휴대폰": "📱",
    "카드": "💳",
  } as Record<string, string>)[parent] || "📦";
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
