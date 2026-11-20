// PRD 4.3-A, 5 — Real UI recovery with fake APIs and Kakao SDK only.
// Vite at 127.0.0.1:5178; playwright-cli run-code --filename <this file>.
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    class LatLng {
      constructor(lat, lng) { this.lat = lat; this.lng = lng; }
      getLat() { return this.lat; }
      getLng() { return this.lng; }
    }
    class FakeObject {
      constructor(_, options) { this.center = options?.center || new LatLng(37.55, 126.97); }
      getCenter() { return this.center; }
      getProjection() { return {
        containerPointFromCoords: point => ({ x: point.getLng() * 1000, y: point.getLat() * 1000 }),
        coordsFromContainerPoint: point => new LatLng(point.y / 1000, point.x / 1000),
      }; }
      setCenter(value) { this.center = value; }
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
      Size: FakeObject, Point: class { constructor(x, y) { this.x = x; this.y = y; } }, Circle: FakeObject, LatLngBounds: FakeObject,
      ControlPosition: {}, MapTypeId: { ROADMAP: 1, HYBRID: 3 }, event: { addListener() {}, removeListener() {} },
    } };
  });

  let requests = [];
  let release;
  const place = { candidate_id: "p", name: "서울역", address: "서울시", latitude: 37.55, longitude: 126.97 };
  const session = { session_id: "session-test", revision: 1, run_id: "run-test", trace_id: "trace-test" };
  const initial = { ...session, status: "awaiting_place_selection", message: "지도에 표시한 장소가 맞나요?",
    place_candidates: [place], conditions: { place: null, item_query: "지갑", radius_m: null, institution_ids: [] },
    search_attempts: [], recommended_item_ids: [], matches: null, error: null };
  function events(response) {
    return [JSON.stringify({ type: "run.started", seq: 1, session_id: session.session_id, run_id: session.run_id }),
      JSON.stringify({ type: "run.finished", seq: 2, run_id: session.run_id, response })].join("\n") + "\n";
  }
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") return route.abort();
    if (url.pathname === "/__KAKAO_SDK_URL__") return route.fulfill({ contentType: "application/javascript", body: "" });
    if (url.pathname === "/api/institutions") return route.fulfill({ json: { institutions: [], counts_available: true } });
    if (url.pathname === "/api/search-chat/turns") {
      requests.push(route.request().postDataJSON());
      if (requests.length === 1) {
        await new Promise(resolve => { release = resolve; });
        // Stream stops after run.started: the browser has no final revision.
        return route.fulfill({ contentType: "application/x-ndjson", body: events(initial).split("\n")[0] + "\n" });
      }
      let response = initial;
      if (requests.length === 3) response = { ...initial, status: "error", message: "시간 초과 시험",
        error: { code: "timeout", message: "검색 응답이 늦어 중단했어요. 같은 요청을 다시 시도해 주세요.", retryable: true } };
      if (requests.length >= 4) response = { ...initial, revision: 2, status: "no_matches", message: "조건에 맞는 물품이 없어요.",
        place_candidates: [], conditions: { place, item_query: "지갑", radius_m: 1000, institution_ids: [42] },
        matches: { items: [], total_count: 0, next_cursor: null } };
      if (requests.length === 5) return route.fulfill({ status: 409, json: { error: { code: "busy", message: "이전 검색을 처리하고 있어요. 잠시 후 같은 요청을 다시 시도해 주세요." } } });
      if (requests.length >= 6) response = { ...response, revision: 3, status: "results", message: "습득물을 찾았어요.",
        recommended_item_ids: [1], matches: { total_count: 1, next_cursor: null,
          items: [{ item_id: 1, institution_id: 42, item_name: "검정 지갑", raw_storage_name: "서울역 보관소", image_url: null }] } };
      return route.fulfill({ contentType: "application/x-ndjson", body: events(response) });
    }
    return route.continue();
  });
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    requests = [];
    release = null;
    await page.setViewportSize(viewport);
    await page.goto("http://127.0.0.1:5178/?search=chat");
    await page.getByRole("textbox", { name: "메시지" }).fill("서울역 지갑");
    await page.getByRole("button", { name: "보내기", exact: true }).click();
    await page.waitForFunction(() => document.querySelector(".agent-send").disabled);
    check(await page.getByRole("button", { name: "보내기", exact: true }).isDisabled(), "loading submit disabled");
    while (!release) await new Promise(resolve => setTimeout(resolve, 10));
    release();
    await page.getByRole("button", { name: "다시 시도", exact: true }).waitFor();
    await page.getByRole("textbox", { name: "메시지" }).fill("새 조건을 덮어쓰지 않아요");
    await page.getByRole("button", { name: "보내기", exact: true }).click();
    await page.getByText("이전 검색의 처리 결과를 먼저 확인해 주세요. ‘다시 시도’를 누르면 이어서 복구해요.", { exact: true }).waitFor();
    check(requests.length === 1, "unknown outcome must be recovered before a new turn");
    await page.getByRole("button", { name: "다시 시도", exact: true }).click();
    await page.getByRole("button", { name: /1 서울역/ }).click();
    check(JSON.stringify(requests[0]) === JSON.stringify(requests[1]), "response-loss retry must preserve full initial request");
    await page.getByRole("button", { name: "이 위치 선택하기", exact: true }).click();
    await page.getByText("검색 응답이 늦어 중단했어요. 같은 요청을 다시 시도해 주세요.", { exact: true }).waitFor();
    await page.getByRole("button", { name: "다시 시도", exact: true }).click();
    await page.getByText("조건에 맞는 물품이 없어요.", { exact: true }).waitFor();
    check(JSON.stringify(requests[2]) === JSON.stringify(requests[3]), "timeout retry must preserve full selection request");
    await page.getByRole("textbox", { name: "메시지" }).fill("검정색이었어요");
    await page.getByRole("button", { name: "보내기", exact: true }).click();
    await page.getByRole("button", { name: "다시 시도", exact: true }).click();
    await page.getByRole("button", { name: /검정 지갑/ }).waitFor();
    check(JSON.stringify(requests[4]) === JSON.stringify(requests[5]), "busy retry preserves ID and revision");
    check(requests[4].revision === 2, "new turn uses completed revision");
    check(requests[4].request_id !== requests[2].request_id, "new turn gets new ID");
    await page.screenshot({ path: `/tmp/retriever-agent-validation/recovery-${viewport.width}.png` });
  }
  check(errors.length === 0, errors.join("\n"));
  console.log("Desktop/mobile loading, disabled, interrupted response, timeout, busy, empty and success passed.");
}
