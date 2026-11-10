// MOR-4388 — page-side instrumentation and measurement helpers.
//
// The wrappers mirror the proven journey instrumentation
// (panorama-settle.spec.ts: raf/texImage2D/drawArrays/fillText wraps) and
// add what the matrix invariants need: the drawn axis-label history and
// per-draw waterfall row/column analysis, read inside the same task as the
// draw so an un-preserved WebGL drawing buffer is still valid.

import type { Page } from '@playwright/test';
import { parseCurrentScopeFrame, parseScopeGranted } from '../../../src/lib/runtime/scope-frame';
import type { ScopeFrame } from '../../../src/lib/runtime/adapters/scope-adapter';
import type { ScopeGranted, ScopeSubscription } from '../../../src/lib/runtime/scope-view';
import type { WsCommand, WsIncoming } from '../../../src/lib/types/protocol';
import { ACK_TRACKER_PROBE_ID, type CommandErrorObservation } from './matrix-contract';

export interface AxisSample { readonly atMs: number; readonly labels: readonly string[] }
export interface WaterfallRowSummary {
  readonly y: number; readonly peakColumn: number; readonly strength: number;
  readonly left: number; readonly right: number;
}
export interface WaterfallCapture {
  readonly atMs: number; readonly width: number; readonly height: number;
  readonly rows: readonly WaterfallRowSummary[];
}

interface MatrixGlobal {
  rafCount: number;
  rafTotalMs: number;
  axisHistory: AxisSample[];
  waterfallCaptures: WaterfallCapture[];
  waterfallDraws: number;
  wantFullCapture: boolean;
  pageErrors: string[];
}
declare global {
  interface Window { __matrix?: MatrixGlobal }
}

export const MATRIX_INIT = (): void => {
  const global: MatrixGlobal = {
    rafCount: 0, rafTotalMs: 0, axisHistory: [], waterfallCaptures: [], waterfallDraws: 0,
    wantFullCapture: false, pageErrors: [],
  };
  window.__matrix = global;
  window.addEventListener('error', event => { global.pageErrors.push(event.message); });
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = callback => raf(time => {
    const start = performance.now(); global.rafCount++;
    try { callback(time); } finally { global.rafTotalMs += performance.now() - start; }
  });
  // Axis labels: every numeric d.ddddd text painted on a 2D context, kept
  // with its paint time so a view change can be located in history. A paint
  // at the left edge (x === 20, the settle spec's heuristic) starts a new
  // axis sample.
  const fillText = CanvasRenderingContext2D.prototype.fillText;
  CanvasRenderingContext2D.prototype.fillText = function (this: CanvasRenderingContext2D, ...args: unknown[]) {
    const text = String(args[0]);
    if (/^\d+\.\d{3}$/.test(text) && typeof args[1] === 'number') {
      if (args[1] === 20) global.axisHistory.push({ atMs: performance.now(), labels: [] });
      const sample = global.axisHistory[global.axisHistory.length - 1];
      if (sample) (sample.labels as string[]).push(text);
      if (global.axisHistory.length > 600) global.axisHistory.splice(0, global.axisHistory.length - 600);
    }
    return Reflect.apply(fillText, this, args as Parameters<typeof fillText>);
  };
  // Waterfall: analyse the drawn framebuffer right after each drawArrays,
  // while the drawing buffer is guaranteed valid. On request (or the first
  // draw after one) the whole canvas is summarised row by row: per row the
  // brightest column (the injected bump is ~235 over a ≤44 floor), its
  // neighbours for sub-pixel position, and the strength.
  const glPrototype = WebGL2RenderingContext.prototype;
  const drawArrays = glPrototype.drawArrays;
  glPrototype.drawArrays = function (this: WebGL2RenderingContext, ...args: unknown[]) {
    const result = Reflect.apply(drawArrays, this, args as Parameters<typeof drawArrays>);
    try {
      const canvas = this.canvas;
      const isWaterfall = document.querySelector('.waterfall-content canvas') === canvas;
      if (isWaterfall && (global.wantFullCapture || global.waterfallCaptures.length === 0)) {
        const width = this.drawingBufferWidth, height = this.drawingBufferHeight;
        if (width > 0 && height > 0) {
          const pixels = new Uint8Array(width * height * 4);
          this.readPixels(0, 0, width, height, this.RGBA, this.UNSIGNED_BYTE, pixels);
          // The plot's edge columns carry border/edge artefacts, not
          // signal (confirmed on the rc8 screenshots); the peak search
          // stays inside the inner band.
          const margin = Math.max(3, Math.floor(width * 0.01));
          const rows: WaterfallRowSummary[] = [];
          for (let y = 0; y < height; y++) {
            let peak = 0, column = -1, left = 0, right = 0;
            for (let x = margin; x < width - margin; x++) {
              const offset = (y * width + x) * 4;
              const brightness = pixels[offset] + pixels[offset + 1] + pixels[offset + 2];
              if (brightness > peak) { peak = brightness; column = x; }
            }
            if (column > margin) {
              const at = (y * width + column - 1) * 4, rt = (y * width + column + 1) * 4;
              left = pixels[at] + pixels[at + 1] + pixels[at + 2];
              right = pixels[rt] + pixels[rt + 1] + pixels[rt + 2];
            }
            rows.push({ y, peakColumn: column, strength: peak, left, right });
          }
          global.waterfallCaptures.push({ atMs: performance.now(), width, height, rows });
          if (global.waterfallCaptures.length > 6) global.waterfallCaptures.shift();
          global.wantFullCapture = false;
        }
      }
      if (isWaterfall) global.waterfallDraws++;
    } catch { /* analysis must never break rendering */ }
    return result;
  };
};

