/**
 * Pure arithmetic for the MOR-1160 "fixed-native" stage model: content is
 * authored at one native size, then scaled UNIFORMLY (one ratio, not
 * independent X/Y stretch) to fit whatever host box it is measured against,
 * never larger than its authored size. Kept here, DOM-free, so the formula
 * is unit-tested without mounting a component.
 *
 * This primitive does not resolve a minimum viable size (no `minScale`) —
 * that decision belongs to the layout-resolution module under
 * `presentation/layouts/`, which this file deliberately does not import,
 * keeping the primitive free of the layout contract.
 */

export interface StageBox {
  readonly width: number;
  readonly height: number;
}

/** The stage never grows past its authored (native) size. */
export const MAX_STAGE_SCALE = 1;

/**
 * Returns the uniform scale factor that fits `native` inside `host` — the
 * shorter of the two axis ratios wins — capped at `MAX_STAGE_SCALE` so the
 * stage never grows past its authored size.
 *
 * Returns 0 for a host that has not been measured yet (zero-size box) or a
 * degenerate native size (zero width or height).
 */
export function computeStageScale(host: StageBox, native: StageBox): number {
  if (native.width <= 0 || native.height <= 0) return 0;
  const fitWidth = host.width / native.width;
  const fitHeight = host.height / native.height;
  return Math.min(fitWidth, fitHeight, MAX_STAGE_SCALE);
}
