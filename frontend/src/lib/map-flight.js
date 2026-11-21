// PRD 4.3 · 4.4 — 장소 확정 후 반경 맞춤을 순간이동 대신 부드러운 이동으로 보여 준다(스프린트 6번).
// 카카오 panTo는 한 화면보다 먼 거리면 애니메이션 없이 건너뛰므로 먼 거리는 축소 → 이동 → 확대로 나눈다.
// https://apis.map.kakao.com/web/documentation/#Map_setLevel

/** 카카오 지도에서 가장 넓은 레벨. */
export const MAX_MAP_LEVEL = 14;

/** 단계별 시간(ms). 먼 거리는 합계 약 2초, 가까운 거리는 한 번의 미끄러짐이다. */
export const FLIGHT_MS = { near: 650, zoomOut: 550, pan: 850, zoomIn: 600 };

/** 축소한 레벨에서의 이동 거리 상한 — 화면 짧은 변에 대한 비율. */
const PAN_SPAN = 0.9;

/**
 * @typedef {{ x: number, y: number }} ScreenPoint
 * @typedef {{ kind: "zoom", level: number, anchor: ScreenPoint | null, duration: number }} ZoomStep
 * @typedef {{ kind: "pan", to: ScreenPoint, duration: number }} PanStep
 * @typedef {ZoomStep | PanStep} FlightStep
 */

/** @param {number} t */
export function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
}

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
 * 시작 레벨 화면 좌표(px)로 이동 단계를 정한다.
 * @param {{ from: ScreenPoint, to: ScreenPoint, fromLevel: number, toLevel: number,
 *   viewport: { width: number, height: number }, maxLevel?: number }} options
 * @returns {FlightStep[]}
 */
export function planFlight({ from, to, fromLevel, toLevel, viewport, maxLevel = MAX_MAP_LEVEL }) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const distance = Math.hypot(dx, dy);
  if (fromLevel === toLevel && distance < 1) return [];

  const halfWidth = viewport.width / 2;
  const halfHeight = viewport.height / 2;
  const targetScale = 2 ** (fromLevel - toLevel);
  const targetInView = Math.abs(dx) <= halfWidth && Math.abs(dy) <= halfHeight;
  const startInTarget = Math.abs(dx) * targetScale <= halfWidth && Math.abs(dy) * targetScale <= halfHeight;
  // 2026-11-21 사용자 결정 — 가까운 거리는 축소 없이 한 번에 미끄러지듯 옮긴다.
  if (targetInView || startInTarget) {
    if (fromLevel === toLevel) return [{ kind: "pan", to, duration: FLIGHT_MS.near }];
    return [{ kind: "zoom", level: toLevel, anchor: zoomAnchor(from, to, targetScale), duration: FLIGHT_MS.near }];
  }

  // 먼 거리는 두 지점이 한 화면 안에 들어오는 레벨까지 축소해 이동 경로를 보여 준다.
  const span = PAN_SPAN * Math.min(viewport.width, viewport.height);
  let midLevel = Math.max(fromLevel, toLevel);
  while (midLevel < maxLevel && distance / 2 ** (midLevel - fromLevel) > span) midLevel += 1;

  /** @type {FlightStep[]} */
  const steps = [];
  if (midLevel > fromLevel) steps.push({ kind: "zoom", level: midLevel, anchor: null, duration: FLIGHT_MS.zoomOut });
  steps.push({ kind: "pan", to, duration: FLIGHT_MS.pan });
  if (toLevel < midLevel) steps.push({ kind: "zoom", level: toLevel, anchor: null, duration: FLIGHT_MS.zoomIn });
  return steps;
}

const browserScheduler = {
  /** @param {FrameRequestCallback} callback */
  frame: (callback) => window.requestAnimationFrame(callback),
  /** @param {number} id */
  cancelFrame: (id) => window.cancelAnimationFrame(id),
  /** @param {() => void} callback @param {number} ms */
  wait: (callback, ms) => window.setTimeout(callback, ms),
  /** @param {number} id */
  clearWait: (id) => window.clearTimeout(id),
  now: () => performance.now(),
};

/** 지도별로 카카오 확대 애니메이션이 끝나는 시각. */
const zoomSettlesAt = new WeakMap();
/** 애니메이션 종료 직후 여유(ms). 2026-11-21 로컬 측정에서 0ms로도 충분했다. */
const SETTLE_MARGIN_MS = 30;

/**
 * 진행 중인 카카오 확대 애니메이션이 끝난 뒤 callback을 부른다. 반환한 함수로 대기를 취소한다.
 * 카카오는 진행 중인 setLevel 애니메이션을 멈출 방법이 없고, 끝날 때 원래 목표로 되돌아간다.
 * 그 사이에 setLevel·setCenter·setBounds를 부르면 애니메이션이 덮어써 엉뚱한 곳에 멈춘다.
 * @param {any} map
 * @param {() => void} callback
 * @param {any} [scheduler]
 * @returns {() => void}
 */
