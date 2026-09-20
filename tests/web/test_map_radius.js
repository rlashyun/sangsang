"use strict";

const assert = require("node:assert/strict");
const { pathToFileURL } = require("node:url");
const path = require("node:path");
const test = require("node:test");

test("radius geometry and marker labels", async () => {
  const sourcePath = path.join(
    __dirname,
    "..",
    "..",
    "frontend",
    "src",
    "lib",
    "geo.js",
  );
  const { distanceMeters, markerCountLabel } = await import(pathToFileURL(sourcePath));
  const seoul = { latitude: 37.5665, longitude: 126.9780 };
  assert.equal(distanceMeters(seoul, seoul), 0);

  const aboutOneKilometerNorth = { latitude: 37.5754932, longitude: 126.9780 };
  const distance = distanceMeters(seoul, aboutOneKilometerNorth);
  assert.ok(distance >= 999 && distance <= 1001, `expected about 1000m, received ${distance}`);
  assert.equal(markerCountLabel(0), "0");
  assert.equal(markerCountLabel(403), "403");
  assert.equal(markerCountLabel(1500), "999+");
});
