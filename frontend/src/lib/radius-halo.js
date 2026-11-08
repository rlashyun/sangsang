// PRD 4.4 · 4.5 — 표시만 바꾸며 Haversine 검색 필터는 그대로 유지한다.
// https://apis.map.kakao.com/web/documentation/#AbstractOverlay
/**
 * @param {any} kakao
 * @param {any} map
 * @param {{ latitude: number, longitude: number }} center
 * @param {number} radiusMeters
 * @param {string} color
 */
export function createRadiusHalo(kakao, map, center, radiusMeters, color) {
  const angularDistance = radiusMeters / 6371008.8;
  const latitudeDelta = angularDistance * 180 / Math.PI;
  const longitudeDelta = Math.asin(Math.min(1,
    Math.sin(angularDistance) / Math.max(.01, Math.cos(center.latitude * Math.PI / 180)),
  )) * 180 / Math.PI;
  const position = new kakao.maps.LatLng(center.latitude, center.longitude);
  const west = new kakao.maps.LatLng(center.latitude, center.longitude - longitudeDelta);
  const east = new kakao.maps.LatLng(center.latitude, center.longitude + longitudeDelta);
  const north = new kakao.maps.LatLng(center.latitude + latitudeDelta, center.longitude);
  const south = new kakao.maps.LatLng(center.latitude - latitudeDelta, center.longitude);

  class RadiusHalo extends kakao.maps.AbstractOverlay {
    node = document.createElement("div");
    constructor() {
      super();
      this.node.className = "map-radius-halo";
      // PRD 4.4 — 모든 마커·클러스터·정보창 아래에 고정한다.
      this.node.style.zIndex = "-1";
      this.node.setAttribute("aria-hidden", "true");
      this.node.style.setProperty("--radius-halo-color", color);
    }
    onAdd() {
      this.getPanels().overlayLayer.prepend(this.node);
    }
    draw() {
      const projection = this.getProjection();
      const point = projection.pointFromCoords(position);
      const width = Math.abs(projection.pointFromCoords(east).x - projection.pointFromCoords(west).x);
      const height = Math.abs(projection.pointFromCoords(south).y - projection.pointFromCoords(north).y);
      this.node.style.width = `${width}px`;
      this.node.style.height = `${height}px`;
      this.node.style.left = `${point.x - width / 2}px`;
      this.node.style.top = `${point.y - height / 2}px`;
    }
    onRemove() {
      this.node.remove();
    }
  }

  // AbstractOverlay는 중심점이 화면 밖으로 나가도 걸쳐 있는 영역을 계속 표시한다.
  const halo = new RadiusHalo();
  halo.setMap(map);
  return halo;
}
