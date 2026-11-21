const test = require("node:test");
const assert = require("node:assert/strict");

// PRD 4.3 · 4.4 — 장소 확정 후 반경 맞춤 이동을 실제 SDK 없이 검증한다.
const viewport = { width: 1000, height: 600 };

test("easing starts at 0, ends at 1 and is symmetric", async () => {
  const { easeInOutCubic } = await import("../../frontend/src/lib/map-flight.js");
  assert.equal(easeInOutCubic(0), 0);
  assert.equal(easeInOutCubic(1), 1);
  assert.equal(easeInOutCubic(0.5), 0.5);
  assert.ok(easeInOutCubic(0.25) < 0.25);
  assert.ok(easeInOutCubic(0.75) > 0.75);
});

test("zoom anchor keeps the anchor fixed and lands the view on the target", async () => {
  const { zoomAnchor } = await import("../../frontend/src/lib/map-flight.js");
  const from = { x: 500, y: 300 };
  const to = { x: 620, y: 260 };
  for (const scale of [2, 4, 0.5, 0.25]) {
    const anchor = zoomAnchor(from, to, scale);
    // 앵커 기준으로 scale배 확대하면 화면 중심이 to로 간다.
    assert.ok(Math.abs(anchor.x + (from.x - anchor.x) / scale - to.x) < 1e-9);
    assert.ok(Math.abs(anchor.y + (from.y - anchor.y) / scale - to.y) < 1e-9);
  }
});

test("near target with a level change becomes one anchored zoom", async () => {
  const { planFlight } = await import("../../frontend/src/lib/map-flight.js");
  // 미리보기 레벨 3에서 같은 장소의 반경 1km(레벨 6)로 확정하는 흐름.
  const steps = planFlight({ from: { x: 500, y: 300 }, to: { x: 540, y: 300 }, fromLevel: 3, toLevel: 6, viewport });
  assert.equal(steps.length, 1);
  assert.equal(steps[0].kind, "zoom");
  assert.equal(steps[0].level, 6);
  assert.ok(steps[0].anchor);
  assert.ok(steps[0].duration >= 400 && steps[0].duration <= 900);
});

test("near target at the same level only pans", async () => {
  const { planFlight } = await import("../../frontend/src/lib/map-flight.js");
  const steps = planFlight({ from: { x: 500, y: 300 }, to: { x: 700, y: 380 }, fromLevel: 6, toLevel: 6, viewport });
  assert.deepEqual(steps.map(step => step.kind), ["pan"]);
});

test("no movement plans no steps", async () => {
  const { planFlight } = await import("../../frontend/src/lib/map-flight.js");
  assert.deepEqual(planFlight({ from: { x: 500, y: 300 }, to: { x: 500, y: 300 }, fromLevel: 6, toLevel: 6, viewport }), []);
});

test("zooming out over a far target stays one zoom when the start remains in view", async () => {
  const { planFlight } = await import("../../frontend/src/lib/map-flight.js");
  // 경유지 두 곳을 담느라 크게 축소하는 경우 — 시작점이 결과 화면 안에 남는다.
  const steps = planFlight({ from: { x: 500, y: 300 }, to: { x: 1300, y: 300 }, fromLevel: 6, toLevel: 9, viewport });
  assert.deepEqual(steps.map(step => step.kind), ["zoom"]);
});

test("far target zooms out, pans, then zooms in (about 2 seconds)", async () => {
  const { planFlight } = await import("../../frontend/src/lib/map-flight.js");
  // 서울→부산처럼 화면 수십 개 떨어진 곳.
  const to = { x: 500 + 40000, y: 300 + 30000 };
  const steps = planFlight({ from: { x: 500, y: 300 }, to, fromLevel: 6, toLevel: 6, viewport });
  assert.deepEqual(steps.map(step => step.kind), ["zoom", "pan", "zoom"]);
  const [zoomOut, pan, zoomIn] = steps;
  assert.ok(zoomOut.level > 6);
  assert.equal(zoomOut.anchor, null);
  // 축소한 레벨에서는 이동 거리가 한 화면 안에 들어와야 부드럽게 보인다.
  const distance = Math.hypot(to.x - 500, to.y - 300) / 2 ** (zoomOut.level - 6);
  assert.ok(distance <= Math.min(viewport.width, viewport.height));
  assert.deepEqual(pan.to, to);
  assert.equal(zoomIn.level, 6);
  assert.equal(zoomIn.anchor, null);
  const total = steps.reduce((sum, step) => sum + step.duration, 0);
  assert.ok(total >= 1700 && total <= 2300, `total ${total}ms`);
});

