const test = require("node:test");
const assert = require("node:assert/strict");

test("loaded pages keep order without a request; crossing 50 fills the full UI page", async () => {
  const { ensureBufferedItemPage } = await import("../../frontend/src/lib/item-pagination.js");
  const items = Array.from({ length: 50 }, (_, index) => ({ id: index + 1 }));
  let requests = 0;
  const loadNext = async cursor => {
    requests += 1;
    assert.equal(cursor, "cursor-50");
    return { items: Array.from({ length: 10 }, (_, index) => ({ id: index + 51 })), next_cursor: null };
  };
  const options = { items, nextCursor: "cursor-50", pageSize: 4, isCurrent: () => true, loadNext };
  const loaded = await ensureBufferedItemPage({ ...options, page: 1 });
  assert.equal(requests, 0);
  assert.deepEqual(loaded.items.slice(4, 8).map(item => item.id), [5, 6, 7, 8]);
  const boundary = await ensureBufferedItemPage({ ...options, page: 12 });
  assert.equal(requests, 1);
  assert.deepEqual(boundary.items.slice(48, 52).map(item => item.id), [49, 50, 51, 52]);
  assert.equal(boundary.nextCursor, null);
  const last = await ensureBufferedItemPage({ ...options, items: boundary.items, nextCursor: null, page: 14 });
  assert.deepEqual(last.items.slice(56, 60).map(item => item.id), [57, 58, 59, 60]);
  assert.equal(requests, 1);
  assert.equal(items.length, 50);
});

test("3-item pages preserve the partial boundary and the final remainder", async () => {
  const { ensureBufferedItemPage } = await import("../../frontend/src/lib/item-pagination.js");
  const result = await ensureBufferedItemPage({
    items: Array.from({ length: 50 }, (_, index) => ({ id: index + 1 })), nextCursor: "next", page: 16, pageSize: 3,
    isCurrent: () => true, loadNext: async () => ({ items: [{ id: 51 }, { id: 52 }], next_cursor: null }),
  });
  assert.deepEqual(result.items.slice(48, 51).map(item => item.id), [49, 50, 51]);
  const last = await ensureBufferedItemPage({ ...result, page: 17, pageSize: 3,
    isCurrent: () => true, loadNext: async () => { throw new Error("must not fetch"); } });
  assert.deepEqual(last.items.slice(51).map(item => item.id), [52]);
});

test("stale requests cannot publish or continue fetching; failed pages keep the original buffer", async () => {
  const { ensureBufferedItemPage } = await import("../../frontend/src/lib/item-pagination.js");
  const items = [{ id: 1 }];
  let current = true;
  let requests = 0;
  const stale = await ensureBufferedItemPage({ items, nextCursor: "next", page: 1, pageSize: 4,
    isCurrent: () => current, loadNext: async () => { requests += 1; current = false;
      return { items: [{ id: 2 }], next_cursor: "later" }; } });
  assert.equal(stale, null);
  assert.equal(requests, 1);
  await assert.rejects(ensureBufferedItemPage({ items, nextCursor: "next", page: 1, pageSize: 4,
    isCurrent: () => true, loadNext: async () => { throw new Error("network error"); } }), /network error/);
  assert.deepEqual(items, [{ id: 1 }]);
});

test("a repeated cursor terminates instead of looping forever", async () => {
  const { ensureBufferedItemPage } = await import("../../frontend/src/lib/item-pagination.js");
  await assert.rejects(ensureBufferedItemPage({ items: [], nextCursor: "same", page: 1, pageSize: 4,
    isCurrent: () => true, loadNext: async () => ({ items: [{ id: 1 }], next_cursor: "same" }) }), /다시 시도/);
});

test("page numbers stay within a fixed five-button window", async () => {
  const { itemPageNumbers } = await import("../../frontend/src/lib/item-pagination.js");
  assert.deepEqual(itemPageNumbers(0, 0), []);
  assert.deepEqual(itemPageNumbers(0, 2), [0, 1]);
  assert.deepEqual(itemPageNumbers(0, 30), [0, 1, 2, 3, 4]);
  assert.deepEqual(itemPageNumbers(8, 30), [6, 7, 8, 9, 10]);
  assert.deepEqual(itemPageNumbers(29, 30), [25, 26, 27, 28, 29]);
  for (let page = 0; page < 30; page += 1) {
    const numbers = itemPageNumbers(page, 30);
    assert.equal(numbers.length, 5);
    assert.ok(numbers.includes(page));
  }
});