export interface ObservedFrame {
  readonly view: readonly [number, number];
  readonly coverage: readonly [number, number];
  readonly width: number;
  readonly pixels: Uint8Array | null;
  readonly seq: number;
  readonly receivedAt: number;
}

export interface PageObservations {
  readonly subscriptions: Array<ScopeSubscription & { type: string }>;
  readonly grants: ScopeGranted[];
  readonly frames: ObservedFrame[];
  readonly commands: WsCommand[];
  readonly commandErrors: CommandErrorObservation[];
}

export function observePage(page: Page, observations: PageObservations): void {
  page.on('websocket', socket => {
    const path = new URL(socket.url()).pathname;
    if (path === '/api/v1/ws') {
      socket.on('framesent', ({ payload }) => {
        if (typeof payload === 'string') {
          const command = JSON.parse(payload) as WsCommand;
          // The ack-tracker probe's own forced read is the scenario, not a
          // page command: never let it pollute tuning/error evidence.
          if (command.type === 'cmd' && command.id !== ACK_TRACKER_PROBE_ID) observations.commands.push(command);
        }
      });
      // Command outcomes: a refused command (the ack-tracker false NAK
      // surfaces here) arrives as a failed response or an error message.
      socket.on('framereceived', ({ payload }) => {
        if (typeof payload !== 'string') return;
        let incoming: WsIncoming;
        try {
          incoming = JSON.parse(payload) as WsIncoming;
        } catch {
          return;
        }
        if (incoming.id === ACK_TRACKER_PROBE_ID) return;
        if (incoming.type === 'response' && incoming.ok === false) {
          observations.commandErrors.push({
            id: incoming.id ?? null,
            message: incoming.error ?? incoming.message ?? 'command failed',
          });
        } else if (incoming.type === 'error') {
          observations.commandErrors.push({
            id: incoming.id ?? null,
            message: incoming.message,
          });
        }
      });
    }
    if (path !== '/api/v1/scope') return;
    socket.on('framesent', ({ payload }) => {
      if (typeof payload === 'string') {
        const message = JSON.parse(payload) as ScopeSubscription & { type: string };
        if (message.type === 'subscribe') observations.subscriptions.push(message);
      }
    });
    socket.on('framereceived', ({ payload }) => {
      if (typeof payload === 'string') {
        const grant = parseScopeGranted(JSON.parse(payload));
        if (grant) observations.grants.push(grant);
      } else {
        const parsed = parseCurrentScopeFrame(Uint8Array.from(payload).buffer, {
          source: 'radio', sourceId: 'radio', epoch: 0,
        });
        if (parsed.ok) {
          observations.frames.push({
            view: [parsed.frame.viewStartHz, parsed.frame.viewEndHz],
            coverage: [parsed.frame.coverageStartHz, parsed.frame.coverageEndHz],
            width: parsed.frame.width,
            pixels: parsed.frame.pixels ?? null,
            seq: parsed.frame.seq,
            receivedAt: Date.now(),
          });
        }
      }
    });
  });
}

