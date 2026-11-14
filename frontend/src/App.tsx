import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { NewItemAlert } from "./components/NewItemAlert";

import { fetchInstitutions, geocodeAddress, searchPlaces } from "./api/client";
import { FoundItemBrowser } from "./components/FoundItemBrowser";
import { KakaoMap, type KakaoMapHandle } from "./components/KakaoMap";
import { LocationSearch, PLACE_PAGE_SIZE } from "./components/LocationSearch";
import { SelectionSummary } from "./components/SelectionSummary";
import {
  DEFAULT_SEARCH_RADIUS_METERS,
  isWithinRadius,
  radiusKilometersLabel,
  nextSearchRadiusMeters,
  previousSearchRadiusMeters,
} from "./lib/geo.js";
import { institutionLocationIds, normalizePlace } from "./lib/presentation";
import {
  applyPreviewTheme,
  type PreviewTheme,
} from "./lib/theme";
import type { Institution, Place, SelectionSummary as SelectionSummaryValue } from "./types";

const MAX_WAYPOINT_PLACES = 3;

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const alertIsOpen = location.state?.newItemAlert === true;
  const [alertQuery, setAlertQuery] = useState("");
  const mapRef = useRef<KakaoMapHandle>(null);
  const searchPanelRef = useRef<HTMLElement>(null);
  const sheetDrag = useRef<{ pointerId: number; y: number; height: number } | null>(null);
  const skipSheetClick = useRef(false);
  const [sheetExpanded, setSheetExpanded] = useState(false);

  function finishSheetDrag(event: PointerEvent<HTMLButtonElement>, canceled = false) {
    const drag = sheetDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    sheetDrag.current = null;
    searchPanelRef.current?.style.removeProperty("--search-sheet-drag-height");
    const distance = drag.y - event.clientY;
    skipSheetClick.current = canceled || Math.abs(distance) > 12;
    if (!canceled && Math.abs(distance) > 24) setSheetExpanded(distance > 0);
  }
  const [previewTheme] = useState<PreviewTheme>(() => {
    // PRD 4.4 — 승인된 파란 기본 테마가 과거 비교 테마보다 우선한다.
    const theme = "retriever-blue";
    applyPreviewTheme(theme);
    return theme;
  });
  const [institutions, setInstitutions] = useState<Institution[]>([]);
  const [institutionsLoaded, setInstitutionsLoaded] = useState(false);
  const [institutionError, setInstitutionError] = useState("");
  const [countsAvailable, setCountsAvailable] = useState(false);
  const [countsLoading, setCountsLoading] = useState(false);
  const [places, setPlaces] = useState<Place[]>([]);
  const [placePage, setPlacePage] = useState(0);
  const [selectedPlace, setSelectedPlace] = useState<Place | null>(null);
  const [waypointPlaces, setWaypointPlaces] = useState<Place[]>([]);
  const [addingWaypoint, setAddingWaypoint] = useState(false);
  const searchCenters = useMemo(() => waypointPlaces.length
    ? waypointPlaces : selectedPlace ? [selectedPlace] : [], [selectedPlace, waypointPlaces]);
  const multiplePlaces = searchCenters.length > 1;
  const canAddWaypoint = waypointPlaces.length < MAX_WAYPOINT_PLACES;
  const [previewPlace, setPreviewPlace] = useState<Place | null>(null);
  const [radiusMeters, setRadiusMeters] = useState(DEFAULT_SEARCH_RADIUS_METERS);
  const manuallySetRadius = useRef(false);
  const [radiusNotice, setRadiusNotice] = useState("");
  const [locationStatus, setLocationStatus] = useState("장소명을 입력해 검색해보세요.");
  const [locationStatusIsError, setLocationStatusIsError] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [selectedInstitutionScope, setSelectedInstitutionScope] = useState<Institution[] | null>(null);
  const pagePlaces = useMemo(() => places.slice(placePage * PLACE_PAGE_SIZE, (placePage + 1) * PLACE_PAGE_SIZE), [places, placePage]);
  const placeNumbers = useMemo(() => pagePlaces.map((_, index) => placePage * PLACE_PAGE_SIZE + index + 1), [pagePlaces, placePage]);

  useEffect(() => {
    const controller = new AbortController();
    void fetchInstitutions(controller.signal, false)
      .then((payload) => {
        const nextInstitutions = (payload.institutions || []).map((institution) => ({
          ...institution,
          latitude: Number(institution.latitude),
          longitude: Number(institution.longitude),
        })).filter((institution) => (
          Number.isFinite(institution.latitude) && Number.isFinite(institution.longitude)
        ));
        setInstitutions(nextInstitutions);
        setCountsAvailable(payload.counts_available === true);
        setCountsLoading(payload.counts_available !== true && nextInstitutions.length > 0);
        setInstitutionsLoaded(true);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setInstitutionError(error instanceof Error ? error.message : "기관 위치를 불러오지 못했습니다.");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!countsLoading) return;
    const controller = new AbortController();
    void fetchInstitutions(controller.signal)
      .then(payload => {
        if (controller.signal.aborted || !payload.counts_available) return;
        const counts = new Map(payload.institutions?.map(institution => [institution.storage_location_id, institution.item_count]));
        function addCounts(locations: Institution[]) {
          return locations.map(location => {
            const itemCount = location.storage_location_id == null ? undefined : counts.get(location.storage_location_id);
            return itemCount === undefined ? location : { ...location, item_count: itemCount };
          });
        }
        setInstitutions(addCounts);
        setSelectedInstitutionScope(current => current ? addCounts(current) : null);
        setCountsAvailable(true);
      })
      .catch(error => {
        if (!controller.signal.aborted) console.warn("기관별 물품 수를 불러오지 못했습니다:", error);
      })
      .finally(() => {
        if (!controller.signal.aborted) setCountsLoading(false);
      });
    return () => controller.abort();
  }, [countsLoading]);

  const radiusScope = useMemo(() => (
    selectedPlace
      ? institutions.filter((institution) => (
        searchCenters.some(place => isWithinRadius(place, institution, radiusMeters))
      ))
      : []
  ), [institutions, radiusMeters, selectedPlace, searchCenters]);
  const visibleInstitutions = selectedPlace ? radiusScope : institutions;
  const activeScope = selectedInstitutionScope ?? radiusScope;
  const previousRadiusLocationIds = useMemo(() => {
    const previousRadius = previousSearchRadiusMeters(radiusMeters);
    if (!selectedPlace || previousRadius === null) return new Set<number>();
    return new Set(institutionLocationIds(radiusScope.filter(institution => (
      searchCenters.some(place => isWithinRadius(place, institution, previousRadius))
    ))));
  }, [selectedPlace, radiusMeters, radiusScope, searchCenters]);
  useEffect(() => {
    // PRD 4.5 — 사용자가 정한 범위는 유지하고, 초기 기관 조회가 0곳이면 자동 확장한다.
    if (!selectedPlace || manuallySetRadius.current || !institutionsLoaded || institutionError || radiusScope.length) return;
    const nextRadius = nextSearchRadiusMeters(radiusMeters);
    if (nextRadius === null) return;
    setRadiusMeters(nextRadius);
    setSelectedInstitutionScope(null);
    setRadiusNotice(`${radiusKilometersLabel(radiusMeters)} 안에 보관기관이 없어 ${radiusKilometersLabel(nextRadius)}로 넓혔어요.`);
  }, [selectedPlace, institutionsLoaded, institutionError, radiusScope.length, radiusMeters]);
  const selection = useMemo<SelectionSummaryValue | null>(() => {
    if (selectedInstitutionScope?.length) {
      const total = selectedInstitutionScope.reduce(
        (sum, institution) => sum + (Number(institution.item_count) || 0),
        0,
      );
      return {
        title: selectedInstitutionScope.length === 1
          ? `${selectedInstitutionScope[0].name} 보관 물품`
          : `이 위치의 ${selectedInstitutionScope.length}개 기관 보관 물품`,
        description: [selectedInstitutionScope[0].address, countsAvailable ? `보관 물품 ${total.toLocaleString("ko-KR")}개` : ""].filter(Boolean).join(" · "),
        institution: true,
      };
    }
    if (!selectedPlace) return null;
    return {
      title: multiplePlaces ? `${searchCenters.length}개 장소 주변 습득물` : `${selectedPlace.name} 주변 습득물`,
      description: multiplePlaces ? "선택한 장소별 반경의 결과를 모아 보여드려요." : selectedPlace.address,
    };
  }, [countsAvailable, multiplePlaces, searchCenters.length, selectedInstitutionScope, selectedPlace]);
  const activeScopeTitle = selection?.title ?? "주변 습득물";

  useEffect(() => {
    if (!selectedPlace) return;
    const resultPrefix = places.length > 1 ? `검색 결과 ${places.length}개 중 ` : "";
    setLocationStatus(
      `${resultPrefix}'${selectedPlace.name}' 기준 ${radiusKilometersLabel(radiusMeters)} 내 보관기관 ${radiusScope.length.toLocaleString("ko-KR")}곳입니다.`,
    );
    setLocationStatusIsError(false);
  }, [places.length, radiusMeters, radiusScope.length, selectedPlace]);

  const handlePlaceSelect = useCallback((place: Place) => {
    // PRD 4.3-A — 후보는 지도에서 미리 보고 명시적으로 확정한 뒤 물품을 조회한다.
    if (!addingWaypoint && waypointPlaces.includes(place)) return;
    setPreviewPlace(place);
    setSelectedPlace(null);
    if (!addingWaypoint) setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
    setRadiusNotice("");
    setSelectedInstitutionScope(null);
    setLocationStatusIsError(false);
    setLocationStatus("지도에서 위치를 확인한 뒤 선택해주세요.");
  }, [addingWaypoint, waypointPlaces]);

  function confirmPlace() {
    if (!previewPlace || isSearching) return;
    if (addingWaypoint && !canAddWaypoint) return;
    if (addingWaypoint && waypointPlaces.some(place => (
        place.latitude === previewPlace.latitude && place.longitude === previewPlace.longitude
    ))) {
      setLocationStatus("이미 추가한 위치예요. 다른 장소를 선택해주세요.");
      return;
    }
    setWaypointPlaces(addingWaypoint ? [...waypointPlaces, previewPlace] : [previewPlace]);
    setAddingWaypoint(false);
    if (!addingWaypoint) setAlertQuery("");
    if (!addingWaypoint) manuallySetRadius.current = false;
    setSelectedPlace(previewPlace);
    setPreviewPlace(null);
  }

  function startAddingWaypoint() {
    if (!canAddWaypoint) return;
    setAddingWaypoint(true);
    setSelectedPlace(null);
    setSelectedInstitutionScope(null);
    setPlaces([]);
    setPlacePage(0);
    setLocationStatusIsError(false);
    setLocationStatus("추가할 장소를 검색해주세요.");
  }

  function cancelAddingWaypoint() {
    setAddingWaypoint(false);
    setPreviewPlace(null);
    setSelectedPlace(waypointPlaces.at(-1) ?? null);
  }

  function removeWaypoint(index: number) {
    const remaining = waypointPlaces.filter((_, position) => position !== index);
    setWaypointPlaces(remaining);
    setSelectedInstitutionScope(null);
    if (!addingWaypoint && selectedPlace === waypointPlaces[index]) setSelectedPlace(remaining.at(-1) ?? null);
  }

  function changePlacePage(page: number) {
    // PRD 4.4 — 페이지 이동은 재검색 없이 후보만 바꾸며 미확정 선택은 해제한다.
    const lastPage = Math.max(0, Math.ceil(places.length / PLACE_PAGE_SIZE) - 1);
    if (isSearching || page === placePage || page < 0 || page > lastPage) return;
    setPlacePage(page);
    setPreviewPlace(null);
    setLocationStatus(`장소 후보 ${places.length.toLocaleString("ko-KR")}곳 · 위치를 확인해주세요.`);
  }

  const handleInstitutionSelect = useCallback((group: Institution[]) => {
    if (!group.length) return;
    if (addingWaypoint || (previewPlace && !selectedPlace)) return;
    setSelectedInstitutionScope(group);
  }, [addingWaypoint, previewPlace, selectedPlace]);

  async function handleSearch(query: string) {
    const center = mapRef.current?.getCenter();
    if (!center) {
      setLocationStatus("지도를 불러오는 중입니다. 잠시 후 다시 시도하세요.");
      return;
    }
    setIsSearching(true);
    setPreviewPlace(null);
    setLocationStatusIsError(false);
    setLocationStatus("카카오맵에서 위치를 찾고 있습니다…");
    try {
      const params = new URLSearchParams({
        query,
        x: center.longitude.toString(),
        y: center.latitude.toString(),
      });
      let payload = await searchPlaces(params);
      if (!(payload.documents || []).length) {
        payload = await geocodeAddress(query);
      }
      const nextPlaces = (payload.documents || [])
        .map(normalizePlace)
        .filter((place) => Number.isFinite(place.latitude) && Number.isFinite(place.longitude));
      setPlaces(nextPlaces);
      setPlacePage(0);
      setSelectedPlace(null);
      if (!addingWaypoint) setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
      setSelectedInstitutionScope(null);
      if (!nextPlaces.length) {
        setLocationStatus("검색 결과가 없습니다. 더 구체적인 장소명이나 주소를 입력해보세요.");
      } else {
        setLocationStatus(`장소 후보 ${nextPlaces.length.toLocaleString("ko-KR")}곳 · 위치를 확인해주세요.`);
      }
    } catch (error) {
      setPlaces([]);
      setSelectedPlace(null);
      if (!addingWaypoint) setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
      setSelectedInstitutionScope(null);
      setLocationStatusIsError(true);
      setLocationStatus(error instanceof Error ? error.message : "검색에 실패했습니다.");
    } finally {
      setIsSearching(false);
    }
  }

  function resetLocation() {
    const previousPlace = selectedPlace;
    setWaypointPlaces([]);
    setAddingWaypoint(false);
    setAlertQuery("");
    setSelectedPlace(null);
    setPreviewPlace(previousPlace);
    setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
    setSelectedInstitutionScope(null);
    setRadiusNotice("");
    setLocationStatus(places.length ? "장소를 다시 확인하거나 새로 검색해주세요." : "장소명을 입력해 검색해보세요.");
    setLocationStatusIsError(false);
  }

  function showRadiusItems() {
    if (!selectedPlace) return;
    setSelectedInstitutionScope(null);
  }

  function handleRadiusChange(nextRadiusMeters: number) {
    if (!selectedPlace) return;
    manuallySetRadius.current = true;
    setRadiusMeters(nextRadiusMeters);
    setRadiusNotice("");
    setSelectedInstitutionScope(null);
  }

  const nearbyItemCount = visibleInstitutions.reduce(
    (total, institution) => total + (Number(institution.item_count) || 0),
    0,
  );
  const mapScopeName = multiplePlaces ? `${searchCenters.length}개 장소` : selectedPlace?.name;
  const mapBadge = selectedPlace
    ? (visibleInstitutions.length
      ? `${mapScopeName} 기준 ${radiusKilometersLabel(radiusMeters)} · 보관기관 ${visibleInstitutions.length.toLocaleString("ko-KR")}곳${countsAvailable ? ` · 물품 ${nearbyItemCount.toLocaleString("ko-KR")}개` : ""}`
      : `${mapScopeName} 기준 ${radiusKilometersLabel(radiusMeters)} · 등록된 기관 없음`)
    : (institutionsLoaded
      ? "숫자를 누르면 확대 · 핀을 누르면 기관 정보"
      : "확대하면 개별 기관을 볼 수 있어요");

  return (
    <>
      {/* PRD 4.4 — 메뉴는 로고 옆에 유지한다. 후속 화면은 구현 후 연결한다. */}
      <header className="app-header">
        <a className="app-wordmark" href="/" aria-label="RETRIEVER 홈">RETRIEVER</a>
        <nav className="app-navigation" aria-label="주요 메뉴">
          <a className={`app-nav-link${alertIsOpen ? "" : " active"}`} href="#page-title" aria-current={alertIsOpen ? undefined : "page"}
            onClick={event => { if (alertIsOpen) { event.preventDefault(); void navigate(-1); } }}>물건 찾기</a>
          <button className={`app-nav-link${alertIsOpen ? " active" : ""}`} type="button" aria-current={alertIsOpen ? "page" : undefined}
            disabled={addingWaypoint}
            aria-haspopup="dialog" onClick={() => { if (!alertIsOpen) void navigate(`${location.pathname}${location.search}${location.hash}`, { state: { ...location.state, newItemAlert: true }, preventScrollReset: true }); }}>새 물건 알림</button>
        </nav>
        <button className="app-guide-link" type="button" disabled title="이용안내 화면 준비 중">이용안내</button>
      </header>
      <main className="app-shell">
        <section ref={searchPanelRef} className={`search-panel${selection ? " items-view" : places.length ? " has-place-results" : ""}`} data-expanded={sheetExpanded} aria-label="장소와 습득물 검색">
          <button
            type="button"
            className="search-sheet-handle"
            aria-label={sheetExpanded ? "검색 패널 줄이기" : "검색 패널 펼치기"}
            aria-controls="search-sheet-content"
            onPointerDown={(event) => {
              if (!event.isPrimary || event.button !== 0 || !searchPanelRef.current) return;
              skipSheetClick.current = false;
              sheetDrag.current = { pointerId: event.pointerId, y: event.clientY, height: searchPanelRef.current.offsetHeight };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              const drag = sheetDrag.current;
              if (drag?.pointerId !== event.pointerId) return;
              searchPanelRef.current?.style.setProperty("--search-sheet-drag-height", `${drag.height + drag.y - event.clientY}px`);
            }}
            onPointerUp={finishSheetDrag}
            onPointerCancel={(event) => finishSheetDrag(event, true)}
            onLostPointerCapture={(event) => finishSheetDrag(event, true)}
            onClick={(event) => {
              if (event.detail === 0 || !skipSheetClick.current) setSheetExpanded((expanded) => !expanded);
              skipSheetClick.current = false;
            }}
          ><span aria-hidden="true" /></button>
          <div id="search-sheet-content" className="search-sheet-content" onFocus={(event) => {
            if (event.target instanceof HTMLInputElement && event.target.type === "search"
              && window.matchMedia("(max-width: 820px)").matches) setSheetExpanded(true);
          }}>
          {waypointPlaces.length > 0 && (
            <section className="waypoint-picker" aria-label="선택한 장소">
              <details key={addingWaypoint ? "adding" : "selected"} className="waypoint-menu"
                onBlur={(event) => {
                  if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape" && event.currentTarget.open) {
                    event.currentTarget.open = false;
                    event.currentTarget.querySelector("summary")?.focus();
                  }
                }}>
                <summary>선택한 장소 {waypointPlaces.length}곳 <span aria-hidden="true">▾</span></summary>
                <div className="waypoint-menu-content">
                  <ol>
                    {waypointPlaces.map((place, index) => <li key={`${place.latitude}:${place.longitude}`}>
                      <span>{index + 1}. {place.name}</span>
                      {waypointPlaces.length > 1 && <button type="button" aria-label={`${place.name} 제거`} onClick={() => removeWaypoint(index)}>제거</button>}
                    </li>)}
                  </ol>
                  {!addingWaypoint && <p>{canAddWaypoint ? "추가로 입력하고 싶은 장소가 있나요?" : "장소는 최대 3곳까지 선택할 수 있어요."}</p>}
                </div>
              </details>
              {addingWaypoint
                ? <button type="button" disabled={isSearching} onClick={cancelAddingWaypoint}>추가 취소</button>
                : <button type="button" disabled={!canAddWaypoint} onClick={startAddingWaypoint}>+ 장소 추가</button>}
            </section>
          )}
          <LocationSearch
            key={addingWaypoint ? "waypoint" : "initial"}
            addingWaypoint={addingWaypoint}
            results={places}
            selectedPlace={previewPlace ?? selectedPlace}
            status={locationStatus}
            statusIsError={locationStatusIsError}
            isSearching={isSearching}
            onSearch={(query) => { void handleSearch(query); }}
            onSelect={handlePlaceSelect}
            onConfirm={confirmPlace}
            page={placePage}
            onPageChange={changePlacePage}
          />

          {selection && (
            <SelectionSummary
              selection={selection}
              onChangeLocation={resetLocation}
              radiusMeters={selectedPlace ? radiusMeters : undefined}
              notice={radiusNotice}
            />
          )}

          {selection && (
            <FoundItemBrowser
              initialQuery={alertQuery}
              scope={activeScope}
              previousRadiusLocationIds={previousRadiusLocationIds}
              scopeTitle={activeScopeTitle}
              canShowRadiusScope={selectedInstitutionScope !== null && selectedPlace !== null}
              radiusMeters={radiusMeters}
              onShowRadius={showRadiusItems}
              onRadiusChange={handleRadiusChange}
              canExpandRadius={selectedPlace !== null}
              onSubmittedQueryChange={setAlertQuery}
              onAlertRequest={() => { void navigate(`${location.pathname}${location.search}${location.hash}`, { state: { ...location.state, newItemAlert: true }, preventScrollReset: true }); }}
            />
          )}
          </div>
        </section>

        <KakaoMap
          themeKey={previewTheme}
          ref={mapRef}
          institutions={institutions}
          institutionsLoaded={institutionsLoaded}
          institutionError={institutionError}
          countsAvailable={countsAvailable}
          countsLoading={countsLoading}
          places={pagePlaces}
          placeNumbers={placeNumbers}
          selectedPlace={selectedPlace}
          searchCenters={searchCenters}
          previewPlace={previewPlace}
          radiusMeters={radiusMeters}
          visibleInstitutions={visibleInstitutions}
          mapBadge={mapBadge}
          onPlaceSelect={handlePlaceSelect}
          onInstitutionSelect={handleInstitutionSelect}
          onResetRadius={resetLocation}
        />
      </main>
      {alertIsOpen && <NewItemAlert places={selectedPlace ? searchCenters : []} query={alertQuery} radiusMeters={radiusMeters} onClose={() => { void navigate(-1); }} />}
    </>
  );
}
