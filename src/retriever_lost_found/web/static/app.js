import { createFoundItemBrowser } from "./found-items.js";
import { escapeHtml, fetchJson } from "./ui.js";

(() => {
  "use strict";

  const SEOUL_CENTER = { latitude: 37.5665, longitude: 126.9780 };
  const SEARCH_RADIUS_METERS = 1000;
  const EARTH_RADIUS_METERS = 6371008.8;
  const form = document.querySelector("#search-form");
  const input = document.querySelector("#search-input");
  const status = document.querySelector("#status");
  const results = document.querySelector("#results");
  const submitButton = form.querySelector("button[type='submit']");
  const institutionStatus = document.querySelector("#institution-status");
  const mapBadge = document.querySelector("#map-badge");
  const resetRadiusButton = document.querySelector("#reset-radius");
  const searchPanel = document.querySelector(".search-panel");
  const selectionSummary = document.querySelector("#selection-summary");
  const selectionKicker = document.querySelector("#selection-kicker");
  const selectionTitle = document.querySelector("#selection-title");
  const selectionDescription = document.querySelector("#selection-description");
  const changeLocationButton = document.querySelector("#change-location");
  let map;
  let searchMarkers = [];
  let institutionMarkers = [];
  let institutions = [];
  let institutionClusterer;
  let infoWindow;
  let radiusCircle;
  let activeSearchPlace;
  let activeResultButton;
  let searchResultCount = 0;
  let itemCountsAvailable = false;
  let radiusScopeInstitutions = [];
  const countMarkerImages = new Map();

  const foundItemBrowser = createFoundItemBrowser({
    onRadiusScopeChange: (scopeTitle) => showItemView({
      title: scopeTitle,
      description: activeSearchPlace
        ? `${activeSearchPlace.address} · 반경 1km 전체 기관의 물품`
        : "선택한 범위에 보관 중인 물품입니다.",
    }),
  });

  function initMap() {
    kakao.maps.load(() => {
      map = new kakao.maps.Map(document.querySelector("#map"), {
        center: new kakao.maps.LatLng(SEOUL_CENTER.latitude, SEOUL_CENTER.longitude),
        level: 8,
      });
      map.addControl(new kakao.maps.ZoomControl(), kakao.maps.ControlPosition.RIGHT);
      map.addControl(new kakao.maps.MapTypeControl(), kakao.maps.ControlPosition.TOPRIGHT);
      infoWindow = new kakao.maps.InfoWindow({ removable: true });
      loadInstitutions();
    });
  }

  function clearSearchMarkers() {
    searchMarkers.forEach((marker) => marker.setMap(null));
    searchMarkers = [];
    if (infoWindow) infoWindow.close();
  }

  function keepOnlySearchMarker(selectedMarker) {
    searchMarkers.forEach((marker) => {
      if (marker !== selectedMarker) marker.setMap(null);
    });
    searchMarkers = selectedMarker ? [selectedMarker] : [];
  }

  function normalizedPlace(document) {
    const address = document.road_address_name
      || document.road_address?.address_name
      || document.address_name
      || document.address?.address_name
      || "주소 정보 없음";
    return {
      name: document.place_name || address,
      address,
      phone: document.phone || "",
      category: document.category_name || "",
      latitude: Number(document.y),
      longitude: Number(document.x),
      url: document.place_url || "",
    };
  }

  function showItemView({ title, description = "선택한 범위에 보관 중인 물품입니다.", institution = false }) {
    searchPanel.classList.add("items-view");
    selectionSummary.hidden = false;
    selectionKicker.textContent = institution ? "SELECTED INSTITUTION" : "SELECTED LOCATION";
    selectionTitle.textContent = title;
    selectionDescription.textContent = description;
  }

  function showLocationSearchView() {
    searchPanel.classList.remove("items-view");
    selectionSummary.hidden = true;
    input.focus();
  }

  function openPlace(place, marker) {
    keepOnlySearchMarker(marker);
    activateRadiusSearch(place);
    const details = [place.address, place.phone].filter(Boolean).join(" · ");
    infoWindow.setContent(
      `<div class="info-window"><strong>${escapeHtml(place.name)}</strong><span>${escapeHtml(details)}</span></div>`,
    );
    infoWindow.open(map, marker);
  }

  function toRadians(value) {
    return value * Math.PI / 180;
  }

  function distanceMeters(from, to) {
    const latitudeDelta = toRadians(to.latitude - from.latitude);
    const longitudeDelta = toRadians(to.longitude - from.longitude);
    const fromLatitude = toRadians(from.latitude);
    const toLatitude = toRadians(to.latitude);
    const a = Math.sin(latitudeDelta / 2) ** 2
      + Math.cos(fromLatitude) * Math.cos(toLatitude) * Math.sin(longitudeDelta / 2) ** 2;
    return 2 * EARTH_RADIUS_METERS * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  function circleBounds(center, radiusMeters) {
    const latitudeDelta = radiusMeters / 111320;
    const longitudeScale = Math.max(0.1, Math.cos(toRadians(center.latitude)));
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

  function drawRadiusCircle(place) {
    if (radiusCircle) radiusCircle.setMap(null);
    radiusCircle = new kakao.maps.Circle({
      map,
      center: new kakao.maps.LatLng(place.latitude, place.longitude),
      radius: SEARCH_RADIUS_METERS,
      strokeWeight: 2,
      strokeColor: "#13845a",
      strokeOpacity: 0.55,
      strokeStyle: "solid",
      fillColor: "#31a979",
      fillOpacity: 0.1,
      zIndex: 1,
    });
  }

  function setClusterMarkers(markers) {
    if (!institutionClusterer) return;
    institutionClusterer.clear();
    if (markers.length) institutionClusterer.addMarkers(markers);
  }

  function renderInstitutionStatus(visibleInstitutions, isRadiusSearch = false) {
    const partnerCount = visibleInstitutions.filter((item) => item.source !== "police").length;
    const policeCount = visibleInstitutions.length - partnerCount;
    institutionStatus.classList.add("loaded");
    institutionStatus.classList.toggle("filtered", isRadiusSearch);
    institutionStatus.innerHTML = `${isRadiusSearch ? "<b>반경 1km</b>" : ""}`
      + `<span class="source-count"><span class="source-dot partner"></span>연계기관 <strong>${partnerCount.toLocaleString("ko-KR")}곳</strong></span>`
      + `<span class="source-count"><span class="source-dot police"></span>경찰청 <strong>${policeCount.toLocaleString("ko-KR")}곳</strong></span>`
      + (itemCountsAvailable ? "" : `<span class="count-warning">물품 수 미연결</span>`);
  }

  function activateRadiusSearch(place) {
    activeSearchPlace = place;
    drawRadiusCircle(place);
    const nearbyMarkers = [];
    const nearbyInstitutions = [];
    institutions.forEach((institution, index) => {
      if (distanceMeters(place, institution) <= SEARCH_RADIUS_METERS) {
        nearbyMarkers.push(institutionMarkers[index]);
        nearbyInstitutions.push(institution);
      }
    });
    radiusScopeInstitutions = nearbyInstitutions;
    setClusterMarkers(nearbyMarkers);
    renderInstitutionStatus(nearbyInstitutions, true);
    const nearbyItemCount = nearbyInstitutions.reduce(
      (total, institution) => total + (Number(institution.item_count) || 0),
      0,
    );
    mapBadge.textContent = nearbyMarkers.length
      ? `${place.name} 기준 1km · 보관기관 ${nearbyMarkers.length.toLocaleString("ko-KR")}곳`
        + (itemCountsAvailable ? ` · 물품 ${nearbyItemCount.toLocaleString("ko-KR")}개` : "")
      : `${place.name} 기준 1km · 등록된 기관 없음`;
    status.textContent = searchResultCount > 1
      ? `검색 결과 ${searchResultCount}개 중 '${place.name}' 기준 1km 내 보관기관 ${nearbyMarkers.length}곳입니다.`
      : `'${place.name}' 기준 1km 내 보관기관 ${nearbyMarkers.length}곳입니다.`;
    resetRadiusButton.hidden = false;
    showItemView({
      title: `${place.name} 주변 습득물`,
      description: `${place.address} · 반경 1km 기관 ${nearbyInstitutions.length.toLocaleString("ko-KR")}곳`,
    });
    foundItemBrowser.setRadiusScope(nearbyInstitutions, `${place.name} 주변 습득물`);
    map.setBounds(circleBounds(place, SEARCH_RADIUS_METERS), 48, 48, 48, 48);
  }

  function resetRadiusFilter({ clearSearch = true } = {}) {
    if (radiusCircle) {
      radiusCircle.setMap(null);
      radiusCircle = null;
    }
    activeSearchPlace = null;
    radiusScopeInstitutions = [];
    if (activeResultButton) activeResultButton.classList.remove("active");
    activeResultButton = null;
    setClusterMarkers(institutionMarkers);
    if (institutions.length) renderInstitutionStatus(institutions);
    mapBadge.textContent = "숫자를 누르면 확대 · 핀을 누르면 기관 정보";
    resetRadiusButton.hidden = true;
    foundItemBrowser.reset();
    if (clearSearch) {
      clearSearchMarkers();
      results.replaceChildren();
      searchResultCount = 0;
      status.textContent = "전체 연계기관과 경찰청 관서를 표시하고 있습니다.";
      showLocationSearchView();
    }
  }

  function coordinateKey(institution) {
    return `${institution.latitude.toFixed(7)},${institution.longitude.toFixed(7)}`;
  }

  function institutionDetails(institutions) {
    const visible = institutions.slice(0, 8);
    const items = visible.map((institution) => {
      const distance = activeSearchPlace
        ? `${Math.round(distanceMeters(activeSearchPlace, institution)).toLocaleString("ko-KR")}m`
        : "";
      const source = institution.source === "police" ? "경찰청" : "연계기관";
      const itemCount = itemCountsAvailable
        ? `보관 물품 ${(Number(institution.item_count) || 0).toLocaleString("ko-KR")}개`
        : "";
      const meta = [distance, source, itemCount, institution.organization, institution.phone]
        .filter((value, index, values) => value && values.indexOf(value) === index)
        .join(" · ");
      return `<li><strong>${escapeHtml(institution.name)}</strong>`
        + `<span>${escapeHtml(institution.address)}</span>`
        + (meta ? `<small>${escapeHtml(meta)}</small>` : "")
        + `</li>`;
    }).join("");
    const remainder = institutions.length - visible.length;
    const title = institutions.length === 1
      ? (institutions[0].source === "police" ? "경찰청 관서" : "연계기관")
      : `같은 위치의 보관기관 ${institutions.length}곳`;
    return `<div class="institution-window">`
      + `<div class="institution-window-title">${title}</div>`
      + `<ul>${items}</ul>`
      + (remainder > 0 ? `<p>외 ${remainder}곳</p>` : "")
      + `</div>`;
  }

  function clusterStyles() {
    return [
      [42, "#f39a45"],
      [48, "#f18a37"],
      [56, "#ee7d2f"],
      [64, "#e96e24"],
      [72, "#df5c19"],
    ].map(([size, color]) => ({
      width: `${size}px`,
      height: `${size}px`,
      background: `${color}e8`,
      border: "2px solid rgba(215, 83, 16, .72)",
      borderRadius: "50%",
      color: "#fff",
      fontSize: size >= 64 ? "18px" : "15px",
      fontWeight: "800",
      lineHeight: `${size - 4}px`,
      textAlign: "center",
      boxShadow: "0 5px 16px rgba(118, 54, 17, .22)",
    }));
  }

  function markerCountLabel(value) {
    const count = Math.max(0, Number(value) || 0);
    return count > 999 ? "999+" : Math.floor(count).toString();
  }

  function countMarkerImage(source, itemCount) {
    const label = markerCountLabel(itemCount);
    const cacheKey = `${source}:${label}`;
    if (countMarkerImages.has(cacheKey)) return countMarkerImages.get(cacheKey);

    const pinColor = source === "police" ? "#f2b825" : "#18845a";
    const pinStroke = source === "police" ? "#ce9410" : "#0f6743";
    const badgeWidth = label.length >= 4 ? 32 : label.length === 3 ? 27 : 23;
    const badgeX = 55 - badgeWidth;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="58" height="62" viewBox="0 0 58 62">`
      + `<filter id="s" x="-30%" y="-25%" width="170%" height="180%"><feDropShadow dx="0" dy="3" stdDeviation="2.4" flood-color="#17211d" flood-opacity=".25"/></filter>`
      + `<g filter="url(#s)"><path d="M28 4C16.4 4 7 13.4 7 25c0 16 21 33 21 33s21-17 21-33C49 13.4 39.6 4 28 4Z" fill="${pinColor}" stroke="#fff" stroke-width="3"/>`
      + `<circle cx="28" cy="25" r="9.5" fill="#fff" fill-opacity=".96"/>`
      + `<path d="M22 21.5h12v7H22zM24 19h8v3h-8z" fill="${pinColor}"/></g>`
      + `<rect x="${badgeX}" y="1" width="${badgeWidth}" height="23" rx="11.5" fill="#667382" stroke="#fff" stroke-width="2"/>`
      + `<text x="${badgeX + badgeWidth / 2}" y="16.2" fill="#fff" font-family="Arial,sans-serif" font-size="11.5" font-weight="700" text-anchor="middle">${label}</text>`
      + `</svg>`;
    const image = new kakao.maps.MarkerImage(
      `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
      new kakao.maps.Size(58, 62),
      { offset: new kakao.maps.Point(28, 60) },
    );
    countMarkerImages.set(cacheKey, image);
    return image;
  }

  async function loadInstitutions() {
    try {
      const payload = await fetchJson("/api/institutions");
      itemCountsAvailable = payload.counts_available === true;
      institutions = (payload.institutions || []).filter((institution) => (
        Number.isFinite(institution.latitude) && Number.isFinite(institution.longitude)
      ));
      const groups = new Map();
      institutions.forEach((institution) => {
        const key = coordinateKey(institution);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(institution);
      });

      const partnerMarkerImage = new kakao.maps.MarkerImage(
        "/institution-marker.svg",
        new kakao.maps.Size(38, 46),
        { offset: new kakao.maps.Point(19, 46) },
      );
      const policeMarkerImage = new kakao.maps.MarkerImage(
        "/police-marker.svg",
        new kakao.maps.Size(38, 46),
        { offset: new kakao.maps.Point(19, 46) },
      );
      institutionMarkers = institutions.map((institution) => {
        const markerImage = itemCountsAvailable
          ? countMarkerImage(institution.source, institution.item_count)
          : (institution.source === "police" ? policeMarkerImage : partnerMarkerImage);
        const marker = new kakao.maps.Marker({
          position: new kakao.maps.LatLng(institution.latitude, institution.longitude),
          image: markerImage,
          title: `${institution.name}${itemCountsAvailable ? ` · 보관 물품 ${institution.item_count || 0}개` : ""}`,
          clickable: true,
        });
        const group = groups.get(coordinateKey(institution));
        kakao.maps.event.addListener(marker, "click", () => {
          infoWindow.setContent(institutionDetails(group));
          infoWindow.open(map, marker);
          const title = group.length === 1
            ? `${group[0].name} 보관 물품`
            : `이 위치의 ${group.length}개 기관 보관 물품`;
          const total = group.reduce((sum, item) => sum + (Number(item.item_count) || 0), 0);
          showItemView({
            title,
            description: `${group[0].address} · 보관 물품 ${total.toLocaleString("ko-KR")}개`,
            institution: true,
          });
          foundItemBrowser.showInstitution(group, title);
        });
        return marker;
      });

      institutionClusterer = new kakao.maps.MarkerClusterer({
        map,
        markers: institutionMarkers,
        averageCenter: true,
        minLevel: 6,
        minClusterSize: 2,
        gridSize: 70,
        calculator: [10, 50, 100, 300],
        styles: clusterStyles(),
      });
      if (activeSearchPlace) {
        activateRadiusSearch(activeSearchPlace);
      } else {
        renderInstitutionStatus(institutions);
        mapBadge.textContent = "숫자를 누르면 확대 · 핀을 누르면 기관 정보";
      }
      if (!itemCountsAvailable && payload.count_error) {
        console.warn("기관별 물품 수를 불러오지 못했습니다:", payload.count_error);
      }
    } catch (error) {
      institutionStatus.classList.add("error");
      institutionStatus.textContent = error instanceof Error
        ? `기관 위치 로딩 실패: ${error.message}`
        : "기관 위치를 불러오지 못했습니다.";
    }
  }

  function renderPlaces(documents) {
    clearSearchMarkers();
    resetRadiusFilter({ clearSearch: false });
    results.replaceChildren();
    const places = documents.map((document) => normalizedPlace(document));
    const validPlaces = places.filter(
      (place) => Number.isFinite(place.latitude) && Number.isFinite(place.longitude),
    );

    if (!validPlaces.length) {
      status.textContent = "검색 결과가 없습니다. 더 구체적인 장소명이나 주소를 입력해보세요.";
      return;
    }

    validPlaces.forEach((place, index) => {
      const position = new kakao.maps.LatLng(place.latitude, place.longitude);
      const marker = new kakao.maps.Marker({ map, position, title: place.name });
      searchMarkers.push(marker);

      const item = document.createElement("li");
      item.className = "result-item";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "result-button";
      button.innerHTML = [
        `<span class="result-copy"><span class="result-title">${index + 1}. ${escapeHtml(place.name)}</span>`,
        `<span class="result-address">${escapeHtml(place.address)}</span>`,
        place.phone || place.category
          ? `<span class="result-meta">${escapeHtml([place.phone, place.category].filter(Boolean).join(" · "))}</span></span>`
          : `</span>`,
        `<span class="select-place-label">이 위치 선택</span>`,
      ].join("");
      const selectPlace = () => {
        if (activeResultButton) activeResultButton.classList.remove("active");
        activeResultButton = button;
        button.classList.add("active");
        openPlace(place, marker);
      };
      kakao.maps.event.addListener(marker, "click", selectPlace);
      button.addEventListener("click", selectPlace);
      item.append(button);
      results.append(item);
    });

    searchResultCount = validPlaces.length;
    status.textContent = `장소 후보 ${validPlaces.length.toLocaleString("ko-KR")}개입니다. 주소를 확인하고 선택해주세요.`;
    const bounds = new kakao.maps.LatLngBounds();
    validPlaces.forEach((place) => bounds.extend(new kakao.maps.LatLng(place.latitude, place.longitude)));
    map.setBounds(bounds, 48, 48, 48, 48);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!map) {
      status.textContent = "지도를 불러오는 중입니다. 잠시 후 다시 시도하세요.";
      return;
    }
    const query = input.value.trim();
    if (!query) return;
    const center = map.getCenter();
    const params = new URLSearchParams({
      query,
      x: center.getLng().toString(),
      y: center.getLat().toString(),
    });

    submitButton.disabled = true;
    status.classList.remove("error");
    status.textContent = "카카오맵에서 위치를 찾고 있습니다…";
    try {
      let payload = await fetchJson(`/api/places?${params}`);
      if (!(payload.documents || []).length) {
        payload = await fetchJson(`/api/geocode?${new URLSearchParams({ address: query })}`);
      }
      renderPlaces(payload.documents || []);
    } catch (error) {
      clearSearchMarkers();
      resetRadiusFilter({ clearSearch: false });
      results.replaceChildren();
      status.classList.add("error");
      status.textContent = error instanceof Error ? error.message : "검색에 실패했습니다.";
    } finally {
      submitButton.disabled = false;
    }
  });

  resetRadiusButton.addEventListener("click", () => resetRadiusFilter());
  changeLocationButton.addEventListener("click", () => resetRadiusFilter());

  initMap();
})();
