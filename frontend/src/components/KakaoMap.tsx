import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";

import { distanceMeters, markerCountLabel, SEARCH_RADIUS_METERS } from "../lib/geo.js";
import { escapeHtml } from "../lib/presentation";
import {
  readMapColorTokens,
  type MapColorTokens,
  type PreviewTheme,
} from "../lib/theme";
import type { Institution, Place } from "../types";

declare global {
  interface Window {
    kakao: any;
  }
}

const SEOUL_CENTER = { latitude: 37.5665, longitude: 126.9780 };

export interface KakaoMapHandle {
  getCenter: () => { latitude: number; longitude: number } | null;
}

interface KakaoMapProps {
  themeKey: PreviewTheme;
  institutions: Institution[];
  institutionsLoaded: boolean;
  institutionError: string;
  countsAvailable: boolean;
  places: Place[];
  selectedPlace: Place | null;
  visibleInstitutions: Institution[];
  mapBadge: string;
  onPlaceSelect: (place: Place) => void;
  onInstitutionSelect: (institutions: Institution[]) => void;
  onResetRadius: () => void;
}

function coordinateKey(institution: Institution) {
  return `${institution.latitude.toFixed(7)},${institution.longitude.toFixed(7)}`;
}

function clusterStyles(colors: MapColorTokens) {
  return colors.clusterFills.map((color, index) => {
    const rawSize = [42, 48, 56, 64, 72][index];
    const size = Number(rawSize);
    return {
      width: `${size}px`,
      height: `${size}px`,
      background: `${color}e8`,
      border: `2px solid ${colors.clusterBorder}`,
      borderRadius: "50%",
      color: colors.textOnSolid,
      fontSize: size >= 64 ? "18px" : "15px",
      fontWeight: "800",
      lineHeight: `${size - 4}px`,
      textAlign: "center",
      boxShadow: `0 5px 16px ${colors.clusterShadow}`,
    };
  });
}

function institutionDetails(
  institutions: Institution[],
  activePlace: Place | null,
  countsAvailable: boolean,
) {
  const visible = institutions.slice(0, 8);
  const items = visible.map((institution) => {
    const distance = activePlace
      ? `${Math.round(distanceMeters(activePlace, institution)).toLocaleString("ko-KR")}m`
      : "";
    const source = institution.source === "police" ? "경찰청" : "연계기관";
    const itemCount = countsAvailable
      ? `보관 물품 ${(Number(institution.item_count) || 0).toLocaleString("ko-KR")}개`
      : "";
    const meta = [distance, source, itemCount, institution.organization, institution.phone]
      .filter((value, index, values) => value && values.indexOf(value) === index)
      .join(" · ");
    return `<li><strong>${escapeHtml(institution.name)}</strong>`
      + `<span>${escapeHtml(institution.address)}</span>`
      + (meta ? `<small>${escapeHtml(meta)}</small>` : "")
      + "</li>";
  }).join("");
  const remainder = institutions.length - visible.length;
  const title = institutions.length === 1
    ? (institutions[0].source === "police" ? "경찰청 관서" : "연계기관")
    : `같은 위치의 보관기관 ${institutions.length}곳`;
  return `<div class="institution-window">`
    + `<div class="institution-window-title">${title}</div>`
    + `<ul>${items}</ul>`
    + (remainder > 0 ? `<p>외 ${remainder}곳</p>` : "")
    + "</div>";
}

function circleBounds(kakao: any, center: Place) {
  const latitudeDelta = SEARCH_RADIUS_METERS / 111320;
  const longitudeScale = Math.max(0.1, Math.cos(center.latitude * Math.PI / 180));
  const longitudeDelta = SEARCH_RADIUS_METERS / (111320 * longitudeScale);
  const bounds = new kakao.maps.LatLngBounds();
  bounds.extend(new kakao.maps.LatLng(
    center.latitude - latitudeDelta,
    center.longitude - longitudeDelta,
  ));
  bounds.extend(new kakao.maps.LatLng(
    center.latitude + latitudeDelta,
    center.longitude + longitudeDelta,
  ));
  return bounds;
}

