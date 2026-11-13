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
  const {
    DEFAULT_SEARCH_RADIUS_METERS,
    SEARCH_RADIUS_OPTIONS_METERS,
    distanceMeters,
    isWithinRadius,
    markerCountLabel,
    radiusKilometersLabel,
  } = await import(pathToFileURL(sourcePath));
  const seoul = { latitude: 37.5665, longitude: 126.9780 };
  assert.equal(distanceMeters(seoul, seoul), 0);

  assert.deepEqual(SEARCH_RADIUS_OPTIONS_METERS, [1000, 1500, 2000]);
  assert.equal(DEFAULT_SEARCH_RADIUS_METERS, 1000);
  assert.equal(radiusKilometersLabel(1000), "1km");
  assert.equal(radiusKilometersLabel(1500), "1.5km");
  assert.equal(radiusKilometersLabel(2000), "2km");

  const aboutOneKilometerNorth = { latitude: 37.5754932, longitude: 126.9780 };
  const distance = distanceMeters(seoul, aboutOneKilometerNorth);
  assert.ok(distance >= 999 && distance <= 1001, `expected about 1000m, received ${distance}`);
  const betweenOneAndOnePointFive = { latitude: 37.5775, longitude: 126.9780 };
  const betweenOnePointFiveAndTwo = { latitude: 37.5825, longitude: 126.9780 };
  const beyondTwo = { latitude: 37.5865, longitude: 126.9780 };
  assert.equal(isWithinRadius(seoul, seoul, 1000), true);
  assert.equal(isWithinRadius(seoul, betweenOneAndOnePointFive, 1000), false);
  assert.equal(isWithinRadius(seoul, betweenOneAndOnePointFive, 1500), true);
  assert.equal(isWithinRadius(seoul, betweenOnePointFiveAndTwo, 1500), false);
  assert.equal(isWithinRadius(seoul, betweenOnePointFiveAndTwo, 2000), true);
  assert.equal(isWithinRadius(seoul, beyondTwo, 2000), false);
  assert.equal(markerCountLabel(0), "0");
  assert.equal(markerCountLabel(403), "403");
  assert.equal(markerCountLabel(1500), "999+");
});

test("radius expands one step at a time and stops at 2km", async () => {
  const { nextSearchRadiusMeters } = await import("../../frontend/src/lib/geo.js");
  assert.equal(nextSearchRadiusMeters(1000), 1500);
  assert.equal(nextSearchRadiusMeters(1500), 2000);
  assert.equal(nextSearchRadiusMeters(2000), null);
  assert.equal(nextSearchRadiusMeters(3000), null);
});
