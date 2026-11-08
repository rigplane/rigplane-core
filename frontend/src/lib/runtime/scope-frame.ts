import type { ScopeGranted, View } from './scope-view';

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

export function parseCurrentScopeFrame(buffer: ArrayBuffer, context: CurrentScopeContext): ScopeParseResult {
  if (buffer.byteLength < 56) return { ok: false, reason: 'short header' };
  const wire = new DataView(buffer);
  if (wire.getUint8(0) !== 2) return { ok: false, reason: 'message type' };
  const source = (['radio', 'audio_fft', 'iq_fft'] as const)[wire.getUint8(1)];
  if (source === undefined) return { ok: false, reason: 'source' };
  const flags = wire.getUint8(2);
  if ((flags & ~3) !== 0) return { ok: false, reason: 'flags' };
  const identityLength = wire.getUint8(3);
  const width = wire.getUint16(6, true);
  const pixelOffset = 56 + identityLength;
  const hasPixels = (flags & 2) !== 0;
  if (width === 0 || buffer.byteLength !== pixelOffset + (hasPixels ? width : 0)) {
    return { ok: false, reason: 'pixel length' };
  }
  const identity = new Uint8Array(buffer, 56, identityLength);
  if (identityLength === 0 || identity.some(byte => byte < 33 || byte > 126)) {
    return { ok: false, reason: 'source id' };
  }
  const sentTime = wire.getBigInt64(16, true);
  if (sentTime < -1n || sentTime > BigInt(Number.MAX_SAFE_INTEGER)) return { ok: false, reason: 'sent time' };
  const startHz = wire.getFloat64(24, true);
  const endHz = wire.getFloat64(32, true);
  const coverageStartHz = wire.getFloat64(40, true);
  const coverageEndHz = wire.getFloat64(48, true);
  if (![startHz, endHz, coverageStartHz, coverageEndHz].every(Number.isFinite)
    || startHz < 0 || endHz <= startHz
    || coverageStartHz < startHz || coverageEndHz < coverageStartHz || coverageEndHz > endHz) {
    return { ok: false, reason: 'frequency range' };
  }
  const receiver = wire.getUint16(4, true);
  return { ok: true, frame: Object.freeze({
    source, sourceId: String.fromCharCode(...identity), epoch: wire.getUint32(8, true),
    receiver: receiver === 65535 ? null : receiver, seq: wire.getUint32(12, true),
    sentAtMs: sentTime === -1n ? null : Number(sentTime),
    viewStartHz: startHz, viewEndHz: endHz, coverageStartHz, coverageEndHz,
    width, pixels: hasPixels ? new Uint8Array(buffer, pixelOffset, width) : null,
    outOfRange: (flags & 1) !== 0,
  }) };
}

/** Qualify the server's negotiation facts before they enter stream geometry. */
export function parseScopeGranted(message: unknown): ScopeGranted | null {
  if (message === null || typeof message !== 'object') return null;
  const value = message as Record<string, unknown>;
  const view = (input: unknown): input is ScopeGranted['view'] => {
    if (input === null || typeof input !== 'object') return false;
    const candidate = input as Record<string, unknown>;
    return typeof candidate.startHz === 'number' && Number.isFinite(candidate.startHz) && candidate.startHz >= 0
      && typeof candidate.endHz === 'number' && Number.isFinite(candidate.endHz) && candidate.endHz > candidate.startHz;
  };
  if (value.type !== 'granted' || typeof value.sourceId !== 'string' || !/^[!-~]{1,255}$/.test(value.sourceId)
    || !(value.receiver === null || (Number.isInteger(value.receiver) && (value.receiver as number) >= 0 && (value.receiver as number) < 65535))
    || !view(value.view) || !(value.coverage === null || view(value.coverage))
    || !Number.isInteger(value.width) || (value.width as number) < 1 || (value.width as number) > 65535
    || typeof value.fps !== 'number' || !Number.isFinite(value.fps) || value.fps < 0) return null;
  if (value.coverage && (value.coverage.startHz < value.view.startHz || value.coverage.endHz > value.view.endHz)) return null;
  return { type: 'granted', sourceId: value.sourceId, receiver: value.receiver as number | null,
    view: { ...value.view }, coverage: value.coverage === null ? null : { ...value.coverage }, width: value.width as number, fps: value.fps };
}

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