export const KakaoMap = forwardRef<KakaoMapHandle, KakaoMapProps>(function KakaoMap({
  themeKey,
  institutions,
  institutionsLoaded,
  institutionError,
  countsAvailable,
  places,
  selectedPlace,
  visibleInstitutions,
  mapBadge,
  onPlaceSelect,
  onInstitutionSelect,
  onResetRadius,
}, forwardedRef) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const infoWindowRef = useRef<any>(null);
  const clustererRef = useRef<any>(null);
  const institutionMarkersRef = useRef<any[]>([]);
  const searchMarkersRef = useRef<Array<{ marker: any; place: Place }>>([]);
  const radiusCircleRef = useRef<any>(null);
  const countMarkerImagesRef = useRef(new Map<string, any>());
  const selectedPlaceRef = useRef<Place | null>(selectedPlace);
  const [mapReady, setMapReady] = useState(false);
  const mapColors = useMemo(() => readMapColorTokens(), [themeKey]);

  selectedPlaceRef.current = selectedPlace;

  useImperativeHandle(forwardedRef, () => ({
    getCenter() {
      if (!mapRef.current) return null;
      const center = mapRef.current.getCenter();
      return { latitude: center.getLat(), longitude: center.getLng() };
    },
  }), []);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    const kakao = window.kakao;
    if (!kakao?.maps) return;
    kakao.maps.load(() => {
      if (cancelled || !containerRef.current || mapRef.current) return;
      const map = new kakao.maps.Map(containerRef.current, {
        center: new kakao.maps.LatLng(SEOUL_CENTER.latitude, SEOUL_CENTER.longitude),
        level: 8,
      });
      map.addControl(new kakao.maps.ZoomControl(), kakao.maps.ControlPosition.RIGHT);
      map.addControl(new kakao.maps.MapTypeControl(), kakao.maps.ControlPosition.TOPRIGHT);
      mapRef.current = map;
      infoWindowRef.current = new kakao.maps.InfoWindow({ removable: true });
      setMapReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const kakao = window.kakao;
    const map = mapRef.current;
    countMarkerImagesRef.current.clear();
    clustererRef.current?.clear();
    institutionMarkersRef.current.forEach((marker) => marker.setMap(null));

    const groups = new Map<string, Institution[]>();
    institutions.forEach((institution) => {
      const key = coordinateKey(institution);
      const group = groups.get(key) || [];
      group.push(institution);
      groups.set(key, group);
    });

    function fallbackMarkerImage(source: "partner" | "police") {
      const pinColor = source === "police" ? mapColors.pinPolice : mapColors.pinPartner;
      const shadowColor = source === "police"
        ? mapColors.pinPoliceShadow
        : mapColors.pinPartnerShadow;
      const iconPath = source === "police"
        ? "M19 10.5 28 15v2H10v-2l9-4.5ZM12 19h3v7h-3v-7Zm5.5 0h3v7h-3v-7Zm5.5 0h3v7h-3v-7ZM10 28h18v3H10v-3Z"
        : "M11 14.5a2.5 2.5 0 0 1 2.5-2.5h11a2.5 2.5 0 0 1 2.5 2.5V27h-3v-3h-3.5v3h-3v-3H14v3h-3V14.5Zm4 1v3h3v-3h-3Zm5 0v3h3v-3h-3Z";
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="38" height="46" viewBox="0 0 38 46">`
        + `<filter id="s" x="-30%" y="-20%" width="160%" height="160%"><feDropShadow dx="0" dy="3" stdDeviation="2.5" flood-color="${shadowColor}" flood-opacity="${source === "police" ? ".3" : ".28"}"/></filter>`
        + `<path filter="url(#s)" fill="${pinColor}" stroke="${mapColors.textOnSolid}" stroke-width="3" d="M19 1.5C9.06 1.5 1 9.56 1 19.5c0 12.28 15.65 24.08 16.31 24.58a2.82 2.82 0 0 0 3.38 0C21.35 43.58 37 31.78 37 19.5c0-9.94-8.06-18-18-18Z"/>`
        + `<path fill="${mapColors.textOnSolid}" d="${iconPath}"/>`
        + "</svg>";
      return new kakao.maps.MarkerImage(
        `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
        new kakao.maps.Size(38, 46),
        { offset: new kakao.maps.Point(19, 46) },
      );
    }

    const fallbackImages = {
      partner: fallbackMarkerImage("partner"),
      police: fallbackMarkerImage("police"),
    };

    function countMarkerImage(source: string | undefined, rawItemCount: number | undefined) {
      const label = markerCountLabel(rawItemCount);
      const cacheKey = `${source}:${label}`;
      const cached = countMarkerImagesRef.current.get(cacheKey);
      if (cached) return cached;
      const pinColor = source === "police" ? mapColors.pinPolice : mapColors.pinPartner;
      const badgeWidth = label.length >= 4 ? 32 : label.length === 3 ? 27 : 23;
      const badgeX = 55 - badgeWidth;
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="58" height="62" viewBox="0 0 58 62">`
        + `<filter id="s" x="-30%" y="-25%" width="170%" height="180%"><feDropShadow dx="0" dy="3" stdDeviation="2.4" flood-color="${mapColors.pinShadow}" flood-opacity=".25"/></filter>`
        + `<g filter="url(#s)"><path d="M28 4C16.4 4 7 13.4 7 25c0 16 21 33 21 33s21-17 21-33C49 13.4 39.6 4 28 4Z" fill="${pinColor}" stroke="${mapColors.textOnSolid}" stroke-width="3"/>`
        + `<circle cx="28" cy="25" r="9.5" fill="${mapColors.textOnSolid}" fill-opacity=".96"/>`
        + `<path d="M22 21.5h12v7H22zM24 19h8v3h-8z" fill="${pinColor}"/></g>`
        + `<rect x="${badgeX}" y="1" width="${badgeWidth}" height="23" rx="11.5" fill="${mapColors.pinBadge}" stroke="${mapColors.textOnSolid}" stroke-width="2"/>`
        + `<text x="${badgeX + badgeWidth / 2}" y="16.2" fill="${mapColors.textOnSolid}" font-family="Arial,sans-serif" font-size="11.5" font-weight="700" text-anchor="middle">${label}</text>`
        + "</svg>";
      const image = new kakao.maps.MarkerImage(
        `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
        new kakao.maps.Size(58, 62),
        { offset: new kakao.maps.Point(28, 60) },
      );
      countMarkerImagesRef.current.set(cacheKey, image);
      return image;
    }

    const markers = institutions.map((institution) => {
      const image = countsAvailable
        ? countMarkerImage(institution.source, institution.item_count)
        : (institution.source === "police" ? fallbackImages.police : fallbackImages.partner);
      const marker = new kakao.maps.Marker({
        position: new kakao.maps.LatLng(institution.latitude, institution.longitude),
        image,
        title: `${institution.name}${countsAvailable ? ` · 보관 물품 ${institution.item_count || 0}개` : ""}`,
        clickable: true,
      });
      const group = groups.get(coordinateKey(institution)) || [institution];
      kakao.maps.event.addListener(marker, "click", () => {
        infoWindowRef.current.setContent(institutionDetails(
          group,
          selectedPlaceRef.current,
          countsAvailable,
        ));
        infoWindowRef.current.open(map, marker);
        onInstitutionSelect(group);
      });
      return marker;
    });

    institutionMarkersRef.current = markers;
    clustererRef.current = new kakao.maps.MarkerClusterer({
      map,
      markers,
      averageCenter: true,
      minLevel: 6,
      minClusterSize: 2,
      gridSize: 70,
      calculator: [10, 50, 100, 300],
      styles: clusterStyles(mapColors),
    });

    return () => {
      clustererRef.current?.clear();
      markers.forEach((marker) => marker.setMap(null));
    };
  }, [countsAvailable, institutions, mapColors, mapReady, onInstitutionSelect]);

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const kakao = window.kakao;
    searchMarkersRef.current.forEach(({ marker }) => marker.setMap(null));
    const nextMarkers = places.map((place) => {
      const marker = new kakao.maps.Marker({
        map: mapRef.current,
        position: new kakao.maps.LatLng(place.latitude, place.longitude),
        title: place.name,
      });
      kakao.maps.event.addListener(marker, "click", () => onPlaceSelect(place));
      return { marker, place };
    });
    searchMarkersRef.current = nextMarkers;

    if (places.length) {
      const bounds = new kakao.maps.LatLngBounds();
      places.forEach((place) => bounds.extend(new kakao.maps.LatLng(place.latitude, place.longitude)));
      mapRef.current.setBounds(bounds, 48, 48, 48, 48);
    }

    return () => nextMarkers.forEach(({ marker }) => marker.setMap(null));
  }, [mapReady, onPlaceSelect, places]);

  useEffect(() => {
    if (!mapReady || !mapRef.current || !clustererRef.current) return;
    const kakao = window.kakao;
    const map = mapRef.current;
    if (radiusCircleRef.current) {
      radiusCircleRef.current.setMap(null);
      radiusCircleRef.current = null;
    }
    clustererRef.current.clear();

    if (!selectedPlace) {
      if (institutionMarkersRef.current.length) {
        clustererRef.current.addMarkers(institutionMarkersRef.current);
      }
      searchMarkersRef.current.forEach(({ marker }) => marker.setMap(map));
      infoWindowRef.current?.close();
      return;
    }

    const nearbyMarkers = institutions.flatMap((institution, index) => (
      distanceMeters(selectedPlace, institution) <= SEARCH_RADIUS_METERS
        ? [institutionMarkersRef.current[index]]
        : []
    )).filter(Boolean);
    if (nearbyMarkers.length) clustererRef.current.addMarkers(nearbyMarkers);
    searchMarkersRef.current.forEach(({ marker, place }) => {
      marker.setMap(place === selectedPlace ? map : null);
    });

    radiusCircleRef.current = new kakao.maps.Circle({
      map,
      center: new kakao.maps.LatLng(selectedPlace.latitude, selectedPlace.longitude),
      radius: SEARCH_RADIUS_METERS,
      strokeWeight: 2,
      strokeColor: mapColors.radiusStroke,
      strokeOpacity: 0.55,
      strokeStyle: "solid",
      fillColor: mapColors.radiusFill,
      fillOpacity: 0.1,
      zIndex: 1,
    });
    const selectedMarker = searchMarkersRef.current.find(({ place }) => place === selectedPlace)?.marker;
    if (selectedMarker) {
      infoWindowRef.current.setContent(
        `<div class="info-window"><strong>${escapeHtml(selectedPlace.name)}</strong>`
        + `<span>${escapeHtml([selectedPlace.address, selectedPlace.phone].filter(Boolean).join(" · "))}</span></div>`,
      );
      infoWindowRef.current.open(map, selectedMarker);
    }
    map.setBounds(circleBounds(kakao, selectedPlace), 48, 48, 48, 48);
  }, [institutions, mapColors, mapReady, selectedPlace]);

  const sourceCounts = useMemo(() => {
    const partner = visibleInstitutions.filter((item) => item.source !== "police").length;
    return { partner, police: visibleInstitutions.length - partner };
  }, [visibleInstitutions]);

  return (
    <section className="map-panel" aria-label="카카오 지도">
      <div id="map" ref={containerRef} />
      <div
        className={`institution-status${institutionError ? " error" : institutionsLoaded ? " loaded" : ""}${selectedPlace ? " filtered" : ""}`}
        aria-live="polite"
      >
        {!institutionsLoaded && !institutionError && (
          <><span className="status-dot" />연계기관과 경찰청 위치를 불러오는 중…</>
        )}
        {institutionError && <>기관 위치 로딩 실패: {institutionError}</>}
        {institutionsLoaded && !institutionError && (
          <>
            <span className="status-dot" />
            {selectedPlace && <b>반경 1km</b>}
            <span className="source-count"><span className="source-dot partner" />연계기관 <strong>{sourceCounts.partner.toLocaleString("ko-KR")}곳</strong></span>
            <span className="source-count"><span className="source-dot police" />경찰청 <strong>{sourceCounts.police.toLocaleString("ko-KR")}곳</strong></span>
            {!countsAvailable && <span className="count-warning">물품 수 미연결</span>}
          </>
        )}
      </div>
      <div className="map-toolbar">
        <div className="map-badge">{mapBadge}</div>
        {selectedPlace && (
          <button className="reset-radius" type="button" onClick={onResetRadius}>
            전체 기관 보기
          </button>
        )}
      </div>
    </section>
  );
});
