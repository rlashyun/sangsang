import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchInstitutions, geocodeAddress, searchPlaces } from "./api/client";
import { FoundItemBrowser } from "./components/FoundItemBrowser";
import { KakaoMap, type KakaoMapHandle } from "./components/KakaoMap";
import { LocationSearch } from "./components/LocationSearch";
import { SelectionSummary } from "./components/SelectionSummary";
import { distanceMeters, SEARCH_RADIUS_METERS } from "./lib/geo.js";
import { normalizePlace } from "./lib/presentation";
import type { Institution, Place, SelectionSummary as SelectionSummaryValue } from "./types";

export function App() {
  const mapRef = useRef<KakaoMapHandle>(null);
  const [institutions, setInstitutions] = useState<Institution[]>([]);
  const [institutionsLoaded, setInstitutionsLoaded] = useState(false);
  const [institutionError, setInstitutionError] = useState("");
  const [countsAvailable, setCountsAvailable] = useState(false);
  const [places, setPlaces] = useState<Place[]>([]);
  const [selectedPlace, setSelectedPlace] = useState<Place | null>(null);
  const [locationStatus, setLocationStatus] = useState("장소명을 입력해 검색해보세요.");
  const [locationStatusIsError, setLocationStatusIsError] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [selection, setSelection] = useState<SelectionSummaryValue | null>(null);
  const [radiusScope, setRadiusScope] = useState<Institution[]>([]);
  const [activeScope, setActiveScope] = useState<Institution[]>([]);
  const [activeScopeTitle, setActiveScopeTitle] = useState("주변 습득물");

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

  const visibleInstitutions = useMemo(() => (
    selectedPlace
      ? institutions.filter((institution) => (
        distanceMeters(selectedPlace, institution) <= SEARCH_RADIUS_METERS
      ))
      : institutions
  ), [institutions, selectedPlace]);

  const handlePlaceSelect = useCallback((place: Place) => {
    const nearby = institutions.filter((institution) => (
      distanceMeters(place, institution) <= SEARCH_RADIUS_METERS
    ));
    setSelectedPlace(place);
    setRadiusScope(nearby);
    setActiveScope(nearby);
    const title = `${place.name} 주변 습득물`;
    setActiveScopeTitle(title);
    setSelection({
      title,
      description: `${place.address} · 반경 1km 기관 ${nearby.length.toLocaleString("ko-KR")}곳`,
    });
    setLocationStatus(places.length > 1
      ? `검색 결과 ${places.length}개 중 '${place.name}' 기준 1km 내 보관기관 ${nearby.length}곳입니다.`
      : `'${place.name}' 기준 1km 내 보관기관 ${nearby.length}곳입니다.`);
    setLocationStatusIsError(false);
  }, [institutions, places.length]);

  const handleInstitutionSelect = useCallback((group: Institution[]) => {
    const title = group.length === 1
      ? `${group[0].name} 보관 물품`
      : `이 위치의 ${group.length}개 기관 보관 물품`;
    const total = group.reduce((sum, item) => sum + (Number(item.item_count) || 0), 0);
    setActiveScope(group);
    setActiveScopeTitle(title);
    setSelection({
      title,
      description: `${group[0].address} · 보관 물품 ${total.toLocaleString("ko-KR")}개`,
      institution: true,
    });
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
      setRadiusScope([]);
      setActiveScope([]);
      setSelection(null);
      if (!nextPlaces.length) {
        setLocationStatus("검색 결과가 없습니다. 더 구체적인 장소명이나 주소를 입력해보세요.");
      } else {
        setLocationStatus(`장소 후보 ${nextPlaces.length.toLocaleString("ko-KR")}개입니다. 주소를 확인하고 선택해주세요.`);
      }
    } catch (error) {
      setPlaces([]);
      setSelectedPlace(null);
      setRadiusScope([]);
      setActiveScope([]);
      setSelection(null);
      setLocationStatusIsError(true);
      setLocationStatus(error instanceof Error ? error.message : "검색에 실패했습니다.");
    } finally {
      setIsSearching(false);
    }
  }

  function resetLocation() {
    setSelectedPlace(null);
    setPlaces([]);
    setSelection(null);
    setRadiusScope([]);
    setActiveScope([]);
    setActiveScopeTitle("주변 습득물");
    setLocationStatus("전체 연계기관과 경찰청 관서를 표시하고 있습니다.");
    setLocationStatusIsError(false);
  }

  function showRadiusItems() {
    if (!selectedPlace || !radiusScope.length) return;
    const title = `${selectedPlace.name} 주변 습득물`;
    setActiveScope(radiusScope);
    setActiveScopeTitle(title);
    setSelection({
      title,
      description: `${selectedPlace.address} · 반경 1km 전체 기관의 물품`,
    });
  }

  const nearbyItemCount = visibleInstitutions.reduce(
    (total, institution) => total + (Number(institution.item_count) || 0),
    0,
  );
  const mapBadge = selectedPlace
    ? (visibleInstitutions.length
      ? `${selectedPlace.name} 기준 1km · 보관기관 ${visibleInstitutions.length.toLocaleString("ko-KR")}곳${countsAvailable ? ` · 물품 ${nearbyItemCount.toLocaleString("ko-KR")}개` : ""}`
      : `${selectedPlace.name} 기준 1km · 등록된 기관 없음`)
    : (institutionsLoaded
      ? "숫자를 누르면 확대 · 핀을 누르면 기관 정보"
      : "확대하면 개별 기관을 볼 수 있어요");

  return (
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
          <SelectionSummary selection={selection} onChangeLocation={resetLocation} />
        )}

        {selection && (
          <FoundItemBrowser
            scope={activeScope}
            scopeTitle={activeScopeTitle}
            radiusScope={radiusScope}
            onShowRadius={showRadiusItems}
          />
        )}
      </section>

      <KakaoMap
        ref={mapRef}
        institutions={institutions}
        institutionsLoaded={institutionsLoaded}
        institutionError={institutionError}
        countsAvailable={countsAvailable}
        places={places}
        selectedPlace={selectedPlace}
        visibleInstitutions={visibleInstitutions}
        mapBadge={mapBadge}
        onPlaceSelect={handlePlaceSelect}
        onInstitutionSelect={handleInstitutionSelect}
        onResetRadius={resetLocation}
      />
    </main>
  );
}
