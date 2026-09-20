import { useState, type FormEvent } from "react";

import type { Place } from "../types";

interface LocationSearchProps {
  results: Place[];
  selectedPlace: Place | null;
  status: string;
  statusIsError: boolean;
  isSearching: boolean;
  onSearch: (query: string) => void;
  onSelect: (place: Place) => void;
}

export function LocationSearch({
  results,
  selectedPlace,
  status,
  statusIsError,
  isSearching,
  onSearch,
  onSelect,
}: LocationSearchProps) {
  const [query, setQuery] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuery = query.trim();
    if (trimmedQuery) onSearch(trimmedQuery);
  }

  return (
    <>
      <header className="landing-header">
        <p className="eyebrow">RETRIEVER MAP</p>
        <h1 id="page-title">보관 장소 찾기</h1>
        <p className="lede">장소명이나 정확한 주소를 검색하면 지도에서 바로 확인할 수 있어요.</p>
      </header>

      <div className="location-search-view">
        <form className="search-form" onSubmit={handleSubmit}>
          <div className="search-row">
            <label className="sr-only" htmlFor="search-input">검색어</label>
            <input
              id="search-input"
              name="query"
              type="search"
              maxLength={200}
              autoComplete="off"
              placeholder="장소명 또는 주소를 입력하세요"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              required
            />
            <button type="submit" disabled={isSearching}>검색</button>
          </div>
        </form>

        <div className={`status${statusIsError ? " error" : ""}`} role="status" aria-live="polite">
          {status}
        </div>
        <ol className="results location-results" aria-label="위치 검색 결과">
          {results.map((place, index) => {
            const isActive = selectedPlace === place;
            const meta = [place.phone, place.category].filter(Boolean).join(" · ");
            return (
              <li className="result-item" key={`${place.latitude}:${place.longitude}:${index}`}>
                <button
                  type="button"
                  className={`result-button${isActive ? " active" : ""}`}
                  onClick={() => onSelect(place)}
                >
                  <span className="result-copy">
                    <span className="result-title">{index + 1}. {place.name}</span>
                    <span className="result-address">{place.address}</span>
                    {meta && <span className="result-meta">{meta}</span>}
                  </span>
                  <span className="select-place-label">이 위치 선택</span>
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </>
  );
}
