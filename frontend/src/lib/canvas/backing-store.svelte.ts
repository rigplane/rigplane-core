import { getStageScale } from '../../primitives/stage/stage-scale';

/** Backing store for a canvas inside a CSS-scaled stage (MOR-1161). */

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
 * Re-runs `apply` whenever the enclosing `ScaledStage` changes its scale.
 *
 * `transform: scale()` does not resize the canvas's own box, so the canvas's
 * `ResizeObserver` never fires for it — without this the backing store stays
 * at the scale from mount and the stage resamples it. Outside a stage
 * `getStageScale()` returns 1 and `apply` runs once. The read of the scale
 * is what subscribes the effect; drop it and a scale change stops redrawing.
 */
export function watchStageScale(apply: () => void): void {
  $effect(() => {
    getStageScale()();
    apply();
  });
}

/** Calls `onChange` when the window pixel ratio changes. Returns the unsubscribe. */
export function watchDevicePixelRatio(onChange: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => {};
  let query = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
  const onMediaChange = () => {
    query.removeEventListener('change', onMediaChange);
    onChange();
    query = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    query.addEventListener('change', onMediaChange);
  };
  query.addEventListener('change', onMediaChange);
  return () => query.removeEventListener('change', onMediaChange);
}
