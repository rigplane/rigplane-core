import type { ScopeGeometry } from '$lib/runtime/scope-frame';

export type LcdSpectrumSource = 'audio-fft' | 'hardware';
export type LcdSpectrumReceiver = 'MAIN' | 'SUB';
export interface LcdSpectrumFrameExpectation {
  readonly source: LcdSpectrumSource;
  readonly receiver: LcdSpectrumReceiver | null;
}
export type LcdSpectrumFrameGhostReason =
  | 'missing' | 'stale' | 'source-mismatch' | 'receiver-unknown' | 'receiver-mismatch' | 'invalid';
export type LcdSpectrumFrameResolution =
  | { readonly state: 'live' | 'out-of-range'; readonly geometry: ScopeGeometry }
  | { readonly state: 'ghost'; readonly reason: LcdSpectrumFrameGhostReason };

/** LCD qualification uses geometry only; pixels remain in the stream sink. */
export function resolveLcdSpectrumFrame(
  geometry: ScopeGeometry | null | undefined,
  expectation: LcdSpectrumFrameExpectation,
): LcdSpectrumFrameResolution {
  if (expectation.receiver === null) return { state: 'ghost', reason: 'receiver-unknown' };
  if (geometry == null) return { state: 'ghost', reason: 'missing' };
  const sourceMatches = expectation.source === 'hardware'
    ? geometry.source === 'radio' || geometry.source === 'iq_fft' : geometry.source === 'audio_fft';
  if (!sourceMatches) {
    return { state: 'ghost', reason: 'source-mismatch' };
  }
  if (!(geometry.source !== 'radio' && geometry.receiver === null)
    && geometry.receiver !== (expectation.receiver === 'MAIN' ? 0 : 1)) {
    return { state: 'ghost', reason: 'receiver-mismatch' };
  }
  if (geometry.freshness !== 'fresh') return { state: 'ghost', reason: 'stale' };
  const headerOnlyOutOfRange = geometry.outOfRange && !geometry.hasPixels;
  if (!Number.isFinite(geometry.view.startHz) || !Number.isFinite(geometry.view.endHz)
    || geometry.view.endHz <= geometry.view.startHz
    || (!headerOnlyOutOfRange && (!geometry.hasPixels || geometry.width < 2))) {
    return { state: 'ghost', reason: 'invalid' };
  }
  return { state: headerOnlyOutOfRange ? 'out-of-range' : 'live', geometry };
}
