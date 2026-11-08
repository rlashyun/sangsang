// PRD 3.3 · 4.4 — 서버의 50건 커서와 화면의 3~4개 페이지를 분리한다.
/** @param {number} page @param {number} count */
export function itemPageNumbers(page, count) {
  const start = Math.max(0, Math.min(page - 2, count - 5));
  return Array.from({ length: Math.min(5, count) }, (_, index) => start + index);
}

/**
 * @param {{ items: import('../types').FoundItem[], nextCursor: string | null,
 * page: number, pageSize: number,
 * loadNext: (cursor: string) => Promise<import('../types').FoundItemsPayload>,
 * isCurrent: () => boolean }} options
 */
export async function ensureBufferedItemPage(options) {
  let items = options.items;
  let cursor = options.nextCursor;
  const cursors = new Set();
  const required = (options.page + 1) * options.pageSize;
  while (items.length < required && cursor) {
    if (!options.isCurrent()) return null;
    if (cursors.has(cursor)) throw new Error("다음 물품 목록을 불러오지 못했습니다. 다시 시도해주세요.");
    cursors.add(cursor);
    const payload = await options.loadNext(cursor);
    if (!options.isCurrent()) return null;
    const batch = payload.items || [];
    cursor = payload.next_cursor ?? null;
    if (!batch.length && cursor) throw new Error("다음 물품 목록을 불러오지 못했습니다. 다시 시도해주세요.");
    items = [...items, ...batch];
  }
  return {
    items,
    nextCursor: cursor,
    page: Math.min(options.page, Math.max(0, Math.ceil(items.length / options.pageSize) - 1)),
  };
}
