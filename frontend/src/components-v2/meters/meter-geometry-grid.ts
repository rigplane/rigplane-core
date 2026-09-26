// MOR-2613. The default S-meter face stretches a fixed viewBox to the rendered
// width, so one user unit is not one device pixel. Geometry that moves every
// animation frame is snapped to whole device pixels of that width. When the
// width is not known yet (and in jsdom, which never measures it) the fallback
// grid is half a user unit.

export const GEOMETRY_FALLBACK_STEP = 0.5;

/**
 * Snap a user-unit length to the device-pixel grid.
 *
 * `pixelsPerUserUnit` is the rendered CSS pixels one user unit occupies.
 * Unknown or non-positive means the width has not been measured, and the
 * value snaps to the 0.5-unit fallback instead.
 */
export function quantizeUserUnits(value: number, pixelsPerUserUnit: number): number {
  const step = pixelsPerUserUnit > 0 ? 1 / pixelsPerUserUnit : GEOMETRY_FALLBACK_STEP;
  return Math.round(value / step) * step;
}
