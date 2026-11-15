import { useEffect, useRef, useState } from "react";

// PRD 4.4 — 지도 설정은 한 아이콘에 모으고 휠/드래그 조작은 유지한다.
export function MapControls({ map }: { map: any }) {
  const [open, setOpen] = useState(false);
  const [skyview, setSkyview] = useState(false);
  const [level, setLevel] = useState(8);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!map) return;
    const update = () => setLevel(map.getLevel());
    const events = window.kakao.maps.event;
    events.addListener(map, "zoom_changed", update);
    update();
    return () => events.removeListener(map, "zoom_changed", update);
  }, [map]);

  useEffect(() => {
    if (!open) return;
    wrapperRef.current?.querySelector<HTMLButtonElement>("button[data-map-type]")?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !wrapperRef.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  function changeType(value: boolean) {
    if (!map) return;
    map.setMapTypeId(value ? window.kakao.maps.MapTypeId.HYBRID : window.kakao.maps.MapTypeId.ROADMAP);
    setSkyview(value);
  }

  function zoom(delta: number) {
    if (!map) return;
    const next = Math.max(1, Math.min(14, map.getLevel() + delta));
    map.setLevel(next);
    setLevel(next);
  }

  return <div className="map-controls-menu" ref={wrapperRef}>
    <button className="map-controls-trigger" ref={triggerRef} type="button" disabled={!map}
      aria-label="지도 설정" aria-expanded={open} aria-controls="map-settings-panel" aria-haspopup="dialog"
      onClick={() => setOpen((value) => !value)}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 8 9-5 9 5-9 5-9-5Zm0 5 9 5 9-5M3 18l9 5 9-5" /></svg>
    </button>
    {open && <div id="map-settings-panel" className="map-settings-panel" role="dialog" aria-label="지도 설정 옵션">
      <div className="map-settings-heading"><strong>지도 설정</strong>
        <button type="button" aria-label="지도 설정 닫기" onClick={() => { setOpen(false); triggerRef.current?.focus(); }}>×</button>
      </div>
      <div className="map-type-options" role="group" aria-label="지도 종류">
        <button data-map-type type="button" aria-pressed={!skyview} onClick={() => changeType(false)}>일반지도</button>
        <button data-map-type type="button" aria-pressed={skyview} onClick={() => changeType(true)}>스카이뷰</button>
      </div>
      <div className="map-zoom-options"><span>확대·축소</span>
        <button type="button" aria-label="지도 축소" disabled={level >= 14} onClick={() => zoom(1)}>−</button>
        <button type="button" aria-label="지도 확대" disabled={level <= 1} onClick={() => zoom(-1)}>+</button>
      </div>
      <p>마우스 휠로도 확대·축소할 수 있어요.</p>
    </div>}
  </div>;
}
