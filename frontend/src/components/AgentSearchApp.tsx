import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ClipboardEvent, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { AgentRequestError, AgentSessionExpired, cancelAgentTurn, fetchAgentPage, fetchInstitutions, runAgentTurn } from "../api/client";
import { DEFAULT_SEARCH_RADIUS_METERS, nextSearchRadiusMeters, radiusKilometersLabel } from "../lib/geo.js";
import { GUIDE_HASH, SURVEY_URL } from "../lib/feedback";
import { itemPageNumbers } from "../lib/item-pagination.js";
import { useChatPhotos, type ChatPhoto } from "../lib/useChatPhotos";
import type { AgentItem, AgentPlace, AgentResponse, AgentTurn, Institution, Place } from "../types";
import { AgentInstitutionResults } from "./AgentInstitutionResults";
import { AGENT_ITEM_PAGE_SIZE, AgentResultList } from "./AgentResultList";
import { FeedbackIcon } from "./FeedbackIcon";
import { FoundItemDetail } from "./FoundItemDetail";
import { KakaoMap } from "./KakaoMap";
import { NewItemAlert } from "./NewItemAlert";
import { UsageGuide } from "./UsageGuide";

// PRD 4.3-A — 2026-11-21 사용자 피드백: 대화와 확정 버튼의 공간을 확보해 5곳씩 보여준다.
const PLACE_PAGE_SIZE = 5;
const TOOL_LABELS: Record<string, string> = {
  places: "장소 후보를 찾고 있어요.",
  institutions: "반경 안의 보관기관을 확인하고 있어요.",
  found_items: "습득물을 찾고 있어요.",
};

type ChatMessage = { author: "user" | "agent"; text: string; photos?: ChatPhoto[] } | { confirmedPlace: AgentPlace };
type Scope = { radius_m: number; institution_ids: number[] };
type ActiveRun = { session_id: string; run_id: string };

function mapPlace(place: AgentPlace): Place {
  return { ...place, phone: "", category: "", url: "" };
}

