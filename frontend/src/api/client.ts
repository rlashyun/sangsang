import type {
  FoundItemsPayload,
  InstitutionPayload,
  Place,
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

export function fetchInstitutions(signal?: AbortSignal, withCounts = true) {
  const url = withCounts ? "/api/institutions" : "/api/institutions?with_counts=false";
  return fetchJson<InstitutionPayload>(url, signal);
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

type AlertReservation = {
  email: string;
  query: string;
  places: Pick<Place, "name" | "latitude" | "longitude">[];
  radius_m: number;
  consent: true;
};

// PRD 4.3-E — HTTP 202 means the confirmation email was sent and activation is pending.
export async function createAlertReservation(reservation: AlertReservation) {
  const response = await fetch("/api/alerts", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(reservation),
  });
  const payload = await response.json().catch(() => null) as { status?: string; error?: string } | null;
  if (response.status !== 202 || payload?.status !== "pending") {
    const fallback = response.status === 429
      ? "알림 신청 한도에 도달했어요. 나중에 다시 시도해 주세요."
      : "지금은 알림을 신청할 수 없어요. 잠시 뒤 다시 시도해 주세요.";
    throw new Error(typeof payload?.error === "string" && payload.error ? payload.error : fallback);
  }
}
