import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
} from "./lib/geo.js";
import { normalizePlace } from "./lib/presentation";
import {
  applyPreviewTheme,
  type PreviewTheme,
} from "./lib/theme";
import type { Institution, Place, SelectionSummary as SelectionSummaryValue } from "./types";

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const alertIsOpen = location.state?.newItemAlert === true;
  const [alertQuery, setAlertQuery] = useState("");
  const mapRef = useRef<KakaoMapHandle>(null);
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
  const [places, setPlaces] = useState<Place[]>([]);
  const [placePage, setPlacePage] = useState(0);
  const [selectedPlace, setSelectedPlace] = useState<Place | null>(null);
  useEffect(() => { setAlertQuery(""); }, [selectedPlace]);
  const [previewPlace, setPreviewPlace] = useState<Place | null>(null);
  const [radiusMeters, setRadiusMeters] = useState(DEFAULT_SEARCH_RADIUS_METERS);
  const [radiusNotice, setRadiusNotice] = useState("");
  const [locationStatus, setLocationStatus] = useState("장소명을 입력해 검색해보세요.");
  const [locationStatusIsError, setLocationStatusIsError] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [selectedInstitutionScope, setSelectedInstitutionScope] = useState<Institution[] | null>(null);
  const pagePlaces = useMemo(() => places.slice(placePage * PLACE_PAGE_SIZE, (placePage + 1) * PLACE_PAGE_SIZE), [places, placePage]);
  const placeNumbers = useMemo(() => pagePlaces.map((_, index) => placePage * PLACE_PAGE_SIZE + index + 1), [pagePlaces, placePage]);

  useEffect(() => {
    const controller = new AbortController();
    void fetchInstitutions(controller.signal)
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
        setInstitutionsLoaded(true);
        if (payload.count_error) {
          console.warn("기관별 물품 수를 불러오지 못했습니다:", payload.count_error);
        }
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setInstitutionError(error instanceof Error ? error.message : "기관 위치를 불러오지 못했습니다.");
      });
    return () => controller.abort();
  }, []);

  const radiusScope = useMemo(() => (
    selectedPlace
      ? institutions.filter((institution) => (
        isWithinRadius(selectedPlace, institution, radiusMeters)
      ))
      : []
  ), [institutions, radiusMeters, selectedPlace]);
  const visibleInstitutions = selectedPlace ? radiusScope : institutions;
  const activeScope = selectedInstitutionScope ?? radiusScope;
  useEffect(() => {
    // PRD 4.5 — 기관 조회가 정상 완료되고 0곳일 때만 1→2→3km로 확장한다.
    if (!selectedPlace || !institutionsLoaded || institutionError || radiusScope.length) return;
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
        description: `${selectedInstitutionScope[0].address} · 보관 물품 ${total.toLocaleString("ko-KR")}개`,
        institution: true,
      };
    }
    if (!selectedPlace) return null;
    return {
      title: `${selectedPlace.name} 주변 습득물`,
      description: selectedPlace.address,
    };
  }, [radiusMeters, radiusScope.length, selectedInstitutionScope, selectedPlace]);
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
    setPreviewPlace(place);
    setSelectedPlace(null);
    setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
    setRadiusNotice("");
    setSelectedInstitutionScope(null);
    setLocationStatusIsError(false);
    setLocationStatus("지도에서 위치를 확인한 뒤 선택해주세요.");
  }, []);

  function confirmPlace() {
    if (!previewPlace || isSearching) return;
    setSelectedPlace(previewPlace);
    setPreviewPlace(null);
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
    if (previewPlace && !selectedPlace) return;
    setSelectedInstitutionScope(group);
  }, [previewPlace, selectedPlace]);

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
      setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
      setSelectedInstitutionScope(null);
      if (!nextPlaces.length) {
        setLocationStatus("검색 결과가 없습니다. 더 구체적인 장소명이나 주소를 입력해보세요.");
      } else {
        setLocationStatus(`장소 후보 ${nextPlaces.length.toLocaleString("ko-KR")}곳 · 위치를 확인해주세요.`);
      }
    } catch (error) {
      setPlaces([]);
      setSelectedPlace(null);
      setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
      setSelectedInstitutionScope(null);
      setLocationStatusIsError(true);
      setLocationStatus(error instanceof Error ? error.message : "검색에 실패했습니다.");
    } finally {
      setIsSearching(false);
    }
  }

  function resetLocation() {
    const previousPlace = selectedPlace;
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
    setRadiusMeters(nextRadiusMeters);
    setRadiusNotice("");
    setSelectedInstitutionScope(null);
  }

  const nearbyItemCount = visibleInstitutions.reduce(
    (total, institution) => total + (Number(institution.item_count) || 0),
    0,
  );
  const mapBadge = selectedPlace
    ? (visibleInstitutions.length
      ? `${selectedPlace.name} 기준 ${radiusKilometersLabel(radiusMeters)} · 보관기관 ${visibleInstitutions.length.toLocaleString("ko-KR")}곳${countsAvailable ? ` · 물품 ${nearbyItemCount.toLocaleString("ko-KR")}개` : ""}`
      : `${selectedPlace.name} 기준 ${radiusKilometersLabel(radiusMeters)} · 등록된 기관 없음`)
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
            aria-haspopup="dialog" onClick={() => { if (!alertIsOpen) void navigate(`${location.pathname}${location.search}${location.hash}`, { state: { ...location.state, newItemAlert: true }, preventScrollReset: true }); }}>새 물건 알림</button>
        </nav>
        <button className="app-guide-link" type="button" disabled title="이용안내 화면 준비 중">이용안내</button>
      </header>
      <main className="app-shell">
        <section className={`search-panel${selection ? " items-view" : places.length ? " has-place-results" : ""}`} aria-labelledby="page-title">
          <LocationSearch
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
              scope={activeScope}
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
        </section>

        <KakaoMap
          themeKey={previewTheme}
          ref={mapRef}
          institutions={institutions}
          institutionsLoaded={institutionsLoaded}
          institutionError={institutionError}
          countsAvailable={countsAvailable}
          places={pagePlaces}
          placeNumbers={placeNumbers}
          selectedPlace={selectedPlace}
          previewPlace={previewPlace}
          radiusMeters={radiusMeters}
          visibleInstitutions={visibleInstitutions}
          mapBadge={mapBadge}
          onPlaceSelect={handlePlaceSelect}
          onInstitutionSelect={handleInstitutionSelect}
          onResetRadius={resetLocation}
        />
      </main>
      {alertIsOpen && <NewItemAlert place={selectedPlace} query={alertQuery} radiusMeters={radiusMeters} onClose={() => { void navigate(-1); }} />}
    </>
  );
}
