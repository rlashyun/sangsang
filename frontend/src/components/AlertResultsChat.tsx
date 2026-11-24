import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchAlertResults, type AlertResults } from "../api/client";
import { radiusKilometersLabel } from "../lib/geo.js";
import { itemPageNumbers } from "../lib/item-pagination.js";
import type { AgentItem, Institution } from "../types";
import { AGENT_ITEM_PAGE_SIZE, AgentResultList } from "./AgentResultList";

// PRD 4.3-E(2026-11-24 결정) — 결과 메일의 "리트리버에서 보기"는 저장된 조건으로 그 메일이 알린 물품만 바로 보여 준다.
// 메일에는 5건까지만 싣지만 여기서는 그 메일의 물품을 모두 같은 물품 카드로 보여 준다.
export function alertSearchText(results: Pick<AlertResults, "places" | "query" | "radius_m">) {
  return `${results.places.map(place => place.name).join(", ")} 반경 ${radiusKilometersLabel(results.radius_m)}에서 ${results.query} 찾아줘`;
}

export function AlertResultsChat({ institutions, selectedItemId, onOpen, onSearchAll, searchDisabled }: {
  institutions: Map<number, Institution>;
  selectedItemId?: number;
  onOpen: (item: AgentItem) => void;
  onSearchAll: (text: string) => boolean;
  searchDisabled: boolean;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const [link] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return { token: params.get("token") || "", message: params.get("message") || "" };
  });
  const [results, setResults] = useState<AlertResults | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [searched, setSearched] = useState(false);
  const top = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 토큰은 메모리에만 두고 주소·이용안내 링크·검색 대화에는 남기지 않는다(AlertManagementChat과 같다).
    navigate(location.pathname, { replace: true, state: null });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    if (!link.token || !link.message) { setLoading(false); setError("메일의 ‘리트리버에서 보기’ 링크를 다시 열어 주세요."); return; }
    void fetchAlertResults(link.token, link.message, controller.signal).then(value => {
      if (!controller.signal.aborted) setResults(value);
    }).catch(error => {
      if (!controller.signal.aborted) setError(error instanceof Error && !(error instanceof TypeError) ? `${error.message} 메일의 링크를 다시 열어 주세요.` : "알림 결과를 불러오지 못했어요. 메일의 링크를 다시 열어 주세요.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [link]);

  const items = results?.items ?? [];
  const pageCount = Math.max(1, Math.ceil(items.length / AGENT_ITEM_PAGE_SIZE));
  const ended = results && results.status !== "active";
  function changePage(next: number) {
    setPage(next);
    top.current?.scrollIntoView({ block: "nearest" });
  }
  function searchAll() {
    if (!results || searched) return;
    if (onSearchAll(alertSearchText(results))) setSearched(true);
  }

  return <>
    {loading && <p className="agent-bubble agent-bubble-agent" role="status">메일에서 알려드린 물품을 불러오고 있어요.</p>}
    {results && <div ref={top} className="agent-bubble agent-bubble-agent agent-mail-results" aria-label="메일에서 알려드린 물품">
      {items.length
        ? <p>‘{results.query}’ 알림으로 새로 등록된 유사 물품 {items.length.toLocaleString("ko-KR")}건을 보여드려요. 본인 물건인지는 보관기관에 직접 확인해 주세요.</p>
        : <p>메일에서 알려드린 물품은 보관 기간이 끝났거나 보관기관 정보가 바뀌어 지금은 볼 수 없어요.</p>}
      <p className="agent-mail-results-scope">
        {results.places.map(place => place.name).join(", ")} 반경 {radiusKilometersLabel(results.radius_m)} · 메일에서 알려드린 물품만 모았어요.
        {ended && " 이 알림은 이미 끝났지만 결과는 계속 볼 수 있어요."}
      </p>
    </div>}
    {results && items.length > 0 && <div className="agent-mail-results-list">
      <AgentResultList selectedItemId={selectedItemId} pageSize={AGENT_ITEM_PAGE_SIZE}
        items={items.slice(page * AGENT_ITEM_PAGE_SIZE, (page + 1) * AGENT_ITEM_PAGE_SIZE)} institutions={institutions} onOpen={onOpen} />
      {pageCount > 1 && <nav className="agent-pagination" aria-label="알림 물품 페이지">
        {itemPageNumbers(page, pageCount).map((number: number) =>
          <button type="button" key={number} aria-label={`${number + 1}페이지`} aria-current={number === page ? "page" : undefined}
            onClick={() => changePage(number)}>{number + 1}</button>)}
      </nav>}
    </div>}
    {error && <p className="agent-bubble agent-bubble-agent" role="alert">{error}</p>}
    {results && !searched && <div className="agent-place-replies" aria-label="알림 결과 다음 행동">
      <button type="button" className="agent-action-chip" disabled={searchDisabled} onClick={searchAll}>전체 검색 결과도 보기</button>
    </div>}
  </>;
}
