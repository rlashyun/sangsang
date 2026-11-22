// PRD 4.3-A · 4.4 — 같은 카드 외형과 명시적인 버튼을 쓰는 지도 팝업.
export function createMapPopup(kakao: any) {
  const overlay = new kakao.maps.CustomOverlay({ zIndex: 30, yAnchor: 1, clickable: true });
  const root = document.createElement("section");
  root.className = "map-popup";
  root.addEventListener("wheel", event => event.stopPropagation());
  root.setAttribute("aria-label", "지도 위치 정보");
  const close = document.createElement("button");
  close.type = "button";
  close.className = "map-popup-close";
  close.setAttribute("aria-label", "지도 정보 닫기");
  close.textContent = "×";
  close.onclick = () => overlay.setMap(null);
  const content = document.createElement("div");
  root.append(close, content);
  root.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.stopPropagation(); overlay.setMap(null); }
  });
  overlay.setContent(root);
  return {
    setContent(value: string | HTMLElement) {
      if (typeof value === "string") content.innerHTML = value;
      else content.replaceChildren(value);
    },
    open(map: any, marker: any) {
      overlay.setPosition(marker.getPosition());
      overlay.setMap(map);
    },
    close() { overlay.setMap(null); },
  };
}
