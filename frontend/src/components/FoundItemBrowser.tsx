import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { fetchFoundItems } from "../api/client";
import {
  categoryIcon,
  formatRegisteredDate,
  institutionLocationIds,
  sourceLabel,
} from "../lib/presentation";
import {
  radiusKilometersLabel,
  SEARCH_RADIUS_OPTIONS_METERS,
} from "../lib/geo.js";
import type { FoundItem, Institution } from "../types";

interface FoundItemBrowserProps {
  scope: Institution[];
  scopeTitle: string;
  canShowRadiusScope: boolean;
  radiusMeters: number;
  onShowRadius: () => void;
  onRadiusChange: (radiusMeters: number) => void;
}

export function FoundItemBrowser({
  scope,
  scopeTitle,
  canShowRadiusScope,
  radiusMeters,
  onShowRadius,
  onRadiusChange,
}: FoundItemBrowserProps) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<FoundItem[]>([]);
  const [status, setStatus] = useState(
    () => `위치를 검색하면 반경 ${radiusKilometersLabel(radiusMeters)}의 습득물을 보여드립니다.`,
  );
  const [statusIsError, setStatusIsError] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  // PRD 3.3 · 5 — 검색어 없는 목록은 서버가 50건씩 준다. 다음 페이지 커서와 총수를 기억한다.
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const requestSequence = useRef(0);
  const locationIds = useMemo(() => institutionLocationIds(scope), [scope]);
  const showCenteredStatus = items.length === 0 && !isLoading && !statusIsError;
  const largerRadiusOptions = SEARCH_RADIUS_OPTIONS_METERS.filter(
    (option) => option > radiusMeters,
  );

  async function load(searchQuery: string, signal?: AbortSignal) {
    const sequence = ++requestSequence.current;
    setNextCursor(null);
    setTotalCount(null);
    setIsLoadingMore(false);
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
      const nextTotal = payload.total_count ?? nextItems.length;
      setItems(nextItems);
      setNextCursor(payload.next_cursor ?? null);
      setTotalCount(nextTotal);
      if (!nextItems.length) {
        setStatus(searchQuery
          ? `'${searchQuery}'와 유사한 습득물이 없습니다.`
          : "현재 조건에 해당하는 습득물이 없습니다.");
      } else {
        setStatus(searchQuery
          ? `후보 ${(payload.candidate_count ?? 0).toLocaleString("ko-KR")}건 중 유사한 물품 ${nextItems.length.toLocaleString("ko-KR")}건`
          : storedItemsStatus(nextTotal));
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

  async function loadMore() {
    if (!nextCursor || isLoadingMore) return;
    // 범위나 검색어가 바뀌어 새 load()가 시작되면 이 응답은 버린다.
    const sequence = requestSequence.current;
    setIsLoadingMore(true);
    try {
      const params = new URLSearchParams({
        location_ids: locationIds.join(","),
        cursor: nextCursor,
      });
      const payload = await fetchFoundItems(params);
      if (sequence !== requestSequence.current) return;
      setItems((current) => [...current, ...(payload.items || [])]);
      setNextCursor(payload.next_cursor ?? null);
      setStatusIsError(false);
      setStatus(storedItemsStatus(totalCount ?? 0));
    } catch (error) {
      if (sequence !== requestSequence.current) return;
      // 이미 보여 준 목록은 유지하고, 같은 버튼으로 다시 시도할 수 있게 둔다.
      setStatusIsError(true);
      setStatus(error instanceof Error
        ? `물품 더 보기 실패: ${error.message}`
        : "물품을 더 불러오지 못했습니다.");
    } finally {
      if (sequence === requestSequence.current) setIsLoadingMore(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    setQuery("");
    void load("", controller.signal);
    return () => controller.abort();
  }, [locationIds.join(","), radiusMeters, scopeTitle]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void load(query);
  }

  return (
    <section className="item-browser" aria-labelledby="item-browser-title">
      <div className="item-browser-header">
        <div>
          <p className="section-kicker">FOUND ITEMS</p>
          <h2 id="item-browser-title">{scopeTitle}</h2>
        </div>
        {canShowRadiusScope && (
          <button className="scope-reset" type="button" onClick={onShowRadius}>
            {radiusKilometersLabel(radiusMeters)} 전체
          </button>
        )}
      </div>

      <form className="found-item-form" onSubmit={handleSubmit}>
        <label className="sr-only" htmlFor="found-item-input">습득물 검색어</label>
        <div className="search-input-shell">
          <input
            id="found-item-input"
            type="search"
            /* PRD 3.3 · D3 — 습득물 검색어 상한 30자 (서버 found_items()와 동일) */
            maxLength={30}
            autoComplete="off"
            placeholder="예: 검정 지갑, 아이퐁, 흰색 카드"
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

      <fieldset className="radius-selector">
        <legend>탐색 반경</legend>
        <div className="radius-options">
          {SEARCH_RADIUS_OPTIONS_METERS.map((option) => (
            <button
              key={option}
              className="radius-option"
              type="button"
              aria-pressed={radiusMeters === option}
              onClick={() => onRadiusChange(option)}
            >
              {radiusKilometersLabel(option)}
            </button>
          ))}
        </div>
      </fieldset>

      <div
        className={`found-item-status${statusIsError ? " error" : ""}${showCenteredStatus ? " empty" : ""}`}
        role="status"
        aria-live="polite"
      >
        {status}
        {!locationIds.length && largerRadiusOptions.length > 0 && !statusIsError && (
          <div className="radius-empty-actions" aria-label="검색 반경 넓히기">
            {largerRadiusOptions.map((option) => (
              <button key={option} type="button" onClick={() => onRadiusChange(option)}>
                {radiusKilometersLabel(option)}로 넓히기
              </button>
            ))}
          </div>
        )}
      </div>
      <ol className="found-items" aria-label="습득물 목록">
        {items.map((item, index) => {
          const color = item.color_name ? ` · ${item.color_name}` : "";
          // PRD 3.1-6 · 4.5 · D2 — 유사도 %는 화면에 노출하지 않는다(match_score는 내부 평가용).
          return (
            <li className="found-item-card" key={item.id ?? `${item.item_name}:${item.registered_on}:${index}`}>
              <div className="item-placeholder" aria-hidden="true">
                <span>{categoryIcon(item.raw_category_name)}</span>
                <small>{sourceLabel(item.item_source_code)}</small>
              </div>
              <div className="item-card-body">
                <div className="item-card-top">
                  <span className="item-category">{item.raw_category_name}</span>
                </div>
                <strong className="item-name">{item.item_name || "이름 미상"}{color}</strong>
                {item.description && <p className="item-description">{item.description}</p>}
                <dl className="item-meta">
                  <div><dt>보관장소</dt><dd>{item.raw_storage_name || "정보 없음"}</dd></div>
                  <div><dt>등록일</dt><dd>{formatRegisteredDate(item.registered_on)}</dd></div>
                </dl>
              </div>
            </li>
          );
        })}
      </ol>
      {nextCursor && items.length > 0 && (
        <div className="found-items-more">
          <button
            type="button"
            onClick={() => { void loadMore(); }}
            disabled={isLoadingMore || isLoading}
          >
            {isLoadingMore
              ? "물품을 더 불러오고 있습니다…"
              : `물품 더 보기 (${items.length.toLocaleString("ko-KR")} / ${(totalCount ?? items.length).toLocaleString("ko-KR")}건)`}
          </button>
        </div>
      )}
    </section>
  );
}

function storedItemsStatus(total: number) {
  return `현재 보관 중인 물품 ${total.toLocaleString("ko-KR")}건 · 최근 등록순`;
}
