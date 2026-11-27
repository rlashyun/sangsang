// PRD 4.3-A · 5 — 중복 전송, 한글 조합 Enter, 중복 이벤트와 실패 복구. 외부 요청은 모두 모의 응답.
async (page) => {
  const check = (ok, message) => { if (!ok) throw Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    class LatLng { constructor(lat, lng) { this.lat=lat; this.lng=lng; } getLat() { return this.lat; } getLng() { return this.lng; } }
    class FakeObject {
      constructor(_, options) { this.center=options?.center || new LatLng(37.55,126.97); }
      getCenter() { return this.center; } getPosition() { return this.center; } getLevel() { return 8; }
      getProjection() { return {containerPointFromCoords:()=>({x:0,y:0}),coordsFromContainerPoint:()=>this.center}; }
      setCenter(value) { this.center=value; } setMapTypeId() {} addControl() {} setBounds() {} extend() {} setMap() {}
      addMarkers() {} clear() {} setPosition() {} setContent() {} setImage() {} setZIndex() {} setLevel() {} panTo() {}
    }
    window.kakao={maps:{load:callback=>callback(),LatLng,Map:FakeObject,CustomOverlay:FakeObject,AbstractOverlay:FakeObject,
      ZoomControl:FakeObject,MapTypeControl:FakeObject,Marker:FakeObject,MarkerClusterer:FakeObject,MarkerImage:FakeObject,
      Size:FakeObject,Point:FakeObject,Circle:FakeObject,LatLngBounds:FakeObject,ControlPosition:{},MapTypeId:{ROADMAP:1,HYBRID:3},event:{addListener(){},removeListener(){}}}};
  });
  let requests=[], release, failures=0;
  const place={candidate_id:'p',name:'서울역',address:'서울',latitude:37.55,longitude:126.97};
  const complete='검색을 마쳤어요. 결과 목록에서 찾으시는 물건이 있는지 확인해 보세요.';
  const expansions=[
    {type:'scope.updated',seq:2,previous_radius_m:1000,radius_m:1500,institution_ids:[42],expansion_reason:'no_matches'},
    {type:'scope.updated',seq:3,previous_radius_m:1500,radius_m:2000,institution_ids:[42],expansion_reason:'no_matches'},
  ];
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='127.0.0.1')return route.abort();
    if(url.pathname==='/api/institutions')return route.fulfill({json:{institutions:[],counts_available:true}});
    if(url.pathname==='/api/search-chat/turns'){
      const body=route.request().postDataJSON(); requests.push(body);
      const index=requests.length;
      if(index===1)await new Promise(resolve=>{release=resolve;});
      if(body.text==='실패 시험'&&failures++===0)return route.fulfill({status:503,json:{error:{code:'state_unavailable',message:'잠시 연결하지 못했어요. 다시 시도해 주세요.'}}});
      const response={session_id:'submission-session',run_id:'run-'+index,revision:index,status:'results',message:'검색 결과',
        conditions:{place,places:[place],item_query:'지갑',radius_m:1000,institution_ids:[42]},place_candidates:[],search_attempts:[],recommended_item_ids:[],
        matches:{items:[{id:1,item_id:1,institution_id:42,item_name:'검정 지갑',image_url:null}],total_count:1,next_cursor:null},error:null};
      const events=[{type:'run.started',seq:1,session_id:response.session_id,run_id:response.run_id}];
      if(index===1)Object.assign(response,{status:'awaiting_place_selection',message:'장소를 선택해 주세요.',conditions:{...response.conditions,place:null,places:[],radius_m:null,institution_ids:[]},place_candidates:[place],matches:null});
      const secondPlace={...place,candidate_id:'p2',name:'용산역',latitude:37.53};
      const thirdPlace={...place,candidate_id:'p3',name:'홍대입구역',latitude:37.56};
      if(body.type==='select_place')response.conditions.places=[place,secondPlace];
      if(body.text==='반복 검색')response.conditions.places=[place,secondPlace,thirdPlace];
      if(body.text==='실패 시험'){
        events.push(expansions[0],expansions[0],expansions[1],expansions[1]);
        Object.assign(response,{status:'no_matches',message:'2km 안에서도 일치하는 물건을 찾지 못했어요.',conditions:{...response.conditions,radius_m:2000},matches:{items:[],total_count:0,next_cursor:null}});
      }
      if(body.type==='remove_place')Object.assign(response,{status:'awaiting_place_clarification',message:'‘서울역’ 장소를 제거했어요.',conditions:{...response.conditions,place:null,places:[],radius_m:null,institution_ids:[]},matches:null});
      if(body.text==='물건 정보를 바꿀게요.')Object.assign(response,{status:'awaiting_clarification',message:'바꿀 물건을 알려주세요.',matches:null});
      if(body.text==='장소를 추가할게요.')Object.assign(response,{status:'awaiting_place_clarification',message:'추가할 장소를 알려주세요.',
        conditions:{...response.conditions,place_operation:'add'},matches:null});
      events.push({type:'run.finished',seq:10,response},{type:'run.finished',seq:10,response});
      return route.fulfill({contentType:'application/x-ndjson',body:events.map(event=>JSON.stringify(event)).join('\n')+'\n'});
    }
    return route.continue();
  });
  const burst=()=>page.locator('.agent-compose').evaluate(form=>{for(let i=0;i<10;i++)form.requestSubmit();});
  const userText=text=>page.locator('.agent-bubble-user .agent-message-text').filter({hasText:new RegExp('^'+text+'$')});
  const agentText=text=>page.locator('.agent-bubble-agent .agent-message-text').filter({hasText:new RegExp('^'+text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$')});
  for(const viewport of [{width:1440,height:768},{width:390,height:844}]){
    requests=[];release=null;failures=0;
    await page.setViewportSize(viewport);await page.goto('http://127.0.0.1:8000/');
    const input=page.getByRole('textbox',{name:'메시지'}),send=page.getByRole('button',{name:'보내기',exact:true});
    const addPlace=page.getByRole('button',{name:'장소 추가',exact:true});
    const limitNotice=page.getByText('메시지를 100자 이내로 줄여 주세요.',{exact:true});
    const maxLengthMessage='가'.repeat(99)+'📍';
    await input.fill(maxLengthMessage+'나');
    await limitNotice.waitFor();
    await page.keyboard.press('Enter');await burst();
    check(await send.isDisabled()&&requests.length===0&&await input.inputValue()===maxLengthMessage+'나',
      'Oversized chat input stays intact and cannot be sent');
    await input.fill(maxLengthMessage);
    check(await send.isEnabled()&&await limitNotice.count()===0,'Exactly 100 characters can be sent without an input warning');
    await input.fill('서울역 지갑');
    await input.dispatchEvent('compositionstart',{data:'갑'});
    await page.keyboard.press('Enter');await burst();
    check(requests.length===0&&await input.inputValue()==='서울역 지갑','IME confirmation and composing submit must not send');
    await input.dispatchEvent('compositionend',{data:'갑'});
    await page.keyboard.down('Enter');
    while(!release)await new Promise(resolve=>setTimeout(resolve,10));
    await burst();check(requests.length===1,'Synchronous submit burst sends one request');
    release();await page.getByText('장소를 선택해 주세요.',{exact:true}).waitFor();
    check(await addPlace.count()===0,'No shortcut before the first place is confirmed');
    await input.fill('아직 보내지 않을 입력');await burst();
    check(requests.length===1,'Replies still being presented lock submit');
    await page.waitForFunction(()=>!document.querySelector('.agent-send').disabled);
    await page.keyboard.down('Enter');
    check(requests.length===1,'Held Enter repeat does not submit a new draft');await page.keyboard.up('Enter');
    check(await input.inputValue()==='아직 보내지 않을 입력','Blocked submit preserves typed draft');await input.fill('');
    // PRD 4.3-A · 5 — 답변 표시 잠금은 실행 환경의 속도와 무관하게 검증한다.
    await page.clock.install();
    await page.clock.pauseAt(await page.evaluate(() => Date.now() + 100));
    await page.locator('.agent-candidate').click();
    await page.getByRole('button',{name:'장소선택',exact:true}).evaluate(button=>{for(let i=0;i<10;i++)button.click();});
    await page.locator('.agent-confirmed-card').waitFor();
    await page.clock.runFor(300);
    await page.getByText(complete,{exact:true}).waitFor();
    check(requests.length===2&&await userText("'서울역' 선택").count()===1,'Place confirmation is requested and recorded once');
    check(await page.getByText(complete,{exact:true}).count()===1,'Duplicate final events produce one completion reply');
    await input.fill('반복 검색');await burst();check(requests.length===2,'Fast final response does not unlock before reply presentation');
    await page.clock.resume();
    await page.getByRole('button',{name:'결과 목록 접기',exact:true}).click();
    check(await addPlace.evaluate(button=>{
      const add=button.getBoundingClientRect();
      const placeName=button.parentElement.previousElementSibling.querySelector('span').getBoundingClientRect();
      return Math.abs(add.left-placeName.left)<1&&add.top>=placeName.bottom;
    }),'The add symbol sits below the place name and aligns with its left edge');
    await input.fill('반복 검색');
    await page.waitForFunction(()=>!document.querySelector('.agent-send').disabled);await burst();
    await page.waitForFunction(text=>Array.from(document.querySelectorAll('.agent-message-text')).filter(element=>element.textContent===text).length===2,complete);
    await page.locator('.agent-result-actions').waitFor();
    check(await send.isDisabled(),'Empty input stays disabled after search finishes');
    check(requests.length===3&&await userText('반복 검색').count()===1,'New intentional search is sent once');
    check(await addPlace.count()===0,'No shortcut when three places are already confirmed');
    check(await page.getByText(complete,{exact:true}).count()===2,'Separate searches keep their own identical completion messages');
    const resultActions=page.locator('.agent-result-actions');
    check(await resultActions.getByRole('button',{name:'반경 넓혀보기'}).isVisible()
      && await resultActions.getByRole('button',{name:'비슷한 물건 알림 받기'}).isVisible()
      && await resultActions.getByRole('link',{name:'당근에서 찾아보기'}).isVisible(),'Search keeps all available follow-up actions');
    check(await page.getByText('찾으시는 물건이 없나요?',{exact:false}).count()===0,'Search does not add the redundant follow-up message');
    await page.locator('.agent-result-actions').waitFor();
    await page.getByRole('button',{name:'비슷한 물건 알림 받기',exact:true}).evaluate(button=>{for(let i=0;i<10;i++)button.click();});
    check(await userText('비슷한 물건 알림 받기').count()===1,'Rapid local choices record one action');
    await input.fill('실패 시험');await burst();
    await page.getByRole('button',{name:'다시 시도',exact:true}).waitFor();
    await page.getByRole('button',{name:'결과 1건 보기',exact:true}).click();
    check(requests.length===4&&await page.locator('.agent-item').count()===1,'Failure preserves existing results');
    await page.getByRole('button',{name:'결과 목록 접기',exact:true}).click();
    check(await input.inputValue()==='실패 시험'&&await userText('실패 시험').count()===1,'Failure preserves draft and original user message');
    const failedRequest=requests.at(-1);
    await page.getByRole('button',{name:'다시 시도',exact:true}).evaluate(button=>{for(let i=0;i<10;i++)button.click();});
    await page.getByText('2km 안에서도 일치하는 물건을 찾지 못했어요.',{exact:true}).waitFor();
    check(requests.length===5&&JSON.stringify(requests.at(-1))===JSON.stringify(failedRequest),'Retry burst keeps same ID, session and revision');
    check(await userText('실패 시험').count()===1&&await userText('다시 시도').count()===1,'Retry is recorded without duplicating original input');
    for(const notice of ['1km 안에 일치하는 물건이 없어 1.5km로 넓혀 찾고 있어요.','1.5km 안에 일치하는 물건이 없어 2km로 넓혀 찾고 있어요.'])check(await agentText(notice).count()===1,'Repeated scope events announce each expansion once');
    check(await agentText('잠시 연결하지 못했어요. 다시 시도해 주세요.').count()===1,'Previous failure remains in chat after retry');
    await page.locator('.agent-result-actions').waitFor();
    check(await input.inputValue()==='','Successful retry clears submitted draft');
    check(await addPlace.isVisible(),'The shortcut is also available with one confirmed place');
    await page.locator('.agent-summary .agent-selected-places button').first().evaluate(button=>{for(let i=0;i<10;i++)button.click();});
    await agentText('‘서울역’ 장소를 제거했어요.').waitFor();
    check(requests.length===6&&await userText("'서울역' 제거").count()===1,'Place removal is sent and recorded once');
    check(await agentText('‘서울역’ 장소를 제거했어요.').count()===1,'Same response notice from frontend and server is not duplicated');
    check(await userText('서울역 지갑').count()===1&&await userText("'서울역' 선택").count()===1,'Earlier typed and selected history remains after state changes');
    await input.fill('작성 중인 다른 문장');
    await page.waitForFunction(()=>!document.querySelector('.agent-send').disabled);
    await page.locator('.agent-summary').getByRole('button',{name:'수정',exact:true})
      .evaluate(button=>{for(let i=0;i<10;i++)button.click();});
    await page.getByText('바꿀 물건을 알려주세요.',{exact:true}).waitFor({timeout:4000});
    check(requests.length===7&&requests.at(-1).text==='물건 정보를 바꿀게요.',
      'Item edit sends the fixed message once without Enter');
    check(await userText('작성 중인 다른 문장').count()===0,'Item edit does not send the old draft');
    await input.fill('작성 중인 장소 이름');
    await page.waitForFunction(()=>!document.querySelector('.agent-send').disabled);
    await addPlace.evaluate(button=>{for(let i=0;i<10;i++)button.click();});
    await agentText('추가할 장소를 알려주세요.').waitFor({timeout:4000});
    check(requests.length===8&&requests.at(-1).text==='장소를 추가할게요.'&&await input.inputValue()==='',
      'Place add sends the fixed request once and clears the input without Enter');
    check(await page.getByRole('button',{name:'서울역 제거',exact:true}).isVisible(),'Place add keeps existing locations');
    console.log('PASS submission/IME/repeat/replayed events/retry/history: '+viewport.width);
  }
  check(errors.length===0,errors.join('\n'));
}
