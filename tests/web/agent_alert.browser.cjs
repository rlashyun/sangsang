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

  let calls = 0, mails = 0;
  const place = {candidate_id: "p", name: "서울역", address: "서울", latitude: 37.55, longitude: 126.97};
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") return route.abort();
    if (url.pathname === "/__KAKAO_SDK_URL__") return route.fulfill({body: ""});
    if (url.pathname === "/api/institutions") return route.fulfill({json:{institutions:[],counts_available:true}});
    if (url.pathname === "/api/alerts") {
      mails++;
      const payload = route.request().postDataJSON();
      check(payload.email === "user@example.com" && payload.consent && payload.places.length === 1, "reservation payload");
      if (mails % 2) return route.fulfill({status:503,json:{error:"일시 실패: 다시 입력해 주세요"}});
      return route.fulfill({status:202,json:{status:"pending"}});
    }
    if (url.pathname === "/api/search-chat/turns") {
      calls++;
      check(!route.request().postData().includes("@"), "email leaked into model route");
      const response = {session_id:"s",run_id:"r",revision:calls,status:"answered",prepare_alert:true,message:"알림 조건 확인",conditions:{place,places:[place],item_query:"지갑",radius_m:1000,institution_ids:[42]},place_candidates:[],search_attempts:[],recommended_item_ids:[],matches:null,error:null};
      return route.fulfill({contentType:"application/x-ndjson",body:JSON.stringify({type:"run.finished",response})+"\n"});
    }
    return route.continue();
  });
  for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
    await page.setViewportSize(viewport);
    await page.goto("http://127.0.0.1:5178/?search=chat");
    const input=page.getByRole("textbox",{name:"메시지"});
    await input.fill("알림 받고 싶어요"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByLabel("알림 신청 조건").waitFor();
    check(await page.locator(".agent-feed .agent-alert-setup").count() === 1, "alert must scroll with messages");
    // 동의 전에도 취소할 수 있고 이메일만 강요하지 않는다.
    const beforeCancel = calls;
    await input.fill("취소할게"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByText("진행 중인 알림 신청을 취소했어요.",{exact:false}).waitFor();
    check(calls === beforeCancel, "cancel reached model");
    check(await page.getByLabel("알림 신청 조건").count() === 0, "cancel kept draft");
    await input.fill("알림 받고 싶어요"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByLabel("개인정보 수집·이용에 동의해요").waitFor();
    await input.fill("반경을 넓혀줘"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByLabel("개인정보 수집·이용에 동의해요").check();
    const before=calls;
    await input.fill("user@example.com"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByText("일시 실패: 다시 입력해 주세요",{exact:true}).waitFor();
    await input.fill("user@example.com"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByText("확인 메일을 보냈어요.",{exact:false}).waitFor();
    check(calls===before,"email invoked chat API");
    check(!(await page.locator(".agent-feed").innerText()).includes("user@example.com"),"email in history");
  }
  check(!errors.length, errors.join(";"));
}
