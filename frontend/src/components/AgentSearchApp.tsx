import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { AgentSessionExpired, cancelAgentTurn, fetchAgentPage, fetchInstitutions, runAgentTurn } from "../api/client";
import { nextSearchRadiusMeters, previousSearchRadiusMeters, radiusKilometersLabel } from "../lib/geo.js";
import { itemPageNumbers } from "../lib/item-pagination.js";
import { applyPreviewTheme } from "../lib/theme";
import type { AgentItem, AgentPlace, AgentResponse, AgentTurn, Institution, Place } from "../types";
import { AgentInstitutionResults } from "./AgentInstitutionResults";
import { AgentResultList } from "./AgentResultList";
import { FoundItemDetail } from "./FoundItemDetail";
import { KakaoMap } from "./KakaoMap";
import { NewItemAlert } from "./NewItemAlert";

const ITEM_PAGE_SIZE = 4;
const PLACE_PAGE_SIZE = 5;
const TOOL_LABELS: Record<string, string> = {
  places: "장소 후보를 찾고 있어요.",
  institutions: "반경 안의 보관기관을 확인하고 있어요.",
  found_items: "습득물을 찾고 있어요.",
};

type ChatMessage = { author: "user" | "agent"; text: string } | { confirmedPlace: AgentPlace };
type Scope = { radius_m: number; institution_ids: number[] };
type ActiveRun = { session_id: string; run_id: string };

function mapPlace(place: AgentPlace): Place {
  return { ...place, phone: "", category: "", url: "" };
}