// ── In-page measurement ────────────────────────────────────────────────────

export interface CanvasAnalysis {
  readonly cssWidth: number;
  readonly backingWidth: number;
  readonly peakCssX: number | null;
  readonly peakStrength: number;
  readonly profileWidthPx: number | null;
  readonly debugHeights: string;
}

export interface TuneLineMeasurement {
  readonly visible: boolean;
  readonly centerCssX: number | null;
  readonly areaCssWidth: number;
}

export interface PageMeasurement {
  readonly spectrum: CanvasAnalysis | null;
  readonly tuneLineSpectrum: TuneLineMeasurement | null;
  readonly tuneLineWaterfall: TuneLineMeasurement | null;
  readonly rafCount: number;
  readonly rafTotalMs: number;
  readonly pageErrors: string[];
}

export async function requestWaterfallCapture(page: Page): Promise<void> {
  await page.evaluate(() => {
    const matrix = window.__matrix;
    if (matrix) matrix.wantFullCapture = true;
  });
  await page.waitForTimeout(300);
}

export async function measurePage(page: Page): Promise<PageMeasurement> {
  return page.evaluate(() => {
    const matrix = window.__matrix;
    const luminance = (data: Uint8ClampedArray, offset: number): number =>
      data[offset] + data[offset + 1] + data[offset + 2];
    const analyzeCanvas = (canvas: HTMLCanvasElement | null): CanvasAnalysis | null => {
      if (!canvas) return null;
      const box = canvas.getBoundingClientRect();
      if (box.width <= 0 || canvas.width <= 0) return null;
      const context = canvas.getContext('2d');
      if (!context) return null;
      const image = context.getImageData(0, 0, canvas.width, canvas.height);
      // Curve-height metric via vertical edges: per column, the topmost
      // strong brightness edge is where the filled trace meets the
      // background. Unlike a global threshold this is independent of the
      // plot's background fill (the rc8 trace paints an opaque scope
      // background, which defeated a threshold-based top-ink metric).
      const marginX = Math.floor(canvas.width * 0.02), marginY = Math.floor(canvas.height * 0.05);
      const heightOf = new Int32Array(canvas.width).fill(canvas.height);
      let maxEdge = 0;
      for (let x = marginX; x < canvas.width - marginX; x++) {
        for (let y = marginY; y < canvas.height - marginY - 1; y++) {
          const here = luminance(image.data, (y * canvas.width + x) * 4);
          const below = luminance(image.data, ((y + 1) * canvas.width + x) * 4);
          const edge = Math.abs(here - below);
          if (edge > maxEdge) maxEdge = edge;
          if (heightOf[x] === canvas.height && edge > 60) heightOf[x] = y;
        }
      }
      if (maxEdge <= 60) return null;
      let minTop = canvas.height;
      for (let x = marginX; x < canvas.width - marginX; x++) {
        if (heightOf[x] < minTop) minTop = heightOf[x];
      }
      if (minTop >= canvas.height) return null;
      // Height-weighted centroid of the peak region: columns whose curve
      // reaches within 2 px of the top, weighted by how close they are.
      let weightSum = 0, totalWeight = 0, count = 0;
      for (let x = marginX; x < canvas.width - marginX; x++) {
        const weight = Math.max(0, minTop + 2 - heightOf[x]);
        if (weight > 0) { weightSum += (x + 0.5) * weight; totalWeight += weight; count++; }
      }
      if (totalWeight <= 0 || count === 0) return null;
      const weighted = weightSum / totalWeight;
      const scale = box.width / canvas.width;
      // Coarse diagnostic: the curve height per 1/64th of the inner band.
      const buckets: number[] = [];
      const bucketWidth = (canvas.width - 2 * marginX) / 64;
      for (let bucket = 0; bucket < 64; bucket++) {
        let top = canvas.height;
        for (let x = marginX + Math.floor(bucket * bucketWidth); x < marginX + Math.floor((bucket + 1) * bucketWidth); x++) {
          if (heightOf[x] < top) top = heightOf[x];
        }
        buckets.push(canvas.height - top);
      }
      return {
        cssWidth: box.width,
        backingWidth: canvas.width,
        peakCssX: weighted * scale,
        peakStrength: maxEdge,
        profileWidthPx: Math.max(1, count * scale),
        debugHeights: buckets.join(','),
      };
    };
    const measureTuneLine = (area: Element | null): TuneLineMeasurement | null => {
      if (!area) return null;
      const areaBox = area.getBoundingClientRect();
      const line = area.querySelector('.tune-line');
      if (!line) return { visible: false, centerCssX: null, areaCssWidth: areaBox.width };
      const box = line.getBoundingClientRect();
      const visible = box.width > 0 && box.height > 0
        && getComputedStyle(line).display !== 'none' && getComputedStyle(line).visibility !== 'hidden';
      return {
        visible,
        centerCssX: visible ? box.left + box.width / 2 - areaBox.left : null,
        areaCssWidth: areaBox.width,
      };
    };
    return {
      spectrum: analyzeCanvas(document.querySelector('.spectrum-area canvas')),
      tuneLineSpectrum: measureTuneLine(document.querySelector('.spectrum-area')),
      tuneLineWaterfall: measureTuneLine(document.querySelector('.waterfall-content')),
      rafCount: matrix?.rafCount ?? 0,
      rafTotalMs: Math.round(matrix?.rafTotalMs ?? 0),
      pageErrors: [...(matrix?.pageErrors ?? [])],
    };
  });
}

