import { vi } from 'vitest';
import { webglFixture } from './webgl-fixture';
  class RecordedPath {
    readonly points: [number, number, boolean][] = [];
    moveTo(x: number, y: number) { this.points.push([x, y, true]); }
    lineTo(x: number, y: number) { this.points.push([x, y, false]); }
    closePath() {}
  }

const wrapped = new WeakSet<CanvasRenderingContext2D>();

/** Replays cached-layer and Path2D operations into a recording context. */
export function recordCanvasLayers(ctx: CanvasRenderingContext2D) {
  if (!ctx.canvas || typeof ctx.canvas !== 'object') {
    Object.defineProperty(ctx, 'canvas', { value: { width: 800, height: 400 }, configurable: true });
  }
  for (const method of ['setTransform', 'drawImage', 'rect', 'save', 'restore', 'clip'] as const) {
    if (typeof ctx[method] !== 'function') Object.defineProperty(ctx, method, { value: () => {}, configurable: true });
  }
  vi.stubGlobal('Path2D', RecordedPath);
  if (!wrapped.has(ctx)) {
  wrapped.add(ctx);
  const stroke = ctx.stroke.bind(ctx);
  ctx.stroke = ((path?: RecordedPath) => {
    if (path instanceof RecordedPath) {
      ctx.beginPath();
      for (const [x, y, move] of path.points) { if (move) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
    }
    stroke();
  }) as typeof ctx.stroke;
  }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, kind: string) { return kind === 'webgl2' ? webglFixture(this.width, this.height).gl : ctx; });
  return ctx;
}
