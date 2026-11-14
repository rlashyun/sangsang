// PRD 4.3-C · 5 — 실제 브라우저에서 수령 정보와 탐색 맥락 복귀를 검증한다.
// 실행: Vite(127.0.0.1:5178)와 playwright-cli 브라우저를 연 뒤
// playwright-cli run-code --filename tests/web/item_detail.browser.cjs
// 모든 외부 통신은 가짜 API와 지도 SDK로 대체한다.
async (page) => {
  function check(value, message) { if (!value) throw new Error(message); }
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  let responseMode = "success";
  let minimumInstitutionId = 77;
  let itemRequests = 0;
  let lastItemQuery = null;
  let placeRequests = 0;
  const photoRequests = new Set();
  let releaseResponse;
  let onSlowRequest;
  let releaseInstitutionCounts;
  let markInstitutionCountsHeld;
  const institutionCountsHeld = new Promise(resolve => { markInstitutionCountsHeld = resolve; });
  const items = Array.from({ length: 60 }, (_, index) => ({
    id: index + 1,
    storage_location_id: index === 3 ? 88 : 77,
    item_name: `검정 지갑 ${index + 1}`,
    raw_category_name: "지갑 > 남성용지갑",
    color_name: "검정",
    description: index === 3 ? null : index === 4
      ? "아주 긴 원문 설명을 생략하지 않고 확인합니다.\n".repeat(60)
      : "원문 첫 줄\n둘째 줄 <script>원문</script>",
    registered_on: "2026-11-07",
    raw_storage_name: "서울역 유실물센터",
    item_source_code: "partner_api",
    image_url: index === 1
      ? "https://minwon24.police.go.kr/images/sub/img02_no_img.gif?default=1"
      : index === 2 ? "https://minwon24.police.go.kr/broken.png"
        : index === 3 ? null : `https://minwon24.police.go.kr/photo.svg?item=${index + 1}`,
    match_score: 0.87,
  }));
  await page.addInitScript(() => {
    class LatLng {
      constructor(lat, lng) { this.lat = lat; this.lng = lng; }
      getLat() { return this.lat; }
      getLng() { return this.lng; }
    }
    class FakeObject {
      constructor(_, options) { this.center = options?.center || new LatLng(37.55, 126.97); }
      getCenter() { return this.center; }
      getLevel() { return this.level ?? 8; }
      setMapTypeId(value) { this.mapTypeId = value; }
      addControl() {} setBounds() {} extend() {} setMap() {} clear() {}
      addMarkers(markers) { window.testMapMarkerCount = markers.length; }
      setContent() {} open() {} close() {}
      setImage() {} setZIndex() {} setLevel(value) { this.level = value; } panTo() {}
    }
    class FakeOverlay {
      setMap(map) {
        if (this.map) this.onRemove();
        this.map = map;
        if (map) { this.onAdd(); this.draw(); }
      }
      getPanels() { return { overlayLayer: document.getElementById("map") }; }
      getProjection() {
        return { pointFromCoords: point => ({ x: point.getLng() * 1000, y: -point.getLat() * 1000 }) };
      }
    }
    window.kakao = { maps: {
      load: callback => callback(), LatLng, Map: FakeObject, AbstractOverlay: FakeOverlay,
      ZoomControl: FakeObject, MapTypeControl: FakeObject, InfoWindow: FakeObject,
      Marker: FakeObject, MarkerClusterer: FakeObject, MarkerImage: FakeObject,
      Size: FakeObject, Point: FakeObject, Circle: FakeObject, LatLngBounds: FakeObject,
      ControlPosition: {}, MapTypeId: { ROADMAP: 1, HYBRID: 3 }, event: { addListener() {}, removeListener() {} },
    } };
  });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname === "minwon24.police.go.kr") {
      if (url.pathname === "/broken.png") return route.abort();
      check(url.pathname === "/photo.svg", "기본 그림은 요청하지 않아야 한다");
      photoRequests.add(url.searchParams.get("item"));
      const portrait = url.searchParams.get("item") === "1";
      const width = portrait ? 160 : 1800;
      const height = portrait ? 1200 : 240;
      return route.fulfill({ contentType: "image/svg+xml", body:
        `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#543b2b"/><text x="20" y="80" fill="white">TEST WALLET</text></svg>` });
    }
    if (url.hostname !== "127.0.0.1") return route.abort();
    if (url.pathname === "/__KAKAO_SDK_URL__") return route.fulfill({
      contentType: "application/javascript",
      body: "",
    });
    if (url.pathname === "/api/institutions") {
      const withCounts = url.searchParams.get("with_counts") !== "false";
      if (withCounts && !releaseInstitutionCounts) await new Promise(resolve => {
        releaseInstitutionCounts = resolve;
        markInstitutionCountsHeld();
      });
      return route.fulfill({ json: {
      institutions: [
        { storage_location_id: 77, name: "서울역 유실물센터", address: "서울시 중구 시험로 1", phone: "02-1234-5678", latitude: 37.55, longitude: 126.97 },
        { storage_location_id: 88, name: "서울역 유실물센터", address: "", phone: "", latitude: 37.55, longitude: 126.97 },
        { storage_location_id: 89, name: "1.5km 범위 기관", address: "", latitude: 37.561, longitude: 126.97 },
        { storage_location_id: 90, name: "2km 범위 기관", address: "", latitude: 37.566, longitude: 126.97 },
        { storage_location_id: 91, name: "2km 밖 기관", address: "", latitude: 37.570, longitude: 126.97 },
      ].filter(institution => institution.storage_location_id >= minimumInstitutionId), counts_available: withCounts,
      } });
    }
    if (url.pathname === "/api/places") {
      placeRequests += 1;
      if (url.searchParams.get("query") === "추가 장소") return route.fulfill({ json: {
        documents: [{ place_name: "추가 장소", address_name: "서울시 중구", y: "37.5585", x: "126.97" }],
      } });
      if (url.searchParams.get("query") === "세 번째 장소") return route.fulfill({ json: {
        documents: [{ place_name: "세 번째 장소", address_name: "서울시 중구", y: "37.565", x: "126.97" }],
      } });
      return route.fulfill({ json: {
        documents: Array.from({ length: 9 }, (_, index) => ({
          place_name: index === 0 ? "서울역" : `서울역 후보 ${index + 1}`,
          address_name: "서울시 중구", y: "37.55", x: "126.97",
        })),
      } });
    }
    if (url.pathname === "/api/found-items") {
      itemRequests += 1;
      lastItemQuery = url.searchParams.get("q");
      if (responseMode === "slow") await new Promise(resolve => {
        releaseResponse = resolve;
        onSlowRequest?.();
        onSlowRequest = undefined;
      });
      if (responseMode === "error") return route.fulfill({ status: 502, json: { error: "시험용 외부 장애" } });
      if (!url.searchParams.get("q") && responseMode !== "empty") {
        const laterPage = url.searchParams.has("cursor");
        return route.fulfill({ json: {
          items: laterPage ? items.slice(50) : items.slice(0, 50),
          total_count: laterPage ? null : 120,
          next_cursor: laterPage ? null : "2026-11-07:50",
        } });
      }
      const matches = responseMode === "empty" ? [] : items;
      const offset = Number(url.searchParams.get("cursor")?.split(":")[1] ?? 0);
      return route.fulfill({ json: {
        items: matches.slice(offset, offset + 50),
        candidate_count: null,
        match_count: matches.length,
        total_count: matches.length,
        semantic_status: "ok",
        next_cursor: offset + 50 < matches.length ? `search:${offset + 50}` : null,
      } });
    }
    return route.continue();
  });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("http://127.0.0.1:5178/");
  await institutionCountsHeld;
  check(await page.getByText("물품 수 불러오는 중…", { exact: true }).count() === 0, "물품 수가 로딩되는 동안 지도 범례에 로딩 문구를 표시하지 않는다");
  await page.waitForFunction(() => window.testMapMarkerCount === 5);
  releaseInstitutionCounts();

  const mapSettings = page.getByRole("button", { name: "지도 설정", exact: true });
  await mapSettings.waitFor();
  check(await page.getByRole("button", { name: "지도 확대", exact: true }).count() === 0, "확대 버튼은 기본 화면에서 숨겨야 한다");
  await mapSettings.click();
  await page.getByRole("button", { name: "스카이뷰", exact: true }).click();
  check(await page.getByRole("button", { name: "스카이뷰", exact: true }).getAttribute("aria-pressed") === "true", "스카이뷰 전환을 표시해야 한다");
  await page.getByRole("button", { name: "지도 확대", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.getByRole("dialog", { name: "지도 설정 옵션", exact: true }).waitFor({ state: "detached" });
  check(await mapSettings.evaluate(el => document.activeElement === el), "설정 닫기 후 아이콘에 포커스를 돌려야 한다");
  await page.getByRole("searchbox", { name: "검색어", exact: true }).fill("서울역");
  await page.getByRole("button", { name: "장소 검색", exact: true }).click();
  const placeRows = page.getByRole("list", { name: "위치 검색 결과" }).locator("li");
  await placeRows.first().waitFor();
  check(await placeRows.count() === 5, "장소 첫 페이지는 5곳이어야 한다");
  const requestsBeforePaging = placeRequests;
  await page.getByRole("button", { name: "장소 2페이지", exact: true }).click();
  check(await placeRows.count() === 4, "마지막 장소 페이지만 나머지 개수를 표시한다");
  check((await placeRows.first().innerText()).startsWith("6."), "장소 번호는 페이지 간 이어져야 한다");
  check(placeRequests === requestsBeforePaging, "장소 페이지 이동은 재검색하지 않아야 한다");
  await page.getByRole("button", { name: "장소 1페이지", exact: true }).click();
  const cardHeightBefore = await placeRows.first().evaluate(el => el.getBoundingClientRect().height);
  const firstPlace = page.getByRole("button", { name: /서울역.*서울시 중구/ }).first();
  const initialCardColor = await placeRows.first().evaluate(card => getComputedStyle(card).backgroundColor);
  await firstPlace.hover();
  check(await page.getByRole("button", { name: "이 장소 선택하기", exact: true }).count() === 0, "마우스 올림만으로 장소 선택 버튼이 나타나면 안 된다");
  check(await firstPlace.evaluate((button, initialColor) => {
    const card = button.closest(".location-result-card");
    return button.clientWidth === card.clientWidth && getComputedStyle(card).backgroundColor !== initialColor;
  }, initialCardColor), "장소 카드 전체에 마우스 올림 효과가 적용되어야 한다");
  await firstPlace.click();
  check(Math.abs(await placeRows.first().evaluate(el => el.getBoundingClientRect().height) - cardHeightBefore) < 1,
    "위치 미리보기 시 카드 높이가 바뀌면 안 된다");
  const locationList = page.getByRole("list", { name: "위치 검색 결과" });
  check(await locationList.evaluate(el => el.scrollHeight <= el.clientHeight + 1), "장소 카드 영역에 스크롤이 생기면 안 된다");
  check(itemRequests === 0, "장소 미리보기만으로 물품을 조회하면 안 된다");
  const previewCard = page.locator(".location-result-card.previewing");
  await previewCard.getByRole("button", { name: "이 장소 선택하기", exact: true }).click();
  const cards = page.getByRole("button", { name: /검정 지갑 \d+ 상세 보기/ });
  async function checkDetailPhoto() {
    const dialog = page.getByRole("dialog", { name: "물품 상세정보" });
    await dialog.getByRole("img").waitFor({ state: "visible" });
    const fits = await dialog.evaluate(root => {
      const photo = root.querySelector(".item-detail-photo").getBoundingClientRect();
      const image = root.querySelector(".item-detail-photo img").getBoundingClientRect();
      const name = root.querySelector(".item-detail-name").getBoundingClientRect();
      const fields = root.querySelector(".item-detail-body .item-detail-basics").getBoundingClientRect();
      return Math.abs(photo.height - 340) < 1 && image.top >= photo.top && image.bottom <= photo.bottom + 1
        && name.left >= photo.right && fields.top >= name.bottom;
    });
    check(fits, "세로/가로 사진 모두 큰 고정 영역 안에 있고 기본 정보는 오른쪽에 있어야 한다");
  }

  async function showItem(index) {
    const target = Math.floor(index / 4);
    for (let attempts = 0; attempts < 40; attempts += 1) {
      const navigation = page.getByRole("navigation", { name: "물품 검색 결과 페이지" });
      const current = Number(await navigation.locator('button[aria-current="page"]').innerText()) - 1;
      if (current === target) break;
      const direct = page.getByRole("button", { name: `물품 ${target + 1}페이지`, exact: true });
      if (await direct.count()) await direct.click();
      else await page.getByRole("button", { name: target > current ? "다음 물품 페이지" : "이전 물품 페이지", exact: true }).click();
      await page.waitForFunction(before => Number(document.querySelector('.item-pagination button[aria-current="page"]').textContent) - 1 !== before, current);
    }
    await page.getByRole("button", { name: `검정 지갑 ${index + 1} 상세 보기`, exact: true }).waitFor();
  }

  await cards.first().waitFor();
  check(await cards.count() === 4, "물품 첫 페이지는 4개여야 한다");
  check(await page.getByRole("list", { name: "습득물 목록" }).evaluate(el => el.scrollHeight <= el.clientHeight + 1), "물품 카드 영역에 스크롤이 없어야 한다");
  check(await page.locator(".item-description").count() === 0, "긴 설명은 목록에서 표시하지 않는다");
  check(await page.getByRole("heading", { name: "서울역 주변 습득물", exact: true }).count() === 1,
    "선택 장소 제목은 한 번만 안내해야 한다");
  check(await page.getByRole("button", { name: "1.5km로 넓혀보기", exact: true }).count() === 0,
    "물품 검색 전에는 수동 반경 확장 버튼을 숨겨야 한다");
  const itemRows = page.getByRole("list", { name: "습득물 목록" }).locator("li");
  const alertDialog = page.getByRole("dialog", { name: "새 물건 알림", exact: true });
  await page.getByRole("button", { name: "새 물건 알림", exact: true }).click();
  await alertDialog.getByRole("heading", { name: "먼저 찾는 물건을 검색해주세요" }).waitFor();
  check(await alertDialog.getByRole("textbox").count() === 0, "검색 조건 없이는 이메일부터 받지 않아야 한다");
  await alertDialog.getByRole("button", { name: "물건 찾기로 돌아가기" }).click();
  await alertDialog.waitFor({ state: "detached" });
  const firstThumbnail = itemRows.first().getByRole("img", { name: "검정 지갑 1 사진" });
  await firstThumbnail.waitFor();
  await page.waitForFunction(() => document.querySelector(".item-thumbnail img")?.naturalWidth > 0);
  check(await firstThumbnail.getAttribute("loading") === "lazy", "목록 사진은 지연 로딩해야 한다");
  check(!photoRequests.has("50"), "화면에서 멀리 떨어진 사진을 미리 받으면 안 된다");
  check(await itemRows.nth(1).locator("img").count() === 0, "목록의 기본 그림도 사진으로 표시하면 안 된다");
  await itemRows.nth(2).getByText("사진을 불러오지 못했습니다.", { exact: true }).waitFor({ state: "attached" });
  check(await itemRows.nth(2).locator("img").count() === 0, "깨진 목록 사진은 아이콘으로 대체해야 한다");
  await showItem(3);
  check(await page.getByRole("button", { name: "검정 지갑 4 상세 보기", exact: true }).locator("..").locator("img").count() === 0, "사진 URL이 없는 카드에도 아이콘을 유지해야 한다");
  await showItem(0);
  await page.screenshot({ path: "/private/tmp/retriever-list-photos-desktop.png" });
  await cards.first().click();
  await checkDetailPhoto();
  check(await page.getByRole("dialog").getByRole("img").evaluate(img => img.naturalHeight > img.naturalWidth), "세로 사진으로 확인해야 한다");
  await page.getByRole("dialog").getByRole("button", { name: /닫기/ }).click();
  await page.getByRole("dialog").waitFor({ state: "detached" });
  check(await cards.count() === 4, "상세를 닫으면 화면 페이지를 유지해야 한다");
  const beforeCursor = itemRequests;
  await showItem(48);
  check(await cards.count() === 4, "50건 커서 경계에서도 4개를 채워야 한다");
  check(await cards.first().getAttribute("aria-label") === "검정 지갑 49 상세 보기", "경계 페이지는 49번부터 이어져야 한다");
  check(itemRequests === beforeCursor + 1, "커서 경계에서만 다음 묶음을 조회해야 한다");
  await showItem(59);
  check(await cards.count() === 4, "마지막 4개를 확인할 수 있어야 한다");
  await showItem(0);
  const search = page.getByRole("searchbox", { name: "습득물 검색어" });
  const itemPagination = page.locator(".item-pagination");
  const paginationBeforeSearch = await itemPagination.boundingBox();
  check(await page.locator(".item-search-followup").isVisible(), "검색 전부터 페이지 아래 구분선을 표시해야 한다");
  await search.fill("검정 지갑");
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.getByText("전체 120건 중 검색 결과 60건 · 유사도 높은 순", { exact: true }).waitFor();
  check(Math.abs((await itemPagination.boundingBox()).y - paginationBeforeSearch.y) < 1, "검색 전후 페이지 번호의 세로 위치를 유지해야 한다");
  check(await page.locator(".item-previous-radius").count() === 0, "기본 반경에는 이전 범위 표식을 표시하지 않는다");
  const requestsBeforeSearchCursor = itemRequests;
  await showItem(51);
  check(itemRequests === requestsBeforeSearchCursor + 1 && lastItemQuery === "검정 지갑", "검색 결과 50건 이후에도 같은 검색어와 서버 커서로 다음 결과를 조회해야 한다");
  await search.fill("아직 제출하지 않은 검색어");
  responseMode = "slow";
  const expansionHeld = new Promise(resolve => { onSlowRequest = resolve; });
  const expansionResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === "/api/found-items" && url.searchParams.get("q") === "검정 지갑";
  });
  await page.getByRole("button", { name: "1.5km로 넓혀보기", exact: true }).click();
  await expansionHeld;
  check(await page.locator(".item-previous-radius").count() === 0, "확장 결과가 도착하기 전에는 이전 카드에 반경 표식을 붙이지 않는다");
  responseMode = "success";
  releaseResponse();
  const expandedResponse = await expansionResponse;
  const expandedLocationIds = new URL(expandedResponse.url()).searchParams.get("location_ids").split(",");
  check(expandedLocationIds.includes("77") && expandedLocationIds.includes("89"), "확장하면 기존 기관과 새 범위 기관을 함께 검색해야 한다");
  await page.waitForFunction(() => document.getElementById("found-item-input").value === "검정 지갑");
  check(new URL(expandedResponse.url()).searchParams.get("q") === "검정 지갑", "반경 확장은 미제출 문구 대신 제출 검색어를 유지해야 한다");
  check(await page.locator(".item-previous-radius").first().innerText() === "1km 이내", "1.5km 결과에는 1km 이내를 표시한다");
  check(await page.locator(".item-radius-actions button").count() === 2, "반경 축소와 확장 버튼을 같은 그룹에 둔다");
  await page.getByRole("button", { name: "2km로 넓혀보기", exact: true }).click();
  await page.getByText("1.5km 이내", { exact: true }).first().waitFor();
  check(await page.getByText("1km 이내", { exact: true }).count() === 0, "2km 결과의 이전 범위 표식은 1.5km로 통일한다");
  await page.getByRole("button", { name: "1.5km로 돌아가기", exact: true }).click();
  check(await page.locator(".selection-radius strong").innerText() === "1.5km", "반경 돌아가기는 한 단계 줄여야 한다");
  await page.getByText("전체 120건 중 검색 결과 60건 · 유사도 높은 순", { exact: true }).waitFor();
  await showItem(8);
  await search.fill("입력 중인 다른 물건");
  const alertTrigger = page.getByRole("button", { name: "새 물건 알림 받기", exact: true });
  const requestsBeforeAlert = itemRequests;
  const pageBeforeAlert = await page.locator('.item-pagination button[aria-current="page"]').innerText();
  await alertTrigger.click();
  await alertDialog.waitFor();
  check(await alertDialog.locator(".alert-query").innerText() === "검정 지갑", "알림에는 마지막 제출 검색어만 전달해야 한다");
  check(await alertDialog.locator(".alert-summary").innerText().then(text => text.includes("서울역") && text.includes("1.5km")), "알림에 선택 장소와 현재 반경을 전달해야 한다");
  check(await page.locator('.app-navigation button[aria-current="page"]').innerText() === "새 물건 알림", "알림 메뉴를 활성 표시해야 한다");
  check(await page.locator(".alert-backdrop").evaluate(el => el.getBoundingClientRect().top >= document.querySelector(".app-header").getBoundingClientRect().bottom - 1), "알림 블러 영역은 상단 메뉴 아래에서 시작해야 한다");
  await alertDialog.getByRole("button", { name: "알림 조건 수정하기", exact: true }).click();
  check(await alertDialog.locator('.alert-condition-form input').count() === 0, "알림 장소와 물품명은 편집할 수 없어야 한다");
  const alertOptions = alertDialog.getByRole("group", { name: "알림 탐색 반경" }).getByRole("button");
  check((await alertOptions.allTextContents()).join(",") === "1km,1.5km,2km", "알림 반경은 탐색 반경과 같아야 한다");
  await alertDialog.getByRole("button", { name: "2km", exact: true }).click();
  await alertDialog.getByRole("button", { name: "수정 완료", exact: true }).click();
  check(await alertDialog.locator(".alert-summary").innerText().then(text => text.includes("2km")), "알림 반경 수정이 요약에 반영되어야 한다");
  await alertDialog.getByRole("button", { name: "알림 조건 수정하기", exact: true }).click();
  await alertDialog.getByRole("button", { name: "1km", exact: true }).click();
  await alertDialog.getByRole("button", { name: "취소", exact: true }).click();
  check(await alertDialog.locator(".alert-summary").innerText().then(text => text.includes("2km")), "수정 취소 시 이전 알림 반경을 유지해야 한다");
  const emailInput = alertDialog.getByRole("textbox", { name: "알림 받을 이메일", exact: true });
  await emailInput.fill("wrong");
  await alertDialog.getByRole("button", { name: "이 조건으로 알림 받기" }).click();
  check(await emailInput.evaluate(el => !el.validity.valid), "잘못된 이메일은 제출을 막아야 한다");
  await emailInput.fill("preview@example.com");
  const requiredConsent = alertDialog.getByRole("checkbox", { name: /필수.*개인정보/ });
  const optionalConsent = alertDialog.getByRole("checkbox", { name: /선택.*검색 품질/ });
  check(!await requiredConsent.isChecked() && !await optionalConsent.isChecked(), "동의는 기본 해제 상태여야 한다");
  await alertDialog.getByRole("button", { name: "이 조건으로 알림 받기" }).click();
  check(await requiredConsent.evaluate(input => input.validity.valueMissing), "필수 동의 없이는 알림 신청을 막아야 한다");
  const consentView = alertDialog.getByRole("button", { name: "알림 개인정보 수집·이용 전문 보기" });
  const consentPopup = page.getByRole("dialog", { name: "알림을 위한 개인정보 수집·이용", exact: true });
  const alertHeight = await alertDialog.evaluate(dialog => dialog.scrollHeight);
  await consentView.click();
  await consentPopup.getByText("이메일 주소, 알림에 포함한 장소 정보(좌표 포함), 탐색 반경, 물품 검색어", { exact: true }).waitFor();
  check(await page.locator(".new-item-alert").evaluate(dialog => dialog.scrollHeight) === alertHeight, "전문 팝업은 신청 창의 높이를 바꾸면 안 된다");
  await page.keyboard.press("Escape");
  await consentPopup.waitFor({ state: "hidden" });
  check(await alertDialog.isVisible(), "전문 팝업을 닫아도 알림 신청 창을 유지해야 한다");
  await requiredConsent.check();
  await alertDialog.getByRole("button", { name: "이 조건으로 알림 받기" }).click();
  await alertDialog.getByRole("status").getByText(/저장되지 않았고/).waitFor();
  await page.screenshot({ path: "/private/tmp/retriever-alert-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  check(await alertDialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1), "작은 화면에서도 알림 창이 가로로 넘치면 안 된다");
  await page.screenshot({ path: "/private/tmp/retriever-alert-mobile.png" });
  await page.keyboard.press("Escape");
  await alertDialog.waitFor({ state: "detached" });
  check(await alertTrigger.evaluate(el => document.activeElement === el), "알림을 닫으면 진입 버튼으로 포커스를 복원해야 한다");
  check(await search.inputValue() === "입력 중인 다른 물건", "알림을 닫아도 입력 중이던 검색어를 보존해야 한다");
  check(await page.locator('.item-pagination button[aria-current="page"]').innerText() === pageBeforeAlert, "알림 종료 시 기존 물품 페이지를 보존해야 한다");
  check(itemRequests === requestsBeforeAlert, "알림 열기와 미리보기 제출은 물품 재검색을 일으키지 않아야 한다");
  check(await page.locator(".selection-radius strong").innerText() === "1.5km", "알림 반경 변경은 검색 화면의 반경을 바꾸면 안 된다");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await alertTrigger.click();
  await alertDialog.waitFor();
  await page.goBack();
  await alertDialog.waitFor({ state: "detached" });
  await page.goForward();
  await alertDialog.waitFor();
  await alertDialog.getByRole("button", { name: /닫기/ }).click();
  await alertDialog.waitFor({ state: "detached" });
  await alertTrigger.click();
  await alertDialog.waitFor();
  await page.getByRole("link", { name: "물건 찾기", exact: true }).click();
  await alertDialog.waitFor({ state: "detached" });
  check(await page.getByRole("link", { name: "물건 찾기", exact: true }).getAttribute("aria-current") === "page", "알림 중에도 물건 찾기 탭으로 복귀할 수 있어야 한다");
  await search.fill("검정 지갑");
  check(await page.locator('.item-pagination button[aria-label^="물품 "]').count() <= 5, "물품 페이지 번호는 최대 5개여야 한다");
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 1280, height: 820 }, { width: 1280, height: 800 }, { width: 1280, height: 760 }]) {
    await page.setViewportSize(viewport);
    const fits = await page.locator(".found-item-card").evaluateAll(cards => cards.every(card => {
      const bounds = card.getBoundingClientRect();
      return [...card.querySelectorAll(".item-name, .item-meta, .item-thumbnail")].every(element => {
        const rect = element.getBoundingClientRect();
        return rect.top >= bounds.top && rect.bottom <= bounds.bottom + 1 && rect.right <= bounds.right + 1;
      });
    }));
    check(fits, "네 물품 카드의 사진·명칭·보관장소·등록일이 카드 안에 들어와야 한다");
    check(await page.getByRole("list", { name: "습득물 목록" }).evaluate(el => el.scrollHeight <= el.clientHeight + 1), "카드 목록은 스크롤 없이 보여야 한다");
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  const list = page.getByRole("list", { name: "습득물 목록" });
  const dialog = page.getByRole("dialog", { name: "물품 상세정보" });
  const close = () => dialog.getByRole("button", { name: /닫기/ });
  await showItem(12);
  const detailCard = page.getByRole("button", { name: "검정 지갑 13 상세 보기", exact: true });
  const scrollBefore = await list.evaluate(el => el.scrollTop);
  const requestsBefore = itemRequests;
  await detailCard.click();
  await dialog.waitFor();
  await dialog.getByRole("img").waitFor({ state: "visible" });
  await checkDetailPhoto();
  check(await dialog.getByRole("img").evaluate(img => img.naturalWidth > img.naturalHeight), "가로 사진으로 확인해야 한다");
  check(await dialog.getByText("원문 첫 줄\n둘째 줄 <script>원문</script>", { exact: true }).count() === 1, "원문은 안전한 텍스트로 표시해야 한다");
  check(await dialog.locator("script").count() === 0, "설명의 HTML을 실행하면 안 된다");
  check(await dialog.getByRole("link", { name: "02-1234-5678" }).getAttribute("href") === "tel:0212345678", "기관 ID의 연락처를 연결해야 한다");
  check(!(await dialog.innerText()).includes("유사도"), "상세에는 내부 점수가 없어야 한다");
  check(await dialog.evaluate(root => {
    const row = root.querySelector(".item-detail-contact-row");
    const label = row.querySelector("dt").getBoundingClientRect();
    const phone = row.querySelector("a").getBoundingClientRect();
    const fields = row.closest("dl");
    const institution = fields.querySelector("dd").getBoundingClientRect();
    return Math.abs((label.top + label.bottom) / 2 - (phone.top + phone.bottom) / 2) < 1
      && Math.abs(phone.left - institution.left) < 1;
  }), "전화번호 라벨·링크는 같은 높이이며 다른 기관 정보와 왼쪽 정렬이 맞아야 한다");

  check(await dialog.evaluate(root => {
    const dialog = root.getBoundingClientRect();
    const actions = root.querySelector(".item-match-actions").getBoundingClientRect();
    const footer = root.querySelector(".item-match-confirmation").getBoundingClientRect();
    return Math.abs((actions.left + actions.right) / 2 - (dialog.left + dialog.right) / 2) < 1
      && footer.bottom <= dialog.bottom && footer.top >= root.querySelector(".item-detail-header").getBoundingClientRect().bottom;
  }), "선택 버튼은 창 전체 하단 중앙에 표시되어야 한다");

  check(await close().evaluate(el => document.activeElement === el), "열면 닫기 버튼에 포커스해야 한다");
  await page.keyboard.press("Shift+Tab");
  check(await dialog.evaluate(el => el.contains(document.activeElement)), "포커스가 모달을 벗어나면 안 된다");
  await page.screenshot({ path: "/private/tmp/retriever-detail-desktop.png" });
  await close().click();
  await dialog.waitFor({ state: "detached" });
  check(await search.inputValue() === "검정 지갑", "검색어를 유지해야 한다");
  check(await list.evaluate(el => el.scrollTop) === scrollBefore, "목록 스크롤을 유지해야 한다");
  check(await detailCard.evaluate(el => document.activeElement === el), "선택 카드로 포커스를 돌려야 한다");
  check(itemRequests === requestsBefore, "상세 열기·닫기로 재조회하면 안 된다");
  const pageBeforePickup = await page.locator('.item-pagination button[aria-current="page"]').innerText();
  await detailCard.click();
  await dialog.getByRole("button", { name: "아니요, 더 찾아볼게요", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  check(await detailCard.evaluate(el => document.activeElement === el), "아니요 선택은 원래 카드로 돌아와야 한다");
  await detailCard.click();
  await dialog.getByRole("button", { name: "예, 수령 방법 보기", exact: true }).click();
  const pickup = page.getByRole("dialog", { name: "물품 수령 안내", exact: true });
  await pickup.waitFor();
  check(await page.getByRole("dialog").count() === 1, "수령 안내는 같은 창 안에서 전환해야 한다");
  check(await pickup.getByRole("heading", { name: "이렇게 수령할 수 있어요" }).evaluate(el => document.activeElement === el), "수령 안내 제목에 포커스를 옮겨야 한다");
  check(await pickup.getByRole("link", { name: "02-1234-5678" }).getAttribute("href") === "tel:0212345678", "수령 안내도 같은 기관 연락처를 사용해야 한다");
  check(await pickup.getByRole("list", { name: "수령 순서" }).locator("li").count() === 3, "연락/준비/수령 순서를 안내해야 한다");
  await pickup.getByRole("button", { name: "물품 정보 다시 보기", exact: true }).click();
  await dialog.waitFor();
  check(await dialog.getByRole("button", { name: "예, 수령 방법 보기", exact: true }).evaluate(el => document.activeElement === el), "수령 안내에서 돌아오면 확인 버튼으로 포커스를 복원해야 한다");
  await dialog.getByRole("button", { name: "예, 수령 방법 보기", exact: true }).click();
  await pickup.waitFor();
  await page.screenshot({ path: "/private/tmp/retriever-pickup-desktop.png" });
  await pickup.getByRole("button", { name: "확인했어요", exact: true }).click();
  await pickup.waitFor({ state: "detached" });
  check(await detailCard.evaluate(el => document.activeElement === el), "확인 후 선택 카드로 돌아와야 한다");
  check(await search.inputValue() === "검정 지갑", "수령 안내를 닫아도 검색어를 유지해야 한다");
  check(await page.locator('.item-pagination button[aria-current="page"]').innerText() === pageBeforePickup, "수령 안내 후 페이지를 유지해야 한다");
  check(itemRequests === requestsBefore, "수령 안내 전환과 닫기는 물품을 재조회하지 않아야 한다");


  await detailCard.press("Enter");
  await dialog.waitFor();
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  await detailCard.click();
  await dialog.waitFor();
  await page.goBack();
  await dialog.waitFor({ state: "detached" });
  check(new URL(page.url()).pathname === "/", "뒤로가기가 앱을 떠나면 안 된다");
  await page.goForward();
  await dialog.waitFor();
  await page.mouse.click(4, 4);
  await dialog.waitFor({ state: "detached" });

  await showItem(4);
  await page.getByRole("button", { name: "검정 지갑 5 상세 보기", exact: true }).click();
  await dialog.waitFor();
  await page.keyboard.press("Tab");
  const content = dialog.getByRole("region", { name: "물품 상세 내용" });
  check(await content.evaluate(el => document.activeElement === el), "긴 내용은 키보드로 접근할 수 있어야 한다");
  await page.keyboard.press("End");
  await page.waitForFunction(() => document.querySelector(".item-detail-content").scrollTop > 0);
  check(await close().isVisible(), "내용을 스크롤해도 닫기 버튼을 유지해야 한다");
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  await showItem(1);
  await page.getByRole("button", { name: "검정 지갑 2 상세 보기", exact: true }).click();
  await dialog.getByText("등록된 사진이 없습니다.").waitFor();
  check(await dialog.locator("img").count() === 0, "기본 그림을 사진으로 노출하면 안 된다");
  await close().click();
  await dialog.waitFor({ state: "detached" });
  await showItem(2);
  await page.getByRole("button", { name: "검정 지갑 3 상세 보기", exact: true }).click();
  await dialog.getByText("사진을 불러오지 못했습니다.").waitFor();
  await close().click();
  await dialog.waitFor({ state: "detached" });

  await page.setViewportSize({ width: 390, height: 844 });
  const sheetHandle = page.getByRole("button", { name: "검색 패널 펼치기" });
  const handleBounds = await sheetHandle.boundingBox();
  check(handleBounds && handleBounds.y > 400, "모바일 검색 패널은 지도 아래에서 시작해야 한다");
  const handleX = handleBounds.x + handleBounds.width / 2;
  const handleY = handleBounds.y + handleBounds.height / 2;
  await page.mouse.move(handleX, handleY);
  await page.mouse.down();
  await page.mouse.move(handleX, handleY - 180, { steps: 6 });
  await page.mouse.up();
  const collapseSheet = page.getByRole("button", { name: "검색 패널 줄이기" });
  await collapseSheet.waitFor();
  check((await collapseSheet.boundingBox()).y < handleBounds.y, "손잡이를 위로 끌면 검색 영역이 넓어져야 한다");
  await collapseSheet.click();
  await sheetHandle.waitFor();
  await sheetHandle.click();
  await collapseSheet.waitFor();
  await cards.first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/private/tmp/retriever-list-photos-mobile.png" });
  await showItem(3);
  await page.getByRole("button", { name: "검정 지갑 4 상세 보기", exact: true }).click();
  await dialog.waitFor();
  await dialog.getByText("등록된 사진이 없습니다.").waitFor();
  check(await dialog.getByRole("link").count() === 0, "동명 기관의 전화번호를 대신 쓰면 안 된다");
  check(await dialog.getByText("정보 없음", { exact: true }).count() === 3, "누락 설명·주소·전화번호를 명시해야 한다");
  const bounds = await dialog.boundingBox();
  check(Math.abs(bounds.y + bounds.height - 844) < 2, "모바일 시트는 화면 하단에 붙어야 한다");
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "가로 스크롤이 없어야 한다");
  await page.screenshot({ path: "/private/tmp/retriever-detail-mobile.png" });
  await dialog.getByRole("button", { name: "예, 수령 방법 보기", exact: true }).click();
  await pickup.waitFor();
  check(await pickup.locator('a[href^="tel:"]').count() === 0, "전화번호 없는 기관에 추정 연락처를 만들면 안 된다");
  await pickup.getByText("연락처 정보가 없어요. 해당 기관의 공식 안내에서 확인해주세요.", { exact: true }).waitFor();
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "모바일 수령 안내에 가로 넘침이 없어야 한다");
  await page.screenshot({ path: "/private/tmp/retriever-pickup-mobile.png" });
  await pickup.getByRole("button", { name: "확인했어요", exact: true }).click();
  await pickup.waitFor({ state: "detached" });

  responseMode = "slow";
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.locator(".found-items-loading").getByText("검색 중…", { exact: true }).waitFor();
  check(await page.locator(".found-item-card").first().evaluate(card => Number(getComputedStyle(card).opacity) < 1), "검색 중 이전 카드를 옅게 표시해야 한다");
  check(await cards.first().isDisabled(), "목록 갱신 중 상세 열기를 막아야 한다");
  check(await page.getByRole("button", { name: "물품 검색", exact: true }).isDisabled(), "중복 검색을 막아야 한다");
  releaseResponse();
  await page.getByText("전체 120건 중 검색 결과 60건 · 유사도 높은 순", { exact: true }).waitFor();
  check(await page.locator(".found-item-status.search-loading").count() === 0, "검색 완료 후 로딩 표시를 제거해야 한다");
  check(await page.locator(".found-item-card").first().evaluate(card => getComputedStyle(card).opacity === "1"), "검색 완료 후 카드 밝기를 복원해야 한다");
  responseMode = "empty";
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.getByText("'검정 지갑'와 유사한 습득물이 없습니다.", { exact: true }).waitFor();
  check(await cards.count() === 0, "빈 결과에서는 상세 열기가 없어야 한다");
  responseMode = "error";
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.getByText("습득물 로딩 실패: 시험용 외부 장애", { exact: true }).waitFor();
  check(await cards.count() === 0, "오류 결과에서는 상세 열기가 없어야 한다");
  check(await search.inputValue() === "검정 지갑", "오류 후에도 재검색할 입력을 유지해야 한다");

  responseMode = "success";
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("http://127.0.0.1:5178/");
  const placeInput = page.getByRole("searchbox", { name: "검색어", exact: true });
  await placeInput.fill("서울역");
  await page.getByRole("button", { name: "장소 검색", exact: true }).click();
  await page.getByRole("button", { name: /서울역.*서울시 중구/ }).first().click();
  await page.getByRole("button", { name: "이 장소 선택하기", exact: true }).click();
  await search.fill("검정 지갑");
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.getByText("전체 120건 중 검색 결과 60건 · 유사도 높은 순", { exact: true }).waitFor();
  await page.getByText("선택한 장소 1곳", { exact: false }).click();
  await page.getByText("추가로 입력하고 싶은 장소가 있나요?", { exact: true }).waitFor();
  await page.getByRole("button", { name: "+ 장소 추가", exact: true }).click();
  await placeInput.fill("추가 장소");
  await page.getByRole("button", { name: "장소 검색", exact: true }).click();
  await page.getByRole("button", { name: /추가 장소.*서울시 중구/ }).click();
  const [combinedResponse] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === "/api/found-items"),
    page.getByRole("button", { name: "이 장소 추가하기", exact: true }).click(),
  ]);
  const combinedParams = new URL(combinedResponse.url()).searchParams;
  const combinedIds = combinedParams.get("location_ids").split(",");
  check(combinedIds.join(",") === "77,88,89,90" && combinedParams.get("q") === "검정 지갑", "중복 기관을 제거하고 기존 검색어로 두 장소 주변을 함께 조회해야 한다");
  await page.getByRole("heading", { name: "2개 장소 주변 습득물" }).waitFor();
  check(await page.locator(".map-radius-halo").count() === 2, "지도에 선택한 두 장소의 반경을 표시해야 한다");
  await page.getByRole("button", { name: "+ 장소 추가", exact: true }).click();
  await placeInput.fill("세 번째 장소");
  await page.getByRole("button", { name: "장소 검색", exact: true }).click();
  await page.getByRole("button", { name: /세 번째 장소.*서울시 중구/ }).click();
  await page.getByRole("button", { name: "이 장소 추가하기", exact: true }).click();
  await page.getByRole("heading", { name: "3개 장소 주변 습득물" }).waitFor();
  await page.getByRole("button", { name: "+ 장소 추가", exact: true }).waitFor({ state: "detached" });

  await page.getByRole("button", { name: "새 물건 알림 받기", exact: true }).click();
  const alertPlaces = alertDialog.locator(".alert-place-list li");
  await alertPlaces.first().waitFor();
  check(await alertPlaces.count() === 3, "새 물건 알림 창으로 선택한 3곳을 모두 전달해야 한다");
  await alertDialog.getByRole("button", { name: "알림 조건 수정하기" }).click();
  await alertDialog.getByRole("button", { name: "세 번째 장소 알림에서 제외" }).click();
  await alertDialog.getByRole("button", { name: "취소", exact: true }).click();
  check(await alertPlaces.count() === 3, "편집 취소 시 제외한 장소를 되돌려야 한다");
  await alertDialog.getByRole("button", { name: "알림 조건 수정하기" }).click();
  await alertDialog.getByRole("button", { name: "세 번째 장소 알림에서 제외" }).click();
  await alertDialog.getByRole("button", { name: "수정 완료" }).click();
  check(await alertPlaces.count() === 2, "수정 완료 후에는 제외한 장소를 알림 조건에서 빼야 한다");
  await alertDialog.getByRole("button", { name: /닫기/ }).click();
  await alertDialog.waitFor({ state: "detached" });
  await page.getByRole("heading", { name: "3개 장소 주변 습득물" }).waitFor();
  await page.getByText("선택한 장소 3곳", { exact: false }).click();
  await page.getByRole("button", { name: "세 번째 장소 제거", exact: true }).click();
  await page.getByRole("heading", { name: "2개 장소 주변 습득물" }).waitFor();
  const waypointMenu = page.locator(".waypoint-menu");
  if (await waypointMenu.evaluate(menu => menu.open)) await waypointMenu.locator("summary").click();
  await page.setViewportSize({ width: 1440, height: 760 });
  const pagePositionBeforeMenu = await page.locator(".item-pagination").boundingBox();
  await page.getByText("선택한 장소 2곳", { exact: false }).click();
  check((await page.locator(".item-pagination").boundingBox()).y === pagePositionBeforeMenu.y, "장소 목록을 열어도 페이지 번호 위치가 바뀌면 안 된다");
  check(await page.locator(".search-panel").evaluate(panel => panel.scrollHeight <= panel.clientHeight + 1), "데스크톱 검색 패널은 스크롤 없이 결과를 표시해야 한다");
  await page.getByRole("button", { name: "추가 장소 제거", exact: true }).click();
  await page.getByRole("heading", { name: "서울역 주변 습득물" }).waitFor();
  check(await search.inputValue() === "검정 지갑", "장소 제거 후에도 마지막 검색어를 유지해야 한다");
  await page.goto("http://127.0.0.1:5178/");
  // PRD 4.5 — 기관이 발견된 단계에서 멈추고, 최대 반경까지 비어 있으면 물품 API를 호출하지 않는다.
  async function confirmPlaceWithInstitutionsFrom(minimumId) {
    minimumInstitutionId = minimumId;
    await page.reload();
    await page.getByRole("searchbox", { name: "검색어", exact: true }).fill("서울역");
    await page.getByRole("button", { name: "장소 검색", exact: true }).click();
    await page.getByRole("button", { name: /서울역.*서울시 중구/ }).first().click();
    await previewCard.getByRole("button", { name: "이 장소 선택하기", exact: true }).click();
  }
  await Promise.all([
    page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === "/api/found-items" && url.searchParams.get("location_ids") === "89";
    }),
    confirmPlaceWithInstitutionsFrom(89),
  ]);
  await page.getByText("현재 반경 1.5km", { exact: true }).waitFor();
  await page.getByText("1km 안에 보관기관이 없어 1.5km로 넓혔어요.", { exact: true }).waitFor();
  const requestsBeforeEmptyScope = itemRequests;
  await confirmPlaceWithInstitutionsFrom(91);
  await page.getByText("현재 반경 2km", { exact: true }).waitFor();
  await page.getByText("1.5km 안에 보관기관이 없어 2km로 넓혔어요.", { exact: true }).waitFor();
  await page.getByText("반경 2km 안에 등록된 보관기관이 없습니다.", { exact: true }).waitFor();
  check(itemRequests === requestsBeforeEmptyScope, "최대 반경에도 기관이 없으면 물품 API를 호출하지 않아야 한다");
  check(errors.length === 0, `브라우저 오류: ${errors.join(", ")}`);
  return "PASS: manual and automatic radius expansion, alert radius, paging, desktop/mobile details, loading/empty/error/disabled";
}