export interface WaterfallRowAnalysis {
  readonly atMs: number;
  readonly cssWidth: number;
  readonly cssScale: number;
  readonly topRow: WaterfallRowSummary | null;
  readonly bottomRow: WaterfallRowSummary | null;
  readonly bottomStrength: number;
  readonly topStrength: number;
}

/** Summarise the latest full waterfall capture relative to a CSS box. */
export async function analyzeWaterfall(page: Page): Promise<WaterfallRowAnalysis | null> {
  const capture = await page.evaluate(() => {
    const matrix = window.__matrix;
    const last = matrix?.waterfallCaptures.at(-1);
    if (!last) return null;
    return { atMs: last.atMs, width: last.width, height: last.height,
      top: last.rows[last.height - 1] ?? null, belowTop: last.rows[last.height - 2] ?? null,
      // The older reference row: well above the possibly-uninitialised
      // bottom, old enough to predate every path's view change (~20 s of
      // rows at 10 fps for a 320-row canvas).
      bottom: last.rows[Math.floor(last.height * 0.35)] ?? null,
      bottomStrength: last.rows[Math.floor(last.height * 0.35)]?.strength ?? 0,
      topStrength: (last.rows[last.height - 1] ?? last.rows[last.height - 2])?.strength ?? 0 };
  });
  if (!capture) return null;
  const box = await page.locator('.waterfall-content').boundingBox();
  if (!box || capture.width <= 0) return null;
  const cssScale = box.width / capture.width;
  return {
    atMs: capture.atMs,
    cssWidth: box.width,
    cssScale,
    topRow: capture.top ?? capture.belowTop,
    bottomRow: capture.bottom,
    bottomStrength: capture.bottomStrength,
    topStrength: capture.topStrength,
  };
}

