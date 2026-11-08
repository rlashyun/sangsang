// PRD 4.3-C · 5 — 실제 브라우저에서 수령 정보와 탐색 맥락 복귀를 검증한다.
// 실행: Vite(127.0.0.1:5178)와 playwright-cli 브라우저를 연 뒤
// playwright-cli run-code --filename tests/web/item_detail.browser.cjs
// 모든 외부 통신은 가짜 API와 지도 SDK로 대체한다.
async (page) => {
  function check(value, message) { if (!value) throw new Error(message); }
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  let responseMode = "success";
  let itemRequests = 0;
  let releaseResponse;
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
      ? "https://minwon24.police.go.kr/images/sub/img02_no_img.gif"
      : index === 2 ? "https://minwon24.police.go.kr/broken.png"
        : index === 3 ? null : "https://minwon24.police.go.kr/photo.svg",
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
      addControl() {} setBounds() {} extend() {} setMap() {} clear() {}
      addMarkers() {} setContent() {} open() {} close() {}
    }
    window.kakao = { maps: {
      load: callback => callback(), LatLng, Map: FakeObject,
      ZoomControl: FakeObject, MapTypeControl: FakeObject, InfoWindow: FakeObject,
      Marker: FakeObject, MarkerClusterer: FakeObject, MarkerImage: FakeObject,
      Size: FakeObject, Point: FakeObject, Circle: FakeObject, LatLngBounds: FakeObject,
      ControlPosition: {}, event: { addListener() {} },
    } };
  });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname === "minwon24.police.go.kr") {
      if (url.pathname === "/broken.png") return route.abort();
      check(url.pathname === "/photo.svg", "기본 그림은 요청하지 않아야 한다");
      return route.fulfill({ contentType: "image/svg+xml", body:
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"><rect width="240" height="160" fill="#543b2b"/><text x="40" y="90" fill="white">TEST WALLET</text></svg>' });
    }
    if (url.hostname !== "127.0.0.1") return route.abort();
    if (url.pathname === "/api/institutions") return route.fulfill({ json: {
      institutions: [
        { storage_location_id: 77, name: "서울역 유실물센터", address: "서울시 중구 시험로 1", phone: "02-1234-5678", latitude: 37.55, longitude: 126.97 },
        { storage_location_id: 88, name: "서울역 유실물센터", address: "", phone: "", latitude: 37.55, longitude: 126.97 },
      ], counts_available: true,
    } });
    if (url.pathname === "/api/places") return route.fulfill({ json: {
      documents: [{ place_name: "서울역", address_name: "서울시 중구", y: "37.55", x: "126.97" }],
    } });
    if (url.pathname === "/api/found-items") {
      itemRequests += 1;
      if (responseMode === "slow") await new Promise(resolve => { releaseResponse = resolve; });
      if (responseMode === "error") return route.fulfill({ status: 502, json: { error: "시험용 외부 장애" } });
      if (!url.searchParams.get("q") && responseMode !== "empty") {
        const laterPage = url.searchParams.has("cursor");
        return route.fulfill({ json: {
          items: laterPage ? items.slice(50) : items.slice(0, 50),
          total_count: laterPage ? null : items.length,
          next_cursor: laterPage ? null : "2026-11-07:50",
        } });
      }
      return route.fulfill({ json: { items: responseMode === "empty" ? [] : items, candidate_count: items.length } });
    }
    return route.continue();
  });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("http://127.0.0.1:5178/");
  await page.getByRole("searchbox", { name: "검색어", exact: true }).fill("서울역");
  await page.getByRole("button", { name: "장소 검색", exact: true }).click();
  await page.getByRole("button", { name: /서울역.*서울시 중구/ }).click();
  const cards = page.getByRole("button", { name: /검정 지갑 \d+ 상세 보기/ });
  await cards.first().waitFor();
  check(await cards.count() === 50, "정상 첫 페이지가 보여야 한다");
  await cards.first().click();
  await page.getByRole("dialog").getByRole("button", { name: /닫기/ }).click();
  await page.getByRole("dialog").waitFor({ state: "detached" });
  check(await cards.count() === 50, "상세를 닫으면 불러온 페이지를 유지해야 한다");
  await page.getByRole("button", { name: "물품 더 보기 (50 / 60건)" }).click();
  await cards.nth(59).waitFor();
  check(await cards.count() === 60, "상세를 닫은 뒤 기존 커서로 다음 페이지를 받아야 한다");
  const search = page.getByRole("searchbox", { name: "습득물 검색어" });
  await search.fill("검정 지갑");
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.getByText("후보 60건 중 유사한 물품 60건", { exact: true }).waitFor();
  const list = page.getByRole("list", { name: "습득물 목록" });
  const dialog = page.getByRole("dialog", { name: "물품 상세정보" });
  const close = () => dialog.getByRole("button", { name: /닫기/ });
  await cards.nth(12).scrollIntoViewIfNeeded();
  const scrollBefore = await list.evaluate(el => el.scrollTop);
  const requestsBefore = itemRequests;
  await cards.nth(12).click();
  await dialog.waitFor();
  await dialog.getByRole("img").waitFor({ state: "visible" });
  check(await dialog.getByText("원문 첫 줄\n둘째 줄 <script>원문</script>", { exact: true }).count() === 1, "원문은 안전한 텍스트로 표시해야 한다");
  check(await dialog.locator("script").count() === 0, "설명의 HTML을 실행하면 안 된다");
  check(await dialog.getByRole("link", { name: "02-1234-5678" }).getAttribute("href") === "tel:0212345678", "기관 ID의 연락처를 연결해야 한다");
  check(!(await dialog.innerText()).includes("유사도"), "상세에는 내부 점수가 없어야 한다");
  check(await close().evaluate(el => document.activeElement === el), "열면 닫기 버튼에 포커스해야 한다");
  await page.keyboard.press("Shift+Tab");
  check(await dialog.evaluate(el => el.contains(document.activeElement)), "포커스가 모달을 벗어나면 안 된다");
  await page.screenshot({ path: "/private/tmp/retriever-detail-desktop.png" });
  await close().click();
  await dialog.waitFor({ state: "detached" });
  check(await search.inputValue() === "검정 지갑", "검색어를 유지해야 한다");
  check(await list.evaluate(el => el.scrollTop) === scrollBefore, "목록 스크롤을 유지해야 한다");
  check(await cards.nth(12).evaluate(el => document.activeElement === el), "선택 카드로 포커스를 돌려야 한다");
  check(itemRequests === requestsBefore, "상세 열기·닫기로 재조회하면 안 된다");

  await cards.nth(12).press("Enter");
  await dialog.waitFor();
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  await cards.nth(12).click();
  await dialog.waitFor();
  await page.goBack();
  await dialog.waitFor({ state: "detached" });
  check(new URL(page.url()).pathname === "/", "뒤로가기가 앱을 떠나면 안 된다");
  await page.goForward();
  await dialog.waitFor();
  await page.mouse.click(4, 4);
  await dialog.waitFor({ state: "detached" });

  await cards.nth(4).click();
  await dialog.waitFor();
  await page.keyboard.press("Tab");
  const content = dialog.getByRole("region", { name: "물품 상세 내용" });
  check(await content.evaluate(el => document.activeElement === el), "긴 내용은 키보드로 접근할 수 있어야 한다");
  await page.keyboard.press("End");
  await page.waitForFunction(() => document.querySelector(".item-detail-content").scrollTop > 0);
  check(await close().isVisible(), "내용을 스크롤해도 닫기 버튼을 유지해야 한다");
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  await cards.nth(1).click();
  await dialog.getByText("등록된 사진이 없습니다.").waitFor();
  check(await dialog.locator("img").count() === 0, "기본 그림을 사진으로 노출하면 안 된다");
  await close().click();
  await dialog.waitFor({ state: "detached" });
  await cards.nth(2).click();
  await dialog.getByText("사진을 불러오지 못했습니다.").waitFor();
  await close().click();
  await dialog.waitFor({ state: "detached" });

  await page.setViewportSize({ width: 390, height: 844 });
  await cards.nth(3).click();
  await dialog.waitFor();
  await dialog.getByText("등록된 사진이 없습니다.").waitFor();
  check(await dialog.getByRole("link").count() === 0, "동명 기관의 전화번호를 대신 쓰면 안 된다");
  check(await dialog.getByText("정보 없음", { exact: true }).count() === 3, "누락 설명·주소·전화번호를 명시해야 한다");
  const bounds = await dialog.boundingBox();
  check(Math.abs(bounds.y + bounds.height - 844) < 2, "모바일 시트는 화면 하단에 붙어야 한다");
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "가로 스크롤이 없어야 한다");
  await page.screenshot({ path: "/private/tmp/retriever-detail-mobile.png" });
  await page.mouse.click(4, 4);
  await dialog.waitFor({ state: "detached" });

  responseMode = "slow";
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.getByText("'검정 지갑'와 유사한 물품을 찾고 있습니다…", { exact: true }).waitFor();
  check(await cards.first().isDisabled(), "목록 갱신 중 상세 열기를 막아야 한다");
  check(await page.getByRole("button", { name: "물품 검색", exact: true }).isDisabled(), "중복 검색을 막아야 한다");
  releaseResponse();
  await page.getByText("후보 60건 중 유사한 물품 60건", { exact: true }).waitFor();
  responseMode = "empty";
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.getByText("'검정 지갑'와 유사한 습득물이 없습니다.", { exact: true }).waitFor();
  check(await cards.count() === 0, "빈 결과에서는 상세 열기가 없어야 한다");
  responseMode = "error";
  await page.getByRole("button", { name: "물품 검색", exact: true }).click();
  await page.getByText("습득물 로딩 실패: 시험용 외부 장애", { exact: true }).waitFor();
  check(await cards.count() === 0, "오류 결과에서는 상세 열기가 없어야 한다");
  check(await search.inputValue() === "검정 지갑", "오류 후에도 재검색할 입력을 유지해야 한다");
  check(errors.length === 0, `브라우저 오류: ${errors.join(", ")}`);
  return "PASS: paging, desktop/mobile details, photos, missing fields, keyboard scrolling, history, scroll, loading/empty/error/disabled";
}
