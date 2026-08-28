export function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value || "";
  return node.innerHTML;
}

export async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "요청 중 오류가 발생했습니다.");
  return payload;
}

export function institutionLocationIds(scope) {
  return [...new Set(scope
    .map((institution) => Number(institution.storage_location_id))
    .filter((value) => Number.isInteger(value) && value > 0))];
}

export function sameInstitutionScope(left, right) {
  const leftIds = institutionLocationIds(left).sort((a, b) => a - b);
  const rightIds = institutionLocationIds(right).sort((a, b) => a - b);
  return leftIds.length === rightIds.length
    && leftIds.every((value, index) => value === rightIds[index]);
}