export function rowPeakCssX(row: WaterfallRowSummary | null, cssScale: number): number | null {
  if (!row || row.peakColumn < 0) return null;
  const { peakColumn, left, right, strength } = row;
  if (strength <= 0) return null;
  const total = left + strength + right;
  if (total <= 0) return (peakColumn + 0.5) * cssScale;
  const subpixel = peakColumn + (right - left) / (2 * total);
  return (subpixel + 0.5) * cssScale;
}

// ── Axis handling ──────────────────────────────────────────────────────────

export interface Axis { readonly startMHz: number; readonly endMHz: number }

export async function currentAxis(page: Page): Promise<Axis | null> {
  const sample = await page.evaluate(() => {
    const history = window.__matrix?.axisHistory ?? [];
    for (let index = history.length - 1; index >= 0; index--) {
      const labels = history[index].labels;
      if (labels.length >= 2) return { labels: [...labels] };
    }
    return null;
  });
  if (!sample) return null;
  const values = sample.labels.map(Number).filter(value => Number.isFinite(value));
  if (values.length < 2) return null;
  return { startMHz: Math.min(...values), endMHz: Math.max(...values) };
}

export function axisHzToCssX(hz: number, axis: Axis, cssWidth: number): number {
  return ((hz / 1e6 - axis.startMHz) / (axis.endMHz - axis.startMHz)) * cssWidth;
}

// ── Frame ground truth ─────────────────────────────────────────────────────

export interface BumpGroundTruth {
  readonly hz: number;
  readonly binIndex: number;
  readonly amplitude: number;
  readonly view: readonly [number, number];
  readonly basis: 'injected' | 'strongest' | 'iq-tone';
}

/**
 * Where the known signal is, in Hz, on the newest received frame's view.
 *
 * `injected` uses the simulator's injection contract — the gaussian bump
 * sits at a fixed FRACTION of the radio's own window (0.625 centre mode,
 * 0.40 fixed-edge mode, tests/e2e/panorama_web_server.py `_signal_pixels`)
 * — so the check does not assume the received bins map linearly onto the
 * view (on rc8 they do not; that is exactly what MOR-4385/4387 are about).
 * `strongest` (audio-FFT) and `iq-tone` (FakeIqSource tone at +96 kHz) are
 * the source-specific truths.
 */
export function bumpGroundTruth(
  frames: readonly ObservedFrame[],
  basis: 'injected' | 'strongest' | 'iq-tone',
  fraction: number,
): BumpGroundTruth | null {
  for (let index = frames.length - 1; index >= 0; index--) {
    const frame = frames[index];
    if (!frame.pixels || frame.pixels.length === 0) continue;
    let amplitude = -1, binIndex = -1;
    for (let bin = 0; bin < frame.pixels.length; bin++) {
      if (frame.pixels[bin] > amplitude) { amplitude = frame.pixels[bin]; binIndex = bin; }
    }
    if (binIndex < 0 || amplitude <= 0) continue;
    const span = frame.view[1] - frame.view[0];
    if (span <= 0) continue;
    if (basis === 'injected') {
      return { hz: Math.round(frame.view[0] + fraction * span), binIndex, amplitude, view: frame.view, basis };
    }
    if (basis === 'iq-tone') {
      const center = (frame.view[0] + frame.view[1]) / 2;
      const tone = center + 96_000;
      if (tone >= frame.view[0] && tone <= frame.view[1]) {
        return { hz: tone, binIndex, amplitude, view: frame.view, basis };
      }
    }
    const hz = Math.round(frame.view[0] + ((binIndex + 0.5) / frame.pixels.length) * span);
    return { hz, binIndex, amplitude, view: frame.view, basis: 'strongest' };
  }
  return null;
}

export type { ScopeFrame, ScopeGranted };