test("far flight never zooms past the widest map level", async () => {
  const { planFlight, MAX_MAP_LEVEL } = await import("../../frontend/src/lib/map-flight.js");
  const steps = planFlight({ from: { x: 0, y: 0 }, to: { x: 1e9, y: 0 }, fromLevel: 3, toLevel: 3, viewport });
  assert.ok(steps.every(step => step.kind !== "zoom" || step.level <= MAX_MAP_LEVEL));
});

test("flyMapToBounds jumps instantly when reduced motion is requested", async () => {
  const { flyMapToBounds } = await import("../../frontend/src/lib/map-flight.js");
  const calls = [];
  const map = { setBounds: (...args) => calls.push(["setBounds", ...args]) };
  const cancel = flyMapToBounds({}, map, "bounds", [48, 48, 48, 96], { viewport, reduceMotion: true });
  assert.deepEqual(calls, [["setBounds", "bounds", 48, 48, 48, 96]]);
  assert.equal(typeof cancel, "function");
});

test("flyMapToBounds restores the start view before animating and ends on the fitted view", async () => {
  const { flyMapToBounds } = await import("../../frontend/src/lib/map-flight.js");
  const frames = [];
  const timers = [];
  const scheduler = {
    frame: (callback) => frames.push(callback),
    cancelFrame: () => {},
    wait: (callback) => timers.push(callback),
    clearWait: () => {},
    now: (() => { let time = 0; return () => (time += 1000); })(),
  };
  class LatLng { constructor(lat, lng) { this.lat = lat; this.lng = lng; } getLat() { return this.lat; } getLng() { return this.lng; } }
  class Point { constructor(x, y) { this.x = x; this.y = y; } }
  const kakao = { maps: { LatLng, Point } };
  const state = { level: 3, center: new LatLng(37.5, 127) };
  const log = [];
  const map = {
    getLevel: () => state.level,
    getCenter: () => state.center,
    setLevel(level, options) { log.push(["setLevel", level, Boolean(options?.animate)]); state.level = level; },
    setCenter(center) { state.center = center; },
    setBounds() { state.level = 6; state.center = new LatLng(37.5, 127.001); log.push(["setBounds"]); },
    getProjection: () => ({
      containerPointFromCoords: (c) => new Point((c.getLng() - 127) * 1e5 + 500, -(c.getLat() - 37.5) * 1e5 + 300),
      coordsFromContainerPoint: (p) => new LatLng(37.5 - (p.y - 300) / 1e5, 127 + (p.x - 500) / 1e5),
    }),
  };
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  // 목표 계산 직후 시작 화면으로 되돌려 첫 프레임에 순간이동이 보이지 않는다.
  assert.deepEqual(log.slice(0, 3), [["setBounds"], ["setLevel", 3, false], ["setLevel", 6, true]]);
  while (timers.length || frames.length) (timers.shift() || frames.shift())();
  assert.equal(state.level, 6);
  assert.ok(Math.abs(state.center.getLng() - 127.001) < 1e-9);
});

