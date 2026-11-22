// PRD 4.2 · 4.3-A — 사진 첨부·붙여넣기·설명 요청·실패 복구. API·지도는 모의 응답.
// Playwright run-code로 로컬 8000 서버에서 실행한다.
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    class LatLng { constructor(lat, lng) { this.lat = lat; this.lng = lng; } getLat() { return this.lat; } getLng() { return this.lng; } }
    class FakeObject {
      constructor(_, options) { this.center = options?.center || new LatLng(37.5, 127.03); }
      getCenter() { return this.center; } getLevel() { return 8; }
      getProjection() { return {containerPointFromCoords: () => ({x: 0, y: 0}), coordsFromContainerPoint: () => this.center}; }
      setCenter(value) { this.center = value; }
      setMapTypeId() {} addControl() {} setBounds() {} extend() {} setMap() {} addMarkers() {} clear() {}
      setPosition() {} setContent() {} setImage() {} setZIndex() {} setLevel() {} panTo() {}
    }
    window.kakao = {maps: {load: callback => callback(), LatLng, Map: FakeObject,
      CustomOverlay: FakeObject, AbstractOverlay: FakeObject, ZoomControl: FakeObject, MapTypeControl: FakeObject,
      Marker: FakeObject, MarkerClusterer: FakeObject, MarkerImage: FakeObject, Size: FakeObject, Point: FakeObject,
      Circle: FakeObject, LatLngBounds: FakeObject, ControlPosition: {}, MapTypeId: {ROADMAP: 1, HYBRID: 3},
      event: {addListener() {}, removeListener() {}}}};
  });
  let requests = [], behavior = 'success', release;
  const place = {candidate_id: 'p', name: '강남역', address: '서울 강남구', latitude: 37.5, longitude: 127.03};
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1') return route.abort();
    if (url.pathname === '/api/institutions') return route.fulfill({json: {institutions: [], counts_available: true}});
    if (url.pathname === '/api/search-chat/turns') {
      const body = route.request().postDataJSON();
      requests.push(body);
      const currentBehavior = behavior;
      if (currentBehavior.startsWith('pending')) await new Promise(resolve => { release = resolve; });
      const failed = currentBehavior === 'error' || currentBehavior === 'pending-error';
      const cancelled = currentBehavior === 'cancelled';
      const response = {session_id: 'photo-session', revision: requests.length, run_id: 'photo-run',
        status: failed ? 'error' : cancelled ? 'cancelled' : 'awaiting_item',
        message: failed ? '사진 첨부 검색을 다시 시도해 주세요.' : cancelled ? '검색을 취소했어요.' : '어떤 물건을 잃어버렸어요?',
        conditions: {place, places: [place], item_query: null, radius_m: 1000, institution_ids: []},
        place_candidates: [], search_attempts: [], recommended_item_ids: [], matches: null,
        error: failed ? {code: 'timeout', message: '사진 첨부 검색을 다시 시도해 주세요.', retryable: true} : null};
      return route.fulfill({contentType: 'application/x-ndjson', body: JSON.stringify({type: 'run.finished', response}) + '\n'});
    }
    return route.continue();
  });
  const guide = '사진과 함께 물건의 이름이나 특징을 간단히 입력해 주세요.';
  const placeGuide = '사진을 첨부하려면 먼저 잃어버린 장소를 입력하고 선택해 주세요.';
  // 유효한 작은 PNG를 메모리에서 만들어 파일 선택과 클립보드에 같은 내용을 사용한다.
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 60; canvas.height = 90;
    const context = canvas.getContext('2d'); context.fillStyle = '#a87c50'; context.fillRect(0, 0, 60, 90);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  const file = name => ({name, mimeType: 'image/png', buffer: Buffer.from(png, 'base64')});
  async function paste() {
    await page.locator('.agent-compose').evaluate((form, data) => {
      const bytes = Uint8Array.from(atob(data), char => char.charCodeAt(0));
      const clipboard = new DataTransfer(); clipboard.items.add(new File([bytes], '붙여넣은 사진.png', {type: 'image/png'}));
      form.dispatchEvent(new ClipboardEvent('paste', {clipboardData: clipboard, bubbles: true, cancelable: true}));
    }, png);
  }
  for (const viewport of [{width: 1440, height: 768}, {width: 390, height: 844}]) {
    requests = []; behavior = 'success';
    await page.emulateMedia({reducedMotion: viewport.width === 390 ? 'reduce' : 'no-preference'});
    await page.setViewportSize(viewport);
    await page.goto('http://127.0.0.1:8000/');
    const input = page.getByRole('textbox', {name: '메시지'});
    const send = page.getByRole('button', {name: '보내기', exact: true});
    const picker = page.locator('input[type=file]');
    const previews = page.locator('.agent-photo-previews img');
    const photoMessages = page.locator('.agent-message-photos img');
    await page.getByRole('button', {name: '사진 추가', exact: true}).click();
    await page.getByText(placeGuide, {exact: true}).waitFor();
    await paste();
    check(await page.locator('.agent-photo').evaluate(button => button.getAnimations().some(animation => animation.playState === 'running')),
      'Blocked paste re-emphasizes attachment button without duplicating message');
    check(await page.getByText(placeGuide, {exact: true}).count() === 1 && await previews.count() === 0 && requests.length === 0,
      'Before place selection both attach and paste explain in chat without adding photos or requests');
    await input.fill('강남역'); await send.click();
    await page.getByText('어떤 물건을 잃어버렸어요?', {exact: true}).waitFor();
    await page.waitForFunction(() => !document.querySelector('.agent-photo').disabled);
    await picker.setInputFiles(file('지갑.png')); await previews.waitFor();
    await page.waitForFunction(() => !document.querySelector('.agent-send').disabled);
    check(await input.evaluate(element => element === document.activeElement), 'Attachment returns focus to description');
    const originalUrl = await previews.first().getAttribute('src');
    await input.fill('   '); await send.click();
    await page.getByText(guide, {exact: true}).waitFor();
    await page.waitForTimeout(700);
    await send.click();
    check(await send.evaluate(button => button.getAnimations().some(animation => animation.playState === 'running')),
      'Repeated send restarts button feedback');
    check(await page.getByText(guide, {exact: true}).evaluate(text => text.closest('.agent-photo-guidance').getAnimations().some(animation => animation.playState === 'running')),
      'Repeated send highlights existing chat guidance');
    check(await send.evaluate(button => getComputedStyle(button).animationName) ===
      (viewport.width === 390 ? 'agent-photo-attention-still' : 'agent-photo-button-attention'),
      'Reduced-motion setting uses static emphasis instead of bounce');
    check(await page.getByText(guide, {exact: true}).evaluate(text => {
      const bubble = text.closest('.agent-bubble').getBoundingClientRect(), feed = text.closest('.agent-feed').getBoundingClientRect();
      return bubble.bottom + 3 <= feed.bottom + 0.5 && bubble.left - 3 >= feed.left - 0.5;
    }), 'Bubble bottom and emphasis ring fit inside scroll area without clipping');
    check(requests.length === 1 && await photoMessages.count() === 0 && await previews.count() === 1,
      'Photo-only sends keep draft and never search or create empty user bubble');
    check(await page.getByText(guide, {exact: true}).count() === 1 && await previews.first().getAttribute('src') === originalUrl,
      'Repeated sends do not duplicate guidance or replace the attachment');
    await page.getByRole('button', {name: '사진 1 삭제', exact: true}).click();
    await input.fill(''); check(await send.isDisabled(), 'Removing last draft disables empty send');
    await paste(); await previews.waitFor();
    await send.click(); check(await page.getByText(guide, {exact: true}).count() === 1, 'Pasted photo follows same description rule');
    behavior = 'pending-error'; release = null; await input.fill('갈색 지갑'); await send.click();
    while (!release) await new Promise(resolve => setTimeout(resolve, 10));
    check(await previews.count() === 0 && await photoMessages.count() === 1,
      'Photo preview clears immediately while submitted image remains in chat');
    check(await page.locator('.agent-photo-caption').count() === 0 && await page.getByText('첨부한 사진', {exact: true}).count() === 0,
      'Sent photo has no redundant caption');
    release();
    await page.getByRole('button', {name: '다시 시도', exact: true}).waitFor();
    check(await input.inputValue() === '갈색 지갑' && await previews.count() === 1 && await photoMessages.count() === 1,
      'Failed search retains description, draft photo and sent photo');
    const failedRequest = requests.at(-1);
    // 실패 후 새로 첨부한 사진을 이전 요청 재시도가 지우면 안 된다.
    await picker.setInputFiles(file('다음 사진.png'));
    await page.waitForFunction(() => document.querySelectorAll('.agent-photo-previews img').length === 2);
    behavior = 'pending'; release = null; await page.getByRole('button', {name: '다시 시도', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('.agent-send').disabled);
    while (!release) await new Promise(resolve => setTimeout(resolve, 10));
    check(await page.getByRole('button', {name: '사진 1 삭제', exact: true}).isDisabled(), 'Cannot delete photos while request is pending');
    check(JSON.stringify(requests.at(-1)) === JSON.stringify(failedRequest), 'Retry preserves exact request identity');
    await input.fill('새로 작성 중인 설명'); await paste();
    check(await previews.count() === 1, 'Retry hides submitted photo, preserves next draft and ignores paste during search');
    release(); await page.waitForFunction(() => document.querySelectorAll('.agent-photo-previews img').length === 1);
    check(await previews.first().getAttribute('alt') === '첨부 예정 사진 1: 다음 사진.png' && await input.inputValue() === '새로 작성 중인 설명',
      'Success clears only original photos and preserves newly added draft and typed description');
    check(await photoMessages.count() === 1 && await photoMessages.first().evaluate(image => image.complete && image.naturalWidth > 0),
      'Retry creates no duplicate photo message and keeps sent image alive');
    await page.waitForFunction(() => !document.querySelector('.agent-send').disabled);
    behavior = 'error'; await input.fill('검정 카드지갑'); await send.click();
    await page.getByRole('button', {name: '다시 시도', exact: true}).waitFor();
    await page.getByRole('button', {name: '사진 1 삭제', exact: true}).click();
    check(await photoMessages.last().evaluate(async image => {
      const reloaded = new Image(); reloaded.src = image.src; await reloaded.decode();
      return image.complete && image.naturalWidth > 0 && reloaded.naturalWidth > 0;
    }),
      'Deleting failed draft does not revoke photo already shown in conversation');
    // 빈/손상 파일과 용량·개수 제한에서 올바른 첨부가 그대로 남는지 확인한다.
    await picker.setInputFiles({name: '손상.png', mimeType: 'image/png', buffer: Buffer.from('invalid')});
    await page.getByRole('alert').filter({hasText: '사진을 열지 못했어요.'}).waitFor();
    await picker.setInputFiles({name: '큰사진.png', mimeType: 'image/png', buffer: Buffer.alloc(10 * 1024 * 1024 + 1)});
    await page.getByRole('alert').filter({hasText: '10MB'}).waitFor();
    await picker.setInputFiles([file('1.png'), file('2.png'), file('3.png'), file('4.png')]);
    await page.getByRole('alert').filter({hasText: '최대 3장'}).waitFor();
    check(await previews.count() === 3, 'Only three valid photos are added');
    check(await page.locator('.agent-compose').evaluate(element => {const rect = element.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight;}),
      'Draft and composer fit desktop/mobile viewport');
    behavior = 'cancelled'; await input.fill('갈색 지퍼 지갑'); await send.click();
    await page.getByText('검색을 취소했어요.', {exact: true}).waitFor();
    check(await previews.count() === 3 && await input.inputValue() === '갈색 지퍼 지갑', 'Cancelled turn also preserves photos and description');
    await page.waitForFunction(() => !document.querySelector('.agent-send').disabled);
    behavior = 'success'; await send.click();
    await page.waitForFunction(() => document.querySelectorAll('.agent-photo-previews img').length === 0);
    check(await input.inputValue() === '' && await page.locator('.agent-bubble-with-photos').last().locator('img').count() === 3,
      'Successful text submission moves all submitted photos into conversation');
    console.log('PASS photo attach/paste, chat guidance, retained drafts, retry identity, busy state, image lifetime and limits: ' + viewport.width);
  }
  check(errors.length === 0, errors.join('\n'));
};
