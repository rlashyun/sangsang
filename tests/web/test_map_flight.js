const test = require("node:test");
const assert = require("node:assert/strict");

// PRD 4.3 · 4.4 — 장소 확정 후 반경 맞춤 이동을 실제 SDK 없이 검증한다(스프린트 6번).
const viewport = { width: 1000, height: 600 };
const load = () => import("../../frontend/src/lib/map-flight.js");

class LatLng {
  constructor(lat, lng) { this.lat = lat; this.lng = lng; }
  getLat() { return this.lat; }
  getLng() { return this.lng; }
}
class Point {
  constructor(x, y) { this.x = x; this.y = y; }
}
const kakao = { maps: { LatLng, Point } };

/**
 * 레벨이 1 줄 때마다 2배 확대되는 가짜 지도. 앵커 확대는 실제 카카오처럼 앵커를 화면에 고정한 채 중심을 옮긴다.
 * setBounds는 fit에 적힌 화면으로 바로 옮긴다.
 */
function fakeMap({ level, center, fit }) {
  const state = { level, center: new LatLng(...center) };
  const log = [];
  const pxPerDegree = () => 1e5 * 2 ** (6 - state.level);
  const map = {
    getLevel: () => state.level,
    getCenter: () => state.center,
    setLevel(next, options) {
      log.push(["setLevel", next, options?.animate ? "animated" : "instant"]);
      if (options?.anchor) {
        const scale = 2 ** (state.level - next);
        const { lat, lng } = options.anchor;
        state.center = new LatLng(lat + (state.center.lat - lat) / scale, lng + (state.center.lng - lng) / scale);
      }
      state.level = next;
    },
    setCenter(next) { state.center = next; },
    panTo(next) { log.push(["panTo"]); state.center = next; },
    setBounds() { log.push(["setBounds"]); state.level = fit.level; state.center = new LatLng(...fit.center); },
    getProjection: () => ({
      containerPointFromCoords: (c) => new Point(
        (c.getLng() - state.center.lng) * pxPerDegree() + viewport.width / 2,
        -(c.getLat() - state.center.lat) * pxPerDegree() + viewport.height / 2,
      ),
      coordsFromContainerPoint: (p) => new LatLng(
        state.center.lat - (p.y - viewport.height / 2) / pxPerDegree(),
        state.center.lng + (p.x - viewport.width / 2) / pxPerDegree(),
      ),
    }),
  };
  return { map, log, state };
}

/** 애니메이션 끝남 신호를 테스트가 직접 보내는 스케줄러. */
function manualScheduler() {
  const ends = [];
  return {
    ends,
    animationEnd(_map, duration, callback) {
      const entry = { duration, callback, cancelled: false };
      ends.push(entry);
      return () => { entry.cancelled = true; };
    },
    finishAll() {
      ends.splice(0).forEach((entry) => { if (!entry.cancelled) entry.callback(); });
    },
  };
}

/** 시간을 직접 움직이는 시계(waitForIdle 검증용). */
function manualClock() {
  const clock = { time: 0, timers: [] };
  clock.now = () => clock.time;
  clock.wait = (callback, ms) => { const entry = { callback, at: clock.time + ms }; clock.timers.push(entry); return entry; };
  clock.clearWait = (entry) => { const index = clock.timers.indexOf(entry); if (index >= 0) clock.timers.splice(index, 1); };
  clock.advanceTo = (time) => {
    clock.time = time;
    clock.timers.filter((entry) => entry.at <= time).forEach((entry) => {
      clock.clearWait(entry);
      entry.callback();
    });
  };
  return clock;
}

test("zoom anchor keeps the anchor fixed and lands the view on the target", async () => {
  const { zoomAnchor } = await load();
  const from = { x: 500, y: 300 };
  const to = { x: 620, y: 260 };
  for (const scale of [2, 4, 0.5, 0.25]) {
    const anchor = zoomAnchor(from, to, scale);
    assert.ok(Math.abs(anchor.x + (from.x - anchor.x) / scale - to.x) < 1e-9);
    assert.ok(Math.abs(anchor.y + (from.y - anchor.y) / scale - to.y) < 1e-9);
  }
});

test("near moves animate: a level change zooms around an anchor, the same level pans", async () => {
  const { planMove, NEAR_MOVE_MS } = await load();
  // 미리보기 레벨 3에서 같은 장소의 반경 1km(레벨 5~6)로 확정하는 흐름.
  const zoom = planMove({ from: { x: 500, y: 300 }, to: { x: 540, y: 300 }, fromLevel: 3, toLevel: 6, viewport });
  assert.equal(zoom.kind, "zoom");
  assert.equal(zoom.level, 6);
  assert.equal(zoom.duration, NEAR_MOVE_MS);
  // 경유지 두 곳을 담느라 크게 축소해 목표 중심이 화면 밖이어도, 시작점이 결과 화면 안이면 가까운 이동이다.
  assert.equal(planMove({ from: { x: 500, y: 300 }, to: { x: 1300, y: 300 }, fromLevel: 6, toLevel: 9, viewport }).kind, "zoom");
  assert.equal(planMove({ from: { x: 500, y: 300 }, to: { x: 700, y: 380 }, fromLevel: 6, toLevel: 6, viewport }).kind, "pan");
  assert.equal(planMove({ from: { x: 500, y: 300 }, to: { x: 500, y: 300 }, fromLevel: 6, toLevel: 6, viewport }).kind, "none");
});

test("far moves do not animate (2026-11-21 decision)", async () => {
  const { planMove } = await load();
  // 서울→부산처럼 시작·목표가 서로의 화면 밖이면 애니메이션 없이 바로 맞춘다.
  assert.equal(planMove({ from: { x: 500, y: 300 }, to: { x: 40500, y: 30300 }, fromLevel: 6, toLevel: 6, viewport }).kind, "jump");
  assert.equal(planMove({ from: { x: 500, y: 300 }, to: { x: 40500, y: 300 }, fromLevel: 3, toLevel: 5, viewport }).kind, "jump");
});

