import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { fetchFoundItems } from "../api/client";
import {
  categoryIcon,
  formatRegisteredDate,
  institutionLocationIds,
  sameInstitutionScope,
  sourceLabel,
} from "../lib/presentation";
import type { FoundItem, Institution } from "../types";

interface FoundItemBrowserProps {
  scope: Institution[];
  scopeTitle: string;
  radiusScope: Institution[];
  onShowRadius: () => void;
}

export function FoundItemBrowser({
  scope,
  scopeTitle,
  radiusScope,
  onShowRadius,
}: FoundItemBrowserProps) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<FoundItem[]>([]);
  const [status, setStatus] = useState("위치를 검색하면 반경 1km의 습득물을 보여드립니다.");
  const [statusIsError, setStatusIsError] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const requestSequence = useRef(0);
  const locationIds = useMemo(() => institutionLocationIds(scope), [scope]);
  const showRadiusButton = radiusScope.length > 0
    && !sameInstitutionScope(scope, radiusScope);

  async function load(searchQuery: string, signal?: AbortSignal) {
    const sequence = ++requestSequence.current;
    if (!locationIds.length) {
      setItems([]);
      setStatus("연결된 보관기관 또는 습득물이 없습니다.");
      setStatusIsError(false);
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
      setItems(nextItems);
      if (!nextItems.length) {
        setStatus(searchQuery
          ? `'${searchQuery}'와 유사한 습득물이 없습니다.`
          : "현재 조건에 해당하는 습득물이 없습니다.");
      } else {
        setStatus(searchQuery
          ? `후보 ${payload.candidate_count.toLocaleString("ko-KR")}건 중 유사한 물품 ${nextItems.length.toLocaleString("ko-KR")}건`
          : `현재 보관 중인 물품 ${nextItems.length.toLocaleString("ko-KR")}건`);
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

  useEffect(() => {
    const controller = new AbortController();
    setQuery("");
    void load("", controller.signal);
    return () => controller.abort();
  }, [locationIds.join(","), scopeTitle]);

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
        {showRadiusButton && (
          <button className="scope-reset" type="button" onClick={onShowRadius}>
            1km 전체
          </button>
        )}
      </div>

      <form className="found-item-form" onSubmit={handleSubmit}>
        <label className="sr-only" htmlFor="found-item-input">습득물 검색어</label>
        <input
          id="found-item-input"
          type="search"
          maxLength={100}
          autoComplete="off"
          placeholder="예: 검정 지갑, 아이퐁, 흰색 카드"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="submit" disabled={isLoading}>물품 검색</button>
      </form>

      <div className={`found-item-status${statusIsError ? " error" : ""}`} role="status" aria-live="polite">
        {status}
      </div>
      <ol className="found-items" aria-label="습득물 목록">
        {items.map((item, index) => {
          const color = item.color_name ? ` · ${item.color_name}` : "";
          const score = Number.isFinite(Number(item.match_score))
            ? Math.round(Number(item.match_score) * 100)
            : null;
          return (
            <li className="found-item-card" key={`${item.item_name}:${item.registered_on}:${index}`}>
              <div className="item-placeholder" aria-hidden="true">
                <span>{categoryIcon(item.raw_category_name)}</span>
                <small>{sourceLabel(item.item_source_code)}</small>
              </div>
              <div className="item-card-body">
                <div className="item-card-top">
                  <span className="item-category">{item.raw_category_name}</span>
                  {score !== null && <span className="match-score">유사도 {score}%</span>}
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
    </section>
  );
}