export function AgentSearchApp() {
  const location = useLocation();
  const navigate = useNavigate();
  // 첫 화면은 인사와 이용 순서 안내(messages가 비었을 때)로 시작한다.
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const { photos, photoError, addingPhotos, addPhotos, removePhoto, takePhotos } = useChatPhotos();
  const photoInput = useRef<HTMLInputElement>(null);
  const [response, setResponse] = useState<AgentResponse | null>(null);
  const [items, setItems] = useState<AgentItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [itemPage, setItemPage] = useState(0);
  const [itemPageSize, setItemPageSize] = useState(AGENT_ITEM_PAGE_SIZE);
  const [placePage, setPlacePage] = useState(0);
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(null);
  const [tab, setTab] = useState<"chat" | "results">("chat");
  const [progress, setProgress] = useState("");
  const [revealingReplies, setRevealingReplies] = useState(false);
  const [followupVisible, setFollowupVisible] = useState(false);
  const replyTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
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
  const [countsAvailable, setCountsAvailable] = useState(false);
  const [selectedInstitution, setSelectedInstitution] = useState<Institution[]>([]);
  const selectedInstitutionRef = useRef(selectedInstitution);
  selectedInstitutionRef.current = selectedInstitution;
  const [institutionItemIds, setInstitutionItemIds] = useState<number[]>([]);
  const [institutionMatchCount, setInstitutionMatchCount] = useState<number | null>(null);
  const [openItem, setOpenItem] = useState<AgentItem | null>(null);
  const [focusedInstitution, setFocusedInstitution] = useState<Institution | null>(null);
  const [mapInset, setMapInset] = useState(0);
  // 장소 변경 중: 확정한 장소를 화면에서 비우고 처음처럼 입력·지도 더블클릭으로 고르게 한다.
  const [placeChanging, setPlaceChanging] = useState(false);
  const searchPath = `${location.pathname}${location.search}`;
  const guideIsOpen = location.hash === GUIDE_HASH;
  const controller = useRef<AbortController | null>(null);
  // PRD 4.3-A, 5 — Retry the original request, including its original revision.
  const retryRequest = useRef<{ id: string; session: { session_id: string; revision: number } | null } | null>(null);
  const sessionIdentity = useRef<{ session_id: string; revision: number } | null>(null);
  const cancellationPending = useRef(false);
  const recoveryRequired = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const feed = useRef<HTMLDivElement>(null);
  const [feedScrolling, setFeedScrolling] = useState(false);
  const scrollIdleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => replyTimers.current.forEach(clearTimeout), []);
  useEffect(() => () => {
    if (scrollIdleTimer.current !== null) clearTimeout(scrollIdleTimer.current);
  }, []);

  function showFeedScrollbar() {
    setFeedScrolling(true);
    if (scrollIdleTimer.current !== null) clearTimeout(scrollIdleTimer.current);
    scrollIdleTimer.current = setTimeout(() => setFeedScrolling(false), 700);
  }

  function hideFollowup() {
    replyTimers.current.forEach(clearTimeout);
    replyTimers.current = [];
    setFollowupVisible(false);
  }

  function revealReplies(replies: ChatMessage[], showFollowup = false) {
    // PRD 4.3-A — 도착한 답변은 순서대로 표시한 뒤 후속 안내를 보여준다.
    hideFollowup();
    setRevealingReplies(true);
    replies.forEach((reply, index) => {
      if (index === 0) setMessages(current => [...current, reply]);
      else replyTimers.current.push(setTimeout(() => setMessages(current => [...current, reply]), index * 300));
    });
    replyTimers.current.push(setTimeout(() => {
      setRevealingReplies(false);
    }, Math.max(1, replies.length) * 300));
    if (showFollowup) {
      replyTimers.current.push(setTimeout(() => setFollowupVisible(true), Math.max(1, replies.length) * 300 + 700));
    }
  }

  useEffect(() => {
    const load = new AbortController();
    void fetchInstitutions(load.signal, false).then(payload => {
      if (load.signal.aborted) return;
      const locations = payload.institutions ?? [];
      setInstitutions(locations);
      setInstitutionsLoaded(true);
      setCountsAvailable(payload.counts_available === true);
      if (payload.counts_available || !locations.length) return;

      // PRD 3.1-4 — 기존 지도처럼 위치를 먼저 띄우고 기관별 물품 수를 나중에 채운다.
      void fetchInstitutions(load.signal).then(counts => {
        if (load.signal.aborted || !counts.counts_available) return;
        const byId = new Map(counts.institutions?.map(row => [row.storage_location_id, row.item_count]));
        setInstitutions(locations.map(row => {
          const itemCount = row.storage_location_id === undefined ? undefined : byId.get(row.storage_location_id);
          return itemCount === undefined ? row : { ...row, item_count: itemCount };
        }));
        setCountsAvailable(true);
      }).catch(() => {
        // 집계를 못 불러와도 이미 표시한 기관은 유지한다.
      });
    }).catch(() => {
      if (!load.signal.aborted) setInstitutionError("기관 위치를 불러오지 못했어요.");
    });
    return () => {
      load.abort();
      controller.current?.abort();
    };
  }, []);

  const conditions = response?.conditions;
  const currentPlace = confirmingPlace ?? (placeChanging ? null : conditions?.place ?? null);
  const canAttachPhotos = Boolean(conditions?.place) && !placeChanging && !confirmingPlace;
  const placeName = currentPlace?.name;
  const placeAddress = currentPlace?.address;
  const placeLatitude = currentPlace?.latitude;
  const placeLongitude = currentPlace?.longitude;
  // PRD 4.4 — 같은 장소에서 물건 단서만 갱신하면 지도의 확대·이동 상태를 유지한다.
  const selectedPlace = useMemo<Place | null>(() => (
    placeLatitude === undefined || placeLongitude === undefined ? null : {
      name: placeName ?? "", address: placeAddress ?? "", latitude: placeLatitude, longitude: placeLongitude,
      phone: "", category: "", url: "",
    }
  ), [placeName, placeAddress, placeLatitude, placeLongitude]);
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
  const mappedRadius = pendingScope?.radius_m ?? (confirmingPlace ? null : conditions?.radius_m) ?? DEFAULT_SEARCH_RADIUS_METERS;
  const institutionIds = pendingScope?.institution_ids ?? (confirmingPlace ? undefined : conditions?.institution_ids);
  const scopeReady = !confirmingPlace || pendingScope !== null;
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
  // 검색 중에는 새 지점을 받지 않는다(submit도 진행 중이면 무시한다).
  function onMapPointSelect(point: { latitude: number; longitude: number; name: string; address: string }) {
    void submit({ type: "select_point", ...point });
  }

  const onMapInstitutionSelect = useCallback((group: Institution[]) => {
    if (controller.current || !response?.matches || !currentPlace || !conditions?.place) return;
    const allowed = new Set(conditions.institution_ids);
    const groupInScope = group.filter(row => row.storage_location_id !== undefined && allowed.has(row.storage_location_id));
    if (groupInScope.length) {
      const currentGroup = selectedInstitutionRef.current;
      const sameGroup = groupInScope.length === currentGroup.length
        && groupInScope.every((row, index) => row.storage_location_id === currentGroup[index].storage_location_id);
      if (!sameGroup) {
        setInstitutionMatchCount(null);
        setInstitutionItemIds([]);
        setSelectedInstitution(groupInScope);
      }
      setTab("results");
    }
  }, [conditions, currentPlace, response?.matches]);

  const matchCount = response?.matches?.total_count ?? 0;
  const pageCount = Math.ceil((cursor ? matchCount : items.length) / itemPageSize);
  const busy = Boolean(progress) || paging || revealingReplies;
  const detail = openItem?.item_id === location.state?.agentItemId ? openItem : null;
  const alertOpen = Boolean(location.state?.agentAlert);
  const nextRadius = nextSearchRadiusMeters(mappedRadius);

  // PRD 4.3-A — 카드 높이를 기준으로 화면에 들어가는 만큼 표시한다.
  useLayoutEffect(() => {
    const body = document.querySelector<HTMLElement>(".agent-drawer-body");
    if (!body) return;
    const resize = () => {
      const reserved = 72;
      const rowHeight = parseFloat(getComputedStyle(body).getPropertyValue("--agent-item-row-height")) || 84;
      const size = Math.max(1, Math.floor((body.clientHeight - reserved) / rowHeight));
      setItemPageSize(size);
      setItemPage(0);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(body);
    resize();
    return () => observer.disconnect();
  }, [tab, response?.matches, selectedInstitution.length]);

  useEffect(() => {
    if (!selectedCandidate && feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [messages, selectedCandidate, followupVisible, photos.length, photoError]);

  function changePlaceCandidates(page: number) {
    setPlacePage(page);
    setSelectedCandidate(null);
    // PRD 4.3-A — 후보 묶음만 교체하며 대화·검색 조건은 유지한다.
    requestAnimationFrame(() => {
      feed.current?.querySelector(".agent-candidates")?.scrollIntoView({ block: "start" });
    });
  }

  // 채팅·결과 서랍이 가린 지도 왼쪽 폭. 모바일은 지도가 위쪽에 따로 있어 가리는 폭이 없다.
  useLayoutEffect(() => {
    const measure = () => {
      if (window.innerWidth <= 820) return setMapInset(0);
      const edge = (document.querySelector(".agent-drawer") ?? document.querySelector(".agent-panel"))?.getBoundingClientRect().right ?? 0;
      setMapInset(Math.round(edge));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [tab, response?.matches]);

  async function submit(turn: AgentTurn, retry = false, attachedPhotos: ChatPhoto[] = []) {
    if (controller.current || revealingReplies) return;
    if (!retry && recoveryRequired.current) {
      setError("이전 검색의 처리 결과를 먼저 확인해 주세요. ‘다시 시도’를 누르면 이어서 복구해요.");
      return;
    }
    const request = new AbortController();
    controller.current = request;
    hideFollowup();
    if (!retry || !retryRequest.current) {
      retryRequest.current = { id: crypto.randomUUID(), session: sessionIdentity.current };
    }
    const identity = retryRequest.current;
    cancellationPending.current = false;
    setLastTurn(turn);
    setError("");
    setProgress("검색 조건을 확인하고 있어요.");
    if (turn.type === "message" && !retry) {
      setMessages(current => [...current, { author: "user", text: turn.text, photos: attachedPhotos }]);
      setDraft("");
    }
    if (turn.type === "select_place") {
      setConfirmingPlace(candidates?.find(place => place.candidate_id === turn.candidate_id) ?? null);
    }
    if (turn.type === "select_point") {
      // 서버 응답 전에도 고른 지점을 바로 보여 준다.
      setConfirmingPlace({
        candidate_id: "point", name: turn.name ?? "지도에서 고른 위치", address: turn.address ?? "",
        latitude: turn.latitude, longitude: turn.longitude,
      });
      if (!retry) setMessages(current => [...current, { author: "user", text: turn.name ? `지도에서 ${turn.name}을(를) 골랐어요.` : "지도에서 위치를 골랐어요." }]);
    }
    try {
      const result = await runAgentTurn(turn, identity.session, event => {
        if (cancellationPending.current) return;
        if (event.type === "run.started") setActiveRun(event);
        if (event.type === "scope.updated") {
          setPendingScope(event);
          if (event.previous_radius_m !== undefined && event.radius_m > event.previous_radius_m) {
            if (event.expansion_reason === "no_matches") {
              const message = `${radiusKilometersLabel(event.previous_radius_m)} 안에 일치하는 물건이 없어 ${radiusKilometersLabel(event.radius_m)}로 넓혀 찾고 있어요.`;
              setMessages(current => [...current, { author: "agent", text: message }]);
              setProgress(message);
            } else {
              setProgress(`보관기관이 없어 반경을 ${radiusKilometersLabel(event.radius_m)}로 넓혔어요.`);
            }
          }
        }
        if (event.type === "tool.started") {
          setProgress(event.is_rewrite ? "다른 표현으로 한 번 더 찾고 있어요." : TOOL_LABELS[event.tool] || "검색 중이에요.");
        }
      }, request.signal, identity.id);
      sessionIdentity.current = result;
      recoveryRequired.current = false;
      if (result.status === "error") {
        recoveryRequired.current = ["state_unavailable", "state_conflict"].includes(result.error?.code ?? "");
        setError(result.error?.message || result.message || "검색 중 문제가 생겼어요.");
        return;
      }
      const replies: ChatMessage[] = [];
      if ((turn.type === "select_place" || turn.type === "select_point") && result.status !== "cancelled" && result.conditions.place) {
        // PRD 4.3-A — Show the confirmed location before asking for item details.
        replies.push({ confirmedPlace: result.conditions.place });
      }
      const completedSearch = (result.status === "results" || result.status === "no_matches") && result.matches !== null;
      const message = result.status === "results" && (result.matches?.total_count ?? 0) > 0
        ? "검색을 마쳤어요. 결과 목록에서 찾으시는 물건이 있는지 확인해 보세요."
        : result.message;
      if (message) replies.push({ author: "agent", text: message });
      revealReplies(replies, completedSearch);
      if (result.status === "answered") {
        // PRD 4.3 — An informational turn advances the session without replacing results.
        setResponse(current => current ? { ...current, revision: result.revision } : result);
        const candidateIndex = result.place_candidates.findIndex(place => place.candidate_id === result.highlight_candidate_id);
        if (candidateIndex >= 0) {
          setPlacePage(Math.floor(candidateIndex / PLACE_PAGE_SIZE));
          setSelectedCandidate(result.highlight_candidate_id ?? null);
        }
      } else if (result.status !== "cancelled") {
        setFocusedInstitution(null);
        setPlaceChanging(result.status === "awaiting_place_clarification");
        setResponse(result.status === "ended" ? null : result);
        setItems(result.matches?.items ?? []);
        setCursor(result.matches?.next_cursor ?? null);
        setItemPage(0);
        setPlacePage(0);
        setSelectedCandidate(null);
        setSelectedInstitution([]);
        setTab((result.matches?.total_count ?? 0) > 0 ? "results" : "chat");
      }
      sessionIdentity.current = result.status === "ended" ? null : result;
      retryRequest.current = null;
      setLastTurn(null);
    } catch (err) {
      if (request.signal.aborted) return;
      recoveryRequired.current = err instanceof AgentRequestError && err.requiresRecovery;
      if (err instanceof AgentSessionExpired) {
        sessionIdentity.current = null;
        retryRequest.current = null;
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
    cancellationPending.current = true;
    try {
      if (!await cancelAgentTurn(activeRun.session_id, activeRun.run_id)) {
        cancellationPending.current = false;
        setCancelling(false);
      }
    } catch {
      setCancelling(false);
      cancellationPending.current = false;
      setError("검색을 취소하지 못했어요. 다시 시도해 주세요.");
    }
  }

  async function changePage(page: number) {
    if (!response || controller.current) return;
    if ((page + 1) * itemPageSize <= items.length || !cursor) {
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
      while (nextCursor && buffered.length < (page + 1) * itemPageSize) {
        const batch = await fetchAgentPage(response.session_id, response.revision, nextCursor, request.signal);
        if (!batch.items.length && batch.next_cursor) throw new Error("다음 결과를 불러오지 못했어요.");
        if (batch.next_cursor === nextCursor) throw new Error("다음 결과를 불러오지 못했어요.");
        buffered = [...buffered, ...batch.items];
        nextCursor = batch.next_cursor;
        setItems(buffered);
        setCursor(nextCursor);
      }
      setItemPage(Math.min(page, Math.max(0, Math.ceil(buffered.length / itemPageSize) - 1)));
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

  function startPlaceChange() {
    hideFollowup();
    setPlaceChanging(true);
    setSelectedInstitution([]);
    setTab("chat");
    setMessages(current => [...current, {
      author: "agent", text: "새 장소를 알려주세요. 장소를 입력하거나 지도에서 보관기관 핀을 두 번 눌러 고를 수 있어요.",
    }]);
    input.current?.focus();
  }

  function send(event: FormEvent) {
    event.preventDefault();
    if (busy || addingPhotos) return;
    const text = draft.trim();
    if (!text && !photos.length) return;
    if (text) {
      const visibleItemIds = selectedInstitution.length
        ? institutionItemIds
        : items.slice(itemPage * itemPageSize, (itemPage + 1) * itemPageSize).map(item => item.item_id);
      const turn: AgentTurn = { type: "message", text, changing_place: placeChanging, visible_item_ids: visibleItemIds };
      if (recoveryRequired.current) {
        void submit(turn);
        return;
      }
      void submit(turn, false, canAttachPhotos ? takePhotos() : []);
    } else {
      if (!canAttachPhotos) {
        explainPhotoPlaceRequirement();
        return;
      }
      // PRD 4.2 — 사진 첨부만 제공하며 실제 이미지 검색 완료로 처리하지 않는다.
      const selectedPhotos = takePhotos();
      setMessages(current => [...current, { author: "user", text: "", photos: selectedPhotos }]);
      setDraft("");
      revealReplies([{ author: "agent", text: currentPlace
        ? "사진을 첨부했어요. 물건의 특징도 글로 알려주세요."
        : "사진을 첨부했어요. 찾을 장소와 물건의 특징도 글로 알려주세요." }]);
    }
  }

  function explainPhotoPlaceRequirement() {
    const text = "사진을 첨부하려면 먼저 잃어버린 장소를 입력하고 선택해 주세요.";
    setMessages(current => {
      const last = current.at(-1);
      if (last && "author" in last && last.author === "agent" && last.text === text) return current;
      return [...current, { author: "agent", text }];
    });
    input.current?.focus();
  }

  function choosePhotos() {
    if (!canAttachPhotos) {
      explainPhotoPlaceRequirement();
      return;
    }
    photoInput.current?.click();
  }

  function addDraftPhotos(files: File[]) {
    if (!canAttachPhotos) {
      explainPhotoPlaceRequirement();
      return;
    }
    if (!busy && !addingPhotos) void addPhotos(files);
  }

  function pastePhotos(event: ClipboardEvent<HTMLFormElement>) {
    const files = Array.from(event.clipboardData.items)
      .filter(item => item.kind === "file" && item.type.startsWith("image/"))
      .flatMap(item => { const file = item.getAsFile(); return file ? [file] : []; });
    if (!files.length) return;
    event.preventDefault();
    addDraftPhotos(files);
  }

  function toggleResults() {
    if (drawerOpen) {
      setTab("chat");
      return;
    }
    showFullScope();
    setTab("results");
  }

  function openDetail(item: AgentItem) {
    // PRD 4.3-C · 4.4 — 물품에 연결된 기관 ID의 검증된 좌표로만 이동한다.
    const institution = institutionIndex.get(item.institution_id);
    setFocusedInstitution(institution ? { ...institution } : null);
    setOpenItem(item);
    void navigate(location.pathname + location.search, { state: { agentItemId: item.item_id } });
  }

  function openAlerts() {
    void navigate(location.pathname + location.search, { state: { agentAlert: true } });
  }

  const drawerOpen = tab === "results" && Boolean(response?.matches) && !placeChanging;
  // 이미 받은 단서는 다시 묻지 않고 빠진 것만 안내한다.
  const hasPlaceClue = Boolean(currentPlace);
  const composePlaceholder = !hasPlaceClue ? "잃어버린 장소를 입력하세요"
    : conditions?.item_query ? "메시지를 입력하세요" : "잃어버린 물건을 입력하세요";
  const groupNames = selectedInstitution.map(institution => institution.name).join(", ");
  const groupIds = selectedInstitution.map(institution => institution.storage_location_id).join(",");
  const mapInstitutionSummary = visibleInstitutions.length
    ? `보관기관 ${visibleInstitutions.length.toLocaleString("ko-KR")}곳`
    : "등록된 기관 없음";
  const mapBadge = currentPlace
    ? `${currentPlace.name} 기준 ${radiusKilometersLabel(mappedRadius)}`
      + (scopeReady && institutionsLoaded ? ` · ${mapInstitutionSummary}` : "")
    : institutionsLoaded ? "숫자를 누르면 확대 · 핀을 누르면 기관 정보" : "확대하면 개별 기관을 볼 수 있어요";
  const submittedQuery = response?.search_attempts.at(-1)?.query ?? conditions?.item_query ?? "";
  // PRD 4.3-A — 외부 검색어에도 사용자가 확정한 분실 장소를 사용한다.
  const daangnSearch = response?.matches && conditions?.place && conditions.item_query
    ? `https://www.daangn.com/kr/search/community/?${new URLSearchParams({ q: `${conditions.place.name} ${conditions.item_query}` })}`
    : null;
  const requestStatus = (progress || paging || error) && <div className={`agent-status${error ? " agent-status-error" : ""}`} role="status">
    <span>{error || (cancelling ? "검색을 취소하고 있어요." : progress) || "다음 결과를 불러오고 있어요."}</span>
    {progress && <button type="button" onClick={() => void cancel()} disabled={!activeRun || cancelling}>취소</button>}
    {error && lastTurn && !busy && <button type="button" onClick={() => void submit(lastTurn, true)}>다시 시도</button>}
  </div>;
  return <>
    <header className="app-header">
      <a className="app-wordmark" href={`/${location.search}`} aria-label="RETRIEVER 홈">RETRIEVER</a>
      <div className="app-header-actions">
        <Link className="app-guide-link" to={`${searchPath}${GUIDE_HASH}`} state={{ openedGuide: true }}
          aria-haspopup="dialog" aria-expanded={guideIsOpen}>이용안내</Link>
        <a className="app-guide-link" href={SURVEY_URL} target="_blank" rel="noopener noreferrer">
          <FeedbackIcon />의견 보내기<span className="sr-only"> (새 창에서 열림)</span>
        </a>
      </div>
    </header>
    <main className={`app-shell agent-shell${drawerOpen ? " agent-shell-drawer" : ""}`}>
      <section className={`agent-panel${drawerOpen ? " agent-panel-joined" : ""}`} aria-label="대화형 습득물 검색">
        {(conditions?.item_query || currentPlace || pagePlaces.length > 0 || placeChanging) && <dl className="agent-summary">
          <div><dt>장소</dt><dd>{currentPlace?.name || "선택 대기 중"}</dd>
            {currentPlace && <button type="button" disabled={busy} onClick={startPlaceChange}>장소 변경</button>}
            {placeChanging && <button type="button" disabled={busy} onClick={() => setPlaceChanging(false)}>취소</button>}</div>
          <div><dt>물건</dt><dd>{conditions?.item_query || "입력 대기 중"}</dd>
            {conditions?.item_query && <button type="button" disabled={busy} onClick={() => fill("물건 정보를 바꿀게요. ")}>수정</button>}</div>
          <div><dt>검색 반경</dt><dd>{radiusKilometersLabel(mappedRadius)}</dd></div>
        </dl>}
        <div ref={feed} className={`agent-feed${feedScrolling ? " is-scrolling" : ""}`} onScroll={showFeedScrollbar}>
          {messages.length === 0 && <>
            <h2 className="agent-welcome-title">이용 방법</h2>
            <ol className="agent-welcome-steps" aria-label="이용 순서">
              <li><span aria-hidden="true">1</span>잃어버린 장소를 적거나, 지도에서 보관기관 핀을 더블클릭하세요</li>
              <li><span aria-hidden="true">2</span>어떤 물건인지 알려주세요</li>
              <li><span aria-hidden="true">3</span>가까운 보관기관의 습득물을 보여드려요</li>
            </ol>
            <hr className="agent-welcome-divider" />
            <p className="agent-bubble agent-bubble-agent">물건을 잃어버린 장소를 알려주세요</p>
          </>}
          {messages.map((message, index) => "confirmedPlace" in message
            ? <div className="agent-confirmed-card" key={index}>
              <small>확정된 장소</small>
              <strong>{message.confirmedPlace.name}</strong>
              {message.confirmedPlace.address && <small>{message.confirmedPlace.address}</small>}
            </div>
            : <div key={index} className={`agent-bubble agent-bubble-${message.author}${message.photos?.length ? " agent-bubble-with-photos" : ""}`}>
              {Boolean(message.photos?.length) && <>
                <div className="agent-message-photos">{message.photos?.map((photo, photoIndex) =>
                  <img key={photo.id} src={photo.url} alt={`첨부한 사진 ${photoIndex + 1}: ${photo.name}`} />)}</div>
                <small className="agent-photo-caption">사진 첨부 · 사진 검색은 준비 중이에요</small>
              </>}
              {message.text && <p className="agent-message-text">{message.text}</p>}
            </div>)}
          {pagePlaces.length > 0 && !confirmingPlace && <ol className="agent-candidates" aria-label="장소 후보" start={placePage * PLACE_PAGE_SIZE + 1}>
            {pagePlaces.map(({ id, place, number }) => <li className={`agent-place-card${id === selectedCandidate ? " is-selected" : ""}`} key={id} id={`agent-place-${id}`}>
              <button type="button" className="agent-candidate" aria-pressed={id === selectedCandidate}
                disabled={busy} onClick={() => setSelectedCandidate(id)}>
                <span>{number}</span><span><strong title={place.name}>{place.name}</strong><small title={place.address}>{place.address}</small></span>
              </button>
              <div className="agent-place-card-action">
                {id === selectedCandidate && <button type="button" className="agent-place-confirm" disabled={busy}
                  onClick={() => void submit({ type: "select_place", candidate_id: id })}>이 장소 선택하기</button>}
              </div>
            </li>)}
          </ol>}
          {numberedPlaces.length > PLACE_PAGE_SIZE && !confirmingPlace && <nav className="agent-place-replies" aria-label="장소 후보 더 살펴보기">
            {placePage > 0 && <button type="button" className="agent-action-chip" disabled={busy}
              onClick={() => changePlaceCandidates(placePage - 1)}>이전 장소 보기</button>}
            {(placePage + 1) * PLACE_PAGE_SIZE < numberedPlaces.length && <button type="button" className="agent-action-chip agent-place-next" disabled={busy}
              onClick={() => changePlaceCandidates(placePage + 1)}>다른 장소 살펴보기</button>}
          </nav>}
          {followupVisible && !busy && !placeChanging && response?.matches && currentPlace && <div className="agent-result-followup">
            <p className="agent-bubble agent-bubble-agent">{nextRadius !== null
              ? "찾으시는 물건이 없나요? 반경을 넓혀보거나, 비슷한 물건 알림을 받아보세요."
              : "찾으시는 물건이 없나요? 비슷한 물건 알림을 받거나 당근에서 찾아보세요."}</p>
            <div className="agent-place-replies agent-result-actions">
              {nextRadius !== null && <button type="button" className="agent-action-chip" disabled={busy}
                  onClick={() => void submit({ type: "change_radius", radius_m: nextRadius })}>
                  반경 넓혀보기
                </button>}
            <button type="button" className="agent-action-chip" onClick={openAlerts}>비슷한 물건 알림 받기</button>
            {daangnSearch && <a className="agent-action-chip" href={daangnSearch} target="_blank" rel="noopener noreferrer">
              당근에서 찾아보기
            </a>}
            </div>
          </div>}
        </div>
        {!drawerOpen && requestStatus}
        <form className="agent-compose" onSubmit={send} onPaste={pastePhotos}>
          {canAttachPhotos && (photos.length > 0 || addingPhotos || photoError) && <div className="agent-photo-draft">
            {photos.length > 0 && <ul className="agent-photo-previews" aria-label="보낼 사진">
              {photos.map((photo, index) => <li key={photo.id}>
                <img src={photo.url} alt={`첨부 예정 사진 ${index + 1}: ${photo.name}`} />
                <button type="button" aria-label={`사진 ${index + 1} 삭제`} onClick={() => { removePhoto(photo.id); input.current?.focus(); }}>×</button>
              </li>)}
            </ul>}
            {photos.length > 0 && <p className="agent-photo-note">사진 검색은 준비 중이에요. 첨부한 사진은 현재 대화에서만 볼 수 있어요.</p>}
            {addingPhotos && <p className="agent-photo-note" role="status">사진을 불러오고 있어요.</p>}
            {photoError && <p className="agent-photo-error" role="alert">{photoError}</p>}
          </div>}
          <div className="agent-compose-row">
          <label className="sr-only" htmlFor="agent-input">메시지</label>
          <input ref={photoInput} type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden aria-label="사진 파일 선택"
            onChange={event => { const files = Array.from(event.currentTarget.files ?? []); event.currentTarget.value = ""; if (files.length) addDraftPhotos(files); }} />
          <button type="button" className="agent-photo" aria-label="사진 추가" title="사진 첨부" disabled={busy || addingPhotos}
            onClick={choosePhotos}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.6l1-1.5h5.8l1 1.5h1.6A2.5 2.5 0 0 1 20 8.5v8a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5z" /><circle cx="12" cy="12.5" r="3.2" /></svg>
          </button>
          <input id="agent-input" ref={input} value={draft} onChange={event => setDraft(event.target.value)}
            maxLength={1000} placeholder={composePlaceholder} />
          <button type="submit" className="agent-send" aria-label="보내기" disabled={busy || addingPhotos || (!draft.trim() && (!canAttachPhotos || !photos.length))}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" /></svg>
          </button>
          </div>
        </form>
      </section>

      {drawerOpen && <aside className="agent-drawer" aria-label="전체 검색 결과">
        <div className="agent-drawer-head">
          <strong>{groupIds ? institutionMatchCount === null ? "기관 결과 확인 중" : `결과 ${institutionMatchCount.toLocaleString("ko-KR")}건` : `결과 ${matchCount.toLocaleString("ko-KR")}건`}</strong>
        </div>
        <div className="agent-drawer-scope">
          <div>
            <strong title={groupNames || "전체 보관기관"}>{groupNames || "전체 보관기관"}</strong>
          </div>
          {groupIds && <button type="button" onClick={showFullScope}>전체 기관 보기</button>}
        </div>
        <div className="agent-drawer-body">
          {groupIds && submittedQuery
            ? <AgentInstitutionResults key={`${submittedQuery}:${groupIds}`} ids={groupIds}
              pageSize={itemPageSize} query={submittedQuery} institutions={institutionIndex} onOpen={openDetail} onVisibleItems={setInstitutionItemIds} onTotalChange={setInstitutionMatchCount} />
            : matchCount === 0 ? <p className="agent-bubble">일치하는 습득물을 찾지 못했어요.</p> : <>
              <AgentResultList pageSize={itemPageSize} items={items.slice(itemPage * itemPageSize, (itemPage + 1) * itemPageSize)} institutions={institutionIndex} onOpen={openDetail} />
              <nav className="agent-pagination" aria-label="습득물 결과 페이지">
                {pageCount <= 1 ? <button type="button" aria-label="1페이지" aria-current="page" disabled={busy}>1</button> : itemPageNumbers(itemPage, pageCount).map((page: number) =>
                  <button type="button" key={page} aria-label={`${page + 1}페이지`} aria-current={page === itemPage ? "page" : undefined}
                    disabled={busy} onClick={() => void changePage(page)}>{page + 1}</button>)}
              </nav>
            </>}
        </div>
        {requestStatus}
      </aside>}

      {response?.matches && matchCount > 0 && !placeChanging && <button type="button" className="agent-handle" aria-expanded={drawerOpen}
        aria-label={drawerOpen ? "결과 목록 접기" : `결과 ${matchCount.toLocaleString("ko-KR")}건 보기`} onClick={toggleResults}>
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d={drawerOpen ? "M10 3L5 8l5 5" : "M6 3l5 5-5 5"} /></svg>
        {!drawerOpen && <span className="agent-handle-badge" aria-hidden="true">{matchCount > 999 ? "999+" : matchCount}</span>}
      </button>}

      <KakaoMap themeKey="retriever-blue" focusedInstitution={focusedInstitution} institutions={institutions} institutionsLoaded={institutionsLoaded}
        institutionError={institutionError} countsAvailable={countsAvailable} places={mapPlaces} placeNumbers={placeNumbers}
        selectedPlace={selectedPlace} searchCenters={searchCenters} previewPlace={previewPlace} radiusMeters={mappedRadius}
        leftInset={mapInset} onPointSelect={busy ? undefined : onMapPointSelect} visibleInstitutions={visibleInstitutions} mapBadge={mapBadge}
        showInstitutionCounts={scopeReady} onPlaceSelect={onMapPlaceSelect} onInstitutionSelect={onMapInstitutionSelect}
        onResetRadius={selectedInstitution.length ? showFullScope : undefined} />
    </main>
    {detail && <FoundItemDetail key={detail.item_id} item={detail} institution={institutionIndex.get(detail.institution_id)} onClose={() => void navigate(-1)} />}
    {guideIsOpen && <UsageGuide variant="chat" searchPath={searchPath} surveyUrl={SURVEY_URL} onDismiss={() => {
      if (location.state?.openedGuide) void navigate(-1);
      else void navigate(searchPath, { replace: true });
    }} />}
    {alertOpen && <NewItemAlert places={searchCenters} query={conditions?.item_query || ""} radiusMeters={mappedRadius} onClose={() => void navigate(-1)} />}
  </>;
}
