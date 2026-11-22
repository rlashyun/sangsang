// PRD 4.3-A · 4.4 — 실제 터치 입력으로 모바일 시트와 대화 스크롤을 검증한다. 모든 API·지도는 모의 응답.
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    class LatLng { constructor(lat, lng) { this.lat = lat; this.lng = lng; } getLat() { return this.lat; } getLng() { return this.lng; } }
    class FakeMap {
      constructor() { this.center = new LatLng(37.5, 127.03); }
      getCenter() { return this.center; } getPosition() { return this.center; }
      getProjection() { return {
        containerPointFromCoords: point => ({x:point.getLng()*1000,y:point.getLat()*1000}),
        coordsFromContainerPoint: point => new LatLng(point.y/1000,point.x/1000),
      }; }
      setCenter(point) { this.center = point; } getLevel() { return 8; }
      setBounds(_, ...padding) { window.testMapPadding = padding; }
      setMapTypeId() {} addControl() {} extend() {} setMap() {} clear() {} addMarkers() {}
      setContent() {} setPosition() {} open() {} close() {} setImage() {} setZIndex() {} setLevel() {} panTo() {}
    }
    class Overlay {
      setMap(map) { if (this.map) this.onRemove(); this.map = map; if (map) { this.onAdd(); this.draw(); } }
      getPanels() { return {overlayLayer:document.getElementById('map')}; }
      getProjection() { return {pointFromCoords:point=>({x:point.getLng()*1000,y:-point.getLat()*1000})}; }
    }
    window.kakao = {maps:{load:cb=>cb(),LatLng,Map:FakeMap,AbstractOverlay:Overlay,
      ZoomControl:FakeMap,MapTypeControl:FakeMap,InfoWindow:FakeMap,CustomOverlay:FakeMap,Marker:FakeMap,
      MarkerClusterer:FakeMap,MarkerImage:FakeMap,Size:FakeMap,Point:class {constructor(x,y){this.x=x;this.y=y;}},
      Circle:FakeMap,LatLngBounds:FakeMap,ControlPosition:{},MapTypeId:{ROADMAP:1,HYBRID:3},event:{addListener(){},removeListener(){}}}};
  });
  let calls = 0;
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1') return route.abort();
    if (url.pathname === '/api/institutions') return route.fulfill({json:{institutions:[],counts_available:true}});
    if (url.pathname === '/api/search-chat/turns') {
      calls++;
      const place = {candidate_id:'p',name:'강남역',address:'서울 강남구',latitude:37.5,longitude:127.03};
      const items = Array.from({length:4},(_,i)=>({item_id:i+1,institution_id:42,item_name:`검정 지갑 ${i+1}`,image_url:null,item_source_code:'partner'}));
      const response = {session_id:'s',run_id:'r',revision:calls,status:'results',message:'새 유사 물품을 찾았어요.\n'+'대화를 스크롤해도 패널 높이는 유지돼요.\n'.repeat(50),
        conditions:{place,places:[place],item_query:'검정 지갑',radius_m:1000,institution_ids:[42]},place_candidates:[],search_attempts:[],recommended_item_ids:[],matches:{items,total_count:4,next_cursor:null},error:null};
      if (calls === 2) Object.assign(response,{status:'answered',matches:null});
      return route.fulfill({contentType:'application/x-ndjson',body:JSON.stringify({type:'run.finished',response})+'\n'});
    }
    return route.continue();
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('http://127.0.0.1:5178/?search=chat');
  const shell = page.locator('.agent-shell');
  const panel = page.getByRole('region',{name:'대화형 습득물 검색'});
  const grip = page.getByRole('button',{name:'채팅 패널 높이 조절',exact:true});
  await grip.waitFor();
  const snap = async expected => {
    await page.waitForFunction(value=>document.querySelector('.agent-shell')?.dataset.sheetSnap === value, expected);
    await page.waitForFunction(() => document.querySelector('.agent-shell')?.dataset.sheetDragging === 'false');
    await page.waitForFunction(value=>{
      const shell=document.querySelector('.agent-shell'), panel=document.querySelector('.agent-panel');
      const height=value==='expanded'?shell.clientHeight:value==='collapsed'?parseFloat(getComputedStyle(panel).minHeight):shell.clientHeight*.56;
      return Math.abs(panel.getBoundingClientRect().height-height)<3;
    },expected);
  };
  const size = await shell.boundingBox();
  check(Math.abs((await panel.boundingBox()).height - size.height*.56) < 3, 'Initial half sheet');
  check(await grip.evaluate(el=>getComputedStyle(el).touchAction) === 'none', 'Only grip captures touch dragging');
  const cdp = await page.context().newCDPSession(page);
  const drag = async (locator, delta, cancel = false) => {
    const box = await locator.boundingBox();
    const x = box.x + box.width/2, from = box.y + box.height/2;
    const to = Math.max(70,Math.min((await page.viewportSize()).height-12,from+delta));
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y:from}]});
    for(let i=1;i<=10;i++) {
      await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:from+(to-from)*i/10}]});
      await new Promise(resolve=>setTimeout(resolve,16));
    }
    await cdp.send('Input.dispatchTouchEvent',{type:cancel?'touchCancel':'touchEnd',touchPoints:[]});
  };
  const screenshot = async name => {
    if(process.env.RETRIEVER_BROWSER_ARTIFACT_DIR) await page.screenshot({path:`${process.env.RETRIEVER_BROWSER_ARTIFACT_DIR}/${name}.png`});
  };
  await screenshot('mobile-half');
  await drag(grip,-600); await snap('expanded');
  check(Math.abs((await panel.boundingBox()).height - size.height) < 3, 'Upward touch opens full chat');
  check(!await page.locator('.map-panel').isVisible(), 'Full chat hides underlying map controls');
  await screenshot('mobile-expanded');
  await drag(grip,700); await snap('collapsed');
  check((await panel.boundingBox()).height < 80 && !await page.locator('.agent-feed').isVisible(), 'Downward touch opens map and hides chat content');
  check(await page.evaluate(()=>Boolean(document.elementFromPoint(190,300)?.closest('.map-panel'))), 'Map receives input above collapsed grip');
  await screenshot('mobile-map');
  await drag(grip,-250); await snap('half');
  await drag(grip,-80,true); await snap('half');
  check(Math.abs((await panel.boundingBox()).height-size.height*.56)<3, 'Cancelled touch restores snap and height');
  await grip.focus(); await grip.press('End'); await snap('expanded');
  await grip.press('Home'); await snap('collapsed');
  await grip.press('ArrowUp'); await snap('half');
  await grip.press('Enter'); await snap('expanded');
  const input = page.getByRole('textbox',{name:'메시지',exact:true});
  await input.fill('강남역 검정 지갑');
  await page.getByRole('button',{name:'보내기',exact:true}).click();
  await page.getByRole('button',{name:'결과 목록 접기',exact:true}).click();
  await input.fill('이용 방법을 알려줘');
  await page.getByRole('button',{name:'보내기',exact:true}).click();
  await page.getByText(/새 유사 물품을 찾았어요/).waitFor();
  await page.waitForFunction(()=>document.querySelector('.agent-feed').scrollHeight>document.querySelector('.agent-feed').clientHeight+100);
  const feed = page.locator('.agent-feed');
  await feed.evaluate(el=>el.scrollTop=el.scrollHeight);
  const scrollBefore = await feed.evaluate(el=>el.scrollTop);
  await drag(feed,200);
  await page.waitForFunction(before=>document.querySelector('.agent-feed').scrollTop<before,scrollBefore);
  await snap('expanded');
  await input.fill('아직 보내지 않은 입력');
  await grip.focus(); await grip.press('Home'); await snap('collapsed');
  await grip.press('ArrowUp'); await snap('half');
  check(await input.inputValue()==='아직 보내지 않은 입력' && await page.getByText(/새 유사 물품을 찾았어요/).count()===1, 'Draft and messages survive sheet changes');
  check(await page.evaluate(()=>window.testMapPadding?.[2]>100), 'Map fit accounts for visible sheet');
  await page.getByRole('button',{name:'결과 4건 보기',exact:true}).click();
  const resultGrip=page.getByRole('button',{name:'검색 결과 패널 높이 조절',exact:true});
  await resultGrip.focus(); await resultGrip.press('End'); await snap('expanded');
  check(Math.abs((await page.getByRole('complementary',{name:'전체 검색 결과'}).boundingBox()).height-size.height)<3, 'Results share sheet height');
  await screenshot('mobile-results');
  await resultGrip.press('Home'); await snap('collapsed');
  await page.getByRole('button',{name:'결과 목록 접기',exact:true}).click();
  await snap('half');
  await grip.focus(); await grip.press('Home'); await snap('collapsed');
  await page.setViewportSize({width:1440,height:1000});
  await input.waitFor();
  check(!await grip.isVisible() && await input.inputValue()==='아직 보내지 않은 입력', 'Desktop ignores collapsed state without losing draft');
  await screenshot('desktop-unchanged');
  await page.setViewportSize({width:390,height:550});
  await grip.waitFor(); await grip.focus(); await grip.press('End'); await snap('expanded');
  await input.focus();
  const inputBox=await input.boundingBox();
  check(inputBox.y+inputBox.height<=550 && await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), 'Short viewport keeps input visible without horizontal overflow');
  await page.setViewportSize({width:740,height:390}); await snap('expanded');
  const landscapeInput=await input.boundingBox();
  check(landscapeInput.y+landscapeInput.height<=390 && await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), 'Landscape keeps the input visible');
  check(calls===2, 'Dragging and resizing never invoke extra search');
  check(!errors.length, errors.join('; '));
  await cdp.detach();
}
