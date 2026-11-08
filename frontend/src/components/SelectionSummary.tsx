import type { SelectionSummary as SelectionSummaryValue } from "../types";
import { radiusKilometersLabel } from "../lib/geo.js";

interface SelectionSummaryProps {
  selection: SelectionSummaryValue;
  onChangeLocation: () => void;
  radiusMeters?: number;
  notice?: string;
}

export function SelectionSummary({
  selection,
  onChangeLocation,
  radiusMeters,
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
        {radiusMeters !== undefined && <p className="selection-radius">현재 반경 <strong>{radiusKilometersLabel(radiusMeters)}</strong></p>}
        {notice && <p className="selection-notice" role="status">{notice}</p>}
      </div>
    </section>
  );
}
