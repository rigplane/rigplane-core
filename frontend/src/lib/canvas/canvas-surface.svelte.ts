/**
 * The one sizing/visibility mechanism for every scope canvas (MOR-4271):
 * ResizeObserver + devicePixelRatio + stage scale + visibility
 * (IntersectionObserver ∧ !document.hidden) behind one object. It applies the
 * canvas backing store and publishes the facts the render scheduler and the
 * upstream subscription demand (`backingW`, `visible`) are computed from.
 *
 * A size change never resets renderer state here — renderers invalidate their
 * own caches from the `sizeChanged` notification (MOR-4243 class: a
 * renderer-owned resize-coupled reset was the blank-audio-scope bug).
 *
 * Must be created during component initialization (or inside `$effect.root`):
 * the stage-scale subscription is an `$effect`.
 */

import { untrack } from 'svelte';
import { NO_STAGE_SCALE, type StageScaleSource } from '../../primitives/stage/stage-scale';

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

export interface CanvasSurfaceFacts {
  readonly cssW: number;
  readonly cssH: number;
  readonly backingW: number;
  readonly backingH: number;
  readonly pixelScale: number;
  readonly visible: boolean;
}

export interface CanvasSurfaceOptions {
  /** The enclosing `ScaledStage`'s scale source (`getStageScale()`); 1 outside a stage. */
  stageScale?: StageScaleSource;
  /**
   * Backing-store application override for components whose renderer owns the
   * canvas dimensions (the waterfall's `WaterfallRenderer.resize`). The
   * default sets `canvas.width/height` and the 2D context transform.
   */
  applyBacking?: (canvas: HTMLCanvasElement, backing: CanvasBackingSize) => void;
  /** IntersectionObserver root margin; defaults to the observer's own (the viewport). */
  rootMargin?: string;
}

function defaultApplyBacking(canvas: HTMLCanvasElement, backing: CanvasBackingSize): void {
  canvas.width = backing.width;
  canvas.height = backing.height;
  canvas.getContext('2d')?.setTransform(backing.pixelScale, 0, 0, backing.pixelScale, 0, 0);
}

/** Calls `onChange` when the window pixel ratio changes. Returns the unsubscribe. */
function watchDevicePixelRatio(onChange: () => void): () => void {
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

export class CanvasSurface {
  cssW = $state(0);
  cssH = $state(0);
  backingW = $state(0);
  backingH = $state(0);
  pixelScale = $state(1);
  visible = $state(false);

  private canvas: HTMLCanvasElement | null = null;
  private stageScaleValue = 1;
  private intersecting = false;
  private lastApplied: CanvasBackingSize | null = null;
  private readonly sizeListeners = new Set<(facts: CanvasSurfaceFacts) => void>();
  private readonly visibilityListeners = new Set<(visible: boolean) => void>();

  constructor(private readonly options: CanvasSurfaceOptions = {}) {
    const stageScale = options.stageScale ?? NO_STAGE_SCALE;
    $effect(() => {
      // The tracked scale read subscribes this effect: `transform: scale()`
      // never resizes the canvas's own box, so the ResizeObserver alone would
      // leave the backing store at the mount-time scale.
      this.stageScaleValue = positiveScale(stageScale());
      untrack(() => this.refreshBacking());
    });
  }

  get sized(): boolean {
    return this.cssW > 0 && this.cssH > 0;
  }

  get facts(): CanvasSurfaceFacts {
    return {
      cssW: this.cssW,
      cssH: this.cssH,
      backingW: this.backingW,
      backingH: this.backingH,
      pixelScale: this.pixelScale,
      visible: this.visible,
    };
  }

  onSizeChanged(listener: (facts: CanvasSurfaceFacts) => void): () => void {
    this.sizeListeners.add(listener);
    return () => this.sizeListeners.delete(listener);
  }

  onVisibilityChanged(listener: (visible: boolean) => void): () => void {
    this.visibilityListeners.add(listener);
    return () => this.visibilityListeners.delete(listener);
  }

  /** Attaches all observers to the canvas. Returns the detach function. */
  attach(canvas: HTMLCanvasElement): () => void {
    this.canvas = canvas;
    this.intersecting = typeof IntersectionObserver === 'undefined';

    const resizeObserver = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      const w = Math.max(0, Math.floor(rect.width));
      const h = Math.max(0, Math.floor(rect.height));
      if (w === this.cssW && h === this.cssH) return;
      this.cssW = w;
      this.cssH = h;
      this.refreshBacking();
    });
    resizeObserver.observe(canvas);

    const intersectionObserver =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver(
            (entries) => {
              this.intersecting = entries[0]?.isIntersecting === true;
              this.updateVisibility();
            },
            { rootMargin: this.options.rootMargin },
          );
    intersectionObserver?.observe(canvas);

    const stopDprWatch = watchDevicePixelRatio(() => this.refreshBacking());
    const onDocumentVisibility = () => this.updateVisibility();
    document.addEventListener('visibilitychange', onDocumentVisibility);

    this.updateVisibility();
    this.refreshBacking();

    return () => {
      resizeObserver.disconnect();
      intersectionObserver?.disconnect();
      stopDprWatch();
      document.removeEventListener('visibilitychange', onDocumentVisibility);
      this.canvas = null;
    };
  }

  private updateVisibility(): void {
    const next = this.intersecting && !document.hidden;
    if (next === this.visible) return;
    this.visible = next;
    for (const listener of this.visibilityListeners) listener(next);
  }

  private refreshBacking(): void {
    const canvas = this.canvas;
    if (!canvas) return;
    if (this.cssW <= 0 || this.cssH <= 0) {
      this.publishFacts(0, 0, this.pixelScale);
      return;
    }
    const backing = canvasBackingSize(this.cssW, this.cssH, window.devicePixelRatio || 1, this.stageScaleValue);
    const applied = this.lastApplied;
    if (!applied || applied.width !== backing.width || applied.height !== backing.height || applied.pixelScale !== backing.pixelScale) {
      (this.options.applyBacking ?? defaultApplyBacking)(canvas, backing);
      this.lastApplied = backing;
    }
    this.publishFacts(backing.width, backing.height, backing.pixelScale);
  }

  private publishFacts(backingW: number, backingH: number, pixelScale: number): void {
    const changed = backingW !== this.backingW || backingH !== this.backingH || pixelScale !== this.pixelScale;
    if (!changed) return;
    this.backingW = backingW;
    this.backingH = backingH;
    this.pixelScale = pixelScale;
    const facts = this.facts;
    for (const listener of this.sizeListeners) listener(facts);
  }
}
