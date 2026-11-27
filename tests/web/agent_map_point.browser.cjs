// PRD 4.3-A · 4.4 — 기관 핀이 아닌 지도 위치도 눌러 주소를 확인하고 장소선택으로 확정한다. 지도 SDK·주소 변환·API는 모두 모의 응답.
async (page) => {
  const check = (ok, message) => { if (!ok) throw Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__listeners = [];
    window.__markers = [];
    window.__geocodeCalls = [];
    window.__heldGeocodes = [];
    class LatLng { constructor(lat, lng) { this.lat=lat; this.lng=lng; } getLat() { return this.lat; } getLng() { return this.lng; } }
    class FakeObject {
      constructor(_, options) { this.center=options?.center || new LatLng(37.55,126.97); }
      getCenter() { return this.center; } getPosition() { return this.position || this.center; } getLevel() { return 8; }
      getProjection() { return {containerPointFromCoords:()=>({x:0,y:0}),coordsFromContainerPoint:()=>this.center}; }
      setCenter(value) { this.center=value; } setMapTypeId() {} addControl() {} setBounds() {} extend() {} setMap() {}
      addMarkers() {} clear() {} setPosition(value) { this.position=value; } setContent() {} setImage() {} setZIndex() {} setLevel() {} panTo() {}
    }
    class FakeMap extends FakeObject { constructor(...args) { super(...args); window.__map=this; } }
    class FakeMarker extends FakeObject {
      constructor(options) { super(null, options); this.title=options?.title; this.position=options?.position; this.map=options?.map || null; window.__markers.push(this); }
      setMap(map) { this.map=map; }
    }
    // 실제 팝업 DOM을 화면에 붙여 버튼을 누를 수 있게 한다.
    class FakeOverlay extends FakeObject {
      setContent(content) { this.content=content; }
      setMap(map) {
        if (map && this.content && !this.content.isConnected) document.body.append(this.content);
        if (!map && this.content?.isConnected) this.content.remove();
      }
    }
    // 좌표별 모의 주소. 127.2는 주소 없음, 127.3은 테스트가 풀어줄 때까지 응답을 미룬다.
    class Geocoder {
      coord2Address(lng, lat, callback) {
        window.__geocodeCalls.push([lat, lng]);
        const reply = () => {
          if (lng === 127.2) return callback([], 'ZERO_RESULT');
          if (lng === 127.4) return callback([{address:{address_name:'서울 송파구 신천동 7-18'},road_address:{address_name:'서울 송파구 올림픽로 240',building_name:''}}], 'OK');
          return callback([{address:{address_name:'서울 송파구 신천동 29'},road_address:{address_name:'서울 송파구 올림픽로 300',building_name:'롯데월드타워'}}], 'OK');
        };
        if (lng === 127.3) window.__heldGeocodes.push(reply); else setTimeout(reply, 20);
      }
    }
    window.kakao={maps:{load:callback=>callback(),LatLng,Map:FakeMap,CustomOverlay:FakeOverlay,AbstractOverlay:FakeObject,
      ZoomControl:FakeObject,MapTypeControl:FakeObject,Marker:FakeMarker,MarkerClusterer:FakeObject,MarkerImage:FakeObject,
      Size:FakeObject,Point:FakeObject,Circle:FakeObject,LatLngBounds:FakeObject,ControlPosition:{},MapTypeId:{ROADMAP:1,HYBRID:3},
      services:{Geocoder,Status:{OK:'OK',ZERO_RESULT:'ZERO_RESULT',ERROR:'ERROR'}},
      event:{addListener(target,type,handler){window.__listeners.push({target,type,handler});},removeListener(target,type,handler){
        window.__listeners=window.__listeners.filter(item=>!(item.target===target&&item.type===type&&item.handler===handler));}}}};
  });
  let requests=[], release=null;
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='127.0.0.1')return route.abort();
    if(url.pathname==='/api/institutions')return route.fulfill({json:{institutions:[],counts_available:true}});
    if(url.pathname==='/api/search-chat/turns'){
      const body=route.request().postDataJSON(); requests.push(body);
      await new Promise(resolve=>{release=resolve;});
      const point={candidate_id:'point',name:body.name,address:body.address,latitude:body.latitude,longitude:body.longitude};
      const response={session_id:'map-point-session',run_id:'run-'+requests.length,revision:requests.length,status:'awaiting_clarification',
        message:'어떤 물건을 잃어버렸어요?',conditions:{place:point,places:[point],item_query:null,radius_m:null,institution_ids:[]},
        place_candidates:[],search_attempts:[],recommended_item_ids:[],matches:null,error:null};
      const events=[{type:'run.started',seq:1,session_id:response.session_id,run_id:response.run_id},{type:'run.finished',seq:2,response}];
      return route.fulfill({contentType:'application/x-ndjson',body:events.map(event=>JSON.stringify(event)).join('\n')+'\n'});
    }
    return route.continue();
  });
  const clickMap=(lat,lng)=>page.evaluate(([lat,lng])=>{
    const listener=window.__listeners.find(item=>item.target===window.__map&&item.type==='click');
    if(!listener)throw Error('no map click listener');
    listener.handler({latLng:new window.kakao.maps.LatLng(lat,lng)});
  },[lat,lng]);
  const popup=page.locator('.map-popup');
  const pickedMarkers=()=>page.evaluate(()=>window.__markers.filter(marker=>marker.title==='지도에서 고른 위치'&&marker.map).length);
  for(const viewport of [{width:1440,height:768},{width:390,height:844}]){
    requests=[];release=null;
    await page.setViewportSize(viewport);await page.goto('http://127.0.0.1:8000/');
    await page.waitForFunction(()=>window.__listeners.some(item=>item.target===window.__map&&item.type==='click'));

    // 주소를 찾으면 건물 이름과 도로명 주소, 장소선택 버튼을 보여 준다.
    await clickMap(37.5125,127.1025);
    check(await popup.getByText('주소 확인 중').isVisible(),'Shows loading while the address is resolved');
    check(await pickedMarkers()===1,'Places a white pin on the clicked point');
    await popup.getByText('롯데월드타워',{exact:true}).waitFor();
    check(await popup.getByText('서울 송파구 올림픽로 300',{exact:true}).isVisible(),'Shows the road address');
    check(await popup.getByRole('button',{name:'장소선택'}).isVisible(),'Resolved point can be selected');

    // 건물 이름이 없으면 도로명 주소를 이름으로 쓰고 같은 글을 두 번 보여 주지 않는다.
    await clickMap(37.5126,127.4);
    await popup.getByText('서울 송파구 올림픽로 240',{exact:true}).waitFor();
    check(await popup.getByText('서울 송파구 올림픽로 240',{exact:true}).count()===1,'Address-only point shows the address once');
    check(await pickedMarkers()===1,'A new click replaces the previous white pin');

    // 주소가 없는 곳은 선택할 수 없다.
    await clickMap(37.40,127.2);
    await popup.getByText('이 위치의 주소를 찾지 못했어요. 다른 곳을 눌러 주세요.').waitFor();
    check(await popup.getByRole('button',{name:'장소선택'}).count()===0,'Point without an address cannot be selected');

    // 늦게 도착한 이전 주소가 새 위치의 팝업을 덮지 않는다.
    await clickMap(37.30,127.3);
    await clickMap(37.5125,127.1025);
    await popup.getByText('롯데월드타워',{exact:true}).waitFor();
    await page.evaluate(()=>window.__heldGeocodes.splice(0).forEach(reply=>reply()));
    await page.waitForTimeout(50);
    check(await popup.getByText('롯데월드타워',{exact:true}).isVisible(),'Stale address result is ignored');

    // 닫으면 흰 핀도 지운다.
    await popup.getByRole('button',{name:'지도 정보 닫기'}).click();
    check(await popup.count()===0&&await pickedMarkers()===0,'Closing the popup removes the white pin');

    // 장소선택은 기관 핀과 같은 select_point로 한 번 보낸다.
    await clickMap(37.5125,127.1025);
    await popup.getByText('롯데월드타워',{exact:true}).waitFor();
    await popup.getByRole('button',{name:'장소선택'}).evaluate(button=>{for(let i=0;i<5;i++)button.click();});
    while(!release)await new Promise(resolve=>setTimeout(resolve,10));
    check(requests.length===1,'Selection is sent once');
    const sent=requests[0];
    check(sent.type==='select_point'&&sent.latitude===37.5125&&sent.longitude===127.1025
      &&sent.name==='롯데월드타워'&&sent.address==='서울 송파구 올림픽로 300','Sends the clicked coordinates, name and address');
    check(await popup.count()===0&&await pickedMarkers()===0,'Selecting closes the popup and its temporary pin');

    // 요청 중에는 지도를 눌러도 새 위치를 고르지 않는다.
    const callsBefore=await page.evaluate(()=>window.__geocodeCalls.length);
    await clickMap(37.5,127.0);
    check(await popup.count()===0&&await page.evaluate(()=>window.__geocodeCalls.length)===callsBefore,'Map clicks are ignored while a turn is running');
    release();
    await page.getByText("'롯데월드타워' 선택",{exact:true}).waitFor();
    console.log('PASS map point selection: '+viewport.width);
  }
  check(errors.length===0,errors.join('\n'));
}
