import type {
  FoundItemsPayload,
  InstitutionPayload,
  PlacesPayload,
} from "../types";

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    signal,
  });
  const payload = await response.json() as T & { error?: string };
  if (!response.ok) {
    throw new Error(payload.error || "요청 중 오류가 발생했습니다.");
  }
  return payload;
}

export function fetchInstitutions(signal?: AbortSignal) {
  return fetchJson<InstitutionPayload>("/api/institutions", signal);
}

export function searchPlaces(params: URLSearchParams, signal?: AbortSignal) {
  return fetchJson<PlacesPayload>(`/api/places?${params}`, signal);
}

export function geocodeAddress(address: string, signal?: AbortSignal) {
  return fetchJson<PlacesPayload>(
    `/api/geocode?${new URLSearchParams({ address })}`,
    signal,
  );
}

export function fetchFoundItems(params: URLSearchParams, signal?: AbortSignal) {
  return fetchJson<FoundItemsPayload>(`/api/found-items?${params}`, signal);
}
