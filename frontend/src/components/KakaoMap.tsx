// @refresh reset
// PRD 4.4 — SDK 컨트롤 변경은 지도 DOM도 재생성해 개발 중 남은 기본 UI를 제거한다.
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  distanceMeters,
  markerCountLabel,
  radiusKilometersLabel,
} from "../lib/geo.js";
import { escapeHtml } from "../lib/presentation";
import { createRadiusHalo } from "../lib/radius-halo.js";
import {
  readMapColorTokens,
  type MapColorTokens,
  type PreviewTheme,
} from "../lib/theme";
import type { Institution, Place } from "../types";
import { MapControls } from "./MapControls";

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
  placeNumbers: number[];
  selectedPlace: Place | null;
  previewPlace: Place | null;
  radiusMeters: number;
  visibleInstitutions: Institution[];
  mapBadge: string;
  onPlaceSelect: (place: Place) => void;
  onInstitutionSelect: (institutions: Institution[]) => void;
  onResetRadius: () => void;
}

function coordinateKey(institution: Institution) {
  return `${institution.latitude.toFixed(7)},${institution.longitude.toFixed(7)}`;
}

function compactMarkerImage(kakao: any, mapColors: MapColorTokens, source: string | undefined, selected = false, number?: number) {
  const police = source === "police";
  const color = police ? mapColors.pinPolice : mapColors.pinPartner;
  const width = 28;
  const height = 36;
  // PRD 4.4 — 범례와 같은 색을 사용하고 클러스터보다 진하게 표시한다.
  const pinPath = "M15 3C8.9 3 4 7.9 4 14c0 8.2 11 18 11 18s11-9.8 11-18C26 7.9 21.1 3 15 3Z";
  const innerShape = number !== undefined
    ? `<text x="15" y="18" text-anchor="middle" font-family="Arial,sans-serif" font-size="11" font-weight="700" fill="${mapColors.textOnSolid}">${number}</text>`
    : police
    ? `<path d="M15 9l4 2v3c0 2-2 3.5-4 4.5-2-1-4-2.5-4-4.5v-3z" fill="${mapColors.pinShadow}" fill-opacity=".65"/>`
    : `<circle cx="15" cy="14" r="3" fill="${mapColors.pinShadow}" fill-opacity=".65"/>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 30 38">`
    + `<filter id="soft-shadow" x="-40%" y="-30%" width="180%" height="180%"><feDropShadow dx="0" dy="1" stdDeviation="1" flood-color="${mapColors.pinShadow}" flood-opacity=".16"/></filter>`
    + (selected ? `<circle cx="15" cy="15" r="14" fill="${color}" fill-opacity=".2"/>` : "")
    + `<path d="${pinPath}" fill="${mapColors.textOnSolid}" filter="url(#soft-shadow)"/>`
    + `<path d="${pinPath}" fill="${color}" fill-opacity="1" stroke="${mapColors.textOnSolid}" stroke-width="${selected ? 2.5 : 1.5}"/>`
    + innerShape + "</svg>";
  return new kakao.maps.MarkerImage(
    `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    new kakao.maps.Size(width, height),
    { offset: new kakao.maps.Point(14, 30) },
  );
}

function placePreviewMarkerImage(kakao: any, colors: MapColorTokens) {
  // PRD 4.3-A — 확인 중인 장소는 기존 기관 핀보다 큰 흰색 핀으로 구분한다.
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="38" height="48" viewBox="0 0 38 48">`
    + `<filter id="s" x="-40%" y="-30%" width="180%" height="180%"><feDropShadow dx="0" dy="2" stdDeviation="1.8" flood-color="${colors.pinShadow}" flood-opacity=".22"/></filter>`
    + `<path d="M19 3C10.7 3 4 9.7 4 18c0 11 15 25 15 25s15-14 15-25C34 9.7 27.3 3 19 3Z" fill="${colors.textOnSolid}" stroke="${colors.pinPartner}" stroke-width="1.5" filter="url(#s)"/>`
    + `<circle cx="19" cy="18" r="5" fill="${colors.pinPartner}"/>`
    + "</svg>";
  return new kakao.maps.MarkerImage(
    `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    new kakao.maps.Size(38, 48),
    { offset: new kakao.maps.Point(19, 43) },
  );
}

function clusterStyles(colors: MapColorTokens, softened: boolean) {
  return colors.clusterFills.map((color, index) => {
    const rawSize = softened ? 42 : [42, 48, 56, 64, 72][index];
    const size = Number(rawSize);
    return {
      width: `${size}px`,
      height: `${size}px`,
      background: `${color}${softened ? "66" : "e8"}`,
      border: softened ? `1px solid ${colors.textOnSolid}` : `2px solid ${colors.clusterBorder}`,
      borderRadius: "50%",
      color: softened ? colors.pinShadow : colors.textOnSolid,
      fontSize: softened ? "13px" : size >= 64 ? "18px" : "15px",
      fontWeight: softened ? "600" : "800",
      lineHeight: `${size - (softened ? 2 : 4)}px`,
      textAlign: "center",
      boxShadow: softened ? `0 2px 6px ${colors.clusterShadow}` : `0 5px 16px ${colors.clusterShadow}`,
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

function circleBounds(kakao: any, center: Place, radiusMeters: number) {
  const latitudeDelta = radiusMeters / 111320;
  const longitudeScale = Math.max(0.1, Math.cos(center.latitude * Math.PI / 180));
  const longitudeDelta = radiusMeters / (111320 * longitudeScale);
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
  placeNumbers,
  selectedPlace,
  previewPlace,
  radiusMeters,
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
  const searchMarkersRef = useRef<Array<{ marker: any; place: Place; number: number }>>([]);
  const countMarkerImagesRef = useRef(new Map<string, any>());
  const selectedPlaceRef = useRef<Place | null>(selectedPlace);
  // PRD 4.4 — 지도 분포는 유지하며 표시 밀도만 비교한다. 전환 시 지도 위치는 유지한다.
  const softenedMarkers = true;
  const preserveViewportRef = useRef(false);
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
      mapRef.current = map;
      infoWindowRef.current = new kakao.maps.InfoWindow({ removable: true, zIndex: 30 });
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

    const compactImages = {
      partner: compactMarkerImage(kakao, mapColors, "partner"),
      police: compactMarkerImage(kakao, mapColors, "police"),
    };
    const selectedImages = {
      partner: compactMarkerImage(kakao, mapColors, "partner", true),
      police: compactMarkerImage(kakao, mapColors, "police", true),
    };
    let highlightedMarker: { marker: any; source: "partner" | "police" } | null = null;

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
      const source = institution.source === "police" ? "police" : "partner";
      const image = softenedMarkers ? compactImages[source] : countsAvailable
        ? countMarkerImage(institution.source, institution.item_count)
        : (institution.source === "police" ? fallbackImages.police : fallbackImages.partner);
      const marker = new kakao.maps.Marker({
        position: new kakao.maps.LatLng(institution.latitude, institution.longitude),
        image,
        title: `${institution.name}${countsAvailable ? ` · 보관 물품 ${institution.item_count || 0}개` : ""}`,
        clickable: true,
        zIndex: 2,
      });
      const group = groups.get(coordinateKey(institution)) || [institution];
      kakao.maps.event.addListener(marker, "click", () => {
        if (softenedMarkers) {
          if (highlightedMarker) {
            highlightedMarker.marker.setImage(compactImages[highlightedMarker.source]);
            highlightedMarker.marker.setZIndex(2);
          }
          marker.setImage(selectedImages[source]);
          marker.setZIndex(3);
          highlightedMarker = { marker, source };
        }
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
      styles: clusterStyles(mapColors, softenedMarkers),
    });

    return () => {
      clustererRef.current?.clear();
      markers.forEach((marker) => marker.setMap(null));
    };
  }, [countsAvailable, institutions, mapColors, mapReady, onInstitutionSelect, softenedMarkers]);

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const kakao = window.kakao;
    infoWindowRef.current?.close();
    searchMarkersRef.current.forEach(({ marker }) => marker.setMap(null));
    const nextMarkers = places.map((place, index) => {
      const number = placeNumbers[index] ?? index + 1;
      const marker = new kakao.maps.Marker({
        map: mapRef.current,
        position: new kakao.maps.LatLng(place.latitude, place.longitude),
        title: place.name,
        image: compactMarkerImage(kakao, mapColors, "partner", false, number),
        zIndex: 4,
      });
      kakao.maps.event.addListener(marker, "click", () => onPlaceSelect(place));
      return { marker, place, number };
    });
    searchMarkersRef.current = nextMarkers;

    if (places.length) {
      const bounds = new kakao.maps.LatLngBounds();
      places.forEach((place) => bounds.extend(new kakao.maps.LatLng(place.latitude, place.longitude)));
      mapRef.current.setBounds(bounds, 48, 48, 48, 48);
    }

    return () => nextMarkers.forEach(({ marker }) => marker.setMap(null));
  }, [mapColors, mapReady, onPlaceSelect, places, placeNumbers]);

  useEffect(() => {
    if (!mapReady || !mapRef.current || !clustererRef.current) return;
    const kakao = window.kakao;
    const map = mapRef.current;
    clustererRef.current.clear();

    if (!selectedPlace) {
      if (institutionMarkersRef.current.length) {
        clustererRef.current.addMarkers(institutionMarkersRef.current);
      }
      searchMarkersRef.current.forEach(({ marker }) => marker.setMap(map));
      infoWindowRef.current?.close();
      preserveViewportRef.current = false;
      return;
    }

    const visibleInstitutionSet = new Set(visibleInstitutions);
    const nearbyMarkers = institutions.flatMap((institution, index) => (
      visibleInstitutionSet.has(institution)
        ? [institutionMarkersRef.current[index]]
        : []
    )).filter(Boolean);
    if (nearbyMarkers.length) clustererRef.current.addMarkers(nearbyMarkers);
    searchMarkersRef.current.forEach(({ marker, place, number }) => {
      marker.setMap(place === selectedPlace ? map : null);
      marker.setImage(place === selectedPlace
        ? placePreviewMarkerImage(kakao, mapColors)
        : compactMarkerImage(kakao, mapColors, "partner", false, number));
    });

    const selectedMarker = searchMarkersRef.current.find(({ place }) => place === selectedPlace)?.marker;
    if (selectedMarker) {
      infoWindowRef.current.setContent(
        `<div class="info-window"><strong>${escapeHtml(selectedPlace.name)}</strong>`
        + `<span>${escapeHtml([selectedPlace.address, selectedPlace.phone].filter(Boolean).join(" · "))}</span></div>`,
      );
      infoWindowRef.current.open(map, selectedMarker);
    }
    if (!preserveViewportRef.current) {
      map.setBounds(circleBounds(kakao, selectedPlace, radiusMeters), 48, 48, 48, 48);
    }
    preserveViewportRef.current = false;
  }, [institutions, mapColors, mapReady, radiusMeters, selectedPlace, visibleInstitutions, softenedMarkers]);

  useEffect(() => {
    if (!mapReady || !mapRef.current || !selectedPlace) return;
    const halo = createRadiusHalo(window.kakao, mapRef.current, selectedPlace, radiusMeters, mapColors.radiusFill);
    return () => halo.setMap(null);
  }, [mapColors, mapReady, radiusMeters, selectedPlace]);

  useEffect(() => {
    if (!mapReady || !mapRef.current || selectedPlace || !previewPlace) return;
    const kakao = window.kakao;
    const map = mapRef.current;
    // PRD 4.3-A — 미리보기는 지도만 이동하고 반경·물품 조회를 시작하지 않는다.
    searchMarkersRef.current.forEach(({ marker, place, number }) => {
      marker.setImage(place === previewPlace
        ? placePreviewMarkerImage(kakao, mapColors)
        : compactMarkerImage(kakao, mapColors, "partner", false, number));
      marker.setZIndex(place === previewPlace ? 6 : 4);
    });
    map.setLevel(3);
    map.panTo(new kakao.maps.LatLng(previewPlace.latitude, previewPlace.longitude));
    const marker = searchMarkersRef.current.find(({ place }) => place === previewPlace)?.marker;
    if (marker) {
      infoWindowRef.current.setContent(
        `<div class="info-window"><strong>${escapeHtml(previewPlace.name)}</strong>`
        + `<span>${escapeHtml(previewPlace.address)}</span></div>`,
      );
      infoWindowRef.current.open(map, marker);
    }
  }, [mapColors, mapReady, previewPlace, selectedPlace, visibleInstitutions, softenedMarkers]);

  const sourceCounts = useMemo(() => {
    const partner = visibleInstitutions.filter((item) => item.source !== "police").length;
    return { partner, police: visibleInstitutions.length - partner };
  }, [visibleInstitutions]);

  return (
    <section className={`map-panel${softenedMarkers ? " map-panel-softened" : ""}`} aria-label="카카오 지도">
      <div id="map" ref={containerRef} />
      <MapControls map={mapReady ? mapRef.current : null} />
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
            {selectedPlace && <b>반경 {radiusKilometersLabel(radiusMeters)}</b>}
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
