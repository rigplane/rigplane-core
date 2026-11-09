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

  private host: Element | null = null;
  private stageScaleValue = 1;
  private intersecting = false;
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

  /** Binds backing dimensions without observing the canvas. Returns the unbind function. */
  bindBacking(canvas: HTMLCanvasElement): () => void {
    const apply = (facts: CanvasSurfaceFacts) => {
      if (facts.backingW <= 0 || facts.backingH <= 0) return;
      (this.options.applyBacking ?? defaultApplyBacking)(canvas, {
        width: facts.backingW, height: facts.backingH, pixelScale: facts.pixelScale,
      });
    };
    apply(this.facts);
    return this.onSizeChanged(apply);
  }

  /** Observes the host and binds its backing store when it is a canvas. */
  attach(host: Element): () => void {
    this.host = host;
    const unbindBacking = host instanceof HTMLCanvasElement ? this.bindBacking(host) : () => {};
    this.intersecting = typeof IntersectionObserver === 'undefined';

    const resizeObserver = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      // The content box keeps its fractional size: the backing computation
      // rounds the product (css × pixelScale), and the pre-surface waterfall
      // fed the un-floored rect straight into it (MOR-4271 visual parity:
      // flooring here changed the backing height of a 324.6px box from 325
      // to 324 and shifted the painted top row). Components that
      // historically floored their draw geometry still do, at the draw.
      const w = Math.max(0, rect.width);
      const h = Math.max(0, rect.height);
      if (w === this.cssW && h === this.cssH) return;
      this.cssW = w;
      this.cssH = h;
      this.refreshBacking();
    });
    resizeObserver.observe(host);

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
    intersectionObserver?.observe(host);

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
      unbindBacking();
      this.host = null;
    };
  }

  private updateVisibility(): void {
    const next = this.intersecting && !document.hidden;
    if (next === this.visible) return;
    this.visible = next;
    for (const listener of this.visibilityListeners) listener(next);
  }

  private refreshBacking(): void {
    if (!this.host) return;
    if (this.cssW <= 0 || this.cssH <= 0) {
      this.publishFacts(0, 0, this.pixelScale);
      return;
    }
    const backing = canvasBackingSize(this.cssW, this.cssH, window.devicePixelRatio || 1, this.stageScaleValue);
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
