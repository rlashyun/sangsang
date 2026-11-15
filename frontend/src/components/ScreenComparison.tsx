import { useState, type ReactNode } from "react";

// PRD 4.4 — 구버전은 별도 문서에 격리해 지도 SDK·CSS·검색 상태 간 간섭을 막는다.
export function ScreenComparison({ children }: { children: ReactNode }) {
  const [baseline, setBaseline] = useState(false);
  const [baselineOpened, setBaselineOpened] = useState(false);
  if (!import.meta.env.DEV) return children;
  return <>
    <div className="screen-comparison-bar" role="group" aria-label="전체 화면 비교">
      <span>전체 화면</span>
      <button type="button" aria-pressed={baseline} onClick={() => { setBaselineOpened(true); setBaseline(true); }}>기존 · 오늘 수정 전</button>
      <button type="button" aria-pressed={!baseline} onClick={() => setBaseline(false)}>수정안</button>
    </div>
    <div className={baseline ? "revised-screen-covered" : ""} inert={baseline}>{children}</div>
    {baselineOpened && <iframe
      className={`baseline-screen${baseline ? " visible" : ""}`}
      src="/?ui-version=baseline"
      title="오늘 UI 수정 전 전체 화면"
      aria-hidden={!baseline}
      inert={!baseline}
    />}
  </>;
}
