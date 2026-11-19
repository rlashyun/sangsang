const test = require("node:test");
const assert = require("node:assert/strict");

// PRD 2.2, 4.5 — 2026-11-17 전체 물품 확대 뒤에도 사진 없는 물품의 대체 그림이 다른 물건처럼 보이면 안 된다.
test("cap and earbud icons need the exact sub-category; other clothing and electronics use the box", async () => {
  const { categoryKind } = await import("../../frontend/src/lib/presentation.ts");
  assert.equal(categoryKind("의류 > 모자"), "cap");
  assert.equal(categoryKind("전자기기 > 무선이어폰"), "buds");
  assert.equal(categoryKind("의류 > 신발"), "box");
  assert.equal(categoryKind("전자기기 > 태블릿"), "box");
});

test("parent-level icons and new categories", async () => {
  const { categoryKind } = await import("../../frontend/src/lib/presentation.ts");
  assert.equal(categoryKind("지갑 > 기타 지갑"), "wallet");
  assert.equal(categoryKind("가방 > 기타가방"), "bag");
  assert.equal(categoryKind("휴대폰 > 아이폰"), "phone");
  assert.equal(categoryKind("카드 > 교통카드"), "card");
  assert.equal(categoryKind("현금 > 현금"), "box");
  assert.equal(categoryKind(undefined), "box");
});
