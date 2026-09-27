export const SEARCH_RADIUS_METERS = 1000;
const EARTH_RADIUS_METERS = 6371008.8;

/** @param {number} value */
function toRadians(value) {
  return value * Math.PI / 180;
}

/**
 * @param {{ latitude: number, longitude: number }} from
 * @param {{ latitude: number, longitude: number }} to
 */
export function distanceMeters(from, to) {
  const latitudeDelta = toRadians(to.latitude - from.latitude);
  const longitudeDelta = toRadians(to.longitude - from.longitude);
  const fromLatitude = toRadians(from.latitude);
  const toLatitude = toRadians(to.latitude);
  const a = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(fromLatitude) * Math.cos(toLatitude) * Math.sin(longitudeDelta / 2) ** 2;
  return 2 * EARTH_RADIUS_METERS * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** @param {unknown} value */
export function markerCountLabel(value) {
  const count = Math.max(0, Number(value) || 0);
  return count > 999 ? "999+" : Math.floor(count).toString();
}
