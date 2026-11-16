import type {
  AgentEvent, AgentMatches, AgentResponse, AgentTurn,
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
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    if (response.status === 410) throw new AgentSessionExpired(payload?.error?.message);
    throw new Error(payload?.error?.message || "대화형 검색에 연결하지 못했어요.");
  }
  return response;
}

export class AgentSessionExpired extends Error {}

export async function runAgentTurn(
  turn: AgentTurn,
  session: { session_id: string; revision: number } | null,
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
): Promise<AgentResponse> {
  const identity = session ? { session_id: session.session_id, revision: session.revision } : {};
  const response = await postAgent("turns", { ...turn, ...identity, request_id: crypto.randomUUID() }, signal);
  const reader = response.body?.pipeThrough(new TextDecoderStream()).getReader();
  if (!reader) throw new Error("검색 응답을 받을 수 없어요.");

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
    if (!result) throw new Error("검색 연결이 끊겼어요. 다시 시도해 주세요.");
    return result;
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
