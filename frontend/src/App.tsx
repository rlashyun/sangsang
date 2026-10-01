import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchInstitutions, geocodeAddress, searchPlaces } from "./api/client";
import { FoundItemBrowser } from "./components/FoundItemBrowser";
import { KakaoMap, type KakaoMapHandle } from "./components/KakaoMap";
import { LocationSearch } from "./components/LocationSearch";
import { SelectionSummary } from "./components/SelectionSummary";
import { ThemePreview } from "./components/ThemePreview";
import {
  DEFAULT_SEARCH_RADIUS_METERS,
  isWithinRadius,
  radiusKilometersLabel,
} from "./lib/geo.js";
import { normalizePlace } from "./lib/presentation";
import {
  applyPreviewTheme,
  readPreviewTheme,
  type PreviewTheme,
} from "./lib/theme";
import type { Institution, Place, SelectionSummary as SelectionSummaryValue } from "./types";

export function App() {
  const mapRef = useRef<KakaoMapHandle>(null);
  const [previewTheme, setPreviewTheme] = useState<PreviewTheme>(() => {
    const theme = import.meta.env.DEV ? readPreviewTheme() : "retriever-modern";
    if (import.meta.env.DEV) applyPreviewTheme(theme);
    return theme;
  });
  const [institutions, setInstitutions] = useState<Institution[]>([]);
  const [institutionsLoaded, setInstitutionsLoaded] = useState(false);
  const [institutionError, setInstitutionError] = useState("");
  const [countsAvailable, setCountsAvailable] = useState(false);
  const [places, setPlaces] = useState<Place[]>([]);
  const [selectedPlace, setSelectedPlace] = useState<Place | null>(null);
  const [radiusMeters, setRadiusMeters] = useState(DEFAULT_SEARCH_RADIUS_METERS);
  const [locationStatus, setLocationStatus] = useState("장소명을 입력해 검색해보세요.");
  const [locationStatusIsError, setLocationStatusIsError] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [selectedInstitutionScope, setSelectedInstitutionScope] = useState<Institution[] | null>(null);

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
      description: `${selectedPlace.address} · 반경 ${radiusKilometersLabel(radiusMeters)} 기관 ${radiusScope.length.toLocaleString("ko-KR")}곳`,
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
    setSelectedPlace(place);
    setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
    setSelectedInstitutionScope(null);
    setLocationStatusIsError(false);
  }, []);

  const handleInstitutionSelect = useCallback((group: Institution[]) => {
    if (!group.length) return;
    setSelectedInstitutionScope(group);
  }, []);

  async function handleSearch(query: string) {
    const center = mapRef.current?.getCenter();
    if (!center) {
      setLocationStatus("지도를 불러오는 중입니다. 잠시 후 다시 시도하세요.");
      return;
    }
    setIsSearching(true);
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
      setSelectedPlace(null);
      setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
      setSelectedInstitutionScope(null);
      if (!nextPlaces.length) {
        setLocationStatus("검색 결과가 없습니다. 더 구체적인 장소명이나 주소를 입력해보세요.");
      } else {
        setLocationStatus(`장소 후보 ${nextPlaces.length.toLocaleString("ko-KR")}개입니다. 주소를 확인하고 선택해주세요.`);
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
    setSelectedPlace(null);
    setPlaces([]);
    setRadiusMeters(DEFAULT_SEARCH_RADIUS_METERS);
    setSelectedInstitutionScope(null);
    setLocationStatus("전체 연계기관과 경찰청 관서를 표시하고 있습니다.");
    setLocationStatusIsError(false);
  }

  function showRadiusItems() {
    if (!selectedPlace) return;
    setSelectedInstitutionScope(null);
  }

  function handleRadiusChange(nextRadiusMeters: number) {
    if (!selectedPlace) return;
    setRadiusMeters(nextRadiusMeters);
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

  function handlePreviewThemeChange(theme: PreviewTheme) {
    applyPreviewTheme(theme);
    setPreviewTheme(theme);
  }

  return (
    <>
      <main className="app-shell">
        <section className={`search-panel${selection ? " items-view" : ""}`} aria-labelledby="page-title">
          <LocationSearch
            results={places}
            selectedPlace={selectedPlace}
            status={locationStatus}
            statusIsError={locationStatusIsError}
            isSearching={isSearching}
            onSearch={(query) => { void handleSearch(query); }}
            onSelect={handlePlaceSelect}
          />

          {selection && (
            <SelectionSummary
              selection={selection}
              onChangeLocation={resetLocation}
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
          places={places}
          selectedPlace={selectedPlace}
          radiusMeters={radiusMeters}
          visibleInstitutions={visibleInstitutions}
          mapBadge={mapBadge}
          onPlaceSelect={handlePlaceSelect}
          onInstitutionSelect={handleInstitutionSelect}
          onResetRadius={resetLocation}
        />
      </main>
      <ThemePreview theme={previewTheme} onChange={handlePreviewThemeChange} />
    </>
  );
}
