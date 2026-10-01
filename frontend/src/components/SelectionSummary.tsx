import type { SelectionSummary as SelectionSummaryValue } from "../types";

interface SelectionSummaryProps {
  selection: SelectionSummaryValue;
  onChangeLocation: () => void;
}

export function SelectionSummary({
  selection,
  onChangeLocation,
}: SelectionSummaryProps) {
  return (
    <section className="selection-summary" aria-label="현재 탐색 범위">
      <div className="selection-summary-main">
        <p className="section-kicker">
          {selection.institution ? "SELECTED INSTITUTION" : "SELECTED LOCATION"}
        </p>
        <h2>{selection.title}</h2>
        <p className="selection-description">{selection.description}</p>
      </div>
      <button className="change-location" type="button" onClick={onChangeLocation}>
        장소 다시 찾기
      </button>
    </section>
  );
}
