/**
 * Backing-store size for a canvas that may sit inside a CSS-scaled stage.
 *
 * `fixed-native` (MOR-1160) paints the stage with one `transform: scale()`.
 * SVG re-rasterizes under that transform; a canvas is resampled and blurs
 * unless its backing store already holds `cssSize * devicePixelRatio *
 * stageScale` device pixels (MOR-1161). Drawing then uses the same product
 * as the context transform, so one canvas pixel still maps to one device
 * pixel at integer multipliers.
 */

export interface CanvasBackingSize {
  readonly width: number;
  readonly height: number;
  /** `devicePixelRatio * stageScale`, the context transform and the store divisor. */
  readonly pixelScale: number;
}

function positiveScale(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 1;
}

export function canvasBackingSize(
  cssWidth: number,
  cssHeight: number,
  devicePixelRatio: number,
  stageScale: number,
): CanvasBackingSize {
  const pixelScale = positiveScale(devicePixelRatio) * positiveScale(stageScale);
  return {
    width: Math.max(1, Math.round(cssWidth * pixelScale)),
    height: Math.max(1, Math.round(cssHeight * pixelScale)),
    pixelScale,
  };
}

/**
 * Uniform scale of the nearest transformed ancestor, or 1.
 *
 * `getBoundingClientRect` already includes every ancestor transform, and
 * `offsetWidth` does not, so their ratio is the scale the canvas is painted
 * at — including `ScaledStage`'s `translate() scale()`. A non-uniform
 * stretch is not a stage scale and is ignored.
 */
export function readAncestorScale(element: HTMLElement): number {
  const layoutWidth = element.offsetWidth;
  const layoutHeight = element.offsetHeight;
  if (layoutWidth <= 0 || layoutHeight <= 0) return 1;
  const box = element.getBoundingClientRect();
  const scaleX = box.width / layoutWidth;
  const scaleY = box.height / layoutHeight;
  if (!Number.isFinite(scaleX) || scaleX <= 0) return 1;
  if (Math.abs(scaleX - scaleY) > 0.01) return 1;
  return scaleX;
}
