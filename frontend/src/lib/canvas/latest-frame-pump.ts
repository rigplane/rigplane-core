// Framework-agnostic latest-wins frame pump (MOR-4206).
//
// A producer (scope WebSocket fan-out) can deliver frames faster than the
// display can paint them. Painting every delivery synchronously turns any
// event-loop stall into a backlog that then drains as a render burst — the
// waterfall visibly "speeds up" while the main thread saturates. The pump
// keeps only the LATEST pending frame and paints it once per animation
// frame: one render per rAF, stale frames dropped (and counted), so the
// paint rate is bounded by the display rate and no backlog can form.
//
// When arrivals are slower than the display (the normal case — a 8-30 fps
// scope stream on a 60 Hz display) every frame paints, exactly as an
// uncoalesced synchronous paint would.

export interface FramePumpTiming {
  requestFrame(callback: FrameRequestCallback): number;
  cancelFrame(id: number): void;
}

const browserTiming: FramePumpTiming = {
  requestFrame: (callback) => requestAnimationFrame(callback),
  cancelFrame: (id) => cancelAnimationFrame(id),
};

export class LatestFramePump<T> {
  private _pending: T | null = null;
  private _rafId: number | null = null;
  private _droppedCount = 0;

  constructor(
    private readonly _paint: (frame: T) => void,
    private readonly _options: {
      onDrop?: (dropped: T) => void;
      timing?: FramePumpTiming;
    } = {},
  ) {}

  /** Frames replaced before they could paint, since construction. */
  get droppedCount(): number {
    return this._droppedCount;
  }

  /** A frame is waiting for the next animation frame. */
  get pending(): boolean {
    return this._pending !== null;
  }

  /** Offer a frame. Replaces (drops) any frame still awaiting paint. */
  push(frame: T): void {
    const timing = this._options.timing ?? browserTiming;
    if (this._pending !== null) {
      this._droppedCount += 1;
      this._options.onDrop?.(this._pending);
    }
    this._pending = frame;
    if (this._rafId === null) {
      this._rafId = timing.requestFrame(this._tick);
    }
  }

  /** Cancel the scheduled paint and discard the pending frame. */
  dispose(): void {
    const timing = this._options.timing ?? browserTiming;
    if (this._rafId !== null) {
      timing.cancelFrame(this._rafId);
      this._rafId = null;
    }
    this._pending = null;
  }

  private readonly _tick: FrameRequestCallback = () => {
    this._rafId = null;
    const frame = this._pending;
    this._pending = null;
    if (frame !== null) this._paint(frame);
  };
}
