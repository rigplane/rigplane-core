import type { View } from './scope-view';

export interface ScopeFrame {
  readonly source: 'radio' | 'audio_fft' | 'iq_fft';
  readonly sourceId: string;
  readonly receiver: number | null;
  readonly viewStartHz: number;
  readonly viewEndHz: number;
  readonly coverageStartHz: number;
  readonly coverageEndHz: number;
  readonly width: number;
  readonly pixels: Uint8Array | null;
  readonly outOfRange: boolean;
  readonly epoch: number;
  readonly seq: number;
  readonly sentAtMs: number | null;
}

export interface CurrentScopeContext {
  readonly source: 'radio' | 'audio_fft';
  readonly sourceId: string;
  readonly epoch: number;
}
export type ScopeParseError = 'short header' | 'message type' | 'pixel length' | 'frequency range';
export type ScopeParseResult = { readonly ok: true; readonly frame: ScopeFrame }
  | { readonly ok: false; readonly reason: ScopeParseError };

/** Today's endpoint supplies source identity and session epoch; the wire has no timestamp. */
export function parseCurrentScopeFrame(buffer: ArrayBuffer, context: CurrentScopeContext): ScopeParseResult {
  if (buffer.byteLength < 16) return { ok: false, reason: 'short header' };
  const wire = new DataView(buffer);
  if (wire.getUint8(0) !== 1) return { ok: false, reason: 'message type' };
  const width = wire.getUint16(14, true);
  if (buffer.byteLength !== 16 + width) return { ok: false, reason: 'pixel length' };
  const startHz = wire.getUint32(3, true);
  const endHz = wire.getUint32(7, true);
  const outOfRange = (wire.getUint8(13) & 1) !== 0;
  if (width > 0 && !outOfRange && endHz <= startHz) return { ok: false, reason: 'frequency range' };
  return { ok: true, frame: {
    source: context.source, sourceId: context.sourceId, epoch: context.epoch,
    receiver: wire.getUint8(1), seq: wire.getUint16(11, true), sentAtMs: null,
    viewStartHz: startHz, viewEndHz: endHz, coverageStartHz: startHz, coverageEndHz: endHz,
    width, pixels: width === 0 ? null : new Uint8Array(buffer, 16, width), outOfRange,
  } };
}

/** Freshness is host supplied; each accepted message marks its geometry fresh. */
export type ScopeFreshness = 'fresh' | 'stale';
export interface ScopeGeometry {
  readonly source: ScopeFrame['source'];
  readonly sourceId: string;
  readonly receiver: ScopeFrame['receiver'];
  readonly view: View;
  readonly coverage: View;
  readonly outOfRange: boolean;
  readonly hasPixels: boolean;
  readonly freshness: ScopeFreshness;
}
export type ScopeHatchReason = 'outOfRange' | 'view wider than coverage' | 'no pixels';
export interface ScopeCoverageProjection {
  /** Normalized output columns, with sample coordinates relative to the granted frame view. */
  readonly covered: readonly { readonly start: number; readonly end: number; readonly sampleStart: number; readonly sampleEnd: number }[];
  readonly hatched: readonly { readonly start: number; readonly end: number; readonly reason: ScopeHatchReason }[];
}

export function projectScopeCoverage(geometry: ScopeGeometry, requested: View): ScopeCoverageProjection {
  const hatch = (reason: ScopeHatchReason): ScopeCoverageProjection => ({ covered: [], hatched: [{ start: 0, end: 1, reason }] });
  if (geometry.outOfRange) return hatch('outOfRange');
  if (!geometry.hasPixels) return hatch('no pixels');
  const startHz = Math.max(requested.startHz, geometry.view.startHz, geometry.coverage.startHz);
  const endHz = Math.min(requested.endHz, geometry.view.endHz, geometry.coverage.endHz);
  if (endHz <= startHz) return hatch('view wider than coverage');
  const span = requested.endHz - requested.startHz;
  const sampleSpan = geometry.view.endHz - geometry.view.startHz;
  const start = (startHz - requested.startHz) / span;
  const end = (endHz - requested.startHz) / span;
  const hatched: { start: number; end: number; reason: ScopeHatchReason }[] = [];
  if (start > 0) hatched.push({ start: 0, end: start, reason: 'view wider than coverage' });
  if (end < 1) hatched.push({ start: end, end: 1, reason: 'view wider than coverage' });
  return { covered: [{ start, end,
    sampleStart: (startHz - geometry.view.startHz) / sampleSpan,
    sampleEnd: (endHz - geometry.view.startHz) / sampleSpan,
  }], hatched };
}
