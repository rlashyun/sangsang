// PRD 4.3 · 4.4 — 장소 확정 후 반경 맞춤을 순간이동 대신 부드러운 확대·축소로 보여 준다(스프린트 6번).
// 2026-11-21 사용자 결정 — 가까운 이동만 애니메이션으로 보여 주고, 화면 밖 먼 곳으로 가는 이동은
// 애니메이션 없이 기존처럼 바로 맞춘다(검색·후보 미리보기 이동도 기존대로 바로 이동).
// https://apis.map.kakao.com/web/documentation/#Map_setLevel

/** 가까운 이동 한 번의 시간(ms). */
export const NEAR_MOVE_MS = 650;

/** 카카오의 끝남 신호(idle)가 끝내 오지 않을 때 더 기다리는 시간(ms). */
const SETTLE_FALLBACK_MS = 5000;

/**
 * 확대 직후·이동 직후에 바로 오는 idle은 그 전 이동의 것이다. 애니메이션은 요청 시간보다 빨리 끝나지 않으므로
 * 요청 시간의 이 비율이 지나기 전에 온 idle은 무시한다.
 */
const MIN_IDLE_RATIO = 0.8;

/**
 * @typedef {{ x: number, y: number }} ScreenPoint
 * @typedef {{ kind: "none" } | { kind: "jump" } | { kind: "pan", duration: number }
 *   | { kind: "zoom", level: number, anchor: ScreenPoint, duration: number }} MovePlan
 */

/**
 * 이 점을 고정한 채 scale배 확대(1보다 작으면 축소)하면 화면 중심이 from에서 to로 간다.
 * @param {ScreenPoint} from
 * @param {ScreenPoint} to
 * @param {number} scale
 * @returns {ScreenPoint}
 */
export function zoomAnchor(from, to, scale) {
  return {
    x: (from.x - scale * to.x) / (1 - scale),
    y: (from.y - scale * to.y) / (1 - scale),
  };
}

/**
 * 시작 레벨 화면 좌표(px)로 어떻게 옮길지 정한다.
 * @param {{ from: ScreenPoint, to: ScreenPoint, fromLevel: number, toLevel: number,
 *   viewport: { width: number, height: number } }} options
 * @returns {MovePlan}
 */
export function planMove({ from, to, fromLevel, toLevel, viewport }) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (fromLevel === toLevel && Math.hypot(dx, dy) < 1) return { kind: "none" };

  const halfWidth = viewport.width / 2;
  const halfHeight = viewport.height / 2;
  const targetScale = 2 ** (fromLevel - toLevel);
  const targetInView = Math.abs(dx) <= halfWidth && Math.abs(dy) <= halfHeight;
  const startInTarget = Math.abs(dx) * targetScale <= halfWidth && Math.abs(dy) * targetScale <= halfHeight;
  // 2026-11-21 사용자 결정 — 먼 이동은 애니메이션하지 않는다.
  if (!targetInView && !startInTarget) return { kind: "jump" };
  if (fromLevel === toLevel) return { kind: "pan", duration: NEAR_MOVE_MS };
  return { kind: "zoom", level: toLevel, anchor: zoomAnchor(from, to, targetScale), duration: NEAR_MOVE_MS };
}

/**
 * 카카오 idle 이벤트로 애니메이션이 실제로 끝났을 때 callback을 부른다. 반환한 함수로 대기를 취소한다.
 * 카카오 애니메이션은 화면 프레임으로 진행돼 탭이 느리거나 가려지면 요청한 시간보다 늦게 끝난다
 * (2026-11-21 로컬 측정: 0.4초 요청이 약 2초). 그래서 시간이 아니라 idle로 판단하고,
 * idle이 끝내 오지 않을 때만 넉넉한 시간 뒤에 넘어간다.
 * @param {any} events kakao.maps.event
 * @param {any} map
 * @param {number} duration
 * @param {() => void} callback
 * @param {{ now: () => number, wait: (callback: () => void, ms: number) => any, clearWait: (id: any) => void }} clock
 * @returns {() => void}
 */
export function waitForIdle(events, map, duration, callback, clock) {
  const startedAt = clock.now();
  let done = false;
  // idle을 처리하는 도중에 리스너를 지우면 카카오가 같은 idle의 다음 리스너를 건너뛴다
  // (2026-11-21 확인). 지우기는 이번 처리가 끝난 뒤로 미룬다.
  const detach = () => clock.wait(() => events.removeListener(map, "idle", onIdle), 0);
  const settle = () => {
    if (done) return;
    done = true;
    detach();
    clock.clearWait(timer);
    callback();
  };
  const onIdle = () => {
    if (clock.now() - startedAt < duration * MIN_IDLE_RATIO) return;
    settle();
  };
  events.addListener(map, "idle", onIdle);
  const timer = clock.wait(settle, duration + SETTLE_FALLBACK_MS);
  return () => {
    if (done) return;
    done = true;
    detach();
    clock.clearWait(timer);
  };
}

const browserScheduler = {
  now: () => performance.now(),
  /** @param {() => void} callback @param {number} ms */
  wait: (callback, ms) => window.setTimeout(callback, ms),
  /** @param {any} id */
  clearWait: (id) => window.clearTimeout(id),
  /** @param {any} map @param {number} duration @param {() => void} callback */
  animationEnd(map, duration, callback) {
    return waitForIdle(window.kakao.maps.event, map, duration, callback, browserScheduler);
  },
};