test("cancel stops remaining steps", async () => {
  const { flyMapToBounds } = await import("../../frontend/src/lib/map-flight.js");
  const timers = [];
  const scheduler = { frame: () => {}, cancelFrame: () => {}, wait: (callback) => timers.push(callback), clearWait: () => { timers.length = 0; }, now: () => 0 };
  class LatLng { constructor(lat, lng) { this.lat = lat; this.lng = lng; } getLat() { return this.lat; } getLng() { return this.lng; } }
  class Point { constructor(x, y) { this.x = x; this.y = y; } }
  const state = { level: 6, center: new LatLng(37.5, 127) };
  const levels = [];
  const map = {
    getLevel: () => state.level,
    getCenter: () => state.center,
    setLevel(level) { levels.push(level); state.level = level; },
    setCenter(center) { state.center = center; },
    setBounds() { state.level = 6; state.center = new LatLng(35.1, 129.0); },
    getProjection: () => ({
      containerPointFromCoords: (c) => new Point((c.getLng() - 127) * 1e5 * 2 ** (6 - state.level) + 500, -(c.getLat() - 37.5) * 1e5 * 2 ** (6 - state.level) + 300),
      coordsFromContainerPoint: (p) => new LatLng(37.5 - (p.y - 300) / 1e5, 127 + (p.x - 500) / 1e5),
    }),
  };
  const cancel = flyMapToBounds({ maps: { LatLng, Point } }, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  const startedLevels = levels.length;
  cancel();
  while (timers.length) timers.shift()();
  assert.equal(levels.length, startedLevels);
});

function fakeKakaoMap(initial) {
  class LatLng { constructor(lat, lng) { this.lat = lat; this.lng = lng; } getLat() { return this.lat; } getLng() { return this.lng; } }
  class Point { constructor(x, y) { this.x = x; this.y = y; } }
  const state = { level: initial.level, center: new LatLng(...initial.center) };
  const log = [];
  const map = {
    getLevel: () => state.level,
    getCenter: () => state.center,
    setLevel(level, options) { log.push(["setLevel", level, Boolean(options?.animate)]); state.level = level; },
    setCenter(center) { state.center = center; },
    setBounds() { log.push(["setBounds"]); state.level = initial.fit.level; state.center = new LatLng(...initial.fit.center); },
    getProjection: () => ({
      containerPointFromCoords: (c) => new Point((c.getLng() - 127) * 1e5 + 500, -(c.getLat() - 37.5) * 1e5 + 300),
      coordsFromContainerPoint: (p) => new LatLng(37.5 - (p.y - 300) / 1e5, 127 + (p.x - 500) / 1e5),
    }),
  };
  return { kakao: { maps: { LatLng, Point } }, map, log, state };
}

function manualScheduler() {
  const clock = { time: 0 };
  const waits = new Map();
  let nextId = 1;
  return {
    clock,
    waits,
    frame: () => 0,
    cancelFrame: () => {},
    wait(callback, ms) { const id = nextId++; waits.set(id, { callback, at: clock.time + ms }); return id; },
    clearWait(id) { waits.delete(id); },
    now: () => clock.time,
    advance(ms) {
      clock.time += ms;
      for (const [id, entry] of [...waits]) {
        if (entry.at <= clock.time) { waits.delete(id); entry.callback(); }
      }
    },
  };
}

test("zero-size map container fits instantly instead of flying across the country", async () => {
  const { flyMapToBounds } = await import("../../frontend/src/lib/map-flight.js");
  const { kakao, map, log } = fakeKakaoMap({ level: 3, center: [37.5, 127], fit: { level: 6, center: [37.5, 127.001] } });
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport: { width: 0, height: 0 } });
  assert.deepEqual(log, [["setBounds"]]);
  // 크기가 있는 지도에서는 같은 이동이 애니메이션으로 진행된다.
  const sized = fakeKakaoMap({ level: 3, center: [37.5, 127], fit: { level: 6, center: [37.5, 127.001] } });
  flyMapToBounds(sized.kakao, sized.map, "bounds", [48, 48, 48, 48], { viewport, scheduler: manualScheduler() });
  assert.ok(sized.log.some(([name, , animated]) => name === "setLevel" && animated));
});

test("a new move waits until the running Kakao zoom animation ends", async () => {
  const { flyMapToBounds, whenMapSettled } = await import("../../frontend/src/lib/map-flight.js");
  const scheduler = manualScheduler();
  const { kakao, map, log } = fakeKakaoMap({ level: 3, center: [37.5, 127], fit: { level: 6, center: [37.5, 127.001] } });
  const cancelFirst = flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  assert.deepEqual(log.at(-1), ["setLevel", 6, true]);
  cancelFirst();

  // 카카오는 진행 중인 확대를 멈추지 못하므로 다음 이동은 끝날 때까지 기다린다.
  const moved = [];
  whenMapSettled(map, () => moved.push(scheduler.now()), scheduler);
  const before = log.length;
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  assert.equal(log.length, before);
  assert.deepEqual(moved, []);
  scheduler.advance(600);
  assert.deepEqual(moved, []);
  scheduler.advance(200);
  assert.equal(moved.length, 1);
  assert.ok(moved[0] >= 650);
  assert.ok(log.length > before);
});

test("cancelling while waiting for the animation never starts the move", async () => {
  const { flyMapToBounds, whenMapSettled } = await import("../../frontend/src/lib/map-flight.js");
  const scheduler = manualScheduler();
  const { kakao, map, log } = fakeKakaoMap({ level: 3, center: [37.5, 127], fit: { level: 6, center: [37.5, 127.001] } });
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler })();
  const before = log.length;
  const cancelSecond = flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  const ran = [];
  const cancelWait = whenMapSettled(map, () => ran.push(true), scheduler);
  cancelSecond();
  cancelWait();
  scheduler.advance(2000);
  assert.equal(log.length, before);
  assert.deepEqual(ran, []);
});

test("an idle map moves immediately", async () => {
  const { whenMapSettled } = await import("../../frontend/src/lib/map-flight.js");
  const ran = [];
  whenMapSettled({}, () => ran.push(true), manualScheduler());
  assert.deepEqual(ran, [true]);
});