test("reduced motion or an unsized map fits instantly", async () => {
  const { flyMapToBounds } = await load();
  for (const options of [{ viewport, reduceMotion: true }, { viewport: { width: 0, height: 0 } }]) {
    const { map, log } = fakeMap({ level: 3, center: [37.5, 127], fit: { level: 5, center: [37.5, 127.0005] } });
    flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { ...options, scheduler: manualScheduler() });
    assert.deepEqual(log, [["setBounds"]]);
  }
});

test("confirming a place zooms out once with an anchor and lands exactly on the fitted view", async () => {
  const { flyMapToBounds } = await load();
  const { map, log, state } = fakeMap({ level: 3, center: [37.5, 127], fit: { level: 5, center: [37.5, 127.0005] } });
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler: manualScheduler() });
  // 목표를 계산한 뒤 시작 화면으로 되돌리고(그리기 전), 애니메이션 확대 한 번으로 옮긴다.
  assert.deepEqual(log, [["setBounds"], ["setLevel", 3, "instant"], ["setLevel", 5, "animated"]]);
  assert.equal(state.level, 5);
  assert.ok(Math.abs(state.center.lat - 37.5) < 1e-9);
  assert.ok(Math.abs(state.center.lng - 127.0005) < 1e-9);
});

test("a far fit jumps without any animation", async () => {
  const { flyMapToBounds } = await load();
  const { map, log, state } = fakeMap({ level: 3, center: [37.5, 127], fit: { level: 5, center: [35.1, 129.0] } });
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler: manualScheduler() });
  assert.ok(log.every(([, , mode]) => mode !== "animated"));
  assert.deepEqual(log.at(-1), ["setBounds"]);
  assert.equal(state.level, 5);
  assert.equal(state.center.lat, 35.1);
});

test("a same-level near fit pans with Kakao panTo", async () => {
  const { flyMapToBounds } = await load();
  const { map, log } = fakeMap({ level: 5, center: [37.5, 127], fit: { level: 5, center: [37.5, 127.001] } });
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler: manualScheduler() });
  assert.deepEqual(log.at(-1), ["panTo"]);
});

test("the next move waits for Kakao's real end signal, not the clock", async () => {
  const { flyMapToBounds, whenMapSettled } = await load();
  // 카카오는 진행 중인 확대를 멈추지 못하고, 끝날 때 원래 목표로 되돌아간다.
  const scheduler = manualScheduler();
  const { map, log } = fakeMap({ level: 3, center: [37.5, 127], fit: { level: 5, center: [37.5, 127.0005] } });
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  const moved = [];
  whenMapSettled(map, () => moved.push("preview"));
  const before = log.length;
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  assert.deepEqual(moved, []);
  assert.equal(log.length, before);
  scheduler.finishAll();
  assert.deepEqual(moved, ["preview"]);
  assert.ok(log.length > before);
});

test("cancelling while waiting never starts the move, and an idle map moves at once", async () => {
  const { flyMapToBounds, whenMapSettled } = await load();
  const scheduler = manualScheduler();
  const { map, log } = fakeMap({ level: 3, center: [37.5, 127], fit: { level: 5, center: [37.5, 127.0005] } });
  flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  const before = log.length;
  const cancelFit = flyMapToBounds(kakao, map, "bounds", [48, 48, 48, 48], { viewport, scheduler });
  const ran = [];
  const cancelWait = whenMapSettled(map, () => ran.push("waited"));
  cancelFit();
  cancelWait();
  scheduler.finishAll();
  assert.equal(log.length, before);
  assert.deepEqual(ran, []);
  // 진행 중인 애니메이션이 없으면 바로 실행한다(대기 경로만 검증하면 항상 기다려도 통과하므로 함께 확인).
  whenMapSettled(map, () => ran.push("now"));
  assert.deepEqual(ran, ["now"]);
});

test("waitForIdle ignores an idle that arrives too early, then removes its listener after dispatch", async () => {
  const { waitForIdle } = await load();
  const clock = manualClock();
  const listeners = new Set();
  const events = {
    addListener: (_map, _name, fn) => listeners.add(fn),
    removeListener: (_map, _name, fn) => listeners.delete(fn),
  };
  const fired = [];
  waitForIdle(events, {}, 650, () => fired.push(clock.time), clock);
  // 직전 이동의 idle(바로 도착)은 무시한다.
  clock.time = 10;
  listeners.forEach((fn) => fn());
  assert.deepEqual(fired, []);
  // 애니메이션이 끝나 오는 idle에서 끝난 것으로 본다. 리스너는 처리 중이 아니라 그다음에 지운다.
  clock.time = 2000;
  listeners.forEach((fn) => fn());
  assert.deepEqual(fired, [2000]);
  assert.equal(listeners.size, 1);
  clock.advanceTo(2000);
  assert.equal(listeners.size, 0);
  // 대체 타이머는 지워져 다시 부르지 않는다.
  clock.advanceTo(10000);
  assert.deepEqual(fired, [2000]);
});

test("waitForIdle falls back to a timer if Kakao never sends idle", async () => {
  const { waitForIdle } = await load();
  const clock = manualClock();
  const events = { addListener() {}, removeListener() {} };
  const fired = [];
  waitForIdle(events, {}, 650, () => fired.push("fallback"), clock);
  clock.advanceTo(650);
  assert.deepEqual(fired, []);
  clock.advanceTo(6000);
  assert.deepEqual(fired, ["fallback"]);
});
