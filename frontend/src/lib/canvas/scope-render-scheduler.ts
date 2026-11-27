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

type BrowserFrame = { callback: (now: number) => void; nativeId: number | null };
const browserFrames = new Map<number, BrowserFrame>();
let nextBrowserFrameId = 0;
let visibilityDocument: Document | null = null;

function documentHidden(): boolean {
  return typeof document !== 'undefined'
    && (document.hidden || document.visibilityState === 'hidden');
}

function releaseVisibilityIfIdle(): void {
  if (browserFrames.size !== 0) return;
  visibilityDocument?.removeEventListener('visibilitychange', onBrowserVisibility);
  visibilityDocument = null;
}

function scheduleBrowserFrame(id: number, frame: BrowserFrame): void {
  if (frame.nativeId !== null || documentHidden()) return;
  const nativeId = requestAnimationFrame((now) => {
    if (browserFrames.get(id) !== frame || frame.nativeId !== nativeId) return;
    frame.nativeId = null;
    if (documentHidden()) return;
    browserFrames.delete(id);
    try {
      frame.callback(now);
    } finally {
      releaseVisibilityIfIdle();
    }
  });
  frame.nativeId = nativeId;
}

function onBrowserVisibility(): void {
  for (const [id, frame] of browserFrames) {
    if (documentHidden()) {
      if (frame.nativeId !== null) cancelAnimationFrame(frame.nativeId);
      frame.nativeId = null;
    } else {
      scheduleBrowserFrame(id, frame);
    }
  }
}

/** Shared by scope redraws and meter animations; hidden work keeps its logical handle. */
export const browserRenderTiming: ScopeRenderTiming = {
  requestFrame(callback) {
    const id = ++nextBrowserFrameId;
    const frame: BrowserFrame = { callback, nativeId: null };
    browserFrames.set(id, frame);
    if (!visibilityDocument && typeof document !== 'undefined') {
      visibilityDocument = document;
      visibilityDocument.addEventListener('visibilitychange', onBrowserVisibility);
    }
    scheduleBrowserFrame(id, frame);
    return id;
  },
  cancelFrame(id) {
    const frame = browserFrames.get(id);
    if (frame?.nativeId != null) cancelAnimationFrame(frame.nativeId);
    browserFrames.delete(id);
    releaseVisibilityIfIdle();
  },
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

  /** True while a draw is pending, including during a hidden-document pause. */
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
    // Recheck activity: the surface may have gone hidden or collapsed
    // between the wake and this animation frame. The rAF is consumed either
    // way, but the DIRTINESS SURVIVES — the size/visibility wake that
    // re-activates the surface requests a fresh frame and paints the
    // still-cached content then.
    if (!(this.options.active?.() ?? true)) return;
    this.dirty = false;
    this.paint(now);
  };
}
