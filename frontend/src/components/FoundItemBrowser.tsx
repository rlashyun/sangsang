import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { fetchFoundItems } from "../api/client";
import { ensureBufferedItemPage, itemPageNumbers } from "../lib/item-pagination.js";
import {
  formatRegisteredDate,
  institutionLocationIds,
} from "../lib/presentation";
import {
  radiusKilometersLabel,
  nextSearchRadiusMeters,
  previousSearchRadiusMeters,
} from "../lib/geo.js";
import type { FoundItem, Institution } from "../types";
import { FoundItemDetail } from "./FoundItemDetail";
import { FoundItemThumbnail } from "./FoundItemThumbnail";

interface FoundItemBrowserProps {
  initialQuery?: string;
  scope: Institution[];
  previousRadiusLocationIds: ReadonlySet<number>;
  scopeTitle: string;
  canShowRadiusScope: boolean;
  radiusMeters: number;
  onShowRadius: () => void;
  onRadiusChange: (radiusMeters: number) => void;
  canExpandRadius: boolean;
  onSubmittedQueryChange: (query: string) => void;
  onAlertRequest?: () => void;
}

export function FoundItemBrowser({
  initialQuery = "",
  scope,
  previousRadiusLocationIds,
  scopeTitle,
  canShowRadiusScope,
  radiusMeters,
  onShowRadius,
  onRadiusChange,
  canExpandRadius,
  onSubmittedQueryChange,
  onAlertRequest,
}: FoundItemBrowserProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<{
    item: FoundItem;
    institution?: Institution;
    historyKey: string;
  } | null>(null);
  const detailIsOpen = detail !== null && location.state?.foundItemDetail === detail.historyKey;
  const [query, setQuery] = useState(initialQuery);
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery);
  const submittedQueryRef = useRef(initialQuery);
  const [items, setItems] = useState<FoundItem[]>([]);
  const [loadedRadius, setLoadedRadius] = useState<number | null>(null);
  const [itemPage, setItemPage] = useState(0);
  const pageSize = 4;
  const [status, setStatus] = useState(
    () => `위치를 검색하면 반경 ${radiusKilometersLabel(radiusMeters)}의 습득물을 보여드립니다.`,
  );
  const [statusIsError, setStatusIsError] = useState(false);
  const [semanticUnavailable, setSemanticUnavailable] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  // PRD 3.3 · 5 — 검색어 없는 목록은 서버가 50건씩 준다. 다음 페이지 커서와 총수를 기억한다.
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [candidateCount, setCandidateCount] = useState<number | null>(null);
  const inventoryCount = useRef<{ scope: string; total: number } | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const requestSequence = useRef(0);
  const locationIds = useMemo(() => institutionLocationIds(scope), [scope]);
  const scopeKey = locationIds.join(",");
  const showCenteredStatus = items.length === 0 && !isLoading && !statusIsError;
  const nextRadius = nextSearchRadiusMeters(radiusMeters);
  const previousRadius = previousSearchRadiusMeters(radiusMeters);
  const loadedPages = Math.ceil(items.length / pageSize);
  const visibleItems = items.slice(itemPage * pageSize, (itemPage + 1) * pageSize);
  const showLoadingOverCards = (isLoading || isLoadingMore) && visibleItems.length > 0;
  const pageNumbers = itemPageNumbers(itemPage, loadedPages);

  async function load(searchQuery: string, signal?: AbortSignal) {
    // PRD 4.1 — 탐색 범위·검색이 바뀌면 이전 물품 상세를 앞으로가기로 다시 열지 않는다.
    setDetail(null);
    setItemPage(0);
    const sequence = ++requestSequence.current;
    setSemanticUnavailable(false);
    setNextCursor(null);
    setTotalCount(null);
    setCandidateCount(null);
    setIsLoadingMore(false);
    // Keep the merged scope intact when it exceeds the server's institution limit.
    if (locationIds.length > 300) {
      setItems([]);
      setStatus("보관기관이 300곳을 넘었어요. 반경을 줄이거나 선택한 장소를 빼주세요.");
      setStatusIsError(true);
      setIsLoading(false);
      return;
    }
    if (!locationIds.length) {
      setItems([]);
      setStatus(`반경 ${radiusKilometersLabel(radiusMeters)} 안에 등록된 보관기관이 없습니다.`);
      setStatusIsError(false);
      setIsLoading(false);
      return;
    }

    setStatusIsError(false);
    setStatus(searchQuery
      ? `'${searchQuery}'와 유사한 물품을 찾고 있습니다…`
      : "습득물 정보를 불러오고 있습니다…");
    setIsLoading(true);
    try {
      const params = new URLSearchParams({ location_ids: locationIds.join(",") });
      if (searchQuery.trim()) params.set("q", searchQuery.trim());
      const payload = await fetchFoundItems(params, signal);
      if (sequence !== requestSequence.current) return;
      const nextItems = payload.items || [];
      setSemanticUnavailable(Boolean(searchQuery.trim()) && payload.semantic_status === "unavailable");
      const nextTotal = payload.total_count ?? (payload.next_cursor ? null : nextItems.length);
      if (!searchQuery && nextTotal !== null) inventoryCount.current = { scope: scopeKey, total: nextTotal };
      let candidates = payload.candidate_count ?? (inventoryCount.current?.scope === scopeKey ? inventoryCount.current.total : null);
      if (searchQuery && candidates === null) {
        // The search endpoint may omit the scope count. The unfiltered first page supplies it.
        const inventory = await fetchFoundItems(new URLSearchParams({ location_ids: scopeKey }), signal).catch(() => null);
        if (sequence !== requestSequence.current || signal?.aborted) return;
        candidates = inventory?.total_count ?? null;
        if (candidates !== null) inventoryCount.current = { scope: scopeKey, total: candidates };
      }
      setItems(nextItems);
      setLoadedRadius(radiusMeters);
      setNextCursor(payload.next_cursor ?? null);
      setTotalCount(nextTotal);
      setCandidateCount(candidates);
      if (!nextItems.length) {
        setStatus(searchQuery
          ? `'${searchQuery}'와 유사한 습득물이 없습니다.`
          : "현재 조건에 해당하는 습득물이 없습니다.");
      } else {
        setStatus(searchQuery
          ? matchedItemsStatus(candidates, payload.match_count ?? nextTotal ?? nextItems.length)
          : storedItemsStatus(nextTotal ?? nextItems.length));
      }
    } catch (error) {
      if (sequence !== requestSequence.current) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      setItems([]);
      setStatusIsError(true);
      setStatus(error instanceof Error
        ? `습득물 로딩 실패: ${error.message}`
        : "습득물 정보를 불러오지 못했습니다.");
    } finally {
      if (sequence === requestSequence.current && !signal?.aborted) setIsLoading(false);
    }
  }

  async function changeItemPage(page: number) {
    if (page < 0 || page === itemPage || isLoadingMore || isLoading) return;
    if (page * pageSize >= items.length && !nextCursor) return;
    if ((page + 1) * pageSize <= items.length || !nextCursor) {
      setItemPage(page);
      return;
    }
    // 범위나 검색어가 바뀌어 새 load()가 시작되면 이 응답은 버린다.
    const sequence = requestSequence.current;
    setIsLoadingMore(true);
    try {
      const searchQuery = submittedQueryRef.current;
      const result = await ensureBufferedItemPage({
        items, nextCursor, page, pageSize,
        isCurrent: () => sequence === requestSequence.current,
        loadNext: (cursor) => {
          const params = new URLSearchParams({ location_ids: locationIds.join(","), cursor });
          if (searchQuery) params.set("q", searchQuery);
          return fetchFoundItems(params);
        },
      });
      if (!result || sequence !== requestSequence.current) return;
      setItems(result.items);
      setNextCursor(result.nextCursor);
      setItemPage(result.page);
      setStatusIsError(false);
      setStatus(submittedQueryRef.current
        ? matchedItemsStatus(candidateCount, totalCount ?? result.items.length)
        : storedItemsStatus(totalCount ?? result.items.length));
    } catch (error) {
      if (sequence !== requestSequence.current) return;
      // 이미 보여 준 목록은 유지하고, 같은 버튼으로 다시 시도할 수 있게 둔다.
      setStatusIsError(true);
      setStatus(error instanceof Error
        ? `다음 물품 조회 실패: ${error.message}`
        : "다음 물품을 불러오지 못했습니다. 다시 시도해주세요.");
    } finally {
      if (sequence === requestSequence.current) setIsLoadingMore(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    // PRD 4.5 — 범위 변경은 입력 중 문구가 아닌 마지막 제출 검색어로 재검색한다.
    setQuery(submittedQueryRef.current);
    void load(submittedQueryRef.current, controller.signal);
    return () => controller.abort();
  }, [locationIds.join(","), radiusMeters, scopeTitle]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = query.trim();
    submittedQueryRef.current = value;
    setSubmittedQuery(value);
    onSubmittedQueryChange(value);
    setQuery(value);
    void load(value);
  }

  function openDetail(item: FoundItem) {
    const historyKey = crypto.randomUUID();
    setDetail({
      item,
      institution: scope.find((institution) => (
        item.storage_location_id != null
        && institution.storage_location_id === item.storage_location_id
      )),
      historyKey,
    });
    // PRD 4.3-C — 수령 정보를 확인한 뒤 기존 탐색 조건과 목록 위치로 복귀한다.
    // 2026-11-08 사용자 결정: 뒤로가기는 상세 창만 닫는다. 물품 데이터는 메모리에만 둔다.
    void navigate(`${location.pathname}${location.search}${location.hash}`, {
      state: { ...location.state, foundItemDetail: historyKey },
      preventScrollReset: true,
    });
  }

  return (
    <section className="item-browser" aria-labelledby="item-browser-title">
      <h2 id="item-browser-title" className="sr-only">습득물 검색과 결과</h2>
      {canShowRadiusScope && <div className="item-browser-header">
        <button className="scope-reset" type="button" onClick={onShowRadius}>
          {radiusKilometersLabel(radiusMeters)} 전체
        </button>
      </div>}

      <form className="found-item-form" onSubmit={handleSubmit}>
        <label className="sr-only" htmlFor="found-item-input">습득물 검색어</label>
        <div className="search-input-shell">
          <input
            id="found-item-input"
            type="search"
            /* PRD 3.3 · D3 — 습득물 검색어 상한 30자 (서버 found_items()와 동일) */
            maxLength={30}
            autoComplete="off"
            placeholder="어떤 물건을 찾고 있나요?"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            className="search-icon-button"
            type="submit"
            aria-label="물품 검색"
            title="물품 검색"
            disabled={isLoading}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m16.25 16.25 4.25 4.25" />
            </svg>
          </button>
        </div>
      </form>

      <div
        className={`found-item-status${(isLoading || isLoadingMore) && !showLoadingOverCards ? " search-loading" : ""}${statusIsError ? " error" : ""}${showCenteredStatus ? " empty" : ""}`}
        aria-hidden={showLoadingOverCards || undefined}
        role="status"
        aria-live="polite"
      >
        {showLoadingOverCards ? "습득물 목록" : status}
        {semanticUnavailable && <span> 의미 검색에 연결하지 못해 키워드 검색 결과를 보여드리고 있어요.</span>}
      </div>
      <div className={`found-items-region${visibleItems.length ? "" : " empty"}`}>
      <ol className="found-items" data-page-size={pageSize} aria-label="습득물 목록" aria-busy={isLoading || isLoadingMore}>
        {visibleItems.map((item, index) => {
          // PRD 3.1-6 · 4.5 · D2 — 유사도 %는 화면에 노출하지 않는다(match_score는 내부 평가용).
          return (
            <li className="found-item-card" key={item.id ?? `${item.item_name}:${item.registered_on}:${index}`}>
              <button
                className="found-item-open"
                type="button"
                aria-label={`${item.item_name || "이름 미상"} 상세 보기`}
                aria-haspopup="dialog"
                disabled={isLoading || isLoadingMore}
                onClick={() => openDetail(item)}
              />
              <FoundItemThumbnail item={item} />
              <div className="item-card-body">
                <div className="item-card-top">
                  <span className="item-category">{item.raw_category_name}</span>
                  {item.color_name?.trim() && <span className="item-color" title={item.color_name}>{item.color_name}</span>}
                  {!isLoading && loadedRadius === radiusMeters && previousRadius !== null && item.storage_location_id != null && previousRadiusLocationIds.has(item.storage_location_id) && (
                    <span className="item-previous-radius">{radiusKilometersLabel(previousRadius)} 이내</span>
                  )}
                </div>
                <strong className="item-name" title={item.item_name || "이름 미상"}>{item.item_name || "이름 미상"}</strong>
                <dl className="item-meta">
                  <div><dt>보관장소</dt><dd>{item.raw_storage_name || "정보 없음"}</dd></div>
                  <div><dt>등록일</dt><dd>{formatRegisteredDate(item.registered_on)}</dd></div>
                </dl>
              </div>
            </li>
          );
        })}
      </ol>
      {showLoadingOverCards && <div className="found-items-loading search-loading" role="status">
        {isLoadingMore ? "다음 물품 불러오는 중…" : "검색 중…"}
      </div>}
      </div>
      {items.length > 0 && (
        <nav className="place-pagination item-pagination" aria-label="물품 검색 결과 페이지">
          <button type="button" aria-label="이전 물품 페이지" disabled={isLoading || isLoadingMore || itemPage === 0}
            onClick={() => { void changeItemPage(itemPage - 1); }}>‹</button>
          {pageNumbers.map((page) => <span key={page} className="item-page-option">
            <button type="button" aria-label={`물품 ${page + 1}페이지`} aria-current={page === itemPage ? "page" : undefined}
              disabled={isLoading || isLoadingMore} onClick={() => { void changeItemPage(page); }}>{page + 1}</button>
          </span>)}
          <button type="button" aria-label="다음 물품 페이지" disabled={isLoading || isLoadingMore || ((itemPage + 1) * pageSize >= items.length && !nextCursor)}
            onClick={() => { void changeItemPage(itemPage + 1); }}>›</button>
          <span className="sr-only" role="status">{`물품 ${itemPage + 1}페이지`}</span>
        </nav>
      )}
      {canExpandRadius && (
        <div className="item-search-followup" aria-label="검색 범위 다시 살펴보기">
          {submittedQuery && <p>찾는 물건이 없나요?</p>}
          <div className="item-followup-actions">
            {(previousRadius !== null || nextRadius !== null) && <div className="item-radius-actions">
              {previousRadius !== null && <button type="button" disabled={isLoading || isLoadingMore} onClick={() => onRadiusChange(previousRadius)}>
                {radiusKilometersLabel(previousRadius)}로 돌아가기
              </button>}
              {submittedQuery && nextRadius !== null && <button type="button" disabled={isLoading || isLoadingMore} onClick={() => onRadiusChange(nextRadius)}>
                {radiusKilometersLabel(nextRadius)}로 넓혀보기
              </button>}
            </div>}
            {submittedQuery && onAlertRequest && <button type="button" className="item-alert-button" aria-haspopup="dialog" disabled={isLoading || isLoadingMore} onClick={onAlertRequest}>새 물건 알림 받기</button>}
          </div>
        </div>
      )}
      {detailIsOpen && (
        <FoundItemDetail
          key={detail.historyKey}
          item={detail.item}
          institution={detail.institution}
          onClose={() => { void navigate(-1); }}
        />
      )}
    </section>
  );
}

function matchedItemsStatus(candidates: number | null, matches: number) {
  const result = `검색 결과 ${matches.toLocaleString("ko-KR")}건`;
  return candidates === null ? result : `전체 ${candidates.toLocaleString("ko-KR")}건 중 ${result}`;
}

function storedItemsStatus(total: number) {
  return `현재 보관 중인 물품 ${total.toLocaleString("ko-KR")}건 · 최근 등록순`;
}
