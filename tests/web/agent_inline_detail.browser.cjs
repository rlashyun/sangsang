// PRD 4.3-A · 4.3-C — 대화 상세 카드 교체와 예·아니요 흐름. 모든 외부 요청은 모의 응답.
// Playwright run-code로 로컬 8000 서버에서 실행한다.
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    class LatLng {
      constructor(lat, lng) { this.lat = lat; this.lng = lng; }
      getLat() { return this.lat; }
      getLng() { return this.lng; }
    }
    class FakeObject {
      constructor(_, options) { if (_?.zIndex === 10) window.testFocusedMarker = {lat: _.position.getLat(), lng: _.position.getLng()}; this.center = options?.center || new LatLng(37.55, 126.97); }
      getCenter() { return this.center; }
      getPosition() { return this.options?.position || this.center; }
      getProjection() { return {
        containerPointFromCoords: point => ({ x: point.getLng() * 1000, y: point.getLat() * 1000 }),
        coordsFromContainerPoint: point => new LatLng(point.y / 1000, point.x / 1000),
      }; }
      setCenter(value) { this.center = value; window.testFocusMoves = (window.testFocusMoves || 0) + 1; }
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
      CustomOverlay: class {
        setContent(content) { this.content = content; }
        setPosition() {}
        setMap(map) { if (map) document.getElementById("map").append(this.content); else this.content?.remove(); }
      },
      ZoomControl: FakeObject, MapTypeControl: FakeObject, InfoWindow: FakeObject,
      Marker: FakeObject, MarkerClusterer: FakeObject, MarkerImage: FakeObject,
      Size: FakeObject, Point: class { constructor(x, y) { this.x = x; this.y = y; } }, Circle: FakeObject, LatLngBounds: FakeObject,
      ControlPosition: {}, MapTypeId: { ROADMAP: 1, HYBRID: 3 }, event: { addListener() {}, removeListener() {} },
    } };
  });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (url.pathname.startsWith('/test-photo/')) {
        if (url.pathname.includes('failed')) return route.fulfill({status: 404, body: ''});
        const [w, h] = url.pathname.includes('portrait') ? [80, 160] : [160, 80];
        return route.fulfill({contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="100%" height="100%" fill="#c6b098"/><rect x="2" y="2" width="${w-4}" height="${h-4}" fill="none" stroke="#6b5440" stroke-width="4"/></svg>`});
      }
      if (url.pathname === '/api/institutions') return route.fulfill({json: {institutions: [{storage_location_id: 42, name: '강남역 보관소', address: '서울 강남구 강남대로 지하 396 (역삼동, 강남역)', latitude: 37.501, longitude: 127.031, source: 'partner', phone: '02-1234-5678', item_count: 4}], counts_available: true}});
      if (url.pathname === '/api/search-chat/turns') {
        const items = ['landscape', 'portrait', 'failed', 'none'].map((kind, i) => ({
          item_id: i + 1, institution_id: i === 3 ? 9999 : 42,
          item_name: i === 1 ? '갈색 지퍼와 긴 끈이 달려 있는 검정색 여성용 카드 지갑 긴 물품명 표시 확인' : '검정 지퍼지갑',
          item_source_code: 'partner', description: '갈색 지갑(브라운(갈)색)을 습득하여 보관하고 있습니다.', raw_category_name: '지갑 > 여성용 지갑', color_name: i === 3 ? null : '검정', registered_on: i === 3 ? null : '2026-11-18',
          raw_storage_name: i === 3 ? null : '강남역 보관소', image_url: kind === 'none' ? null : '/test-photo/' + kind,
        }));
        const response = {session_id: 's', run_id: 'r', revision: 1, status: 'results', message: '유사한 습득물을 찾았어요.', place_candidates: [],
          conditions: {place: {candidate_id: 'p', name: '강남역', address: '서울', latitude: 37.5, longitude: 127.03}, item_query: '검정 지갑', radius_m: 1000, institution_ids: [42]},
          search_attempts: [{query: '검정 지갑'}], recommended_item_ids: [1, 2, 3], matches: {items, total_count: items.length, next_cursor: null}, error: null};
        return route.fulfill({contentType: 'application/x-ndjson', body: JSON.stringify({type: 'run.finished', response}) + '\n'});
      }
      return route.continue();
    });

    for (const viewport of [{width:1440,height:1000},{width:1440,height:768},{width:390,height:844}]) {
      await page.setViewportSize(viewport);
      await page.goto('http://127.0.0.1:8000/');
      await page.getByRole('textbox',{name:'메시지'}).fill('강남역 검정 지갑');
      await page.getByRole('button',{name:'보내기',exact:true}).click();
      await page.locator('.agent-item').first().waitFor();
      await page.locator('.agent-result-followup').waitFor();
      const messages=await page.locator('.agent-feed > .agent-bubble').count();
      const chatScroll=await page.locator('.agent-feed').evaluate(e=>{e.scrollTop=0;return e.scrollTop;});
      await page.evaluate(()=>{window.testFocusMoves=0;window.testFocusedMarker=null;});
      await page.locator('.agent-item').first().click();
      await page.getByRole('region',{name:'선택한 물품 상세',exact:true}).waitFor();
      check(await page.getByRole('dialog',{name:'물품 상세정보',exact:true}).count()===0,'No detail modal');
      check(await page.locator('.agent-result-followup > p').evaluate(e=>{const p=e.getBoundingClientRect(),f=e.closest('.agent-feed').getBoundingClientRect();const card=e.closest('.agent-feed').querySelector('.agent-chat-detail').getBoundingClientRect();return p.bottom>f.top&&p.top<f.bottom||card.top>=f.top-1;}),'Previous conversation stays in view');check(await page.locator('.agent-feed .agent-detail-card.agent-bubble-agent').count()===1,'One detail chat bubble');check(await page.getByRole('textbox',{name:'메시지'}).isVisible(),'Composer stays visible');
      const focus=await page.evaluate(()=>({moves:window.testFocusMoves,pin:window.testFocusedMarker}));
      check(focus.moves>0&&focus.pin?.lat===37.501,'Institution pin focus retained');
      if(viewport.width>820){
        await page.locator('.agent-item').nth(1).click();
        check(await page.locator('.agent-detail-view').count()===1,'One replaceable detail');
        check((await page.locator('.agent-detail-view .item-detail-name').innerText()).includes('긴 물품명'),'Second item replaces first');
      }
      check(await page.locator('.agent-feed > .agent-bubble').count()===messages,'Details do not accumulate as messages');
      check(await page.locator('.agent-detail-heading').count()===0,'No separate detail header');
      check(await page.locator('.agent-detail-view .item-detail-description').count()===0,'Descriptions are omitted from the UI');
      const summary=await page.locator('.agent-detail-card').evaluate(e=>{const title=e.querySelector('.item-detail-name').getBoundingClientRect(),meta=e.querySelector('.item-detail-basics').getBoundingClientRect(),photo=e.querySelector('.item-detail-photo').getBoundingClientRect();return {gap:meta.top-title.bottom,aligned:Math.abs(title.top-photo.top),columns:Math.abs(title.left-meta.left)};});
      check(Math.abs(summary.gap-12)<1&&summary.aligned<1&&summary.columns<1,'Photo and summary form a compact aligned group: '+JSON.stringify(summary));
      const bubbleWidths=await page.evaluate(()=>({detail:document.querySelector('.agent-detail-card').getBoundingClientRect().width,chat:document.querySelector('.agent-result-followup > p').getBoundingClientRect().width}));
      check(Math.abs(bubbleWidths.detail-bubbleWidths.chat)<1,'Existing message width retained');
      await page.getByRole('button',{name:/사진 크게 보기$/}).click();
      await page.getByRole('dialog',{name:'물품 사진 확대',exact:true}).waitFor();
      await page.getByRole('button',{name:'사진 닫기',exact:true}).click();
      await page.keyboard.press('Escape');
      await page.locator('.agent-feed').waitFor();
      check(Math.abs((await page.locator('.agent-feed').evaluate(e=>e.scrollTop))-chatScroll)<1,'Chat scroll restored');
      await page.locator('.agent-item').first().click();
      const yes=page.locator('.agent-detail-view').getByRole('button',{name:'예',exact:true});
      if(viewport.width>820){
        const fit=await page.locator('.agent-chat-detail').evaluate(e=>{const photo=e.querySelector('.item-detail-photo').getBoundingClientRect(),end=e.querySelector('.agent-detail-answers').getBoundingClientRect(),feed=e.closest('.agent-feed').getBoundingClientRect();return {top:photo.top-feed.top,bottom:feed.bottom-end.bottom,height:e.getBoundingClientRect().height,available:feed.height};});
        check(fit.top>=-1&&fit.bottom>=-1,'Photo through yes/no fits without scrolling: '+JSON.stringify(fit));
      }
      await page.mouse.move(1400,40);
      check(await page.locator('.agent-item.is-selected[aria-pressed="true"]').count()===1,'Persistent selected row');
      await yes.scrollIntoViewIfNeeded();
      const fits=await page.locator('.agent-detail-view').evaluate(e=>e.scrollWidth<=e.clientWidth);
      check(fits,'No horizontal overflow');
      await yes.click();
      const yesRecord=page.locator('.agent-chat-detail.is-recorded[data-answer="yes"]');
      await yesRecord.waitFor();
      check(await yesRecord.locator('.agent-detail-card').count()===1,'Answered detail retained');
      check(await page.locator('.agent-chat-detail:not(.is-recorded)').count()===0,'Answered card becomes history');
      check(await yesRecord.getByRole('list',{name:'수령 순서'}).locator('li').count()===3,'All pickup steps');
      check(await yesRecord.locator('.agent-pickup-card').getByRole('link',{name:'02-1234-5678',exact:true}).getAttribute('href')==='tel:0212345678','Correct contact');
      if(viewport.width>820){
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        const pickupFit=await page.evaluate(()=>{const card=document.querySelector('.agent-pickup-card').getBoundingClientRect(),feed=document.querySelector('.agent-feed').getBoundingClientRect();return {top:card.top-feed.top,bottom:feed.bottom-card.bottom,height:card.height,available:feed.height};});
        check(pickupFit.top>=-1&&pickupFit.bottom>=-1,'Compact pickup fits: '+JSON.stringify(pickupFit));
      }
      await yesRecord.getByText('방문 전 참고사항 · 공식 안내',{exact:true}).click();
      check(await yesRecord.getByRole('link',{name:'공식 수령 안내 참고 ↗',exact:true}).isVisible(),'Official source remains accessible');
      await yesRecord.getByText('방문 전 참고사항 · 공식 안내',{exact:true}).click();
      await yesRecord.getByRole('button',{name:'확인했어요',exact:true}).click();
      await page.getByText('물건을 꼭 되찾으시길 바라요. 리트리버가 도움이 되었다면 짧은 설문으로 의견을 남겨주세요.',{exact:true}).waitFor();
      check(await yesRecord.locator('.agent-detail-card').count()===1&&await yesRecord.locator('.agent-pickup-card').count()===1,'Confirmation preserves detail and guide');
      check(await yesRecord.getByRole('button',{name:'확인했어요',exact:true}).isDisabled(),'Confirm only once');
      check(await yesRecord.locator('.agent-survey-reply').count()===1,'One survey reply');
      check(await yesRecord.locator('.agent-survey-reply').getAttribute('href')===await page.locator('.app-header-actions a[target="_blank"]').getAttribute('href'),'Existing survey URL');
      check(await yesRecord.locator('.agent-survey-reply').getAttribute('target')==='_blank','Survey opens new tab');
      await yesRecord.getByRole('button',{name:'물품 정보 다시 보기',exact:true}).click();
      check(await yesRecord.locator('.agent-pickup-card').count()===1,'Review does not remove pickup history');
      if(viewport.width<=820) await page.getByRole('button',{name:'결과 4건 보기',exact:true}).click();
      await page.locator('.agent-item').nth(1).click();
      const pending=page.locator('.agent-chat-detail:not(.is-recorded)');
      check(await yesRecord.count()===1,'New selection preserves answered detail');
      await pending.getByRole('button',{name:'아니요',exact:true}).click();
      const noRecord=page.locator('.agent-chat-detail.is-recorded[data-answer="no"]');
      await noRecord.waitFor();
      check(await noRecord.locator('.agent-detail-card').count()===1,'No also preserves detail');
      check(await yesRecord.count()===1,'Previous yes history remains');
      await page.getByText('다른 물건도 살펴보세요. 찾는 물건이 없으면 아래 선택지를 이용해 보세요.',{exact:true}).waitFor();
      check(await page.locator('.agent-result-actions .agent-action-chip').count()===3,'Three search options retained');
      const option=page.locator('.agent-result-actions button').first();
      await option.scrollIntoViewIfNeeded();
      check(await option.evaluate(e=>{const r=e.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('button')===e;}),'Followup is not covered by mobile results');
      if(viewport.width<=820) await page.getByRole('button',{name:'결과 4건 보기',exact:true}).click();
      await page.locator('.agent-item').nth(2).click();
      await pending.getByText('사진을 불러오지 못했습니다.',{exact:true}).waitFor();
      check(await pending.getByRole('button',{name:'사진 다시 불러오기',exact:true}).count()===1,'Photo failure supports retry');
      await page.keyboard.press('Escape');
      check(await page.locator('.agent-chat-detail.is-recorded').count()===2,'Only unanswered preview is dismissed');
      console.log('PASS replaceable inline detail / map focus / history and scroll / zoom / yes pickup / no followup choices: '+viewport.width);
    }
    check(!errors.length,errors.join('\n'));

};
