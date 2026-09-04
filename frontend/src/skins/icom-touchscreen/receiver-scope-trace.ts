/**
 * Dependency-free trace drawing for App-owned scope amplitude bins.
 *
 * The established scope-frame domain is 0..160. This helper deliberately
 * draws only that supplied trace: no grid, labels, carrier, passband, tuning
 * marker, averaging, peak hold, subscription, or runtime-owned state.
 */
const SCOPE_PIXEL_MAX = 160;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function renderReceiverScopeTrace(
  context: CanvasRenderingContext2D,
  pixels: Uint8Array,
  width: number,
  height: number,
): void {
  if (pixels.length === 0 || width <= 0 || height <= 0) return;

  context.strokeStyle = '#c9e6df';
  context.lineWidth = 1.2;
  context.beginPath();

  for (let x = 0; x < width; x += 1) {
    const index = Math.min(pixels.length - 1, Math.floor((x / width) * pixels.length));
    const fraction = clamp(pixels[index] / SCOPE_PIXEL_MAX, 0, 1);
    const y = height * (1 - fraction);
    if (x === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  }

  context.stroke();
}
