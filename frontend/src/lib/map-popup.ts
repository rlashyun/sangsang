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
  // 팝업을 닫거나 다른 내용으로 바꿀 때 그 팝업이 만든 임시 핀 등을 정리한다.
  let onClose: (() => void) | null = null;
  const runClose = () => { const callback = onClose; onClose = null; callback?.(); };
  const hide = () => { overlay.setMap(null); runClose(); };
  close.onclick = hide;
  const content = document.createElement("div");
  root.append(close, content);
  root.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.stopPropagation(); hide(); }
  });
  overlay.setContent(root);
  return {
    setContent(value: string | HTMLElement) {
      if (typeof value === "string") content.innerHTML = value;
      else content.replaceChildren(value);
    },
    open(map: any, marker: any, closeCallback?: () => void) {
      runClose();
      onClose = closeCallback ?? null;
      overlay.setPosition(marker.getPosition());
      overlay.setMap(map);
    },
    close() { hide(); },
    /** 팝업이 지도 보이는 영역 밖으로 잘리면 그만큼 지도를 밀어 전체를 보여 준다. */
    keepInside(map: any, container: HTMLElement, insets: { top?: number; bottom?: number; left?: number } = {}, margin = 12) {
      requestAnimationFrame(() => {
        if (!root.isConnected || typeof map.panBy !== "function") return;
        const popup = root.getBoundingClientRect();
        const area = container.getBoundingClientRect();
        const left = area.left + (insets.left ?? 0);
        const top = area.top + (insets.top ?? 0);
        const bottom = area.bottom - (insets.bottom ?? 0);
        const dx = popup.right > area.right - margin ? popup.right - area.right + margin
          : popup.left < left + margin ? popup.left - left - margin : 0;
        const dy = popup.top < top + margin ? popup.top - top - margin
          : popup.bottom > bottom - margin ? popup.bottom - bottom + margin : 0;
        if (dx || dy) map.panBy(dx, dy);
      });
    },
  };
}
