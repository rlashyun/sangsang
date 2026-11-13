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
  const aboutTwoKilometersNorth = { latitude: 37.5844864, longitude: 126.9780 };
  const aboutFourKilometersNorth = { latitude: 37.6024728, longitude: 126.9780 };
  const aboutSixKilometersNorth = { latitude: 37.6204592, longitude: 126.9780 };
  assert.equal(isWithinRadius(seoul, aboutTwoKilometersNorth, 1000), false);
  assert.equal(isWithinRadius(seoul, aboutTwoKilometersNorth, 3000), true);
  assert.equal(isWithinRadius(seoul, aboutFourKilometersNorth, 3000), false);
  assert.equal(isWithinRadius(seoul, aboutFourKilometersNorth, 5000), true);
  assert.equal(isWithinRadius(seoul, aboutSixKilometersNorth, 5000), false);
  assert.equal(markerCountLabel(0), "0");
  assert.equal(markerCountLabel(403), "403");
  assert.equal(markerCountLabel(1500), "999+");
});

test("search radius moves one step at a time between 1km and 2km", async () => {
  const { nextSearchRadiusMeters, previousSearchRadiusMeters } = await import("../../frontend/src/lib/geo.js");
  assert.equal(nextSearchRadiusMeters(1000), 1500);
  assert.equal(nextSearchRadiusMeters(1500), 2000);
  assert.equal(nextSearchRadiusMeters(2000), null);
  assert.equal(previousSearchRadiusMeters(2000), 1500);
  assert.equal(previousSearchRadiusMeters(1500), 1000);
  assert.equal(previousSearchRadiusMeters(1000), null);
});
