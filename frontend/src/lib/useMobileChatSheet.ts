import { useCallback, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent, type KeyboardEvent, type MouseEvent } from "react";

type Snap = 0 | 1 | 2;
const MOBILE_QUERY = "(max-width: 820px)";
const snapNames = ["collapsed", "half", "expanded"] as const;

// PRD 4.3-A · 4.4 — 모바일 채팅을 지도·반반·전체 대화의 세 높이로 이동한다.
export function useMobileChatSheet() {
  const shellRef = useRef<HTMLElement>(null);
  const [mobile, setMobile] = useState(() => window.matchMedia(MOBILE_QUERY).matches);
  const [snap, setSnap] = useState<Snap>(1);
  const [dragging, setDragging] = useState(false);
  const [viewportHeight, setViewportHeight] = useState(() => window.visualViewport?.height ?? window.innerHeight);
  const [metrics, setMetrics] = useState({ height: 0, collapsed: 52 });
  const drag = useRef<{ id: number; y: number; height: number; snap: Snap; lastY: number; lastTime: number; velocity: number } | null>(null);
  const skipClick = useRef(false);

  function bounds() {
    const panel = shellRef.current?.querySelector<HTMLElement>(".agent-panel");
    return { height: shellRef.current?.clientHeight || 1, collapsed: panel ? parseFloat(getComputedStyle(panel).minHeight) || 52 : 52 };
  }
  useLayoutEffect(() => {
    const media = window.matchMedia(MOBILE_QUERY);
    const resize = () => {
      const viewport = window.visualViewport;
      setViewportHeight(viewport && viewport.scale === 1 ? viewport.height : window.innerHeight);
      const next = bounds();
      setMetrics(previous => previous.height === next.height && previous.collapsed === next.collapsed ? previous : next);
    };
    const changeMode = () => { setMobile(media.matches); drag.current = null; setDragging(false); resize(); };
    const observer = new ResizeObserver(resize);
    if (shellRef.current) observer.observe(shellRef.current);
    resize();
    media.addEventListener("change", changeMode);
    window.addEventListener("resize", resize);
    window.visualViewport?.addEventListener("resize", resize);
    return () => {
      observer.disconnect(); media.removeEventListener("change", changeMode);
      window.removeEventListener("resize", resize);
      window.visualViewport?.removeEventListener("resize", resize);
    };
  }, []);

  const reveal = useCallback(() => { if (mobile) setSnap(previous => previous === 0 ? 1 : previous); }, [mobile]);
  const expand = useCallback(() => { if (mobile) setSnap(2); }, [mobile]);
  const snapHeight = (value: Snap) => value === 0 ? "var(--agent-sheet-collapsed)" : value === 2 ? "100%" : "56%";
  function onPointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (!mobile || !event.isPrimary || event.button !== 0) return;
    const focused = document.activeElement;
    if (focused instanceof HTMLInputElement || focused instanceof HTMLTextAreaElement) focused.blur();
    skipClick.current = false;
    setDragging(true);
    drag.current = { id: event.pointerId, y: event.clientY, height: event.currentTarget.parentElement!.getBoundingClientRect().height, snap, lastY: event.clientY, lastTime: event.timeStamp, velocity: 0 };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function onPointerMove(event: PointerEvent<HTMLButtonElement>) {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    const size = bounds();
    current.velocity = (current.lastY - event.clientY) / Math.max(1, event.timeStamp - current.lastTime);
    current.lastY = event.clientY; current.lastTime = event.timeStamp;
    // 드래그 프레임마다 긴 대화와 지도 전체를 다시 렌더링하지 않는다.
    shellRef.current?.style.setProperty("--agent-sheet-height", `${Math.max(size.collapsed, Math.min(size.height, current.height + current.y - event.clientY))}px`);
  }
  function finish(event: PointerEvent<HTMLButtonElement>, cancelled = false) {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    drag.current = null;
    const distance = current.y - event.clientY;
    skipClick.current = cancelled || Math.abs(distance) > 6;
    let target = current.snap;
    if (!cancelled && Math.abs(distance) > 6) {
      const size = bounds();
      const heights = [size.collapsed, size.height * .56, size.height];
      const released = Math.max(size.collapsed, Math.min(size.height, current.height + distance));
      let next = heights.reduce((closest, height, index) => Math.abs(height - released) < Math.abs(heights[closest] - released) ? index : closest, 0);
      if (event.timeStamp - current.lastTime < 120 && Math.abs(current.velocity) > .45) {
        next = current.velocity > 0 ? Math.max(next, Math.min(2, current.snap + 1)) : Math.min(next, Math.max(0, current.snap - 1));
      }
      target = next as Snap;
      setSnap(target);
    }
    shellRef.current?.style.setProperty("--agent-sheet-height", snapHeight(target));
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : event.key === "ArrowUp" ? Math.min(2, snap + 1) : event.key === "ArrowDown" ? Math.max(0, snap - 1) : null;
    if (next !== null) { event.preventDefault(); setSnap(next as Snap); }
  }
  const style = mobile ? {
    "--agent-viewport-height": `${viewportHeight}px`,
    "--agent-sheet-height": snapHeight(snap),
  } as CSSProperties : undefined;
  return {
    shellRef, style, mobile, collapsed: mobile && snap === 0 && !dragging, snap: snapNames[snap], dragging,
    // 전체 대화 상태에서는 지도를 숨기고 다시 열 때 쓸 유효한 지도 여백은 유지한다.
    mapBottomInset: mobile ? Math.round(snap === 0 ? metrics.collapsed : metrics.height * .56) : 0,
    reveal, expand,
    gripProps: {
      onPointerDown, onPointerMove, onPointerUp: (event: PointerEvent<HTMLButtonElement>) => finish(event),
      onPointerCancel: (event: PointerEvent<HTMLButtonElement>) => finish(event, true),
      onLostPointerCapture: (event: PointerEvent<HTMLButtonElement>) => finish(event, true),
      onKeyDown,
      onClick: (event: MouseEvent<HTMLButtonElement>) => { const skip = skipClick.current && event.detail !== 0; skipClick.current = false; if (!skip) setSnap(previous => previous === 2 ? 0 : (previous + 1) as Snap); },
      "aria-expanded": snap !== 0,
    },
  };
}