/** 지도별로 진행 중인 애니메이션과, 그것이 끝나기를 기다리는 콜백. */
const pendingAnimations = new WeakMap();

/**
 * 카카오 애니메이션을 시작했음을 기록하고, 끝나면 기다리던 이동을 이어 간다.
 * @param {any} map @param {number} duration @param {any} scheduler
 */
function trackAnimation(map, duration, scheduler) {
  /** @type {{ waiters: Set<() => void> }} */
  const entry = { waiters: new Set() };
  pendingAnimations.set(map, entry);
  scheduler.animationEnd(map, duration, () => {
    if (pendingAnimations.get(map) === entry) pendingAnimations.delete(map);
    entry.waiters.forEach((waiter) => waiter());
  });
}

/**
 * 진행 중인 카카오 애니메이션이 끝난 뒤 callback을 부른다. 반환한 함수로 대기를 취소한다.
 * 카카오는 진행 중인 setLevel 애니메이션을 멈출 방법이 없고, 끝날 때 원래 목표로 되돌아간다.
 * 그 사이에 setLevel·setCenter·setBounds를 부르면 애니메이션이 덮어써 엉뚱한 곳에 멈춘다(2026-11-21 확인).
 * @param {any} map
 * @param {() => void} callback
 * @returns {() => void}
 */
export function whenMapSettled(map, callback) {
  const entry = pendingAnimations.get(map);
  if (!entry) {
    callback();
    return () => {};
  }
  let active = true;
  let cancelNext = () => {};
  const waiter = () => {
    if (!active) return;
    active = false;
    // 그 사이에 다른 애니메이션이 시작됐으면 그것도 끝날 때까지 기다린다.
    cancelNext = whenMapSettled(map, callback);
  };
  entry.waiters.add(waiter);
  return () => {
    active = false;
    entry.waiters.delete(waiter);
    cancelNext();
  };
}

/**
 * map.setBounds와 같은 화면으로 옮긴다. 가까우면 부드럽게, 멀면 바로 맞춘다.
 * 반환한 함수를 부르면 아직 시작하지 않은 이동을 취소한다.
 * @param {any} kakao
 * @param {any} map
 * @param {any} bounds
 * @param {[number, number, number, number]} padding 위·오른쪽·아래·왼쪽(px)
 * @param {{ viewport: { width: number, height: number }, reduceMotion?: boolean, scheduler?: any }} options
 * @returns {() => void}
 */
export function flyMapToBounds(kakao, map, bounds, padding, { viewport, reduceMotion = false, scheduler = browserScheduler }) {
  return whenMapSettled(map, () => moveToBounds(kakao, map, bounds, padding, viewport, reduceMotion, scheduler));
}

/**
 * @param {any} kakao
 * @param {any} map
 * @param {any} bounds
 * @param {[number, number, number, number]} padding
 * @param {{ width: number, height: number }} viewport
 * @param {boolean} reduceMotion
 * @param {any} scheduler
 */
function moveToBounds(kakao, map, bounds, padding, viewport, reduceMotion, scheduler) {
  const [top, right, bottom, left] = padding;
  const fit = () => map.setBounds(bounds, top, right, bottom, left);
  // team-roles 6번 — 움직임 감소 설정이면 기존처럼 바로 맞춘다.
  // 지도 영역 크기를 모르면(0px) 경로를 계산할 수 없으므로 같은 방식으로 바로 맞춘다.
  if (reduceMotion || !(viewport.width > 0 && viewport.height > 0)) {
    fit();
    return;
  }

  const startLevel = map.getLevel();
  const startCenter = map.getCenter();
  // 카카오가 고르는 최종 레벨·중심을 그대로 쓰려고 한 번 맞춘 뒤, 화면을 그리기 전에 되돌린다.
  fit();
  const endLevel = map.getLevel();
  const endCenter = map.getCenter();
  map.setLevel(startLevel, { animate: false });
  map.setCenter(startCenter);

  const projection = map.getProjection();
  /** @param {any} coords @returns {ScreenPoint} */
  const toScreen = (coords) => {
    const point = projection.containerPointFromCoords(coords);
    return { x: point.x, y: point.y };
  };
  const plan = planMove({
    from: toScreen(startCenter),
    to: toScreen(endCenter),
    fromLevel: startLevel,
    toLevel: endLevel,
    viewport,
  });
  if (plan.kind === "none") return;
  if (plan.kind === "jump") {
    fit();
    return;
  }
  if (plan.kind === "pan") {
    // 한 화면 안의 평행 이동은 카카오 panTo가 부드럽게 옮긴다.
    map.panTo(endCenter);
  } else {
    // 앵커를 고정한 확대·축소 한 번으로 중심까지 함께 옮긴다. 도착 오차는 2026-11-21 로컬 확인에서 0px였다.
    const anchor = projection.coordsFromContainerPoint(new kakao.maps.Point(plan.anchor.x, plan.anchor.y));
    map.setLevel(plan.level, { animate: { duration: plan.duration }, anchor });
  }
  trackAnimation(map, plan.duration, scheduler);
}