export function AgentSearchApp() {
  const location = useLocation();
  const navigate = useNavigate();
  // PRD 4.3-A — 대화는 분실 장소를 묻는 메시지로 시작한다.
  const [messages, setMessages] = useState<ChatMessage[]>([
    { author: "agent", text: "어디서 잃어버렸어요?" },
  ]);
  const [draft, setDraft] = useState("");
  const [response, setResponse] = useState<AgentResponse | null>(null);
  const [items, setItems] = useState<AgentItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [itemPage, setItemPage] = useState(0);
  const [placePage, setPlacePage] = useState(0);
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(null);
  const [tab, setTab] = useState<"chat" | "results">("chat");
  const [showHistory, setShowHistory] = useState(false);
  const [progress, setProgress] = useState("");
  const [pendingScope, setPendingScope] = useState<Scope | null>(null);
  const [confirmingPlace, setConfirmingPlace] = useState<AgentPlace | null>(null);
  const [error, setError] = useState("");
  const [lastTurn, setLastTurn] = useState<AgentTurn | null>(null);
  const [paging, setPaging] = useState(false);
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [institutions, setInstitutions] = useState<Institution[]>([]);
  const [institutionsLoaded, setInstitutionsLoaded] = useState(false);
  const [institutionError, setInstitutionError] = useState("");
  const [selectedInstitution, setSelectedInstitution] = useState<Institution[]>([]);
  const [openItem, setOpenItem] = useState<AgentItem | null>(null);
  const [resultsSeen, setResultsSeen] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const feed = useRef<HTMLDivElement>(null);

  useEffect(() => {
    applyPreviewTheme("retriever-blue");
    const load = new AbortController();
    fetchInstitutions(load.signal, false).then(payload => {
      setInstitutions(payload.institutions ?? []);
      setInstitutionsLoaded(true);
    }).catch(() => {
      if (!load.signal.aborted) setInstitutionError("기관 위치를 불러오지 못했어요.");
    });
    return () => {
      load.abort();
      controller.current?.abort();
    };
  }, []);

  const conditions = response?.conditions;
  const currentPlace = confirmingPlace ?? conditions?.place;
  const selectedPlace = useMemo(() => currentPlace ? mapPlace(currentPlace) : null, [currentPlace]);
  const searchCenters = useMemo(() => selectedPlace ? [selectedPlace] : [], [selectedPlace]);
  const candidates = response?.place_candidates;
  const numberedPlaces = useMemo(() => (candidates ?? []).map((candidate, index) => ({
    id: candidate.candidate_id, number: index + 1, place: mapPlace(candidate),
  })), [candidates]);
  const pagePlaces = useMemo(() => numberedPlaces.slice(placePage * PLACE_PAGE_SIZE, (placePage + 1) * PLACE_PAGE_SIZE),
    [numberedPlaces, placePage]);
  const mapPlaces = useMemo(() => selectedPlace ? [] : pagePlaces.map(entry => entry.place), [pagePlaces, selectedPlace]);
  const placeNumbers = useMemo(() => pagePlaces.map(entry => entry.number), [pagePlaces]);
  const previewPlace = pagePlaces.find(entry => entry.id === selectedCandidate)?.place ?? null;
  const mappedRadius = pendingScope?.radius_m ?? (confirmingPlace ? null : conditions?.radius_m) ?? 1000;
  const institutionIds = pendingScope?.institution_ids ?? conditions?.institution_ids;
  const visibleInstitutions = useMemo(() => {
    if (!selectedPlace) return institutions;
    const scope = new Set(institutionIds);
    return institutions.filter(row => row.storage_location_id !== undefined && scope.has(row.storage_location_id));
  }, [institutions, institutionIds, selectedPlace]);
  const institutionIndex = useMemo(() => new Map(institutions.flatMap(row =>
    row.storage_location_id === undefined ? [] : [[row.storage_location_id, row] as const])), [institutions]);
  const onMapPlaceSelect = useCallback((place: Place) => {
    const entry = pagePlaces.find(candidate => candidate.place === place);
    if (entry) {
      setSelectedCandidate(entry.id);
      document.getElementById(`agent-place-${entry.id}`)?.scrollIntoView({ block: "nearest" });
    }
  }, [pagePlaces]);
  const showFullScope = useCallback(() => setSelectedInstitution([]), []);

  const onMapInstitutionSelect = useCallback((group: Institution[]) => {
    if (!response?.matches || !conditions?.place) return;
    const allowed = new Set(conditions.institution_ids);
    const groupInScope = group.filter(row => row.storage_location_id !== undefined && allowed.has(row.storage_location_id));
    if (groupInScope.length) {
      setSelectedInstitution(groupInScope);
      setResultsSeen(true);
      setTab("results");
    }
  }, [conditions, response?.matches]);

  const matchCount = response?.matches?.total_count ?? 0;
  const pageCount = Math.ceil((cursor ? matchCount : items.length) / ITEM_PAGE_SIZE);
  const picks = response?.recommended_item_ids.flatMap(id => items.filter(item => item.item_id === id)) ?? [];
  const busy = Boolean(progress) || paging;
  const detail = openItem?.item_id === location.state?.agentItemId ? openItem : null;
  const alertOpen = Boolean(location.state?.agentAlert);
  const previousRadius = previousSearchRadiusMeters(mappedRadius);
  const nextRadius = nextSearchRadiusMeters(mappedRadius);

  useEffect(() => {
    if (tab === "chat" && !picks.length && !selectedCandidate && feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [messages, tab, picks.length, selectedCandidate]);

  async function submit(turn: AgentTurn, retry = false) {
    if (controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setLastTurn(turn);
    setError("");
    setProgress("검색 조건을 확인하고 있어요.");
    setTab("chat");
    // PRD 4.3-B — Only the newest search supplies recommendations and result pages.
    setResponse(current => current && {
      ...current, matches: null, recommended_item_ids: [], search_attempts: [],
    });
    setItems([]);
    setCursor(null);
    setItemPage(0);
    setSelectedInstitution([]);
    if (turn.type === "message" && !retry) {
      setMessages(current => [...current, { author: "user", text: turn.text }]);
      setDraft("");
    }
    if (turn.type === "select_place") {
      setConfirmingPlace(candidates?.find(place => place.candidate_id === turn.candidate_id) ?? null);
    }
    try {
      const result = await runAgentTurn(turn, response, event => {
        if (event.type === "run.started") setActiveRun(event);
        if (event.type === "scope.updated") {
          setPendingScope(event);
          if (event.previous_radius_m !== undefined && event.radius_m > event.previous_radius_m) {
            setProgress(`보관기관이 없어 반경을 ${radiusKilometersLabel(event.radius_m)}로 넓혔어요.`);
          }
        }
        if (event.type === "tool.started") {
          setProgress(event.attempt === 2 ? "다른 표현으로 한 번 더 찾고 있어요." : TOOL_LABELS[event.tool] || "검색 중이에요.");
        }
      }, request.signal);
      if (result.status === "error") {
        setError(result.error?.message || result.message || "검색 중 문제가 생겼어요.");
        return;
      }
      const replies: ChatMessage[] = [];
      if (turn.type === "select_place" && result.status !== "cancelled" && result.conditions.place) {
        // PRD 4.3-A — Show the confirmed location before asking for item details.
        replies.push({ confirmedPlace: result.conditions.place });
      }
      if (result.message) replies.push({ author: "agent", text: result.message });
      if (replies.length) setMessages(current => [...current, ...replies]);
      if (result.status !== "cancelled") {
        setResponse(result.status === "ended" ? null : result);
        setItems(result.matches?.items ?? []);
        setCursor(result.matches?.next_cursor ?? null);
        setItemPage(0);
        setPlacePage(0);
        setSelectedCandidate(null);
        setSelectedInstitution([]);
        setShowHistory(false);
        setResultsSeen(false);
      }
      setLastTurn(null);
    } catch (err) {
      if (request.signal.aborted) return;
      if (err instanceof AgentSessionExpired) {
        setResponse(null);
        setItems([]);
        setCursor(null);
        setSelectedInstitution([]);
        if (turn.type !== "message") setLastTurn(null);
      }
      setError(err instanceof Error ? err.message : "대화형 검색에 연결하지 못했어요.");
    } finally {
      if (controller.current === request) controller.current = null;
      if (!request.signal.aborted) {
        setProgress("");
        setActiveRun(null);
        setCancelling(false);
        setPendingScope(null);
        setConfirmingPlace(null);
      }
    }
  }

  async function cancel() {
    if (!activeRun) return;
    setCancelling(true);
    try {
      if (!await cancelAgentTurn(activeRun.session_id, activeRun.run_id)) setCancelling(false);
    } catch {
      setCancelling(false);
      setError("검색을 취소하지 못했어요. 다시 시도해 주세요.");
    }
  }

  async function changePage(page: number) {
    if (!response || controller.current) return;
    if ((page + 1) * ITEM_PAGE_SIZE <= items.length || !cursor) {
      setItemPage(page);
      return;
    }
    const request = new AbortController();
    controller.current = request;
    setPaging(true);
    setError("");
    let buffered = items;
    let nextCursor: string | null = cursor;
    try {
      while (nextCursor && buffered.length < (page + 1) * ITEM_PAGE_SIZE) {
        const batch = await fetchAgentPage(response.session_id, response.revision, nextCursor, request.signal);
        if (!batch.items.length && batch.next_cursor) throw new Error("다음 결과를 불러오지 못했어요.");
        if (batch.next_cursor === nextCursor) throw new Error("다음 결과를 불러오지 못했어요.");
        buffered = [...buffered, ...batch.items];
        nextCursor = batch.next_cursor;
        setItems(buffered);
        setCursor(nextCursor);
      }
      setItemPage(Math.min(page, Math.max(0, Math.ceil(buffered.length / ITEM_PAGE_SIZE) - 1)));
    } catch (err) {
      if (!request.signal.aborted) setError(err instanceof Error ? err.message : "다음 결과를 불러오지 못했어요.");
    } finally {
      if (controller.current === request) controller.current = null;
      if (!request.signal.aborted) setPaging(false);
    }
  }

  function fill(text: string) {
    setDraft(text);
    input.current?.focus();
  }

  function send(event: FormEvent) {
    event.preventDefault();
    if (draft.trim()) void submit({ type: "message", text: draft.trim() });
  }

  function openResults() {
    setResultsSeen(true);
    setTab("results");
  }

  function openDetail(item: AgentItem) {
    setOpenItem(item);
    void navigate(location.pathname + location.search, { state: { agentItemId: item.item_id } });
  }

  function openAlerts() {
    void navigate(location.pathname + location.search, { state: { agentAlert: true } });
  }

  const previousMessage = messages.at(-2);
  const recentCount = previousMessage && "confirmedPlace" in previousMessage ? 3 : 1;
  const hiddenCount = Math.max(0, messages.length - recentCount);
  const rows = picks.length && !showHistory ? messages.slice(-recentCount) : messages;
  const groupNames = selectedInstitution.map(institution => institution.name).join(", ");
  const groupIds = selectedInstitution.map(institution => institution.storage_location_id).join(",");
  const submittedQuery = response?.search_attempts.at(-1)?.query ?? conditions?.item_query ?? "";
  // PRD 4.3-A — 외부 검색어에도 사용자가 확정한 분실 장소를 사용한다.
  const daangnSearch = response?.matches && conditions?.place && conditions.item_query
    ? `https://www.daangn.com/kr/search/community/?${new URLSearchParams({ q: `${conditions.place.name} ${conditions.item_query}` })}`
    : null;
  return <>
    <header className="app-header">
      <a className="app-wordmark" href="/" aria-label="RETRIEVER 홈">RETRIEVER</a>
      <nav className="app-navigation" aria-label="주요 메뉴">
        <a className={`app-nav-link${alertOpen ? "" : " active"}`} href="#agent-input">물건 찾기</a>
        <button type="button" className={`app-nav-link${alertOpen ? " active" : ""}`} onClick={openAlerts}>새 물건 알림</button>
      </nav>
    </header>
    <main className="app-shell agent-shell">
      <section className="agent-panel" aria-label="대화형 습득물 검색">
        {(conditions?.item_query || currentPlace || pagePlaces.length > 0) && <dl className="agent-summary">
          <div><dt>잃어버린 곳</dt><dd>{currentPlace?.name || "선택 대기 중"}</dd>
            {currentPlace && <button type="button" disabled={busy} onClick={() => fill("장소를 바꿀게요. ")}>다른 곳이에요</button>}</div>
          <div><dt>검색 반경</dt><dd>{radiusKilometersLabel(mappedRadius)}</dd>
            {conditions?.place && response?.matches && <span className="agent-radius-actions">
              {previousRadius && <button type="button" disabled={busy} onClick={() => void submit({ type: "change_radius", radius_m: previousRadius })}>{radiusKilometersLabel(previousRadius)}로 줄이기</button>}
              {nextRadius && <button type="button" disabled={busy} onClick={() => void submit({ type: "change_radius", radius_m: nextRadius })}>{radiusKilometersLabel(nextRadius)}로 넓히기</button>}
            </span>}</div>
          <div><dt>찾는 물건</dt><dd>{conditions?.item_query || "입력 대기 중"}</dd>
            {conditions?.item_query && <button type="button" disabled={busy} onClick={() => fill("물건 정보를 바꿀게요. ")}>수정</button>}</div>
        </dl>}
        {response?.matches && <nav className="agent-tabs" aria-label="대화와 검색 결과">
          <button type="button" aria-pressed={tab === "chat"} onClick={() => setTab("chat")}>대화</button>
          <button type="button" aria-pressed={tab === "results"} onClick={() => { showFullScope(); openResults(); }}>
            결과 {matchCount.toLocaleString("ko-KR")}{matchCount > 0 && !resultsSeen && <span className="agent-unread" aria-label="아직 안 본 전체 목록" />}
          </button>
        </nav>}
        <div ref={feed} className="agent-feed">
          {tab === "chat" ? <>
            {picks.length > 0 && hiddenCount > 0 && <button type="button" className="agent-history" onClick={() => setShowHistory(!showHistory)}>
              {showHistory ? "이전 대화 접기" : `이전 대화 보기 (${hiddenCount})`}
            </button>}
            {rows.map((message, index) => "confirmedPlace" in message
              ? <div className="agent-confirmed-card" key={index}>
                <small>확정된 장소</small>
                <strong>{message.confirmedPlace.name}</strong>
                {message.confirmedPlace.address && <small>{message.confirmedPlace.address}</small>}
              </div>
              : <p key={index} className={`agent-bubble agent-bubble-${message.author}`}>{message.text}</p>)}
            {pagePlaces.length > 0 && !confirmingPlace && <ol className="agent-candidates">
              {pagePlaces.map(({ id, place, number }) => <li key={id} id={`agent-place-${id}`}>
                <button type="button" className="agent-candidate" aria-pressed={id === selectedCandidate}
                  disabled={busy} onClick={() => setSelectedCandidate(id)}>
                  <span>{number}</span><span><strong>{place.name}</strong><small>{place.address}</small></span>
                </button>
                {id === selectedCandidate && <button type="button" className="agent-place-confirm" disabled={busy}
                  onClick={() => void submit({ type: "select_place", candidate_id: id })}>이 위치 선택하기</button>}
              </li>)}
            </ol>}
            {numberedPlaces.length > PLACE_PAGE_SIZE && !confirmingPlace && <nav className="agent-pagination" aria-label="장소 후보 페이지">
              {Array.from({ length: Math.ceil(numberedPlaces.length / PLACE_PAGE_SIZE) }, (_, page) =>
                <button key={page} type="button" disabled={busy} aria-current={page === placePage ? "page" : undefined}
                  onClick={() => { setPlacePage(page); setSelectedCandidate(null); }}>{page + 1}</button>)}
            </nav>}
            {picks.length > 0 && <section aria-label="먼저 볼 후보">
              <div className="agent-list-heading">
                <span>먼저 볼 후보 {picks.length}건 · 전체 {matchCount.toLocaleString("ko-KR")}건</span>
                <button type="button" onClick={() => { showFullScope(); openResults(); }}>전체 {matchCount.toLocaleString("ko-KR")}건 보기 ›</button>
              </div>
              <AgentResultList items={picks} institutions={institutionIndex} onOpen={openDetail} />
            </section>}
            {response?.matches && conditions?.place && <button type="button" className="agent-alert-link" onClick={openAlerts}>새 물건 알림 받기</button>}
          </> : <>
            {groupIds && submittedQuery
              ? <AgentInstitutionResults key={`${response?.revision}:${groupIds}`} ids={groupIds}
                query={submittedQuery} name={groupNames} institutions={institutionIndex} onOpen={openDetail} onClear={showFullScope} />
              : matchCount === 0 ? <p className="agent-bubble">일치하는 습득물을 찾지 못했어요.</p> : <>
              <AgentResultList items={items.slice(itemPage * ITEM_PAGE_SIZE, (itemPage + 1) * ITEM_PAGE_SIZE)} institutions={institutionIndex} onOpen={openDetail} />
              {pageCount > 1 && <nav className="agent-pagination" aria-label="습득물 결과 페이지">
                {itemPageNumbers(itemPage, pageCount).map((page: number) =>
                  <button type="button" key={page} aria-label={`${page + 1}페이지`} aria-current={page === itemPage ? "page" : undefined}
                    disabled={busy} onClick={() => void changePage(page)}>{page + 1}</button>)}
              </nav>}
            </>}
          </>}
          {daangnSearch && <a className="agent-daangn-link" href={daangnSearch} target="_blank" rel="noopener noreferrer">
            당근에서 찾아보기 <span aria-hidden="true">↗</span>
          </a>}
        </div>
        {(progress || paging || error) && <div className={`agent-status${error ? " agent-status-error" : ""}`} role="status">
          <span>{error || (cancelling ? "검색을 취소하고 있어요." : progress) || "다음 결과를 불러오고 있어요."}</span>
          {progress && <button type="button" onClick={() => void cancel()} disabled={!activeRun || cancelling}>취소</button>}
          {error && lastTurn && !busy && <button type="button" onClick={() => void submit(lastTurn, true)}>다시 시도</button>}
        </div>}
        <form className="agent-compose" onSubmit={send}>
          <label className="sr-only" htmlFor="agent-input">메시지</label>
          <input id="agent-input" ref={input} value={draft} onChange={event => setDraft(event.target.value)}
            maxLength={1000} placeholder="메시지를 입력하세요" />
          <button type="submit" disabled={busy || !draft.trim()}>보내기</button>
        </form>
      </section>

      <KakaoMap themeKey="retriever-blue" institutions={institutions} institutionsLoaded={institutionsLoaded}
        institutionError={institutionError} countsAvailable={false} places={mapPlaces} placeNumbers={placeNumbers}
        selectedPlace={selectedPlace} searchCenters={searchCenters} previewPlace={previewPlace} radiusMeters={mappedRadius}
        visibleInstitutions={visibleInstitutions} mapBadge={selectedPlace ? `잃어버린 곳 · ${selectedPlace.name}` : "잃어버린 곳을 먼저 찾아보세요."}
        onPlaceSelect={onMapPlaceSelect} onInstitutionSelect={onMapInstitutionSelect} onResetRadius={showFullScope} />
    </main>
    {detail && <FoundItemDetail key={detail.item_id} item={detail} institution={institutionIndex.get(detail.institution_id)} onClose={() => void navigate(-1)} />}
    {alertOpen && <NewItemAlert places={searchCenters} query={conditions?.item_query || ""} radiusMeters={mappedRadius} onClose={() => void navigate(-1)} />}
  </>;
}
