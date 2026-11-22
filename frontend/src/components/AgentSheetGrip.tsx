import type { ButtonHTMLAttributes } from "react";

export function AgentSheetGrip({ label, controls, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; controls: string }) {
  return <button {...props} type="button" className="agent-sheet-grip" aria-label={`${label} 패널 높이 조절`} aria-controls={controls}
    title="위아래로 드래그하거나 방향키로 패널 높이를 조절하세요">
    <span className="agent-sheet-grip-bar" aria-hidden="true" />
    <span>{label}<span aria-hidden="true"> ↕</span></span>
  </button>;
}
