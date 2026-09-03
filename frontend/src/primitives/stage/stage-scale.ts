/**
 * Pure arithmetic for the MOR-1160 "fixed-native" stage model: content is
 * authored at one native size, then scaled UNIFORMLY (one ratio, not
 * independent X/Y stretch) to fit whatever host box it is measured against,
 * never larger than its authored size. Kept here, DOM-free, so the formula
 * is unit-tested without mounting a component.
 *
 * The minimum viable scale is not resolved here: it arrives as an optional
 * argument, so this file still imports nothing at all — no layout and no
 * group contract.
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
 * stage never grows past its authored size, and floored at `minScale` when
 * the caller declares one.
 *
 * With no `minScale`, the result is the unfloored fit for every input,
 * including 0 for a host that has not been measured yet (zero-size box). A
 * degenerate native size (zero width or height) returns 0 whether or not a
 * floor is given. `stage-scale.test.ts` pins each of those three cases.
 */
export function computeStageScale(host: StageBox, native: StageBox, minScale?: number): number {
  if (native.width <= 0 || native.height <= 0) return 0;
  const fitWidth = host.width / native.width;
  const fitHeight = host.height / native.height;
  const fit = Math.min(fitWidth, fitHeight, MAX_STAGE_SCALE);
  return minScale === undefined ? fit : Math.max(fit, minScale);
}

export interface StageOffset {
  readonly x: number;
  readonly y: number;
}

/**
 * Returns the `translate()` offset (in host-box CSS pixels) that keeps a
 * `scale`d `native` box centred inside `host`, for `ScaledStage`'s `anchor:
 * 'center'` prop (see that component's file header). The stage element sits
 * at `host`'s top-left corner before any transform, so — applied AFTER
 * `scale` in the same `transform` list, per CSS's composition order — this
 * offset shifts the already-shrunk box by half the leftover space on each
 * axis, independent of the host's own layout mode (flex, grid, or plain
 * flow): it depends only on the measured `host` box, not on how an ancestor
 * positioned it.
 *
 * Each axis is clamped at 0. A `minScale` floor can hold the scaled box
 * LARGER than `host`, where half the leftover is negative and would place
 * the box's start edge before the host's origin. Clamped, the box starts at
 * the origin instead. `stage-scale.test.ts` pins the both-axes and the
 * per-axis case.
 */
export function computeStageCenterOffset(host: StageBox, native: StageBox, scale: number): StageOffset {
  return {
    x: Math.max(0, (host.width - native.width * scale) / 2),
    y: Math.max(0, (host.height - native.height * scale) / 2),
  };
}