export function whenMapSettled(map, callback, scheduler = browserScheduler) {
  const remaining = (zoomSettlesAt.get(map) ?? 0) - scheduler.now();
  if (remaining <= 0) {
    callback();
    return () => {};
  }
  const id = scheduler.wait(callback, remaining + SETTLE_MARGIN_MS);
  return () => scheduler.clearWait(id);
}

/**
 * map.setBounds와 같은 화면으로 부드럽게 이동한다. 반환한 함수를 부르면 남은 이동을 멈춘다.
 * @param {any} kakao
 * @param {any} map
 * @param {any} bounds
 * @param {[number, number, number, number]} padding 위·오른쪽·아래·왼쪽(px)
 * @param {{ viewport: { width: number, height: number }, reduceMotion?: boolean, scheduler?: any }} options
 * @returns {() => void}
 */
export function flyMapToBounds(kakao, map, bounds, padding, { viewport, reduceMotion = false, scheduler = browserScheduler }) {
  let stopFlight = () => {};
  const stopWaiting = whenMapSettled(map, () => {
    stopFlight = startFlight(kakao, map, bounds, padding, viewport, reduceMotion, scheduler);
  }, scheduler);
  return () => {
    stopWaiting();
    stopFlight();
  };
}

/**
 * @param {any} kakao
 * @param {any} map
 * @param {any} bounds
 * @param {[number, number, number, number]} padding
 * @param {{ width: number, height: number }} viewport
 * @param {boolean} reduceMotion
 * @param {any} scheduler
 * @returns {() => void}
 */
function startFlight(kakao, map, bounds, padding, viewport, reduceMotion, scheduler) {
  const [top, right, bottom, left] = padding;
  // team-roles 6번 — 움직임 감소 설정이면 기존처럼 바로 맞춘다.
  // 지도 영역 크기를 모르면(0px) 경로를 계산할 수 없으므로 같은 방식으로 바로 맞춘다.
  if (reduceMotion || !(viewport.width > 0 && viewport.height > 0)) {
    map.setBounds(bounds, top, right, bottom, left);
    return () => {};
  }

  const startLevel = map.getLevel();
  const startCenter = map.getCenter();
  // 카카오가 고르는 최종 레벨·중심을 그대로 쓰려고 한 번 맞춘 뒤, 화면을 그리기 전에 되돌린다.
  map.setBounds(bounds, top, right, bottom, left);
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
  /** @param {ScreenPoint} point */
  const toCoords = (point) => projection.coordsFromContainerPoint(new kakao.maps.Point(point.x, point.y));
  const steps = planFlight({
    from: toScreen(startCenter),
    to: toScreen(endCenter),
    fromLevel: startLevel,
    toLevel: endLevel,
    viewport,
  }).map((step) => step.kind === "zoom" ? { ...step, anchor: step.anchor && toCoords(step.anchor) } : step);

  let cancelled = false;
  /** @type {any} */
  let frameId;
  /** @type {any} */
  let waitId;

  // 앵커 확대의 반올림 오차를 마지막에 정확한 목표로 맞춘다.
  const finish = () => {
    if (map.getLevel() !== endLevel) map.setLevel(endLevel, { animate: false });
    map.setCenter(endCenter);
  };

  /** @param {number} index */
  const run = (index) => {
    if (cancelled) return;
    const step = steps[index];
    if (!step) {
      finish();
      return;
    }
    if (step.kind === "zoom") {
      /** @type {{ animate: { duration: number }, anchor?: any }} */
      const options = { animate: { duration: step.duration } };
      if (step.anchor) options.anchor = step.anchor;
      map.setLevel(step.level, options);
      zoomSettlesAt.set(map, scheduler.now() + step.duration);
      waitId = scheduler.wait(() => run(index + 1), step.duration + 50);
      return;
    }
    const from = map.getCenter();
    const startedAt = scheduler.now();
    const tick = () => {
      if (cancelled) return;
      const progress = Math.min(1, (scheduler.now() - startedAt) / step.duration);
      const eased = easeInOutCubic(progress);
      map.setCenter(new kakao.maps.LatLng(
        from.getLat() + (endCenter.getLat() - from.getLat()) * eased,
        from.getLng() + (endCenter.getLng() - from.getLng()) * eased,
      ));
      if (progress < 1) frameId = scheduler.frame(tick);
      else run(index + 1);
    };
    frameId = scheduler.frame(tick);
  };
  run(0);

  return () => {
    cancelled = true;
    scheduler.cancelFrame(frameId);
    scheduler.clearWait(waitId);
  };
}
