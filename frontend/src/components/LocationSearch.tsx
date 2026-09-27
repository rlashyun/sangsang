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
  const showCenteredStatus = results.length === 0 && !isSearching && !statusIsError;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuery = query.trim();
    if (trimmedQuery) onSearch(trimmedQuery);
  }

  return (
    <>
      <header className="landing-header">
        <p className="eyebrow">RETRIEVER MAP</p>
        <h1 id="page-title">어디서 잃어버렸어요?</h1>
        <p className="lede">기억나는 장소를 입력하면 근처 습득물을 보여줘요.</p>
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
          className={`status${statusIsError ? " error" : ""}${showCenteredStatus ? " empty" : ""}`}
          role="status"
          aria-live="polite"
        >
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
