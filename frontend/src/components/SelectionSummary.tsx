import type { SelectionSummary as SelectionSummaryValue } from "../types";

interface SelectionSummaryProps {
  selection: SelectionSummaryValue;
  onChangeLocation: () => void;
  notice?: string;
}

export function SelectionSummary({
  selection,
  onChangeLocation,
  notice,
}: SelectionSummaryProps) {
  return (
    <section className="selection-summary" aria-label="현재 탐색 범위">
      <button className="change-location" type="button" onClick={onChangeLocation}>
        <span aria-hidden="true">‹</span> 장소 다시 찾기
      </button>
      <div className="selection-summary-main">
        <h2>{selection.title}</h2>
        <p className="selection-description">{selection.description}</p>
        {notice && <p className="selection-notice" role="status">{notice}</p>}
      </div>
    </section>
  );
}
