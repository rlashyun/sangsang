import {
  escapeHtml,
  fetchJson,
  institutionLocationIds,
  sameInstitutionScope,
} from "./ui.js";

function categoryIcon(rawCategory) {
  const parent = String(rawCategory || "").split(">")[0].trim();
  return ({
    "가방": "👜",
    "의류": "🧢",
    "전자기기": "🎧",
    "지갑": "👛",
    "휴대폰": "📱",
    "카드": "💳",
  })[parent] || "📦";
}

function sourceLabel(item) {
  return item.item_source_code === "police_api" ? "경찰청" : "연계기관";
}

function formatRegisteredDate(value) {
  const parts = String(value || "").split("-");
  return parts.length === 3 ? `${parts[0]}.${parts[1]}.${parts[2]}` : String(value || "");
}

export function createFoundItemBrowser({ onRadiusScopeChange } = {}) {
  const form = document.querySelector("#found-item-form");
  const input = document.querySelector("#found-item-input");
  const submitButton = form.querySelector("button[type='submit']");
  const status = document.querySelector("#found-item-status");
  const list = document.querySelector("#found-items");
  const title = document.querySelector("#item-browser-title");
  const showRadiusButton = document.querySelector("#show-radius-items");
  let radiusScope = [];
  let radiusTitle = "주변 습득물";
  let activeScope = [];
  let activeTitle = "주변 습득물";
  let requestSequence = 0;

  function render(payload, scopeTitle) {
    list.replaceChildren();
    const items = payload.items || [];
    const query = payload.query || "";
    title.textContent = scopeTitle;

    if (!items.length) {
      status.textContent = query
        ? `'${query}'와 유사한 습득물이 없습니다.`
        : "현재 조건에 해당하는 습득물이 없습니다.";
      return;
    }

    status.textContent = query
      ? `후보 ${payload.candidate_count.toLocaleString("ko-KR")}건 중 유사한 물품 ${items.length.toLocaleString("ko-KR")}건`
      : `현재 보관 중인 물품 ${items.length.toLocaleString("ko-KR")}건`;

    const fragment = document.createDocumentFragment();
    items.forEach((item) => {
      const card = document.createElement("li");
      card.className = "found-item-card";
      const color = item.color_name ? ` · ${escapeHtml(item.color_name)}` : "";
      const score = Number.isFinite(Number(item.match_score))
        ? `<span class="match-score">유사도 ${Math.round(Number(item.match_score) * 100)}%</span>`
        : "";
      card.innerHTML = `<div class="item-placeholder" aria-hidden="true">`
        + `<span>${categoryIcon(item.raw_category_name)}</span>`
        + `<small>${escapeHtml(sourceLabel(item))}</small>`
        + `</div>`
        + `<div class="item-card-body">`
        + `<div class="item-card-top"><span class="item-category">${escapeHtml(item.raw_category_name)}</span>${score}</div>`
        + `<strong class="item-name">${escapeHtml(item.item_name || "이름 미상")}${color}</strong>`
        + (item.description ? `<p class="item-description">${escapeHtml(item.description)}</p>` : "")
        + `<dl class="item-meta">`
        + `<div><dt>보관장소</dt><dd>${escapeHtml(item.raw_storage_name || "정보 없음")}</dd></div>`
        + `<div><dt>등록일</dt><dd>${escapeHtml(formatRegisteredDate(item.registered_on))}</dd></div>`
        + `</dl></div>`;
      fragment.append(card);
    });
    list.append(fragment);
  }

  async function load(scope, scopeTitle, query = "") {
    activeScope = scope;
    activeTitle = scopeTitle;
    const locationIds = institutionLocationIds(scope);
    const sequence = ++requestSequence;
    showRadiusButton.hidden = !radiusScope.length || sameInstitutionScope(scope, radiusScope);
    input.disabled = false;
    submitButton.disabled = false;
    input.placeholder = "예: 검정 지갑, 아이퐁, 흰색 카드";

    if (!locationIds.length) {
      list.replaceChildren();
      title.textContent = scopeTitle;
      status.textContent = "연결된 보관기관 또는 습득물이 없습니다.";
      return;
    }

    status.classList.remove("error");
    status.textContent = query
      ? `'${query}'와 유사한 물품을 찾고 있습니다…`
      : "습득물 정보를 불러오고 있습니다…";
    submitButton.disabled = true;
    try {
      const params = new URLSearchParams({ location_ids: locationIds.join(",") });
      if (query.trim()) params.set("q", query.trim());
      const payload = await fetchJson(`/api/found-items?${params}`);
      if (sequence !== requestSequence) return;
      render(payload, scopeTitle);
    } catch (error) {
      if (sequence !== requestSequence) return;
      list.replaceChildren();
      status.classList.add("error");
      status.textContent = error instanceof Error
        ? `습득물 로딩 실패: ${error.message}`
        : "습득물 정보를 불러오지 못했습니다.";
    } finally {
      if (sequence === requestSequence) submitButton.disabled = false;
    }
  }

  function setRadiusScope(scope, scopeTitle) {
    radiusScope = scope;
    radiusTitle = scopeTitle;
    input.value = "";
    return load(scope, scopeTitle);
  }

  function showInstitution(scope, scopeTitle) {
    input.value = "";
    return load(scope, scopeTitle);
  }

  function reset() {
    radiusScope = [];
    activeScope = [];
    activeTitle = "주변 습득물";
    requestSequence += 1;
    showRadiusButton.hidden = true;
    input.value = "";
    input.disabled = true;
    input.placeholder = "먼저 위치를 검색해주세요";
    submitButton.disabled = true;
    list.replaceChildren();
    title.textContent = "주변 습득물";
    status.classList.remove("error");
    status.textContent = "위치를 검색하면 반경 1km의 습득물을 보여드립니다.";
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!activeScope.length) return;
    load(activeScope, activeTitle, input.value);
  });

  showRadiusButton.addEventListener("click", () => {
    if (!radiusScope.length) return;
    input.value = "";
    onRadiusScopeChange?.(radiusTitle);
    load(radiusScope, radiusTitle);
  });

  return { reset, setRadiusScope, showInstitution };
}
