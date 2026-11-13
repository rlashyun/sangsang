import { useState, type FormEvent } from "react";

import { placeCategoryLabel } from "../lib/presentation";
import type { Place } from "../types";

export const PLACE_PAGE_SIZE = 4;

interface LocationSearchProps {
  addingWaypoint: boolean;
  results: Place[];
  selectedPlace: Place | null;
  status: string;
  statusIsError: boolean;
  isSearching: boolean;
  onSearch: (query: string) => void;
  onSelect: (place: Place) => void;
  onConfirm: () => void;
  page: number;
  onPageChange: (page: number) => void;
}

export function LocationSearch({
  addingWaypoint,
  results,
  selectedPlace,
  status,
  statusIsError,
  isSearching,
  onSearch,
  onSelect,
  onConfirm,
  page,
  onPageChange,
}: LocationSearchProps) {
  const [query, setQuery] = useState("");
  const showCenteredStatus = results.length === 0 && !isSearching && !statusIsError;
  const pageCount = Math.ceil(results.length / PLACE_PAGE_SIZE);
  const start = page * PLACE_PAGE_SIZE;
  const visibleResults = results.slice(start, start + PLACE_PAGE_SIZE);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuery = query.trim();
    if (trimmedQuery) onSearch(trimmedQuery);
  }

  return (
    <>
      <header className="landing-header">
        <p className="eyebrow">RETRIEVER MAP</p>
        <h1 id="page-title">{addingWaypoint ? "추가할 장소를 찾아주세요" : "어디서 잃어버렸어요?"}</h1>
        <p className="lede">{addingWaypoint ? "장소를 추가하면 각 장소 주변의 습득물을 함께 찾아요." : "기억나는 장소를 입력하면 근처 습득물을 보여줘요."}</p>
      </header>

      <div className="location-search-view">
        <form className="search-form" onSubmit={handleSubmit}>
          <div className="search-row">
            <label className="sr-only" htmlFor="search-input">검색어</label>
            <div className="search-input-shell">
              <input
                id="search-input"
                name="query"
                type="search"
                maxLength={200}
                autoComplete="off"
                autoFocus={addingWaypoint}
                placeholder="장소명 또는 주소를 입력하세요"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                required
              />
              <button
                className="search-icon-button"
                type="submit"
                aria-label="장소 검색"
                title="장소 검색"
                disabled={isSearching}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <circle cx="11" cy="11" r="7" />
                  <path d="m16.25 16.25 4.25 4.25" />
                </svg>
              </button>
            </div>
          </div>
        </form>

        <div
          className={`status${isSearching ? " search-loading" : ""}${statusIsError ? " error" : ""}${showCenteredStatus ? " empty" : ""}`}
          role="status"
          aria-live="polite"
        >
          {status}
        </div>
        <ol className="results location-results" aria-label="위치 검색 결과">
          {visibleResults.map((place, index) => {
            const isActive = selectedPlace === place;
            const category = placeCategoryLabel(place.category);
            return (
              <li className={`result-item location-result-card${isActive ? " previewing" : ""}`} key={`${place.latitude}:${place.longitude}:${index}`}>
                <button
                  type="button"
                  className="result-button"
                  onClick={() => onSelect(place)}
                  disabled={isSearching}
                  aria-pressed={isActive}
                >
                  <span className="result-copy">
                    <span className="result-title">{start + index + 1}. {place.name}</span>
                    <span className="result-address">{place.address}</span>
                    {category && <span className="place-category-tag">{category}</span>}
                  </span>
                </button>
                {isActive && <div className="place-card-action">
                  <button
                    type="button"
                    onClick={onConfirm}
                    disabled={isSearching}
                  >{addingWaypoint ? "이 장소 추가하기" : "이 장소 선택하기"}</button>
                </div>}
              </li>
            );
          })}
        </ol>
        {pageCount > 0 && (
          <nav className="place-pagination" aria-label="장소 검색 결과 페이지">
            <button type="button" aria-label="이전 장소 페이지" disabled={isSearching || page === 0} onClick={() => onPageChange(page - 1)}>‹</button>
            {Array.from({ length: pageCount }, (_, index) => (
              <button key={index} type="button" aria-label={`장소 ${index + 1}페이지`} aria-current={index === page ? "page" : undefined}
                disabled={isSearching} onClick={() => onPageChange(index)}>{index + 1}</button>
            ))}
            <button type="button" aria-label="다음 장소 페이지" disabled={isSearching || page === pageCount - 1} onClick={() => onPageChange(page + 1)}>›</button>
          </nav>
        )}
      </div>
    </>
  );
}
