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
      getPosition() { return this.center; }
      getProjection() { return {
        containerPointFromCoords: point => ({ x: point.getLng() * 1000, y: point.getLat() * 1000 }),
        coordsFromContainerPoint: point => new LatLng(point.y / 1000, point.x / 1000),
      }; }
      setCenter(value) { this.center = value; }
      getLevel() { return this.level ?? 8; }
      setMapTypeId(value) { this.mapTypeId = value; }
      addControl() {} setBounds() {} extend() {} setMap() {} clear() {}
      addMarkers(markers) { window.testMapMarkerCount = markers.length; }
      setContent() {} setPosition() {} open() {} close() {}
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
      ZoomControl: FakeObject, MapTypeControl: FakeObject, InfoWindow: FakeObject, CustomOverlay: FakeObject,
      Marker: FakeObject, MarkerClusterer: FakeObject, MarkerImage: FakeObject,
      Size: FakeObject, Point: class { constructor(x, y) { this.x = x; this.y = y; } }, Circle: FakeObject, LatLngBounds: FakeObject,
      ControlPosition: {}, MapTypeId: { ROADMAP: 1, HYBRID: 3 }, event: { addListener() {}, removeListener() {} },
    } };
  });

  let calls = 0, mails = 0, editedMails = 0, institutionCalls = 0;
  const place = {candidate_id: "p", name: "서울역", address: "서울", latitude: 37.55, longitude: 126.97};
  let multiplePlaces = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") return route.abort();
    if (url.pathname === "/__KAKAO_SDK_URL__") return route.fulfill({body: ""});
    if (url.pathname === "/api/institutions") { institutionCalls++; return route.fulfill({json:{institutions:[],counts_available:true}}); }
    if (url.pathname === "/api/alerts") {
      const payload = route.request().postDataJSON();
      if (payload.email === 'edited@example.com') {
        editedMails++;
        check(payload.query === '검정 가방' && payload.radius_m === 1500 && payload.places[0].name === '강남역' && payload.consent,
          'Reservation uses newly confirmed item, place and radius');
        return route.fulfill({status:503,json:{error:'수정 조건 시험 오류'}});
      }
      mails++;
      check(payload.email === "user@example.com" && payload.consent && payload.places.length === 1, "reservation payload");
      if (mails % 2) return route.fulfill({status:503,json:{error:"일시 실패: 다시 입력해 주세요"}});
      return route.fulfill({status:202,json:{status:"pending",reservation_id:7}});
    }
    if (url.pathname === "/api/search-chat/turns") {
      calls++;
      check(!route.request().postData().includes("@"), "email leaked into model route");
      const response = {session_id:"s",run_id:"r",revision:calls,status:"answered",prepare_alert:true,message:"알림 조건 확인",conditions:{place,places:[place],item_query:"지갑",radius_m:1000,institution_ids:[42]},place_candidates:[],search_attempts:[],recommended_item_ids:[],matches:null,error:null};
      const turn = route.request().postDataJSON();
      const newPlace = {...place, candidate_id:'gangnam', name:'강남역', address:'서울 강남구', latitude:37.5, longitude:127.03};
      if (turn.text === '알림 받고 싶어요') Object.assign(response,{status:'results',
        conditions:{...response.conditions, places:[place,newPlace,{...place,candidate_id:'jamsil',name:'잠실역',latitude:37.51,longitude:127.1}]},
        matches:{items:[{id:1,item_id:1,institution_id:42,item_name:'기존 검색 결과 지갑',image_url:null}],total_count:1,next_cursor:null}});
      if (turn.text === '여러 장소 알림 신청') {
        multiplePlaces = [place, newPlace, {...place, candidate_id:'jamsil', name:'잠실역', latitude:37.51, longitude:127.1}];
        response.conditions = {...response.conditions, places:multiplePlaces};
      }
      if (turn.type === 'remove_place' || turn.type === 'change_radius') {
        if (turn.type === 'remove_place') multiplePlaces = multiplePlaces.filter((_, index) => index + 1 !== turn.place_target);
        Object.assign(response, {status:'no_matches', prepare_alert:false, message:'수정한 조건으로 검색했어요.',
          conditions:{...response.conditions, place:multiplePlaces[0] ?? place, places:multiplePlaces.length ? multiplePlaces : [place], radius_m:turn.radius_m ?? 1000},
          matches:{items:[],total_count:0,next_cursor:null}});
      }
      if (route.request().postData().includes("서울역 지갑")) {
        Object.assign(response, {status:"no_matches",prepare_alert:false,message:"일치하는 물건을 찾지 못했어요.",matches:{items:[],total_count:0,next_cursor:null}});
      }
      return route.fulfill({contentType:"application/x-ndjson",body:JSON.stringify({type:"run.finished",response})+"\n"});
    }
    return route.continue();
  });
  for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
    multiplePlaces = [];
    await page.setViewportSize(viewport);
    await page.goto("http://127.0.0.1:5178/?search=chat");
    const input=page.getByRole("textbox",{name:"메시지"});
    await input.fill("알림 받고 싶어요"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByLabel("알림 신청 조건").waitFor();
    check(await page.locator(".agent-feed .agent-alert-setup").count() === 1, "alert must scroll with messages");
    const privacy = page.locator('.agent-alert-privacy');
    check(await privacy.getAttribute('open') === null, 'Privacy details start collapsed');
    await privacy.locator('summary').click();
    await page.waitForFunction(()=>{const feed=document.querySelector('.agent-feed');return feed.scrollHeight-feed.clientHeight-feed.scrollTop<2;});
    check((await privacy.innerText()).includes('동의를 거부할 수 있어요.'), 'Full consent and refusal information remains available');
    check(await input.evaluate(element => {const r = element.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;}), 'Expanded privacy keeps composer visible');
    await privacy.locator('summary').click();
    check(await page.locator('.agent-alert-setup').evaluate(element => element.scrollWidth <= element.clientWidth), 'Alert messages fit desktop/mobile width');
    check(await input.getAttribute('inputmode') === 'email', 'Email setup uses email keyboard');
    await page.getByLabel('개인정보 수집·이용에 동의해요').check();
    const beforeEdit = calls, beforeEditMails = mails, beforeInstitutions = institutionCalls;
    const searchSummary = await page.locator('.agent-summary').innerText();
    await page.getByRole('button',{name:'결과 1건 보기',exact:true}).click();
    const originalResult = await page.locator('.agent-item').innerText();
    await page.getByRole('button',{name:'결과 목록 접기',exact:true}).click();
    const originalConditions = await page.locator('.agent-alert-setup .agent-alert-conditions').innerText();
    await page.getByRole('button', {name:'조건 수정', exact:true}).click();
    await page.getByText('어떤 조건을 바꿀까요? 장소, 물건, 검색 반경을 말씀해 주세요.', {exact:true}).waitFor();
    check(calls === beforeEdit && mails === beforeEditMails && await page.getByLabel('알림 신청 조건').count() === 0,
      'Edit button asks in chat without requesting a search or changing a reservation');
    check(await page.getByLabel('이전 알림 조건', {exact:true}).last().innerText() === originalConditions,
      'Previous conditions remain as an immutable chat message when editing');
    await input.fill('물건 변경'); await page.getByRole('button',{name:'보내기',exact:true}).click();
    await input.fill('검정 가방'); await page.locator('.agent-compose').evaluate(form=>{for(let i=0;i<10;i++)form.requestSubmit();});
    await page.getByLabel('알림 신청 조건').waitFor();
    check((await page.locator('.agent-alert-setup .agent-alert-conditions').innerText()).includes('검정 가방') && calls === beforeEdit,
      'Item edit updates alert draft locally even under repeated submits');
    check(!await page.getByLabel('개인정보 수집·이용에 동의해요').isChecked(), 'Editing resets consent');
    await page.getByRole('button', {name:'조건 수정', exact:true}).click();
    await input.fill('장소 변경'); await page.getByRole('button',{name:'보내기',exact:true}).click();
    await page.locator('.agent-alert-place-options').getByRole('button',{name:'서울역 제거',exact:true}).click();
    await page.getByLabel('알림 신청 조건').waitFor();
    check(!(await page.locator('.agent-alert-setup .agent-alert-conditions').innerText()).includes('서울역') && calls === beforeEdit,
      'Place removal changes only alert draft without search');
    await page.getByRole('button',{name:'조건 수정',exact:true}).click();
    await page.getByRole('button',{name:'장소 변경',exact:true}).click();
    await page.locator('.agent-alert-place-options').getByRole('button',{name:'잠실역 제거',exact:true}).click();
    await page.getByRole('button',{name:'조건 수정',exact:true}).click();
    await page.getByRole('button',{name:'장소 변경',exact:true}).click();
    check(await page.locator('.agent-alert-place-options').getByRole('button',{name:'강남역 제거',exact:true}).isDisabled(),
      'Keep at least one location required by alert reservation');
    await page.getByRole('button',{name:'돌아가기',exact:true}).click();
    await page.getByRole('button', {name:'조건 수정', exact:true}).click();
    await input.fill('반경을 1.5km로 바꿔줘'); await page.getByRole('button',{name:'보내기',exact:true}).click();
    await page.getByLabel('알림 신청 조건').waitFor();
    check((await page.locator('.agent-alert-setup .agent-alert-conditions').innerText()).includes('1.5km') && !await page.getByLabel('개인정보 수집·이용에 동의해요').isChecked(),
      'Radius change is reflected and requires fresh consent');
    check(calls === beforeEdit && institutionCalls === beforeInstitutions && await page.locator('.agent-summary').innerText() === searchSummary,
      'Alert edits do not query institutions/items or change current search conditions');
    check(await page.locator('.agent-status').count() === 0, 'No search progress notice during local alert edits');
    await page.getByRole('button',{name:'결과 1건 보기',exact:true}).click();
    check(await page.locator('.agent-item').innerText() === originalResult, 'Current item results remain unchanged after editing alert conditions');
    await page.getByRole('button',{name:'결과 목록 접기',exact:true}).click();
    check(mails === beforeEditMails, 'Condition edits never submit a reservation');
    const beforeEditedMails = editedMails;
    await input.fill('edited@example.com'); await page.getByRole('button',{name:'보내기',exact:true}).click();
    await page.getByText('먼저 개인정보 수집·이용에 동의해 주세요.',{exact:true}).waitFor();
    check(editedMails === beforeEditedMails, 'Previous consent cannot authorize edited conditions');
    await page.getByLabel('개인정보 수집·이용에 동의해요').check();
    await page.getByRole('button',{name:'보내기',exact:true}).click();
    await page.getByText('수정 조건 시험 오류',{exact:true}).waitFor();
    check(editedMails === beforeEditedMails + 1, 'Updated conditions reach reservation API only after fresh consent');
    // 동의 전에도 취소할 수 있고 이메일만 강요하지 않는다.
    const beforeCancel = calls;
    await input.fill("취소할게"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByText("진행 중인 알림 신청을 취소했어요.",{exact:false}).waitFor();
    check(calls === beforeCancel, "cancel reached model");
    check(await page.getByLabel("알림 신청 조건").count() === 0, "cancel kept draft");
    await input.fill('알림 받고 싶어요'); await page.getByRole('button', {name:'보내기', exact:true}).click();
    await page.getByLabel('알림 신청 조건').waitFor();
    check(await page.getByRole('button', {name:'신청 취소', exact:true}).count() === 0, 'No cancel button in alert signup');
    await page.getByRole('button', {name:'조건 수정', exact:true}).click();
    const beforeButtonCancel = calls;
    await page.getByRole('button', {name:'돌아가기', exact:true}).click();
    check(calls === beforeButtonCancel && await page.getByLabel('알림 신청 조건').count() === 1, 'Back returns to signup without cancelling or requesting the model');
    await input.fill("알림 받고 싶어요"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByLabel("개인정보 수집·이용에 동의해요").waitFor();
    await input.fill("반경을 넓혀줘"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByLabel('알림 검색 반경 선택').getByRole('button',{name:'2km',exact:true}).click();
    await page.getByLabel("알림 신청 조건").waitFor();
    await input.fill("서울역 지갑"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    const followup = page.locator(".agent-result-followup");
    const prompt = followup.getByText("찾으시는 물건이 없나요?", {exact:false});
    await prompt.waitFor();
    const daangn = followup.getByRole('link', {name:'당근에서 찾아보기'});
    check(await daangn.getAttribute('target') === '_blank', 'Daangn remains an external new-tab link');
    await daangn.evaluate(link => link.addEventListener('click', event => event.preventDefault(), {once:true}));
    await daangn.click();
    check(await page.locator('.agent-bubble-user').filter({hasText:'당근에서 찾아보기'}).count() === 1,
      'Daangn choice is recorded in chat');
    await followup.getByRole('button', {name:'반경 넓혀보기'}).click();
    await prompt.waitFor();
    check(await page.locator('.agent-bubble-user').filter({hasText:'반경 넓혀보기'}).count() === 1,
      'Radius expansion choice is recorded in chat');
    await followup.getByRole("button", {name:"비슷한 물건 알림 받기"}).click();
    check(await page.locator('.agent-bubble-user').filter({hasText:'비슷한 물건 알림 받기'}).count() === 1,
      'Alert choice is recorded in chat');
    const historicalPrompt = page.locator('.agent-feed > .agent-bubble-agent').filter({hasText:'찾으시는 물건이 없나요?'});
    check(await historicalPrompt.count() >= 2, 'Follow-up prompts remain after choosing actions');
    await page.getByLabel("개인정보 수집·이용에 동의해요").check();
    const before=calls;
    await input.fill("user@example.com"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByText("일시 실패: 다시 입력해 주세요",{exact:true}).waitFor();
    check(await page.getByText('확인 메일을 보냈어요.',{exact:true}).count() === 0, 'Failure never displays a sent confirmation');
    check(await historicalPrompt.count() >= 2, 'Mail failure preserves preceding chat prompts');
    await input.fill("user@example.com"); await page.getByRole("button",{name:"보내기",exact:true}).click();
    await page.getByText("확인 메일을 보냈어요.",{exact:false}).waitFor();
    await page.getByText('받은 메일의 확인 링크를 눌러 주세요. 24시간 안에 확인하면 알림이 시작돼요.',{exact:true}).waitFor();
    check(await followup.locator('> p').count() === 0 && await historicalPrompt.count() >= 2,
      'Success keeps the prompts in history and leaves current actions without a repeated prompt');
    check(await page.getByLabel('이전 알림 조건',{exact:true}).count() > 0, 'Sent signup keeps its conditions in chat');
    await followup.getByRole("button", {name:"반경 넓혀보기"}).waitFor();
    await followup.getByRole("button", {name:"비슷한 물건 알림 받기"}).waitFor();
    await followup.getByRole("link", {name:"당근에서 찾아보기"}).waitFor();
    check(calls===before,"email invoked chat API");
    check(!(await page.locator(".agent-feed").innerText()).includes("user@example.com"),"email in history");
    const confirmation = await page.context().newPage();
    await confirmation.setViewportSize(viewport);
    let confirms = 0;
    let confirmResult = 'link_expired';
    await confirmation.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (url.pathname === '/api/alerts/confirm') {
        confirms++;
        check(route.request().method() === 'POST', 'Confirmation uses POST');
        return route.fulfill({json:{result:confirmResult,message:confirmResult === 'link_expired' ? '확인 링크가 만료됐어요.' : '알림이 켜졌습니다.'}});
      }
      if (url.pathname === '/api/institutions') return route.fulfill({json:{institutions:[],counts_available:true}});
      return route.continue();
    });
    await confirmation.goto('http://127.0.0.1:5178/alerts/confirm?search=chat&token=7.00000000000000000000000000000000');
    await confirmation.getByText('확인 링크가 만료됐어요.',{exact:true}).waitFor();
    check(confirms === 1 && await page.getByText('확인되었습니다. 알림이 시작됐어요.',{exact:true}).count() === 0,
      'Expired links never announce success; StrictMode sends only one POST');
    confirmResult = 'confirmed';
    await confirmation.getByRole('button',{name:'다시 확인하기',exact:true}).click();
    await confirmation.getByText('확인되었습니다. 알림이 시작됐어요.',{exact:true}).waitFor();
    await page.getByText('확인되었습니다. 알림이 시작됐어요.',{exact:true}).waitFor();
    check(confirms === 2 && !confirmation.url().includes('token='), 'One POST per attempt and token removed');
    check(await confirmation.getByLabel('이전 알림 조건',{exact:true}).count() > 0, 'Mail tab restores signup conditions');
    check(!(await confirmation.locator('.agent-feed').innerText()).includes('user@example.com'), 'Restored chat excludes email');
    if (process.env.RETRIEVER_BROWSER_ARTIFACT_DIR) await confirmation.screenshot({path:`${process.env.RETRIEVER_BROWSER_ARTIFACT_DIR}/alert-return-${viewport.width}.png`});
    await confirmation.close();
    await input.fill('여러 장소 알림 신청'); await page.getByRole('button',{name:'보내기',exact:true}).click();
    await page.getByLabel('알림 신청 조건').waitFor();
    await page.getByRole('button',{name:'조건 수정',exact:true}).click();
    const beforePlaceChoices = calls;
    await input.fill('장소 변경'); await page.getByRole('button',{name:'보내기',exact:true}).click();
    await page.locator('.agent-alert-place-options').waitFor();
    check(calls === beforePlaceChoices && await page.locator('.agent-alert-place-options li').count() === 3,
      'Generic place-change request shows confirmed places locally');
    check(await page.locator('.agent-alert-place-options').getByRole('button').count() === 3,
      'Place list offers one remove button per location');
    await page.locator('.agent-alert-place-options').getByRole('button',{name:'강남역 제거',exact:true}).click();
    await page.getByLabel('알림 신청 조건').waitFor();
    check(calls === beforePlaceChoices, 'Removing an alert location does not submit a search turn');
    const updatedPlaces = await page.locator('.agent-alert-setup .agent-alert-conditions').innerText();
    check(updatedPlaces.includes('서울역') && updatedPlaces.includes('잠실역') && !updatedPlaces.includes('강남역'),
      'Remaining places are shown in updated alert conditions');
    await page.getByRole('button',{name:'조건 수정',exact:true}).click();
    const beforeRadiusChoices = calls;
    await input.fill('반경 변경'); await page.getByRole('button',{name:'보내기',exact:true}).click();
    const radiusChoices = page.getByLabel('알림 검색 반경 선택');
    await radiusChoices.waitFor();
    check(calls === beforeRadiusChoices && JSON.stringify(await radiusChoices.getByRole('button').allTextContents()) === JSON.stringify(['1km','1.5km','2km']),
      'Generic radius-change request offers exactly the supported radii without a model request');
    await radiusChoices.getByRole('button',{name:'1.5km',exact:true}).click();
    await page.getByLabel('알림 신청 조건').waitFor();
    check(calls === beforeRadiusChoices, 'Alert radius choice does not submit a search turn');
    check((await page.locator('.agent-alert-setup .agent-alert-conditions').innerText()).includes('1.5km') && !await page.getByLabel('개인정보 수집·이용에 동의해요').isChecked(),
      'Selected radius is reflected and consent remains unchecked');
    await page.getByRole('button',{name:'조건 수정',exact:true}).click();
    await page.getByRole('button',{name:'장소 변경',exact:true}).click();
    await page.locator('.agent-alert-place-options').waitFor();
    await page.getByRole('button',{name:'돌아가기',exact:true}).click();
    check(await page.getByLabel('알림 조건 수정', {exact:true}).count() === 0 && await page.getByLabel('알림 신청 조건').count() === 1,
      'Back exits guided editing and restores the current signup conditions');
  }
  check(!errors.length, errors.join(";"));
}
