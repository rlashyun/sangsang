// PRD 3.1-9 · 4.3-C · 5 — Vite 127.0.0.1:5178에서 실제 상세 컴포넌트를 가짜 사진으로 검증한다.
// playwright-cli run-code --filename tests/web/item_image_retry.browser.cjs
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let mode = 'fail';
  const requests = [];
  const held = [];
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#4488eb"/></svg>';
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'minwon24.police.go.kr') {
      requests.push(url.href);
      let outcome = mode;
      if (mode === 'hold') outcome = await new Promise(resolve => held.push(resolve));
      if (outcome === 'fail') return route.abort();
      return route.fulfill({ contentType: 'image/svg+xml', headers: { 'Cache-Control': 'no-store' }, body: svg });
    }
    if (url.hostname !== '127.0.0.1') return route.abort();
    if (url.pathname === '/__image-test') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="ko" data-theme="retriever-modern"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/src/styles.css"></head><body><button id="opener">상세 열기</button><div id="fixture"></div><script type="module">
      import RefreshRuntime from '/@react-refresh';
      RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
      window.__vite_plugin_react_preamble_installed__ = true;
      const { default: React } = await import('/node_modules/.vite/deps/react.js');
      const { default: { createRoot } } = await import('/node_modules/.vite/deps/react-dom_client.js');
      const { FoundItemDetail } = await import('/src/components/FoundItemDetail.tsx');
      const root = createRoot(document.getElementById('fixture'));
      window.openDetail = image_url => root.render(React.createElement(FoundItemDetail, {
        key: image_url, item: { item_name: '시험 지갑', raw_category_name: '지갑', image_url, description: '원문 첫 줄\\n둘째 줄' },
        onClose: () => root.render(null)
      }));
    </script></body></html>` });
    if (url.pathname.startsWith('/api/')) throw new Error('격리 이미지 검증에서 API를 호출하면 안 된다');
    return route.continue();
  });
  await page.goto('http://127.0.0.1:5178/__image-test');
  await page.waitForFunction(() => typeof window.openDetail === 'function');
  await page.clock.install();
  await page.clock.pauseAt(new Date());
  const dialog = page.getByRole('dialog', { name: '물품 상세정보', exact: true });
  const retry = dialog.getByRole('button', { name: '사진 다시 불러오기', exact: true });
  const photo = dialog.locator('.item-detail-photo');
  const slowText = '사진을 불러오는 데 시간이 걸리고 있어요.';
  const loadingText = '사진을 불러오고 있습니다…';
  async function open(url) {
    await page.locator('#opener').focus();
    await page.evaluate(url => window.openDetail(url), url);
    await dialog.waitFor();
  }
  async function close() {
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'detached' });
    check(await page.locator('#opener').evaluate(el => document.activeElement === el), '닫으면 원래 초점을 복원한다');
  }
  async function clickRetry() {
    await retry.focus();
    await page.keyboard.press('Enter');
  }
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    // 사진 없음·기본 그림은 요청하거나 재시도하지 않는다.
    const beforeMissing = requests.length;
    for (const missing of [null, 'https://minwon24.police.go.kr/images/sub/img02_no_img.gif?default=1']) {
      await open(missing);
      await dialog.getByText('등록된 사진이 없습니다.').waitFor();
      await page.clock.runFor(10000);
      check(await retry.count() === 0 && requests.length === beforeMissing, '사진 없음에는 타이머·재시도·요청이 없어야 한다');
      await close();
    }
    mode = 'fail';
    const originalUrl = `https://minwon24.police.go.kr/test-photo.svg?case=${width}`;
    await open(originalUrl);
    await dialog.getByText('사진을 불러오지 못했습니다.').waitFor();
    const failedCount = requests.length;
    await clickRetry();
    await dialog.getByText('사진을 불러오지 못했습니다.').waitFor();
    check(requests.length === failedCount + 1, '실패 재시도는 새 요청을 보낸다');
    mode = 'hold';
    await clickRetry();
    await dialog.getByText(loadingText).waitFor();
    await page.waitForFunction(() => !!document.querySelector('.item-detail-photo img'));
    const height = await photo.evaluate(el => el.getBoundingClientRect().height);
    await page.clock.runFor(9999);
    check(await retry.count() === 0 && await dialog.getByText(slowText).count() === 0, '10초 전에는 지연 안내를 표시하지 않는다');
    await page.clock.runFor(1);
    await dialog.getByText(slowText).waitFor();
    check(await retry.count() === 0 && await photo.locator('.item-detail-image').getAttribute('aria-busy') === 'true', '10초 이후 안내만 하며 지연을 실패로 처리하지 않는다');
    check(await photo.evaluate(el => el.getBoundingClientRect().height) === height, '지연 중에도 고정 높이를 유지한다');
    check(await dialog.getByRole('heading', { name: '물품 설명', exact: true }).isVisible(), '사진 지연 중에도 원문을 확인할 수 있다');
    await page.screenshot({ path: `/private/tmp/retriever-image-20261120/slow-${width}.png` });
    const pendingCount = requests.length;
    await page.clock.runFor(20000);
    check(requests.length === pendingCount && await retry.count() === 0, '장기 로딩도 자동/수동 재시도 없이 기존 요청을 계속 기다린다');
    while (held.length) held.shift()('fail');
    await dialog.getByText('사진을 불러오지 못했습니다.').waitFor();
    mode = 'hold';
    const nextRequest = page.waitForRequest(request => request.url() === originalUrl);
    await clickRetry();
    await nextRequest;
    await dialog.getByText(loadingText).waitFor();
    check(await photo.evaluate(el => document.activeElement === el), '실패 재시도 후 사진 영역에 초점을 유지한다');
    await page.clock.runFor(9999);
    check(await dialog.getByText(slowText).count() === 0 && await retry.count() === 0, '재시도마다 10초 대기를 다시 시작한다');
    await page.clock.runFor(1);
    await dialog.getByText(slowText).waitFor();
    check(await retry.count() === 0, '실패 재시도 이후에도 지연 중에는 버튼을 표시하지 않는다');
    while (held.length) held.shift()('success');
    await dialog.locator('img:not([hidden])').waitFor();
    check(await retry.count() === 0, '늦은 성공은 안내와 재시도를 없앤다');
    check(await dialog.locator('img').getAttribute('src') === originalUrl, '원본 URL에 캐시 우회 인자를 붙이지 않는다');
    check(await photo.evaluate(el => el.getBoundingClientRect().height) === height, '성공 후에도 고정 높이를 유지한다');
    await page.clock.runFor(10000);
    check(await retry.count() === 0, '성공한 사진에는 지연 안내가 다시 나타나지 않는다');
    await close();
    mode = 'hold';
    await open(`https://minwon24.police.go.kr/test-photo.svg?closing=${width}`);
    await dialog.getByText(loadingText).waitFor();
    await close();
    await page.clock.runFor(10000);
    while (held.length) held.shift()('fail');
    check(await dialog.count() === 0, '대기 중 닫으면 타이머가 상세를 다시 열지 않는다');
  }
  await page.clock.resume();
  check(errors.length === 0, errors.join('\n'));
  return 'PASS: desktop/mobile 10s boundary, missing/default/failure, failure-only retry and uninterrupted pending load, late success, original URL, fixed layout, keyboard and cleanup';
}
