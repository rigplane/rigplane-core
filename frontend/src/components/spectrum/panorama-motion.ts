import { prefersReducedMotion } from '$lib/utils/smoothing.svelte';

/** Settle duration; the approved band is 80–120 ms. */
export const PANORAMA_SETTLE_MS = 100;

/** Ease-out cubic: monotone, exactly 0 at 0 and 1 at 1. */
export function panoramaEaseOutCubic(fraction: number): number {
  if (!Number.isFinite(fraction)) return 0;
  if (fraction <= 0) return 0;
  if (fraction >= 1) return 1;
  const inverse = 1 - fraction;
  return 1 - inverse * inverse * inverse;
}

export class PanoramaViewportCenter {
  private origin: number;
  private target: number;
  private originTime: number;

  constructor(initialHz: number) {
    this.origin = initialHz;
    this.target = initialHz;
    this.originTime = 0;
  }

  /** Aim at a new absolute center, starting from the sampled value. */
  retarget(targetHz: number, nowMs: number): void {
    if (targetHz === this.target) return;
    this.origin = this.sample(nowMs);
    this.target = targetHz;
    this.originTime = nowMs;
  }

  /** Hard snap: adopt the value and stop animating. */
  reset(valueHz: number, nowMs: number): void {
    this.origin = valueHz;
    this.target = valueHz;
    this.originTime = nowMs;
  }

  /** Viewport center at the given monotonic timestamp, in Hz. */
  sample(nowMs: number): number {
    if (prefersReducedMotion()) return this.target;
    const elapsed = nowMs - this.originTime;
    if (elapsed <= 0) return this.origin;
    if (elapsed >= PANORAMA_SETTLE_MS || this.target === this.origin) return this.target;
    return this.origin + (this.target - this.origin) * panoramaEaseOutCubic(elapsed / PANORAMA_SETTLE_MS);
  }

  /** True while an animation is still in flight at the given timestamp. */
  settling(nowMs: number): boolean {
    if (prefersReducedMotion()) return false;
    if (this.target === this.origin) return false;
    return nowMs - this.originTime < PANORAMA_SETTLE_MS;
  }
}
