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
  readonly source: ScopeFrame['source'];
  readonly sourceId: string;
  readonly epoch: number;
  readonly mode?: number;
}
export type ScopeParseError = 'short header' | 'message type' | 'pixel length' | 'frequency range' | 'source' | 'flags' | 'source id' | 'sent time';
export type ScopeParseResult = { readonly ok: true; readonly frame: ScopeFrame }
  | { readonly ok: false; readonly reason: ScopeParseError };

/** Freshness is host supplied; each accepted message marks its geometry fresh. */
export type ScopeFreshness = 'fresh' | 'stale';
export interface ScopeGeometry {
  readonly source: ScopeFrame['source'];
  readonly sourceId: string;
  readonly receiver: ScopeFrame['receiver'];
  /** Control-plane scope mode, or presentation fallback zero while unobserved. */
  readonly mode: number;
  readonly width: number;
  readonly view: View;
  readonly coverage: View;
  readonly outOfRange: boolean;
  readonly hasPixels: boolean;
  readonly freshness: ScopeFreshness;
}
/** Affine RF-bin coordinates for an AF axis; the original pixel plane is retained. */
export interface ScopeAudioProjection {
  readonly zero: number;
  readonly perHz: number;
  readonly spanHz: number;
}

export type ScopeHatchReason = 'outOfRange' | 'view wider than coverage' | 'no pixels';
export interface ScopeCoverageProjection {
  /** Normalized output columns, with sample coordinates relative to the granted frame view. */
  readonly covered: readonly { readonly start: number; readonly end: number; readonly sampleStart: number; readonly sampleEnd: number }[];
  readonly hatched: readonly { readonly start: number; readonly end: number; readonly reason: ScopeHatchReason }[];
}

export function projectScopeCoverage(geometry: ScopeGeometry, requested: View): ScopeCoverageProjection {
  const hatch = (reason: ScopeHatchReason): ScopeCoverageProjection => ({ covered: [], hatched: [{ start: 0, end: 1, reason }] });
  // IqFftScope._tx_frame retains pixels while setting the out-of-range flag.
  if (geometry.outOfRange && !geometry.hasPixels) return hatch('outOfRange');
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
