"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const element = {
  addEventListener() {},
  querySelector() { return element; },
  classList: { add() {}, remove() {}, toggle() {} },
};
const document = {
  querySelector() { return element; },
  querySelectorAll() { return []; },
};
const kakao = { maps: { load() {} } };
const sourcePath = require("node:path").join(
  __dirname,
  "..",
  "..",
  "src",
  "retriever_lost_found",
  "web",
  "static",
  "app.js",
);
const fullSource = fs.readFileSync(sourcePath, "utf8");
const source = fullSource.slice(fullSource.indexOf("(() => {")).replace(
  "  initMap();",
  "  globalThis.mapRadiusTest = { distanceMeters, markerCountLabel };",
);
const context = {
  document,
  kakao,
  console,
  createFoundItemBrowser() {
    return { reset() {}, setRadiusScope() {}, showInstitution() {} };
  },
  escapeHtml(value) { return String(value || ""); },
  async fetchJson() { return {}; },
};
vm.runInNewContext(source, context);

const { distanceMeters } = context.mapRadiusTest;
const { markerCountLabel } = context.mapRadiusTest;
const seoul = { latitude: 37.5665, longitude: 126.9780 };
assert.equal(distanceMeters(seoul, seoul), 0);

const aboutOneKilometerNorth = { latitude: 37.5754932, longitude: 126.9780 };
const distance = distanceMeters(seoul, aboutOneKilometerNorth);
assert.ok(distance >= 999 && distance <= 1001, `expected about 1000m, received ${distance}`);
assert.equal(markerCountLabel(0), "0");
assert.equal(markerCountLabel(403), "403");
assert.equal(markerCountLabel(1500), "999+");

console.log("radius geometry tests passed");
