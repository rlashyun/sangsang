"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
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

  assert.deepEqual(SEARCH_RADIUS_OPTIONS_METERS, [1000, 3000, 5000]);
  assert.equal(DEFAULT_SEARCH_RADIUS_METERS, 1000);
  assert.equal(radiusKilometersLabel(1000), "1km");
  assert.equal(radiusKilometersLabel(3000), "3km");
  assert.equal(radiusKilometersLabel(5000), "5km");

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

test("radius selector is rendered below the found-item search form", () => {
  const componentPath = path.join(
    __dirname,
    "..",
    "..",
    "frontend",
    "src",
    "components",
    "FoundItemBrowser.tsx",
  );
  const summaryPath = path.join(
    __dirname,
    "..",
    "..",
    "frontend",
    "src",
    "components",
    "SelectionSummary.tsx",
  );
  const componentSource = readFileSync(componentPath, "utf8");
  const summarySource = readFileSync(summaryPath, "utf8");
  const searchFormEnd = componentSource.indexOf("</form>");
  const radiusSelector = componentSource.indexOf('<fieldset className="radius-selector">');
  const resultStatus = componentSource.indexOf('<div\n        className={`found-item-status');

  assert.ok(searchFormEnd >= 0, "found-item search form should exist");
  assert.ok(radiusSelector > searchFormEnd, "radius selector should follow the search form");
  assert.ok(resultStatus > radiusSelector, "result status should follow the radius selector");
  assert.equal(summarySource.includes("radius-selector"), false);
});
