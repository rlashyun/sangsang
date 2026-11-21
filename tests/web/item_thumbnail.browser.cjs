// PRD 3.2-2 — 실제 목록 컴포넌트의 축소본·원본 대체를 외부 호출 없이 검증한다.
async (page) => {
  const requests = [];
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="red"/></svg>';
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'project.supabase.co' || url.hostname === 'minwon24.police.go.kr') {
      requests.push(url.href);
      if (url.pathname.includes('fail')) return route.abort();
      return route.fulfill({ contentType: 'image/svg+xml', body: svg });
    }
    if (url.hostname !== '127.0.0.1') return route.abort();
    if (url.pathname === '/__thumbnail-test') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><body><div id="root"></div><script type="module">
      import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;
      const { default: React } = await import('/node_modules/.vite/deps/react.js');
      const { default: { createRoot } } = await import('/node_modules/.vite/deps/react-dom_client.js');
      const { FoundItemThumbnail } = await import('/src/components/FoundItemThumbnail.tsx');
      const root = createRoot(document.getElementById('root'));
      window.renderItem = item => root.render(React.createElement(FoundItemThumbnail, { key: item.id, item }));
    </script></body></html>` });
    return route.continue();
  });
  await page.goto('http://127.0.0.1:5178/__thumbnail-test');
  await page.waitForFunction(() => typeof window.renderItem === 'function');
  const original = 'https://minwon24.police.go.kr/original.svg';
  await page.evaluate(url => window.renderItem({id: 1, image_url: url, thumbnail_url: 'https://project.supabase.co/ok.svg', item_name: '물품'}), original);
  await page.locator('img[src="https://project.supabase.co/ok.svg"].is-loaded').waitFor();
  if (requests.includes(original)) throw new Error('축소본 성공 시 원본 요청이 발생했습니다.');
  if (await page.locator('img').getAttribute('loading') !== 'lazy') throw new Error('지연 로딩이 누락됐습니다.');
  await page.evaluate(url => window.renderItem({id: 2, image_url: url, thumbnail_url: 'https://project.supabase.co/fail.svg'}), original);
  await page.locator(`img[src="${original}"].is-loaded`).waitFor();
  await page.evaluate(() => window.renderItem({id: 3, image_url: 'https://minwon24.police.go.kr/fail.svg', thumbnail_url: 'https://project.supabase.co/fail.svg'}));
  await page.getByText('사진을 불러오지 못했습니다.').waitFor();
  await page.evaluate(() => window.renderItem({id: 4, image_url: null, thumbnail_url: 'https://project.supabase.co/ok.svg'}));
  await page.getByText('등록된 사진이 없습니다.').waitFor();
  return '축소본 성공·원본 대체·전체 실패·사진 없음·지연 로딩 통과';
}
