/**
 * The one redraw scheduler for every scope canvas (MOR-4271): a single rAF
 * loop per visible surface, woken by a new frame, a View change, a size
 * change or a visibility change. Latest-wins: any number of wakes inside one
 * animation frame produce exactly one paint. While the surface is inactive
 * (hidden, off-screen or unsized) no animation frame is ever requested; the
 * wake that accompanies the return to visible repaints from the caller's
 * cached frame.
 */

export type ScopeRenderWakeReason = 'frame' | 'view' | 'size' | 'visibility';

export interface ScopeRenderTiming {
  requestFrame(cb: (now: number) => void): number;
  cancelFrame(id: number): void;
}

export const browserRenderTiming: ScopeRenderTiming = {
  requestFrame: (cb) => requestAnimationFrame(cb),
  cancelFrame: (id) => cancelAnimationFrame(id),
};

export interface ScopeRenderSchedulerOptions {
  /** Injectable frame clock; tests drive `flush()` instead of a real rAF. */
  timing?: ScopeRenderTiming;
  /**
   * Surface activity gate (typically `() => surface.visible && surface.sized`).
   * While it returns false, `wake()` only records dirtiness and never
   * requests a frame.
   */
  active?: () => boolean;
}

export class ScopeRenderScheduler {
  private rafId = 0;
  private dirty = false;
  private disposed = false;

  constructor(
    private readonly paint: (now: number) => void,
    private readonly options: ScopeRenderSchedulerOptions = {},
  ) {}

  /** True while an animation frame carrying a paint is scheduled. */
  get pending(): boolean {
    return this.rafId !== 0;
  }

  /**
   * Clears a pending frame and the recorded dirtiness; the scheduler stays
   * usable (the next wake requests a fresh frame). SpectrumPanel's panorama
   * glide cancels on retarget instead of letting a superseded tick paint.
   */
  cancel(): void {
    this.dirty = false;
    if (this.rafId !== 0) {
      (this.options.timing ?? browserRenderTiming).cancelFrame(this.rafId);
      this.rafId = 0;
    }
  }

  wake(_reason: ScopeRenderWakeReason = 'frame'): void {
    if (this.disposed) return;
    this.dirty = true;
    if (this.rafId !== 0) return;
    if (!(this.options.active?.() ?? true)) return;
    const timing = this.options.timing ?? browserRenderTiming;
    this.rafId = timing.requestFrame(this.tick);
  }

  dispose(): void {
    this.disposed = true;
    this.dirty = false;
    if (this.rafId !== 0) {
      (this.options.timing ?? browserRenderTiming).cancelFrame(this.rafId);
      this.rafId = 0;
    }
  }

  private readonly tick = (now: number): void => {
    this.rafId = 0;
    if (this.disposed || !this.dirty) return;
    this.dirty = false;
    this.paint(now);
  };
}
