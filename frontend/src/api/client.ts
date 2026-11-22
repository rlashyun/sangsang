import type {
  AgentEvent, AgentMatches, AgentResponse, AgentTurn,
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

async function postAgent(path: string, body: object, signal?: AbortSignal) {
  const response = await fetch(`/api/search-chat/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  }).catch(error => {
    if (signal?.aborted) throw error;
    throw new AgentRequestError("검색 연결이 끊겼어요. 같은 요청을 다시 시도해 주세요.", "connection_lost");
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    if (response.status === 410) throw new AgentSessionExpired(payload?.error?.message);
    throw new AgentRequestError(payload?.error?.message || "대화형 검색에 연결하지 못했어요.", payload?.error?.code ?? (response.status >= 500 ? "state_unavailable" : undefined));
  }
  return response;
}

export class AgentSessionExpired extends Error {}

export class AgentRequestError extends Error {
  constructor(message: string, readonly code?: string) { super(message); }
  get requiresRecovery() { return ["connection_lost", "busy", "state_unavailable"].includes(this.code ?? ""); }
}

export async function runAgentTurn(
  turn: AgentTurn,
  session: { session_id: string; revision: number } | null,
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
  requestId: string = crypto.randomUUID(),
): Promise<AgentResponse> {
  const identity = session ? { session_id: session.session_id, revision: session.revision } : {};
  const response = await postAgent("turns", { ...turn, ...identity, request_id: requestId }, signal);
  const reader = response.body?.pipeThrough(new TextDecoderStream()).getReader();
  if (!reader) throw new AgentRequestError("검색 응답을 받을 수 없어요. 같은 요청을 다시 시도해 주세요.", "connection_lost");

  let pending = "";
  let result: AgentResponse | undefined;
  function consume(line: string) {
    if (!line.trim()) return;
    const event = JSON.parse(line) as AgentEvent;
    if (event.type === "run.finished") result = event.response;
    else onEvent(event);
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      const lines = (pending + value).split("\n");
      pending = lines.pop() ?? "";
      lines.forEach(consume);
    }
    consume(pending);
    if (!result) throw new AgentRequestError("검색 연결이 끊겼어요. 다시 시도해 주세요.", "connection_lost");
    return result;
  } catch (error) {
    if (!signal.aborted && (error instanceof TypeError || error instanceof SyntaxError)) {
      throw new AgentRequestError("검색 응답을 확인하지 못했어요. 같은 요청을 다시 시도해 주세요.", "connection_lost");
    }
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export async function fetchAgentPage(session_id: string, revision: number, cursor: string, signal: AbortSignal) {
  const response = await postAgent("pages", { session_id, revision, cursor }, signal);
  return (await response.json() as { matches: AgentMatches }).matches;
}

export async function cancelAgentTurn(session_id: string, run_id: string) {
  const response = await postAgent("cancel", { session_id, run_id });
  return (await response.json() as { accepted: boolean }).accepted;
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
  const payload = await response.json().catch(() => null) as { status?: string; error?: string; reservation_id?: number } | null;
  if (response.status !== 202 || payload?.status !== "pending") {
    const fallback = response.status === 429
      ? "알림 신청 한도에 도달했어요. 나중에 다시 시도해 주세요."
      : "지금은 알림을 신청할 수 없어요. 잠시 뒤 다시 시도해 주세요.";
    throw new Error(typeof payload?.error === "string" && payload.error ? payload.error : fallback);
  }
  return payload.reservation_id;
}

export type AlertSummary = {
  status: string; query: string; places: { name: string }[]; radius_m: number;
  last_day: string; expires_at: string; extensions_left: number;
};
export function fetchAlertSummary(token: string, signal?: AbortSignal) {
  return fetchJson<AlertSummary>(`/api/alerts/summary?${new URLSearchParams({ token })}`, signal);
}
// PRD 4.3-E — 토큰은 예약 API에만 보내며 검색 대화·모델에 전달하지 않는다.
export async function manageAlertReservation(action: "extend" | "cancel", token: string) {
  const response = await fetch(`/api/alerts/${action}`, {
    method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result?.error || "예약을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.");
  if (typeof result?.result !== "string" || typeof result?.message !== "string") throw new Error("예약 처리 결과를 확인하지 못했어요.");
  return result as { result: string; message: string; last_day?: string; expires_at?: string; extensions_left?: number };
}
