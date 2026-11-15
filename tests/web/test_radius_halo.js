const test = require("node:test");
const assert = require("node:assert/strict");

// PRD 4.4 — 실제 SDK/외부 API 없이 줌·이동·반경 변경과 제거를 검증한다.
test("radius halo follows map projection and cleans up without native circles", async () => {
  const { createRadiusHalo } = await import("../../frontend/src/lib/radius-halo.js");
  const previousDocument = global.document;
  const children = new Set();
  const view = { scale: 10000, x: 500, y: 350 };
  global.document = {
    createElement() {
      return {
        style: { setProperty(name, value) { this[name] = value; } },
        setAttribute() {},
        remove() { children.delete(this); },
      };
    },
  };
  class LatLng {
    constructor(latitude, longitude) { this.latitude = latitude; this.longitude = longitude; }
  }
  class AbstractOverlay {
    setMap(map) {
      if (this.map) this.onRemove();
      this.map = map;
      if (map) { this.onAdd(); this.draw(); }
    }
    getPanels() { return { overlayLayer: { prepend(node) { children.add(node); } } }; }
    getProjection() {
      return { pointFromCoords(point) {
        return { x: (point.longitude - 127) * view.scale + view.x,
          y: -(point.latitude - 37.5) * view.scale + view.y };
      } };
    }
  }
  const kakao = { maps: { LatLng, AbstractOverlay } };
  const center = { latitude: 37.5, longitude: 127 };
  let halo;
  try {
    halo = createRadiusHalo(kakao, {}, center, 1000, "#79acf3");
    const node = [...children][0];
    assert.ok(Number(node.style.zIndex) < 0, "반경 면은 기본 마커/클러스터 아래에 있어야 한다");
    const width = parseFloat(node.style.width);
    const height = parseFloat(node.style.height);
    assert.ok(width > 0 && height > 0);
    assert.equal(node.style["--radius-halo-color"], "#79acf3");
    assert.equal(parseFloat(node.style.left) + width / 2, view.x);
    assert.equal(parseFloat(node.style.top) + height / 2, view.y);
    view.scale *= 2;
    halo.draw();
    assert.equal(parseFloat(node.style.width), width * 2);
    assert.equal(parseFloat(node.style.height), height * 2);
    const zoomedWidth = parseFloat(node.style.width);
    const left = parseFloat(node.style.left);
    view.x -= 1200; // 중심점이 화면 밖이어도 일부 반경이 사라지지 않아야 한다.
    halo.draw();
    assert.equal(parseFloat(node.style.width), zoomedWidth);
    assert.equal(parseFloat(node.style.left), left - 1200);
    assert.equal(children.size, 1);
    halo.setMap(null);
    assert.equal(children.size, 0);
    halo = createRadiusHalo(kakao, {}, center, 2000, "#79acf3");
    assert.ok(Math.abs(parseFloat([...children][0].style.width) / zoomedWidth - 2) < .001);
    assert.equal(children.size, 1);
  } finally {
    halo?.setMap(null);
    global.document = previousDocument;
  }
  assert.equal(children.size, 0);
});
